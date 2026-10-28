"""只读工具集（决策 #14：≤5 个，避免本地小模型在工具选择上翻车）。

工具返回统一结构 ``{status, name, data | message}``，异常不抛出，
而是以 ``status=error`` 回灌给模型——这条约定来自 Anx 的 ``RepositoryTool``。
"""

from __future__ import annotations

import json

from ..db.conn import Database
from ..store import facts as fact_store
from ..retrieve import structured

TOOL_SPECS: list[dict] = [
    {
        "name": "get_entity_profile",
        "description": "查询某个人物的档案：别名、关系、相关事件。",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {
        "name": "search_text",
        "description": "在原文中检索关键词，返回命中的段落与位置。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_chapter",
        "description": "按章节序号取该章摘要与事实列表（序号从 1 开始）。",
        "parameters": {"type": "object", "properties": {"chapter_no": {"type": "integer"}}, "required": ["chapter_no"]},
    },
    {
        "name": "get_relation_timeline",
        "description": "取两个人物的关系演变区间。",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "get_events",
        "description": "按章节范围取事件列表。",
        "parameters": {
            "type": "object",
            "properties": {"from_chapter": {"type": "integer"}, "to_chapter": {"type": "integer"}},
            "required": ["from_chapter", "to_chapter"],
        },
    },
]


def dispatch(
    db: Database,
    book_id: str,
    name: str,
    args: dict,
    *,
    as_of_seq: int | None = None,
) -> dict:
    try:
        if name == "get_entity_profile":
            data = structured.entity_profile(db, book_id, str(args.get("name", "")), as_of_seq=as_of_seq)
            return _ok(name, data)
        if name == "search_text":
            rows = db.query(
                "SELECT seq, chapter_idx, cfi, text FROM paragraphs WHERE book_id=? AND text LIKE ? "
                + ("AND seq<=? " if as_of_seq is not None else "")
                + "ORDER BY seq LIMIT ?",
                (
                    (book_id, f"%{args.get('query', '')}%", as_of_seq, int(args.get("limit", 5)))
                    if as_of_seq is not None
                    else (book_id, f"%{args.get('query', '')}%", int(args.get("limit", 5)))
                ),
            )
            return _ok(name, [dict(r) for r in rows])
        if name == "get_chapter":
            idx = int(args.get("chapter_no", 1)) - 1
            payload = fact_store.get_chapter_fact(db, book_id, idx)
            summary = fact_store.summary_span(db, book_id, idx, idx)
            return _ok(name, {"chapter_no": idx + 1, "summary": summary, "facts": payload})
        if name == "get_relation_timeline":
            a = fact_store.entity_by_name(db, book_id, str(args.get("a", "")))
            b = fact_store.entity_by_name(db, book_id, str(args.get("b", "")))
            if not a or not b:
                return _err(name, "人物不存在")
            rows = db.query(
                "SELECT rel_type, valid_from_seq, valid_to_seq, version FROM relations "
                "WHERE book_id=? AND ((a_entity=? AND b_entity=?) OR (a_entity=? AND b_entity=?)) "
                "ORDER BY version",
                (book_id, a["id"], b["id"], b["id"], a["id"]),
            )
            return _ok(name, [dict(r) for r in rows])
        if name == "get_events":
            from_ch, to_ch = int(args.get("from_chapter", 1)), int(args.get("to_chapter", 1))
            rows = db.query(
                "SELECT seq, chapter_idx, kind, summary, actors FROM events "
                "WHERE book_id=? AND chapter_idx BETWEEN ? AND ? ORDER BY seq",
                (book_id, from_ch - 1, to_ch - 1),
            )
            return _ok(name, structured.events_json(rows))
        return _err(name, "未知工具")
    except Exception as exc:  # noqa: BLE001
        return _err(name, str(exc))


def _ok(name: str, data) -> dict:
    return {"status": "ok", "name": name, "data": data}


def _err(name: str, message: str) -> dict:
    return {"status": "error", "name": name, "message": message}


def specs_json() -> str:
    return json.dumps(TOOL_SPECS, ensure_ascii=False)
