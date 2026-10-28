"""实体归一（决策 #17）：规则 + 原文锚定双向校验，LLM 只做歧义裁决。

「双向锚定」的含义：
1. 别名必须在**本章原文**里出现过（原文锚定）；
2. 别名必须能连到一条**已存在的实体档案**（档案锚定）。
两条都满足才合并；只满足第一条时先建别名、不合并——宁可晚合并，不可误合并。

冲突（同一个别名同时被两个实体主张）才交给 LLM 裁决，
且每一次裁决都写进 ``resolution_audit``，可回溯、可复现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..db.conn import Database
from ..domain.models import ChapterFact
from ..llm.gateway import LlmGateway
from ..store import facts as fact_store

ARBITER_SYSTEM = "你是小说实体消歧助手。只输出 JSON。"

ARBITER_TEMPLATE = """小说原文片段：
{excerpt}

现有两个人物档案，都在争夺同一个别名「{alias}」：
- 候选一：{a}
- 候选二：{b}

请判断「{alias}」在原文中实际指谁。只输出 JSON：{{"choice": "候选一或候选二的原文名称", "reason": "10 字以内"}}
如果无法判断，choice 输出空字符串。
"""


class AliasArbiter(Protocol):
    def arbitrate(self, alias: str, candidates: list[str], excerpt: str) -> str: ...


@dataclass(slots=True)
class LlmAliasArbiter:
    gateway: LlmGateway
    name: str = "llm-arbiter"

    def arbitrate(self, alias: str, candidates: list[str], excerpt: str) -> str:
        if len(candidates) < 2:
            return ""
        prompt = ARBITER_TEMPLATE.format(
            excerpt=excerpt[:400], alias=alias, a=candidates[0], b=candidates[1]
        )
        try:
            raw = self.gateway.complete("extract", prompt, system=ARBITER_SYSTEM, max_tokens=256)
        except Exception:  # noqa: BLE001 - 裁决失败就不合并
            return ""
        return _parse_choice(raw, candidates)


def _parse_choice(raw: str, candidates: list[str]) -> str:
    if not raw:
        return ""
    for name in candidates:
        if name and f'"{name}"' in raw:
            return name
    for name in candidates:
        if name and name in raw:
            return name
    return ""


@dataclass(slots=True)
class ResolutionReport:
    created: int = 0
    merged: int = 0
    aliases: int = 0
    conflicts: int = 0
    adjudicated: int = 0


def resolve(
    db: Database,
    book_id: str,
    fact: ChapterFact,
    *,
    chapter_body: str,
    chapter_start_seq: int,
    arbiter: AliasArbiter | None = None,
) -> tuple[ChapterFact, dict[str, str], ResolutionReport]:
    """返回 ``(规范化后的 fact, 名称→实体ID 映射, 报告)``。"""
    report = ResolutionReport()
    name_to_entity: dict[str, str] = {}

    for ch in fact.characters:
        name = (ch.name or "").strip()
        if not name:
            continue

        eid = fact_store.entity_of_alias(db, book_id, name)
        canonical = name
        if eid is not None:
            canonical = _canonical_of(db, eid) or name
        else:
            merged_id = _merge_candidate(db, book_id, ch.aliases, chapter_body)
            if merged_id is not None:
                canonical = _canonical_of(db, merged_id) or name
                eid = merged_id
                report.merged += 1
                if name in chapter_body:
                    fact_store.add_alias(db, book_id, eid, name, seq=chapter_start_seq, source="merged")
            else:
                eid = fact_store.upsert_entity(
                    db, book_id, name, kind=ch.kind or "person", first_seq=chapter_start_seq
                )
                report.created += 1

        # ---- 冲突检测与裁决
        for alias in ch.aliases:
            alias = (alias or "").strip()
            if not alias or alias == canonical or alias not in chapter_body:
                continue
            holder = fact_store.entity_of_alias(db, book_id, alias)
            if holder is None or holder == eid:
                continue
            report.conflicts += 1
            candidates = [canonical, _canonical_of(db, holder) or alias]
            decision = arbiter.arbitrate(alias, candidates, _excerpt(chapter_body, alias)) if arbiter else ""
            fact_store.add_audit(
                db,
                book_id,
                fact.chapter_idx,
                alias,
                candidates,
                decision,
                source="llm" if decision else "skipped",
                note="alias claimed by two entities",
            )
            if decision and decision == candidates[1]:
                # 裁决结果：当前名字应并入 alias 的归属实体
                prev = eid
                eid = holder
                canonical = candidates[1]
                report.adjudicated += 1
                if name in chapter_body and prev != holder:
                    fact_store.add_alias(db, book_id, holder, name, seq=chapter_start_seq, source="adjudicated")
            break

        name_to_entity[name] = eid
        name_to_entity[canonical] = eid
        ch.name = canonical

        for alias in ch.aliases:
            alias = (alias or "").strip()
            if not alias or alias == canonical:
                continue
            if alias not in chapter_body:  # 原文锚定失败
                continue
            if fact_store.add_alias(db, book_id, eid, alias, seq=chapter_start_seq, source="fact"):
                report.aliases += 1
            name_to_entity.setdefault(alias, eid)

    for rel in fact.relations:
        rel.person_a = _canonicalize(db, book_id, rel.person_a, name_to_entity, chapter_start_seq)
        rel.person_b = _canonicalize(db, book_id, rel.person_b, name_to_entity, chapter_start_seq)

    fact.relations = [r for r in fact.relations if r.person_a and r.person_b and r.person_a != r.person_b]
    return fact, name_to_entity, report


def _canonical_of(db: Database, entity_id: str) -> str:
    row = db.query_one("SELECT canonical_name FROM entities WHERE id=?", (entity_id,))
    return row["canonical_name"] if row else ""


def _excerpt(body: str, alias: str, width: int = 160) -> str:
    pos = body.find(alias)
    if pos < 0:
        return body[:width]
    start = max(0, pos - width // 2)
    return body[start : start + width]


def _merge_candidate(
    db: Database, book_id: str, aliases: list[str], chapter_body: str
) -> str | None:
    for alias in aliases:
        alias = (alias or "").strip()
        if not alias or alias not in chapter_body:
            continue
        eid = fact_store.entity_of_alias(db, book_id, alias)
        if eid is not None:
            return eid
    return None


def _canonicalize(
    db: Database,
    book_id: str,
    name: str,
    name_to_entity: dict[str, str],
    chapter_start_seq: int,
) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    if name in name_to_entity:
        eid = name_to_entity[name]
    else:
        eid = fact_store.entity_of_alias(db, book_id, name)
        if eid is None:
            eid = fact_store.upsert_entity(db, book_id, name, first_seq=chapter_start_seq)
        name_to_entity[name] = eid
    return _canonical_of(db, eid) or name
