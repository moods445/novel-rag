"""抽取流水线 + 任务状态机（决策 #18）。

状态机：``queued → running ⇄ paused``，结束 ``done``，异常 ``failed``。
每条章节幂等：已有同 ``chapter_fp`` 的事实就跳过。
单章失败只标 ``failed`` 并继续，轮末可整体重试一次。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import Settings, get_settings
from ..db.conn import Database
from ..domain.models import Chapter, ChapterFact, Paragraph
from ..ingest.seq_indexer import index_chapters, paragraphs_of_chapter  # noqa: F401
from ..llm.embedding import EmbeddingProvider
from ..llm.gateway import LlmGateway
from ..store import facts as fact_store
from ..store import index as index_store
from .context_builder import build_extraction_context, chapter_start_seq
from .derive import derive
from .extractor import ChapterFactExtractor
from .ner import NerResult
from .prescan import scan_with_aliases
from .resolver import AliasArbiter, LlmAliasArbiter, resolve
from .validator import validate


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class RunReport:
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    state: str = ""
    cursor: int = 0
    total: int = 0
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "processed": self.processed,
            "skipped": self.skipped,
            "failed": self.failed,
            "state": self.state,
            "cursor": self.cursor,
            "total": self.total,
            "errors": self.errors,
        }


@dataclass
class Pipeline:
    db: Database
    gateway: LlmGateway
    embedder: EmbeddingProvider
    settings: Settings = field(default_factory=get_settings)
    arbiter: AliasArbiter | None = None

    def __post_init__(self) -> None:
        if self.arbiter is None:
            self.arbiter = LlmAliasArbiter(self.gateway)

    # ------------------------------------------------------------ 状态机

    def status(self, book_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM analysis_tasks WHERE book_id=?", (book_id,))
        if row is None:
            return {"book_id": book_id, "state": "absent", "cursor": 0, "total": 0, "message": ""}
        coverage = fact_store.coverage_summary(self.db, book_id)
        return {
            "book_id": book_id,
            "state": row["state"],
            "cursor": int(row["cursor"]),
            "total": int(row["total"]),
            "message": row["message"],
            "updated_at": row["updated_at"],
            "coverage": coverage,
        }

    def start(self, book_id: str) -> dict:
        total = int(
            self.db.query_one("SELECT COUNT(*) AS c FROM chapters WHERE book_id=?", (book_id,))["c"]
        )
        self.db.execute(
            "INSERT INTO analysis_tasks(book_id, state, cursor, total, message, updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(book_id) DO UPDATE SET state='running', "
            "total=excluded.total, message='', updated_at=excluded.updated_at",
            (book_id, "running", 0, total, "", now_iso()),
        )
        return self.status(book_id)

    def pause(self, book_id: str) -> dict:
        self.db.execute(
            "UPDATE analysis_tasks SET state='paused', updated_at=? WHERE book_id=? AND state='running'",
            (now_iso(), book_id),
        )
        return self.status(book_id)

    def retry_from(self, book_id: str, chapter_idx: int) -> dict:
        """重跑某一章（清掉该章事实与拒收记录，游标回退）。

        用于 LLM 偶发返回坏 JSON 时的定点修复，不必整本书重来。
        """
        chapter_idx = max(0, chapter_idx)
        with self.db.tx() as conn:
            conn.execute(
                "DELETE FROM chapter_facts WHERE book_id=? AND chapter_idx=?", (book_id, chapter_idx)
            )
            conn.execute(
                "DELETE FROM fact_rejects WHERE book_id=? AND chapter_idx=?", (book_id, chapter_idx)
            )
            conn.execute(
                "UPDATE chapter_coverage SET state='pending', errors=0 "
                "WHERE book_id=? AND chapter_idx=?",
                (book_id, chapter_idx),
            )
            conn.execute(
                "UPDATE analysis_tasks SET state='running', cursor=MIN(cursor, ?), updated_at=? "
                "WHERE book_id=?",
                (chapter_idx, now_iso(), book_id),
            )
        return self.status(book_id)

    def resume(self, book_id: str) -> dict:
        self.db.execute(
            "UPDATE analysis_tasks SET state='running', updated_at=? WHERE book_id=? AND state IN ('paused','failed')",
            (now_iso(), book_id),
        )
        return self.status(book_id)

    # ------------------------------------------------------------ 主循环

    def run(self, book_id: str, *, max_chapters: int | None = None) -> RunReport:
        task = self.status(book_id)
        report = RunReport(state=task["state"], cursor=task["cursor"], total=task["total"])
        if task["state"] != "running":
            return report

        chapters = self._chapters(book_id)
        ner = self._ner(book_id, chapters)
        self._register_alias_entities(book_id, ner)

        processed = 0
        for chapter in chapters:
            if chapter.idx < task["cursor"]:
                continue
            state = self._task_state(book_id)
            if state not in ("running",):
                report.state = state
                break
            if max_chapters is not None and processed >= max_chapters:
                break

            if fact_store.has_fact(self.db, book_id, chapter.idx, chapter.fp):
                fact_store.set_coverage(self.db, book_id, chapter.idx, "done")
                report.skipped += 1
                self._advance(book_id, chapter.idx + 1)
                continue

            try:
                self._process_chapter(book_id, chapter, ner)
                report.processed += 1
            except Exception as exc:  # noqa: BLE001 - 单章失败不中断
                report.failed += 1
                report.errors.append({"chapter_idx": chapter.idx, "error": str(exc)})
                fact_store.set_coverage(self.db, book_id, chapter.idx, "failed", errors=1)
                fact_store.add_reject(
                    self.db, book_id, chapter.idx, "chapter", "pipeline_error", {"error": str(exc)}
                )
            processed += 1
            self._advance(book_id, chapter.idx + 1)

        # 收尾判定
        final = self.status(book_id)
        cursor = final["cursor"]
        total = final["total"]
        if final["state"] == "running" and cursor >= total:
            self.db.execute(
                "UPDATE analysis_tasks SET state='done', updated_at=? WHERE book_id=?",
                (now_iso(), book_id),
            )
        report.state = self.status(book_id)["state"]
        report.cursor = self.status(book_id)["cursor"]
        return report

    def _process_chapter(self, book_id: str, chapter: Chapter, ner: NerResult) -> None:
        fact_store.set_coverage(self.db, book_id, chapter.idx, "running")
        paragraphs: list[Paragraph] = paragraphs_of_chapter(self.db, book_id, chapter.idx)
        start_seq = chapter_start_seq(self.db, book_id, chapter.idx)
        context = build_extraction_context(
            self.db, book_id, chapter, dictionary=ner.dictionary()
        )

        extractor = ChapterFactExtractor(self.gateway)
        outcome = extractor.extract(
            chapter,
            context=context,
            paragraphs=paragraphs,
            dictionary=ner.dictionary(),
            alias_map=ner.alias_map(),
        )
        fact: ChapterFact = outcome.fact

        fact, _report = validate(
            self.db,
            book_id,
            fact,
            paragraphs,
            grounding=self.settings.evidence_grounding,
        )
        fact, entity_map, _resolution = resolve(
            self.db,
            book_id,
            fact,
            chapter_body=chapter.body,
            chapter_start_seq=start_seq,
            arbiter=self.arbiter,
        )
        derive(self.db, book_id, fact, entity_map, chapter_start_seq=start_seq)

        payload = fact.to_dict()
        payload["extract_error"] = outcome.error
        fact_store.put_chapter_fact(self.db, book_id, chapter.idx, chapter.fp, payload)
        fact_store.set_coverage(self.db, book_id, chapter.idx, "done")

        # 索引随章节完成增量可用（决策 #6：导入后立即可问）。
        # FTS 行在导入阶段就已写入，这里不能重建——逐章重建会让整本书 O(n²)。
        index_store.index_vectors(
            self.db,
            book_id,
            self.embedder,
            chapter_idx=chapter.idx,
            batch=self.settings.embedding_batch,
        )

    # ------------------------------------------------------------ 辅助

    def _chapters(self, book_id: str) -> list[Chapter]:
        rows = self.db.query(
            "SELECT idx, title, body, char_start, char_end, fp, spine_index, nav_href, split_source, confidence "
            "FROM chapters WHERE book_id=? ORDER BY idx",
            (book_id,),
        )
        return [
            Chapter(
                idx=r["idx"],
                title=r["title"],
                body=r["body"],
                char_start=r["char_start"],
                char_end=r["char_end"],
                fp=r["fp"],
                spine_index=r["spine_index"],
                nav_href=r["nav_href"],
                split_source=r["split_source"],
                confidence=r["confidence"],
            )
            for r in rows
        ]

    def _ner(self, book_id: str, chapters: list[Chapter]) -> NerResult:
        """人名识别结果缓存在 ``extensions``，暂停恢复时不重跑（jieba 全书约数十秒）。"""
        row = self.db.query_one(
            "SELECT payload FROM extensions WHERE book_id=? AND key='ner'", (book_id,)
        )
        if row:
            try:
                import json

                data = json.loads(row["payload"])
                return NerResult(
                    names=[(n, int(c)) for n, c in data.get("names", [])],
                    alias_pairs=[(a, b) for a, b in data.get("alias_pairs", [])],
                )
            except Exception:  # noqa: BLE001 - 缓存损坏就重算
                pass

        text = "\n".join(ch.body for ch in chapters)
        result = scan_with_aliases(text)
        import json

        self.db.execute(
            "INSERT OR REPLACE INTO extensions(book_id, key, payload) VALUES(?, 'ner', ?)",
            (
                book_id,
                json.dumps(
                    {"names": result.names, "alias_pairs": result.alias_pairs},
                    ensure_ascii=False,
                ),
            ),
        )
        return result

    def _register_alias_entities(self, book_id: str, ner: NerResult) -> None:
        """把「姓X名Y字Z」得到的正名/别名预先落库，离线模式下也能正确合并。"""
        for canonical, aliases in ner.alias_map().items():
            eid = fact_store.upsert_entity(
                self.db, book_id, canonical, first_seq=self._first_seq(book_id, canonical)
            )
            for alias in aliases:
                fact_store.add_alias(
                    self.db,
                    book_id,
                    eid,
                    alias,
                    seq=self._first_seq(book_id, alias),
                    source="ner",
                )

    def _first_seq(self, book_id: str, name: str) -> int:
        """名字第一次出现的 seq。用于让防剧透闸门对实体也生效。"""
        row = self.db.query_one(
            "SELECT MIN(seq) AS s FROM paragraphs WHERE book_id=? AND text LIKE ?",
            (book_id, f"%{name}%"),
        )
        value = row["s"] if row else None
        return int(value) if value is not None else 0

    def _task_state(self, book_id: str) -> str:
        row = self.db.query_one("SELECT state FROM analysis_tasks WHERE book_id=?", (book_id,))
        return row["state"] if row else "absent"

    def _advance(self, book_id: str, cursor: int) -> None:
        self.db.execute(
            "UPDATE analysis_tasks SET cursor=?, updated_at=? WHERE book_id=?",
            (cursor, now_iso(), book_id),
        )
