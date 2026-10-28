"""派生计算：把章节事实写成读模型（区间边、时间线、章节摘要）。

原则：**读模型不反向写事实**（架构第五节）。关系区间边是唯一需要
回填历史的表，回填只允许定向 UPDATE ``valid_to_seq``。
"""

from __future__ import annotations

from ..db.conn import Database
from ..domain.models import ChapterFact
from ..store import facts as fact_store


def derive(
    db: Database,
    book_id: str,
    fact: ChapterFact,
    entity_map: dict[str, str],
    *,
    chapter_start_seq: int,
) -> None:
    if fact.summary:
        fact_store.put_summary(db, book_id, fact.chapter_idx, fact.summary)

    for ev in fact.events:
        seq = _seq_of(ev.evidence, chapter_start_seq)
        cfi = _cfi_of(ev.evidence)
        fact_store.add_event(
            db,
            book_id,
            fact.chapter_idx,
            seq,
            ev.summary,
            actors=ev.actors,
            kind=ev.kind,
            evidence_cfi=cfi,
        )

    for rel in fact.relations:
        a_id = entity_map.get(rel.person_a)
        b_id = entity_map.get(rel.person_b)
        if not a_id or not b_id or a_id == b_id:
            continue
        # 无序对归一：A→B 与 B→A 必须落在同一对区间边上，
        # 否则「两人关系变化」会被存成两条互不相干的边。
        a_id, b_id = sorted((a_id, b_id))
        seq = _seq_of(rel.evidence, chapter_start_seq)
        fact_store.append_relation(
            db,
            book_id,
            a_id,
            b_id,
            rel.rel_type,
            valid_from_seq=seq,
            evidence_seq=seq,
            evidence_cfi=_cfi_of(rel.evidence),
        )


def _seq_of(evidence: dict | None, default: int) -> int:
    if isinstance(evidence, dict):
        value = evidence.get("seq")
        if isinstance(value, int) and value > 0:
            return value
    return default


def _cfi_of(evidence: dict | None) -> str:
    if isinstance(evidence, dict):
        return str(evidence.get("cfi") or "")
    return ""
