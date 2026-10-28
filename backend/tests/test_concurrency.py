"""并发回归：ThreadingHTTPServer 下多线程必须各用各的 sqlite 连接。

历史缺陷：共享一个 Connection 时，并发 SELECT 会互相踩，
表现为「书明明存在却查不到」这类偶发 500。
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from novel_rag.db.conn import open_db
from novel_rag.ingest import importer
from novel_rag.config import Settings

from .fixtures import SAMPLE_TEXT


class ConcurrentAccessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(home=Path(self._tmp.name), embedding_backend="hash")
        self.db = open_db(Path(self._tmp.name) / "t.db")
        self.book_id = importer.import_text(
            self.db, text=SAMPLE_TEXT, title="样本", settings=self.settings
        ).book.id

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_parallel_reads_do_not_corrupt_results(self) -> None:
        errors: list[str] = []
        stop = threading.Event()

        def reader(worker: int) -> None:
            while not stop.is_set():
                try:
                    book = importer.get_book(self.db, self.book_id)
                    if book is None:
                        errors.append(f"worker {worker}: get_book 返回 None")
                        return
                    rows = self.db.query(
                        "SELECT seq, text FROM paragraphs WHERE book_id=? ORDER BY seq",
                        (self.book_id,),
                    )
                    if len(rows) != 3:
                        errors.append(f"worker {worker}: 段落数 {len(rows)} != 3")
                        return
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"worker {worker}: {exc}")
                    return

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        threading.Event().wait(0.6)
        stop.set()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(errors, [])

    def test_parallel_writes_then_read(self) -> None:
        def writer(index: int) -> None:
            self.db.execute(
                "INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)",
                (f"k{index}", str(index)),
            )

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        rows = self.db.query("SELECT COUNT(*) AS c FROM app_settings")
        self.assertEqual(rows[0]["c"], 8)

    def test_connections_are_per_thread(self) -> None:
        seen: list[int] = []
        lock = threading.Lock()

        def probe() -> None:
            self.db.query("SELECT 1")
            with lock:
                seen.append(id(self.db.conn))

        threads = [threading.Thread(target=probe) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(len(set(seen)), 4)


if __name__ == "__main__":
    unittest.main()
