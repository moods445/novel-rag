"""事实校验：evidence 必须能回查到原文（决策 #21）。

策略：**入库软剔除并记录被拒项**。被拒项保留在 ``fact_rejects``，
用于调参和重跑，而不是静默丢弃——这是「可溯源」能被审计的前提。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..db.conn import Database
from ..domain.models import ChapterFact, Paragraph
from ..store import facts as fact_store


@dataclass(slots=True)
class ValidationReport:
    kept: int = 0
    rejected: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


def anchor_paragraph(paragraphs: list[Paragraph], snippet: str) -> Paragraph | None:
    """在章节段落里定位证据原文。先精确子串，再用前 12 字做前缀匹配。"""
    text = (snippet or "").strip()
    if not text:
        return None
    for p in paragraphs:
        if text in p.text:
            return p
    head = text[:12]
    if len(head) >= 6:
        for p in paragraphs:
            if head in p.text:
                return p
    return None


def validate(
    db: Database,
    book_id: str,
    fact: ChapterFact,
    paragraphs: list[Paragraph],
    *,
    grounding: bool = True,
) -> tuple[ChapterFact, ValidationReport]:
    report = ValidationReport()
    if not grounding:
        report.kept = len(fact.characters) + len(fact.relations) + len(fact.events)
        return fact, report

    kept_chars = []
    for ch in fact.characters:
        snippet = (ch.evidence or {}).get("text", "")
        para = anchor_paragraph(paragraphs, snippet)
        if para is None:
            report.reject("character:no_evidence_anchor")
            fact_store.add_reject(db, book_id, fact.chapter_idx, "character", "no_evidence_anchor", ch.to_dict())
            continue
        ch.evidence = {"text": snippet, "seq": para.seq, "cfi": para.cfi}
        kept_chars.append(ch)
        report.kept += 1

    kept_rels = []
    for rel in fact.relations:
        if not rel.person_a or not rel.person_b or rel.person_a == rel.person_b:
            report.reject("relation:invalid_pair")
            continue
        snippet = (rel.evidence or {}).get("text", "")
        para = anchor_paragraph(paragraphs, snippet)
        if para is None:
            report.reject("relation:no_evidence_anchor")
            fact_store.add_reject(db, book_id, fact.chapter_idx, "relation", "no_evidence_anchor", rel.to_dict())
            continue
        rel.evidence = {"text": snippet, "seq": para.seq, "cfi": para.cfi}
        kept_rels.append(rel)
        report.kept += 1

    kept_events = []
    for ev in fact.events:
        snippet = (ev.evidence or {}).get("text", "")
        para = anchor_paragraph(paragraphs, snippet)
        if para is None:
            report.reject("event:no_evidence_anchor")
            fact_store.add_reject(db, book_id, fact.chapter_idx, "event", "no_evidence_anchor", ev.to_dict())
            continue
        ev.evidence = {"text": snippet, "seq": para.seq, "cfi": para.cfi}
        kept_events.append(ev)
        report.kept += 1

    fact.characters = kept_chars
    fact.relations = kept_rels
    fact.events = kept_events
    return fact, report
