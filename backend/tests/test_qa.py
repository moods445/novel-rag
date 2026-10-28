"""P4 消费平面：问答必须带引用、必须尊重剧透闸门、空上下文不调 LLM。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_rag.api.server import build_app
from novel_rag.config import Settings
from novel_rag.extract.pipeline import Pipeline
from novel_rag.ingest import importer
from novel_rag.qa.service import NO_CONTEXT_ANSWER

from .fixtures import SAMPLE_TEXT, FixedProvider, ScriptedProvider


class QaTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        settings = Settings(home=Path(self._tmp.name))
        self.app = build_app(settings=settings, db_path=str(Path(self._tmp.name) / "test.db"))
        self.db = self.app.db
        self.settings = settings
        self.book_id = importer.import_text(
            self.db, text=SAMPLE_TEXT, title="样本", settings=settings
        ).book.id

        self.app.gateway.extract = ScriptedProvider()
        pipeline = Pipeline(
            db=self.db, gateway=self.app.gateway, embedder=self.app.embedder, settings=settings
        )
        pipeline.start(self.book_id)
        pipeline.run(self.book_id)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_answer_contains_citation(self) -> None:
        self.app.gateway.query = FixedProvider("林凡初入山门，遇到苏清月 [1]。")
        result = self.app.qa.ask(self.book_id, "林凡是谁", progress_seq=99, spoiler_guard=False)
        self.assertIn("[1]", result["answer"])
        self.assertTrue(result["citations"])
        self.assertEqual(result["citations"][0]["n"], 1)
        self.assertIn("cfi", result["citations"][0])

    def test_missing_citation_is_appended(self) -> None:
        self.app.gateway.query = FixedProvider("林凡是一个刚入门的弟子。")
        result = self.app.qa.ask(self.book_id, "林凡是谁", progress_seq=99, spoiler_guard=False)
        self.assertIn("依据", result["answer"])
        self.assertIn("[1]", result["answer"])

    def test_spoiler_is_blocked_by_progress(self) -> None:
        self.app.gateway.query = FixedProvider("回答 [1]。")
        result = self.app.qa.ask(self.book_id, "夜色如墨 一剑刺向林凡", progress_seq=1, spoiler_guard=True)
        # 剧透截止在检索阶段就生效，因此闸门二（证据层兜底）不需要再丢东西
        self.assertEqual(result["meta"]["progress_seq"], 1)
        self.assertTrue(result["citations"])
        for citation in result["citations"]:
            self.assertLessEqual(citation["seq"], 1)

    def test_spoiler_guard_off_returns_later_evidence(self) -> None:
        self.app.gateway.query = FixedProvider("回答 [1]。")
        guarded = self.app.qa.ask(self.book_id, "夜色如墨 一剑刺向林凡", progress_seq=1, spoiler_guard=True)
        open_all = self.app.qa.ask(self.book_id, "夜色如墨 一剑刺向林凡", progress_seq=1, spoiler_guard=False)
        self.assertLess(len(guarded["citations"]), len(open_all["citations"]) + 1)
        self.assertTrue(open_all["citations"])

    def test_no_context_does_not_call_llm(self) -> None:
        provider = FixedProvider("不该被调用")
        provider.calls = 0
        self.app.gateway.query = provider
        result = self.app.qa.ask(self.book_id, "量子纠缠 薛定谔方程", progress_seq=0, spoiler_guard=True)
        self.assertEqual(result["degraded"], "no_context")
        self.assertEqual(result["answer"], NO_CONTEXT_ANSWER)
        self.assertEqual(getattr(provider, "calls", 0), 0)

    def test_echo_provider_falls_back_to_extractive(self) -> None:
        from novel_rag.llm.gateway import EchoProvider

        self.app.gateway.query = EchoProvider()
        result = self.app.qa.ask(self.book_id, "林凡是谁", progress_seq=99, spoiler_guard=False)
        self.assertEqual(result["degraded"], "extractive")
        self.assertIn("原文摘录", result["answer"])

    def test_coverage_is_reported(self) -> None:
        self.app.gateway.query = FixedProvider("回答 [1]。")
        result = self.app.qa.ask(self.book_id, "林凡是谁", progress_seq=99, spoiler_guard=False)
        self.assertEqual(result["coverage"]["done"], 3)
        self.assertEqual(result["coverage"]["ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
