"""人工校正与 LLM 兜底：LLM 分章、重新分章、标题修改、别名裁决。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_rag.api.server import build_app
from novel_rag.config import Settings
from novel_rag.db.conn import open_db
from novel_rag.domain.models import ChapterFact, CharacterFact
from novel_rag.extract.pipeline import Pipeline
from novel_rag.extract.resolver import resolve
from novel_rag.ingest import importer
from novel_rag.ingest.resplit import resplit_book, update_chapter_title
from novel_rag.ingest.llm_split import build_chapters, propose_split
from novel_rag.llm.gateway import EchoProvider, LlmGateway, StubProvider
from novel_rag.store import facts as fact_store

from .fixtures import SAMPLE_TEXT, ScriptedProvider

UNMARKED = """1
山门前站着一个人，抬头望向云雾深处的石阶，一言不发。
2
试炼场上人声鼎沸，众人围成一圈，目光落在高台之上。
3
夜色如墨，山风呼啸，一道剑光骤然亮起，又迅速熄灭。
"""


class LlmSplitTest(unittest.TestCase):
    def test_rule_fails_so_llm_is_needed(self) -> None:
        from novel_rag.ingest.splitter import split_chapters

        split = split_chapters(UNMARKED)
        self.assertTrue(split.needs_llm)

    def test_proposal_and_build(self) -> None:
        gateway = LlmGateway(
            extract=StubProvider('{"title_lines": [1, 3, 5], "pattern": "^[0-9]$"}')
        )
        proposal = propose_split(gateway, UNMARKED)
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.line_numbers, [1, 3, 5])
        chapters = build_chapters(UNMARKED, proposal)
        self.assertEqual([c.title for c in chapters], ["1", "2", "3"])
        self.assertEqual(chapters[0].split_source, "rule:llm")

    def test_proposal_rejects_single_line(self) -> None:
        gateway = LlmGateway(extract=StubProvider('{"title_lines": [1]}'))
        self.assertIsNone(propose_split(gateway, UNMARKED))

    def test_echo_provider_yields_no_proposal(self) -> None:
        gateway = LlmGateway(extract=EchoProvider())
        self.assertIsNone(propose_split(gateway, UNMARKED))

    def test_import_uses_llm_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = open_db(Path(tmp) / "t.db")
            gateway = LlmGateway(extract=StubProvider('{"title_lines": [1, 3, 5]}'))
            result = importer.import_text(
                db, text=UNMARKED, title="无标记", settings=Settings(home=Path(tmp)), llm=gateway
            )
            self.assertEqual(result.chapter_count, 3)
            self.assertEqual(result.split_rule, "llm")
            self.assertFalse(result.needs_llm)
            db.close()


class ResplitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(home=Path(self._tmp.name), embedding_backend="hash")
        self.app = build_app(settings=self.settings, db_path=str(Path(self._tmp.name) / "t.db"))
        self.db = self.app.db
        self.book_id = importer.import_text(
            self.db, text=SAMPLE_TEXT, title="样本", settings=self.settings
        ).book.id
        self.app.gateway.extract = ScriptedProvider()
        self.pipeline = Pipeline(
            db=self.db, gateway=self.app.gateway, embedder=self.app.embedder, settings=self.settings
        )
        self.pipeline.start(self.book_id)
        self.pipeline.run(self.book_id)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_raw_text_is_stored(self) -> None:
        row = self.db.query_one("SELECT raw_text FROM books WHERE id=?", (self.book_id,))
        self.assertIn("第一章 初入山门", row["raw_text"])

    def test_resplit_with_pattern_merges_chapters(self) -> None:
        before_facts = self.db.query_one(
            "SELECT COUNT(*) AS c FROM chapter_facts WHERE book_id=?", (self.book_id,)
        )["c"]
        self.assertEqual(before_facts, 3)

        result = resplit_book(
            self.db, self.book_id, pattern=r"^第[一三]章.*$", settings=self.settings
        )
        self.assertEqual(result.chapter_count, 2)
        # 合并后第一章把第二章的标题与正文一并吞进来，因此段落数不是简单相加
        self.assertEqual(result.paragraph_count, 4)
        self.assertEqual(result.split_rule, "manual")
        self.assertEqual(result.invalidated["facts"], 3)
        self.assertTrue(any("已作废" in w for w in result.warnings))

        coverage = fact_store.coverage_summary(self.db, self.book_id)
        self.assertEqual(coverage["total"], 2)
        self.assertEqual(coverage["pending"], 2)

        seqs = [
            r["seq"]
            for r in self.db.query(
                "SELECT seq FROM paragraphs WHERE book_id=? ORDER BY seq", (self.book_id,)
            )
        ]
        self.assertEqual(seqs, [1, 2, 3, 4])

    def test_resplit_invalid_regex(self) -> None:
        from novel_rag.errors import InvalidInput

        with self.assertRaises(InvalidInput):
            resplit_book(self.db, self.book_id, pattern="([unclosed", settings=self.settings)
        with self.assertRaises(InvalidInput):
            resplit_book(self.db, self.book_id, pattern="XXXX", settings=self.settings)

    def test_notes_survive_resplit(self) -> None:
        self.db.execute(
            "INSERT INTO notes(id, book_id, kind, cfi, seq, chapter_idx, text, note, created_at) "
            "VALUES('n1',?,'highlight','epubcfi(/6/2!/4/2)',1,0,'林凡','备注',datetime('now'))",
            (self.book_id,),
        )
        result = resplit_book(
            self.db, self.book_id, pattern=r"^第[一三]章.*$", settings=self.settings
        )
        self.assertEqual(result.invalidated["notes"], 1)
        still = self.db.query_one("SELECT COUNT(*) AS c FROM notes WHERE book_id=?", (self.book_id,))
        self.assertEqual(still["c"], 1)
        self.assertTrue(any("笔记" in w for w in result.warnings))

    def test_update_chapter_title(self) -> None:
        out = update_chapter_title(self.db, self.book_id, 0, "  新的标题  ")
        self.assertEqual(out["title"], "新的标题")
        row = self.db.query_one(
            "SELECT title FROM chapters WHERE book_id=? AND idx=0", (self.book_id,)
        )
        self.assertEqual(row["title"], "新的标题")

    def test_fts_rebuilt_after_resplit(self) -> None:
        resplit_book(self.db, self.book_id, pattern=r"^第[一二三]章.*$", settings=self.settings)
        from novel_rag.store import index as index_store

        rows = index_store.fts_search(self.db, self.book_id, "夜色如墨", limit=3)
        self.assertTrue(rows)
        self.assertLessEqual(len(rows), 3)


class StubArbiter:
    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.calls = 0

    def arbitrate(self, alias: str, candidates: list[str], excerpt: str) -> str:
        self.calls += 1
        return self.choice


class AliasConflictTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = open_db(Path(self._tmp.name) / "t.db")
        self.book_id = "b1"
        self.a = fact_store.upsert_entity(self.db, self.book_id, "林凡", first_seq=1)
        self.b = fact_store.upsert_entity(self.db, self.book_id, "林轩", first_seq=1)
        fact_store.add_alias(self.db, self.book_id, self.a, "林子", seq=1)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _resolve(self, arbiter):
        fact = ChapterFact(
            chapter_idx=0,
            characters=[
                CharacterFact(name="林轩", aliases=["林子"], evidence={"text": "林子站在山门前"})
            ],
        )
        body = "林子站在山门前。林轩点了点头，转身离开。"
        return resolve(
            self.db,
            self.book_id,
            fact,
            chapter_body=body,
            chapter_start_seq=1,
            arbiter=arbiter,
        )

    def test_conflict_detected_and_adjudicated(self) -> None:
        fact, mapping, report = self._resolve(StubArbiter("林凡"))
        self.assertEqual(report.conflicts, 1)
        self.assertEqual(report.adjudicated, 1)
        self.assertEqual(fact.characters[0].name, "林凡")
        self.assertEqual(mapping["林凡"], self.a)

        audit = fact_store.audit_of(self.db, self.book_id)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["alias"], "林子")
        self.assertEqual(audit[0]["decision"], "林凡")
        self.assertEqual(audit[0]["source"], "llm")

    def test_conflict_without_arbiter_is_recorded_not_merged(self) -> None:
        fact, _mapping, report = self._resolve(None)
        self.assertEqual(report.conflicts, 1)
        self.assertEqual(report.adjudicated, 0)
        self.assertEqual(fact.characters[0].name, "林轩")
        audit = fact_store.audit_of(self.db, self.book_id)
        self.assertEqual(audit[0]["source"], "skipped")

    def test_arbiter_choice_of_self_does_nothing(self) -> None:
        fact, _mapping, report = self._resolve(StubArbiter("林轩"))
        self.assertEqual(report.adjudicated, 0)
        self.assertEqual(fact.characters[0].name, "林轩")


if __name__ == "__main__":
    unittest.main()
