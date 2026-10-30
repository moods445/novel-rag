"""向量构建：继续（断点续跑）、暂停（章节边界停）、重启后自动续跑。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from novel_rag.api.server import build_app
from novel_rag.config import Settings
from novel_rag.ingest import importer
from novel_rag.store import index as index_store

from .fixtures import SAMPLE_TEXT


class SlowEmbedder:
    """包一层真 embedder，放慢编码速度，制造「构建到一半」的时间窗口。"""

    def __init__(self, inner, delay: float = 0.2) -> None:
        self.inner = inner
        self.delay = delay
        self.model_id = inner.model_id
        self.dim = inner.dim
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        time.sleep(self.delay)
        return self.inner.embed(texts)


class VectorBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.settings = Settings(home=self.home, embedding_backend="hash", embedding_batch=8)
        self.app = build_app(settings=self.settings, db_path=str(self.home / "t.db"))
        self.book_id = importer.import_text(
            self.app.db, text=SAMPLE_TEXT, title="样本", settings=self.settings
        ).book.id

    def tearDown(self) -> None:
        self.app.db.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------ 辅助

    def _wait(self, states: tuple[str, ...], timeout: float = 15.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.app.vector_status(self.book_id)
            if status["job"].get("state") in states:
                return status
            time.sleep(0.05)
        raise AssertionError(f"任务未进入状态 {states}：{self.app.vector_status(self.book_id)['job']}")

    def _wait_done(self, timeout: float = 15.0) -> dict:
        return self._wait(("done", "paused", "failed"), timeout)

    # ------------------------------------------------------------ 用例

    def test_continue_builds_only_missing_chapters(self) -> None:
        """继续构建必须复用数据库里的断点，只补未完成的章节。"""
        index_store.index_vectors(
            self.app.db, self.book_id, self.app.embedder, chapter_idx=0, batch=8
        )
        index_store.index_vectors(
            self.app.db, self.book_id, self.app.embedder, chapter_idx=1, batch=8
        )
        before = self.app.vector_status(self.book_id)
        self.assertEqual(before["chapters_done"], 2)

        touched: list[int | None] = []
        real = index_store.index_vectors

        def recording(db, book_id, embedder, *, chapter_idx=None, batch=8):
            touched.append(chapter_idx)
            return real(db, book_id, embedder, chapter_idx=chapter_idx, batch=batch)

        with mock.patch.object(index_store, "index_vectors", side_effect=recording):
            self.app.start_vector_build(self.book_id, rebuild=False)
            status = self._wait_done()
        self.assertEqual(status["job"]["mode"], "continue")
        self.assertEqual(touched, [2])
        self.assertEqual(status["chapters_done"], 3)
        self.assertGreater(status["indexed"], before["indexed"])

    def test_rebuild_clears_and_reindexes_everything(self) -> None:
        index_store.index_vectors(
            self.app.db, self.book_id, self.app.embedder, chapter_idx=0, batch=8
        )
        touched: list[int | None] = []
        real = index_store.index_vectors

        def recording(db, book_id, embedder, *, chapter_idx=None, batch=8):
            touched.append(chapter_idx)
            return real(db, book_id, embedder, chapter_idx=chapter_idx, batch=batch)

        with mock.patch.object(index_store, "index_vectors", side_effect=recording):
            self.app.start_vector_build(self.book_id, rebuild=True)
            status = self._wait_done()
        self.assertEqual(status["job"]["mode"], "rebuild")
        self.assertEqual(touched, [0, 1, 2])

    def test_pause_stops_after_current_chapter_and_continue_resumes(self) -> None:
        self.app.embedder = SlowEmbedder(self.app.embedder, delay=0.2)
        self.settings.embedding_batch = 1
        self.app.start_vector_build(self.book_id, rebuild=False)

        self._wait(("running",))
        self.app.pause_vector_build(self.book_id)
        status = self._wait(("paused", "done"))
        self.assertEqual(status["job"]["state"], "paused")
        self.assertTrue(self.app._vector_paused(self.book_id))
        # 暂停点是章节边界，进度保留但未完成
        self.assertLess(status["chapters_done"], status["chapters_total"])
        self.assertLess(status["indexed"], status["paragraphs"])

        self.app.start_vector_build(self.book_id, rebuild=False)
        self._wait(("running",))
        done = self._wait_done()
        self.assertEqual(done["job"]["state"], "done")
        self.assertEqual(done["chapters_done"], done["chapters_total"])
        self.assertFalse(self.app._vector_paused(self.book_id))

    def test_resume_on_startup_after_interruption(self) -> None:
        """服务重启：残留的半成品向量由 resume_vector_builds 自动续跑。"""
        index_store.index_vectors(
            self.app.db, self.book_id, self.app.embedder, chapter_idx=0, batch=8
        )
        self.app.db.close()

        self.app = build_app(
            settings=Settings(home=self.home, embedding_backend="hash"),
            db_path=str(self.home / "t.db"),
        )
        resumed = self.app.resume_vector_builds()
        self.assertIn(self.book_id, resumed)
        status = self._wait_done()
        self.assertEqual(status["job"]["state"], "done")
        self.assertEqual(status["chapters_done"], status["chapters_total"])

    def test_paused_marker_survives_restart_and_blocks_auto_resume(self) -> None:
        self.app.pause_vector_build(self.book_id)
        self.assertTrue(self.app._vector_paused(self.book_id))
        self.app.db.close()

        self.app = build_app(
            settings=Settings(home=self.home, embedding_backend="hash"),
            db_path=str(self.home / "t.db"),
        )
        self.assertEqual(self.app.resume_vector_builds(), [])
        status = self.app.vector_status(self.book_id)
        self.assertTrue(status["paused"])
        self.assertEqual(status["job"]["state"], "paused")


if __name__ == "__main__":
    unittest.main()
