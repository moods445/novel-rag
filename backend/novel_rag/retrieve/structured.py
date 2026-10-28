"""结构化事实检索（第一路）。

这一路是「知识库」区别于「全文搜索」的地方：它直接查事实表，
不需要语义相似度就能回答「A 和 B 什么关系」「第 7 章发生了什么」。
"""

from __future__ import annotations

import json
import re

from ..db.conn import Database
from ..domain.models import Evidence
from ..store import facts as fact_store

_NAME_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")


def search(
    db: Database,
    book_id: str,
    question: str,
    *,
    as_of_seq: int | None,
    limit: int = 6,
) -> list[Evidence]:
    out: list[Evidence] = []

    entities = _match_entities(db, book_id, question)
    for ent in entities[:3]:
        out.extend(_entity_evidence(db, book_id, ent, limit=2))

    rels = fact_store.relation_pairs_at(db, book_id, as_of_seq)
    names = {e["canonical_name"] for e in entities}
    for rel in rels:
        a, b = rel.get("a_name") or "", rel.get("b_name") or ""
        if not names or a in names or b in names:
            out.append(
                Evidence(
                    seq=int(rel["valid_from_seq"]),
                    cfi=rel.get("evidence_cfi") or "",
                    text=f"{a} {rel['rel_type']} {b}（起始于第 {int(rel['valid_from_seq'])} 个段落）",
                    chapter_idx=0,
                    source="fact",
                    coverage="extracted",
                    kind="relation",
                    score=0.9,
                    label="关系",
                )
            )

    keywords = _keywords(question)
    if keywords:
        rows = db.query(
            "SELECT seq, chapter_idx, summary, evidence_cfi FROM events WHERE book_id=? "
            + ("AND seq<=? " if as_of_seq is not None else "")
            + "ORDER BY seq",
            ((book_id, as_of_seq) if as_of_seq is not None else (book_id,)),
        )
        for row in rows:
            summary = row["summary"] or ""
            if any(k in summary for k in keywords):
                out.append(
                    Evidence(
                        seq=int(row["seq"]),
                        cfi=row["evidence_cfi"] or "",
                        text=summary,
                        chapter_idx=int(row["chapter_idx"]),
                        source="fact",
                        coverage="extracted",
                        kind="event",
                        score=0.8,
                        label="事件",
                    )
                )
            if len(out) > limit * 3:
                break

    return out[: limit * 2]


def _match_entities(db: Database, book_id: str, question: str) -> list[dict]:
    rows = db.query(
        "SELECT e.*, a.alias FROM entities e LEFT JOIN entity_aliases a ON a.entity_id=e.id "
        "WHERE e.book_id=?",
        (book_id,),
    )
    hits: dict[str, dict] = {}
    for r in rows:
        alias = r["alias"] or ""
        name = r["canonical_name"] or ""
        if (name and name in question) or (alias and alias in question):
            hits[r["id"]] = dict(r)
    return list(hits.values())


def _entity_evidence(db: Database, book_id: str, entity: dict, *, limit: int) -> list[Evidence]:
    out: list[Evidence] = []
    payload_row = db.query_one(
        "SELECT payload FROM chapter_facts WHERE book_id=? ORDER BY chapter_idx", (book_id,)
    )
    _ = payload_row  # 保留：后续可按实体索引事实，不必扫全表

    aliases = db.query(
        "SELECT alias FROM entity_aliases WHERE book_id=? AND entity_id=?", (book_id, entity["id"])
    )
    canonical = entity["canonical_name"]
    alias_names = [a["alias"] for a in aliases if a["alias"] != canonical]
    text = (
        f"{canonical}（别名：{'、'.join(alias_names)}）" if alias_names else f"{canonical}"
    )
    out.append(
        Evidence(
            seq=int(entity.get("first_seq") or 0),
            cfi="",
            text=text,
            chapter_idx=0,
            source="fact",
            coverage="extracted",
            kind="entity",
            score=0.95,
            label="人物档案",
        )
    )
    _ = limit
    return out


def _keywords(question: str) -> list[str]:
    stop = {"什么", "为什么", "怎么", "如何", "哪里", "哪个", "谁", "是否", "请问", "一下", "他们"}
    return [w for w in _NAME_RE.findall(question) if w not in stop][:4]


def entity_profile(db: Database, book_id: str, name: str, *, as_of_seq: int | None = None) -> dict | None:
    entity = fact_store.entity_by_name(db, book_id, name)
    if entity is None:
        return None
    aliases = db.query(
        "SELECT alias, evidence_seq FROM entity_aliases WHERE book_id=? AND entity_id=?",
        (book_id, entity["id"]),
    )
    rels = [
        r
        for r in fact_store.relation_pairs_at(db, book_id, as_of_seq)
        if entity["id"] in (r["a_entity"], r["b_entity"])
    ]
    events = db.query(
        "SELECT seq, chapter_idx, summary FROM events WHERE book_id=? AND actors LIKE ? ORDER BY seq LIMIT 30",
        (book_id, f"%{name}%"),
    )
    return {
        "entity": {"id": entity["id"], "name": entity["canonical_name"], "kind": entity["kind"]},
        "aliases": [a["alias"] for a in aliases],
        "relations": [
            {
                "a": r.get("a_name"),
                "b": r.get("b_name"),
                "rel_type": r["rel_type"],
                "valid_from_seq": int(r["valid_from_seq"]),
                "valid_to_seq": r["valid_to_seq"],
            }
            for r in rels
        ],
        "events": [{"seq": int(e["seq"]), "chapter_idx": int(e["chapter_idx"]), "summary": e["summary"]} for e in events],
    }


def events_json(rows) -> list[dict]:
    return [
        {
            "seq": int(r["seq"]),
            "chapter_idx": int(r["chapter_idx"]),
            "kind": r["kind"],
            "summary": r["summary"],
            "actors": json.loads(r["actors"] or "[]"),
        }
        for r in rows
    ]
