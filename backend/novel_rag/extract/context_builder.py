"""上下文构建：给抽取器提供「前情 + 档案 + 候选名单」。

关键约束（决策 #6 / #21）：上下文**按 seq 截断到当前章**，
因此抽取阶段天然不接触未来章节，不会把后文信息写进早期事实。
"""

from __future__ import annotations

from ..db.conn import Database
from ..domain.models import Chapter
from ..store import facts


def build_extraction_context(
    db: Database,
    book_id: str,
    chapter: Chapter,
    *,
    dictionary: list[str] | None = None,
    summary_limit: int = 3,
    name_limit: int = 40,
) -> str:
    parts: list[str] = []

    summaries = facts.recent_summaries(db, book_id, chapter.idx, limit=summary_limit)
    if summaries:
        block = "\n".join(f"- 第{s['chapter_idx'] + 1}章：{s['summary']}" for s in summaries)
        parts.append("【前情摘要】\n" + block)

    if dictionary:
        parts.append("【已知人物候选】\n" + "、".join(dictionary[:name_limit]))

    known = facts.relations_at(db, book_id, _chapter_start_seq(db, book_id, chapter.idx))
    if known:
        names = _entity_names(db, {e.a_entity for e in known} | {e.b_entity for e in known})
        lines = [
            f"- {names.get(e.a_entity, e.a_entity)} → {e.rel_type} → {names.get(e.b_entity, e.b_entity)}"
            for e in known[:20]
        ]
        parts.append("【已知关系】\n" + "\n".join(lines))

    return "\n\n".join(parts)


def _entity_names(db: Database, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = db.query(f"SELECT id, canonical_name FROM entities WHERE id IN ({placeholders})", tuple(ids))
    return {r["id"]: r["canonical_name"] for r in rows}


def _chapter_start_seq(db: Database, book_id: str, chapter_idx: int) -> int:
    row = db.query_one(
        "SELECT MIN(seq) AS s FROM paragraphs WHERE book_id=? AND chapter_idx=?", (book_id, chapter_idx)
    )
    return int(row["s"]) if row and row["s"] is not None else 0


def chapter_start_seq(db: Database, book_id: str, chapter_idx: int) -> int:
    return _chapter_start_seq(db, book_id, chapter_idx)
