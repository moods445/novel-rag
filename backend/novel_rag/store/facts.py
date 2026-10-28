"""事实层读写：章节事实、覆盖度、被拒项、实体、区间边、事件、摘要。

区间边（决策 #19）：
- 表 append-only + version。
- 唯一允许的 UPDATE 是对旧版本单列 ``valid_to_seq`` 做定向回填，
  因为「关系何时结束」只能在后续章节被发现（延迟确定）。
  禁止对整行做 load-modify-save（V2 的 last-writer-wins 事故同源）。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..db.conn import Database
from ..domain.models import TemporalEdge


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 章节事实


def put_chapter_fact(
    db: Database, book_id: str, chapter_idx: int, chapter_fp: str, payload: dict[str, Any]
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO chapter_facts(book_id, chapter_idx, chapter_fp, payload, created_at) "
        "VALUES(?,?,?,?,?)",
        (book_id, chapter_idx, chapter_fp, json.dumps(payload, ensure_ascii=False), now_iso()),
    )


def get_chapter_fact(db: Database, book_id: str, chapter_idx: int) -> dict[str, Any] | None:
    row = db.query_one(
        "SELECT payload FROM chapter_facts WHERE book_id=? AND chapter_idx=? ORDER BY created_at DESC LIMIT 1",
        (book_id, chapter_idx),
    )
    return json.loads(row["payload"]) if row else None


def has_fact(db: Database, book_id: str, chapter_idx: int, chapter_fp: str) -> bool:
    row = db.query_one(
        "SELECT 1 FROM chapter_facts WHERE book_id=? AND chapter_idx=? AND chapter_fp=?",
        (book_id, chapter_idx, chapter_fp),
    )
    return row is not None


# ---------------------------------------------------------------- 覆盖度


def set_coverage(db: Database, book_id: str, chapter_idx: int, state: str, *, errors: int = 0) -> None:
    db.execute(
        "INSERT INTO chapter_coverage(book_id, chapter_idx, state, errors, updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(book_id, chapter_idx) DO UPDATE SET state=excluded.state, "
        "errors=errors + excluded.errors, updated_at=excluded.updated_at",
        (book_id, chapter_idx, state, errors, now_iso()),
    )


def coverage_of(db: Database, book_id: str, chapter_idx: int) -> str:
    row = db.query_one(
        "SELECT state FROM chapter_coverage WHERE book_id=? AND chapter_idx=?", (book_id, chapter_idx)
    )
    return row["state"] if row else "pending"


def coverage_summary(db: Database, book_id: str) -> dict[str, Any]:
    rows = db.query(
        "SELECT state, COUNT(*) AS c FROM chapter_coverage WHERE book_id=? GROUP BY state", (book_id,)
    )
    counts = {r["state"]: int(r["c"]) for r in rows}
    total = sum(counts.values())
    done = counts.get("done", 0)
    return {
        "total": total,
        "done": done,
        "failed": counts.get("failed", 0),
        "pending": counts.get("pending", 0) + counts.get("running", 0),
        "ratio": round(done / total, 4) if total else 0.0,
    }


def extracted_chapters(db: Database, book_id: str) -> set[int]:
    rows = db.query(
        "SELECT chapter_idx FROM chapter_coverage WHERE book_id=? AND state='done'", (book_id,)
    )
    return {int(r["chapter_idx"]) for r in rows}


# ---------------------------------------------------------------- 被拒项


def add_reject(db: Database, book_id: str, chapter_idx: int, kind: str, reason: str, payload: dict) -> None:
    db.execute(
        "INSERT INTO fact_rejects(book_id, chapter_idx, kind, reason, payload) VALUES(?,?,?,?,?)",
        (book_id, chapter_idx, kind, reason, json.dumps(payload, ensure_ascii=False)),
    )


def rejects_of(db: Database, book_id: str) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT chapter_idx, kind, reason, payload FROM fact_rejects WHERE book_id=? ORDER BY id", (book_id,)
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 实体归一


def upsert_entity(
    db: Database, book_id: str, name: str, *, kind: str = "person", first_seq: int = 0
) -> str:
    found = db.query_one(
        "SELECT id FROM entities WHERE book_id=? AND canonical_name=?", (book_id, name)
    )
    if found:
        return found["id"]
    entity_id = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO entities(id, book_id, canonical_name, kind, first_seq, meta) VALUES(?,?,?,?,?,?)",
        (entity_id, book_id, name, kind, first_seq, "{}"),
    )
    db.execute(
        "INSERT OR IGNORE INTO entity_aliases(book_id, entity_id, alias, evidence_seq, evidence_cfi, source) "
        "VALUES(?,?,?,?,?,'canonical')",
        (book_id, entity_id, name, first_seq, ""),
    )
    return entity_id


def entity_of_alias(db: Database, book_id: str, alias: str) -> str | None:
    row = db.query_one(
        "SELECT entity_id FROM entity_aliases WHERE book_id=? AND alias=?", (book_id, alias)
    )
    return row["entity_id"] if row else None


def add_alias(
    db: Database,
    book_id: str,
    entity_id: str,
    alias: str,
    *,
    seq: int | None,
    cfi: str = "",
    source: str = "fact",
) -> bool:
    """写入别名。返回是否新增。已存在且指向同一实体的别名视为无操作。"""
    existing = entity_of_alias(db, book_id, alias)
    if existing is not None:
        return existing != entity_id
    db.execute(
        "INSERT INTO entity_aliases(book_id, entity_id, alias, evidence_seq, evidence_cfi, source) "
        "VALUES(?,?,?,?,?,?)",
        (book_id, entity_id, alias, seq, cfi, source),
    )
    return True


def add_audit(
    db: Database,
    book_id: str,
    chapter_idx: int,
    alias: str,
    candidates: list[str],
    decision: str,
    *,
    source: str = "rule",
    note: str = "",
) -> None:
    db.execute(
        "INSERT INTO resolution_audit(book_id, chapter_idx, alias, candidates, decision, source, note, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            book_id,
            chapter_idx,
            alias,
            json.dumps(candidates, ensure_ascii=False),
            decision,
            source,
            note,
            now_iso(),
        ),
    )


def audit_of(db: Database, book_id: str) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT chapter_idx, alias, candidates, decision, source, note FROM resolution_audit "
        "WHERE book_id=? ORDER BY id",
        (book_id,),
    )
    return [
        {
            "chapter_idx": r["chapter_idx"],
            "alias": r["alias"],
            "candidates": json.loads(r["candidates"] or "[]"),
            "decision": r["decision"],
            "source": r["source"],
            "note": r["note"],
        }
        for r in rows
    ]


def entity_by_name(db: Database, book_id: str, name: str) -> dict[str, Any] | None:
    row = db.query_one(
        "SELECT * FROM entities WHERE book_id=? AND canonical_name=?", (book_id, name)
    )
    if row is None:
        eid = entity_of_alias(db, book_id, name)
        if eid is None:
            return None
        row = db.query_one("SELECT * FROM entities WHERE id=?", (eid,))
        if row is None:
            return None
    return dict(row)


# ---------------------------------------------------------------- 区间边


def _open_relation(db: Database, book_id: str, a: str, b: str):
    return db.query_one(
        "SELECT * FROM relations WHERE book_id=? AND a_entity=? AND b_entity=? "
        "ORDER BY version DESC, id DESC LIMIT 1",
        (book_id, a, b),
    )


def append_relation(
    db: Database,
    book_id: str,
    a_entity: str,
    b_entity: str,
    rel_type: str,
    *,
    valid_from_seq: int,
    evidence_seq: int | None = None,
    evidence_cfi: str = "",
) -> int:
    """追加一条区间边。关系未变化则不新增。

    返回新版本号。
    """
    if a_entity == b_entity or not rel_type:
        return 0

    current = _open_relation(db, book_id, a_entity, b_entity)
    if current is not None and current["rel_type"] == rel_type and current["valid_to_seq"] is None:
        return int(current["version"])

    version = int(current["version"]) + 1 if current is not None else 1

    with db.tx() as conn:
        if current is not None and current["valid_to_seq"] is None:
            # 唯一允许的定向回填：关系结束于发现变化的上一章之前
            conn.execute(
                "UPDATE relations SET valid_to_seq=? WHERE id=?",
                (max(int(current["valid_from_seq"]), valid_from_seq - 1), current["id"]),
            )
        conn.execute(
            "INSERT INTO relations(book_id, a_entity, b_entity, rel_type, valid_from_seq, valid_to_seq, "
            "version, evidence_seq, evidence_cfi, meta) VALUES(?,?,?,?,?,NULL,?,?,?,'{}')",
            (book_id, a_entity, b_entity, rel_type, valid_from_seq, version, evidence_seq, evidence_cfi),
        )
    return version


def relations_at(db: Database, book_id: str, as_of_seq: int | None) -> list[TemporalEdge]:
    """物化到 ``as_of_seq`` 的关系图（``None`` 表示全书最新）。"""
    if as_of_seq is None:
        rows = db.query(
            "SELECT * FROM relations WHERE book_id=? AND valid_to_seq IS NULL ORDER BY id", (book_id,)
        )
    else:
        rows = db.query(
            "SELECT * FROM relations WHERE book_id=? AND valid_from_seq<=? "
            "AND (valid_to_seq IS NULL OR valid_to_seq>=?) ORDER BY id",
            (book_id, as_of_seq, as_of_seq),
        )
    return [
        TemporalEdge(
            a_entity=r["a_entity"],
            b_entity=r["b_entity"],
            rel_type=r["rel_type"],
            valid_from_seq=int(r["valid_from_seq"]),
            valid_to_seq=int(r["valid_to_seq"]) if r["valid_to_seq"] is not None else None,
            version=int(r["version"]),
            evidence_seq=r["evidence_seq"],
            evidence_cfi=r["evidence_cfi"],
        )
        for r in rows
    ]


def relation_pairs_at(db: Database, book_id: str, as_of_seq: int | None) -> list[dict[str, Any]]:
    """带实体名的关系列表，供图谱读模型直接使用。"""
    if as_of_seq is None:
        rows = db.query(
            "SELECT r.*, ea.canonical_name AS a_name, eb.canonical_name AS b_name FROM relations r "
            "LEFT JOIN entities ea ON ea.id=r.a_entity LEFT JOIN entities eb ON eb.id=r.b_entity "
            "WHERE r.book_id=? AND r.valid_to_seq IS NULL ORDER BY r.id",
            (book_id,),
        )
    else:
        rows = db.query(
            "SELECT r.*, ea.canonical_name AS a_name, eb.canonical_name AS b_name FROM relations r "
            "LEFT JOIN entities ea ON ea.id=r.a_entity LEFT JOIN entities eb ON eb.id=r.b_entity "
            "WHERE r.book_id=? AND r.valid_from_seq<=? AND (r.valid_to_seq IS NULL OR r.valid_to_seq>=?) "
            "ORDER BY r.id",
            (book_id, as_of_seq, as_of_seq),
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 事件与摘要


def add_event(
    db: Database,
    book_id: str,
    chapter_idx: int,
    seq: int,
    summary: str,
    *,
    actors: list[str] | None = None,
    kind: str = "plot",
    evidence_cfi: str = "",
) -> None:
    if not summary.strip():
        return
    db.execute(
        "INSERT OR IGNORE INTO events(book_id, chapter_idx, seq, kind, summary, actors, evidence_cfi) "
        "VALUES(?,?,?,?,?,?,?)",
        (book_id, chapter_idx, seq, kind, summary.strip(), json.dumps(actors or [], ensure_ascii=False), evidence_cfi),
    )


def events_between(db: Database, book_id: str, from_seq: int, to_seq: int) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM events WHERE book_id=? AND seq BETWEEN ? AND ? ORDER BY seq",
        (book_id, from_seq, to_seq),
    )
    return [dict(r) for r in rows]


def timeline(db: Database, book_id: str, up_to_seq: int | None = None) -> list[dict[str, Any]]:
    if up_to_seq is None:
        rows = db.query("SELECT * FROM events WHERE book_id=? ORDER BY seq", (book_id,))
    else:
        rows = db.query("SELECT * FROM events WHERE book_id=? AND seq<=? ORDER BY seq", (book_id, up_to_seq))
    return [
        {
            "seq": int(r["seq"]),
            "chapter_idx": int(r["chapter_idx"]),
            "kind": r["kind"],
            "summary": r["summary"],
            "actors": json.loads(r["actors"] or "[]"),
            "cfi": r["evidence_cfi"],
        }
        for r in rows
    ]


def put_summary(db: Database, book_id: str, chapter_idx: int, summary: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO chapter_summaries(book_id, chapter_idx, summary) VALUES(?,?,?)",
        (book_id, chapter_idx, summary),
    )


def recent_summaries(db: Database, book_id: str, before_idx: int, limit: int = 3) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT chapter_idx, summary FROM chapter_summaries WHERE book_id=? AND chapter_idx<? "
        "ORDER BY chapter_idx DESC LIMIT ?",
        (book_id, before_idx, limit),
    )
    return [dict(r) for r in reversed(rows)]


def summary_span(db: Database, book_id: str, from_idx: int, to_idx: int) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT chapter_idx, summary FROM chapter_summaries WHERE book_id=? AND chapter_idx BETWEEN ? AND ? "
        "ORDER BY chapter_idx",
        (book_id, from_idx, to_idx),
    )
    return [dict(r) for r in rows]
