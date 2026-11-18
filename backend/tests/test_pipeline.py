"""P1 抽取平面端到端：章节事实 → 校验 → 归一 → 区间边 → 覆盖度。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_rag.api.server import build_app
from novel_rag.config import Settings
from novel_rag.extract.pipeline import Pipeline
from novel_rag.ingest import importer
from novel_rag.store import facts as fact_store

from .fixtures import SAMPLE_TEXT, ScriptedProvider


def make_app(tmp: str):
    settings = Settings(home=Path(tmp))
    app = build_app(settings=settings, db_path=str(Path(tmp) / "test.db"))
    app.gateway.extract = ScriptedProvider()
    return app


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.app = make_app(self._tmp.name)
        self.db = self.app.db
        self.settings = self.app.settings
        result = importer.import_text(
            self.db, text=SAMPLE_TEXT, title="样本", author="作者", settings=self.settings
        )
        self.book_id = result.book.id
        self.pipeline = Pipeline(
            db=self.db, gateway=self.app.gateway, embedder=self.app.embedder, settings=self.settings
        )

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_import_produces_seq_and_coverage_rows(self) -> None:
        rows = self.db.query(
            "SELECT seq, chapter_idx, text FROM paragraphs WHERE book_id=? ORDER BY seq", (self.book_id,)
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["seq"] for r in rows], [1, 2, 3])
        self.assertEqual([r["chapter_idx"] for r in rows], [0, 1, 2])
        summary = fact_store.coverage_summary(self.db, self.book_id)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["pending"], 3)

    def test_import_is_idempotent(self) -> None:
        again = importer.import_text(
            self.db, text=SAMPLE_TEXT, title="样本", settings=self.settings
        )
        self.assertTrue(again.reused)
        count = self.db.query_one("SELECT COUNT(*) AS c FROM books")["c"]
        self.assertEqual(count, 1)

    def test_full_run_extracts_all_chapters(self) -> None:
        self.pipeline.start(self.book_id)
        report = self.pipeline.run(self.book_id)
        self.assertEqual(report.failed, 0, report.errors)
        self.assertEqual(report.processed, 3)
        self.assertEqual(report.state, "done")

        coverage = fact_store.coverage_summary(self.db, self.book_id)
        self.assertEqual(coverage["done"], 3)
        self.assertEqual(coverage["ratio"], 1.0)

        fact = fact_store.get_chapter_fact(self.db, self.book_id, 0)
        self.assertIsNotNone(fact)
        self.assertEqual(fact["source"], "llm")
        names = {c["name"] for c in fact["characters"]}
        self.assertIn("林凡", names)
        self.assertIn("苏清月", names)

    def test_evidence_is_anchored_to_seq(self) -> None:
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id)
        fact = fact_store.get_chapter_fact(self.db, self.book_id, 0)
        for ch in fact["characters"]:
            self.assertIsNotNone(ch["evidence"])
            self.assertIsInstance(ch["evidence"]["seq"], int)
            self.assertTrue(ch["evidence"]["cfi"].startswith("epubcfi"))

    def test_alias_merged_into_canonical_entity(self) -> None:
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id)
        entity = fact_store.entity_by_name(self.db, self.book_id, "苏清月")
        self.assertIsNotNone(entity)
        # 「苏师姐」在本章原文中不存在，必须被原文锚定拒绝
        alias = self.db.query_one(
            "SELECT * FROM entity_aliases WHERE book_id=? AND alias='苏师姐'", (self.book_id,)
        )
        self.assertIsNone(alias)

    def test_relation_interval_closes_on_change(self) -> None:
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id)

        at_early = fact_store.relation_pairs_at(self.db, self.book_id, 2)
        types_early = {r["rel_type"] for r in at_early}
        self.assertIn("同门", types_early)
        self.assertNotIn("敌对", types_early)

        at_late = fact_store.relation_pairs_at(self.db, self.book_id, 3)
        types_late = {r["rel_type"] for r in at_late}
        self.assertIn("敌对", types_late)
        self.assertNotIn("同门", types_late)

        versions = self.db.query(
            "SELECT rel_type, version, valid_from_seq, valid_to_seq FROM relations "
            "WHERE book_id=? ORDER BY version",
            (self.book_id,),
        )
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["valid_to_seq"], 2)
        self.assertIsNone(versions[1]["valid_to_seq"])

    def test_timeline_and_summaries(self) -> None:
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id)
        events = fact_store.timeline(self.db, self.book_id)
        self.assertEqual(len(events), 3)
        self.assertEqual([e["seq"] for e in events], [1, 2, 3])
        span = fact_store.summary_span(self.db, self.book_id, 0, 2)
        self.assertEqual(len(span), 3)

    def test_pause_stops_loop(self) -> None:
        self.pipeline.start(self.book_id)
        self.pipeline.pause(self.book_id)
        report = self.pipeline.run(self.book_id)
        self.assertEqual(report.state, "paused")
        self.assertEqual(report.processed, 0)

    def test_resume_continues_from_cursor(self) -> None:
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id, max_chapters=1)
        status = self.pipeline.status(self.book_id)
        self.assertEqual(status["cursor"], 1)
        self.pipeline.resume(self.book_id)
        report = self.pipeline.run(self.book_id)
        self.assertEqual(report.skipped, 0)
        self.assertEqual(report.processed, 2)
        self.assertEqual(self.pipeline.status(self.book_id)["state"], "done")

    def test_retry_from_reprocesses_single_chapter(self) -> None:
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id)
        self.assertIsNotNone(fact_store.get_chapter_fact(self.db, self.book_id, 1))

        state = self.pipeline.retry_from(self.book_id, 1)
        self.assertEqual(state["state"], "running")
        self.assertEqual(state["cursor"], 1)
        self.assertIsNone(fact_store.get_chapter_fact(self.db, self.book_id, 1))
        self.assertEqual(fact_store.coverage_of(self.db, self.book_id, 1), "pending")

        report = self.pipeline.run(self.book_id)
        self.assertEqual(report.processed, 1)
        self.assertIsNotNone(fact_store.get_chapter_fact(self.db, self.book_id, 1))

    def test_start_reextracts_heuristic_chapters_once_model_available(self) -> None:
        """离线跑完的章节没有关系；配置模型后点“开始/继续”必须重抽这些章节。"""
        from novel_rag.llm.gateway import EchoProvider

        self.app.gateway.extract = EchoProvider(name="echo-extract")
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id)
        self.assertEqual(fact_store.get_chapter_fact(self.db, self.book_id, 0)["source"], "heuristic")
        self.assertEqual(fact_store.relation_pairs_at(self.db, self.book_id, None), [])

        self.app.gateway.extract = ScriptedProvider()
        self.pipeline.start(self.book_id)
        report = self.pipeline.run(self.book_id)
        self.assertEqual(report.processed, 3)
        self.assertEqual(report.skipped, 0)
        self.assertEqual(fact_store.get_chapter_fact(self.db, self.book_id, 0)["source"], "llm")
        self.assertGreater(len(fact_store.relation_pairs_at(self.db, self.book_id, None)), 0)
        # 重抽前清掉离线事件，时间线不应出现重复
        self.assertEqual(len(fact_store.timeline(self.db, self.book_id)), 3)

    def test_start_keeps_heuristic_facts_when_model_is_still_offline(self) -> None:
        from novel_rag.llm.gateway import EchoProvider

        self.app.gateway.extract = EchoProvider(name="echo-extract")
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id)
        self.pipeline.start(self.book_id)
        report = self.pipeline.run(self.book_id)
        self.assertEqual(report.processed, 0)
        self.assertEqual(report.skipped, 0)

    def test_start_is_idempotent_while_running(self) -> None:
        """重复点「开始抽取」不能重置游标或重开任务。"""
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id)
        self.db.execute(
            "UPDATE analysis_tasks SET state='running', cursor=1 WHERE book_id=?", (self.book_id,)
        )
        state = self.pipeline.start(self.book_id)
        self.assertEqual(state["state"], "running")
        self.assertEqual(state["cursor"], 1)

    def test_extract_runs_concurrently_and_persists_in_order(self) -> None:
        """抽取慢的主因是串行 LLM 调用；并发只覆盖 LLM 段，落库仍按章节顺序。"""
        import threading
        import time

        base = ScriptedProvider()
        lock = threading.Lock()
        stats = {"active": 0, "peak": 0}

        class SlowProvider:
            name = "slow"

            def complete(self, prompt, *, system=None, max_tokens=1024):
                with lock:
                    stats["active"] += 1
                    stats["peak"] = max(stats["peak"], stats["active"])
                try:
                    time.sleep(0.08)
                    return base.complete(prompt, system=system, max_tokens=max_tokens)
                finally:
                    with lock:
                        stats["active"] -= 1

        self.settings.extract_concurrency = 3
        self.app.gateway.extract = SlowProvider()
        self.pipeline.start(self.book_id)
        report = self.pipeline.run(self.book_id)
        self.assertEqual(report.processed, 3)
        self.assertEqual(report.failed, 0)
        self.assertGreaterEqual(stats["peak"], 2, "章节抽取没有并发执行")
        # 并发不影响顺序：关系区间仍按 seq 推导
        versions = self.db.query(
            "SELECT rel_type, valid_from_seq, valid_to_seq FROM relations WHERE book_id=? ORDER BY version",
            (self.book_id,),
        )
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["valid_to_seq"], 2)
        self.assertIsNone(versions[1]["valid_to_seq"])

    def test_rebuild_clears_derived_data_and_restarts_from_zero(self) -> None:
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id)
        self.assertGreater(len(fact_store.relation_pairs_at(self.db, self.book_id, None)), 0)

        state = self.pipeline.rebuild(self.book_id)
        self.assertEqual(state["state"], "running")
        self.assertEqual(state["cursor"], 0)
        self.assertIsNone(fact_store.get_chapter_fact(self.db, self.book_id, 0))
        self.assertEqual(fact_store.relation_pairs_at(self.db, self.book_id, None), [])
        self.assertEqual(len(fact_store.timeline(self.db, self.book_id)), 0)
        coverage = fact_store.coverage_summary(self.db, self.book_id)
        self.assertEqual(coverage["total"], 3)
        self.assertEqual(coverage["done"], 0)

        report = self.pipeline.run(self.book_id)
        self.assertEqual(report.processed, 3)
        self.assertGreater(len(fact_store.relation_pairs_at(self.db, self.book_id, None)), 0)

    def test_ungrounded_fact_is_rejected(self) -> None:
        from novel_rag.extract.validator import validate
        from novel_rag.domain.models import ChapterFact, CharacterFact
        from novel_rag.ingest.seq_indexer import paragraphs_of_chapter

        paragraphs = paragraphs_of_chapter(self.db, self.book_id, 0)
        fact = ChapterFact(
            chapter_idx=0,
            characters=[CharacterFact(name="不存在的人", evidence={"text": "这句话原文里没有"})],
        )
        kept, report = validate(self.db, self.book_id, fact, paragraphs, grounding=True)
        self.assertEqual(len(kept.characters), 0)
        self.assertEqual(report.rejected, 1)
        rejects = fact_store.rejects_of(self.db, self.book_id)
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0]["reason"], "no_evidence_anchor")


if __name__ == "__main__":
    unittest.main()
