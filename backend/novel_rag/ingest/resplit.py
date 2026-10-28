"""人工校正：改章节标题 / 重新分章（决策 #16 的「人工校正」环节）。

重新分章会重算 ``seq`` 与 ``cfi``，因此**必须显式告知知识库失效**：
- 章节事实 / 实体 / 关系 / 事件 / 摘要全部作废（它们以 ``chapter_fp`` 为锚）；
- 笔记与阅读进度保留（用户数据），但位置会漂移，返回里显式标注。

不做「静默重排」——这正是 Anx 静态 CFI 集体失效的教训。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db.conn import Database
from ..errors import InvalidInput, NotFound
from ..llm.gateway import LlmGateway
from .importer import now_iso
from .llm_split import build_chapters as build_llm_chapters
from .llm_split import propose_split
from .seq_indexer import index_chapters
from .splitter import build_chapters_from_hits, split_chapters


@dataclass(slots=True)
class ResplitResult:
    chapter_count: int = 0
    paragraph_count: int = 0
    split_rule: str = ""
    confidence: float = 0.0
    chapter_fp: str = ""
    invalidated: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chapter_count": self.chapter_count,
            "paragraph_count": self.paragraph_count,
            "split_rule": self.split_rule,
            "confidence": self.confidence,
            "chapter_fp": self.chapter_fp,
            "invalidated": self.invalidated,
            "warnings": self.warnings,
        }


def _raw_text(db: Database, book_id: str) -> str:
    row = db.query_one("SELECT raw_text FROM books WHERE id=?", (book_id,))
    if row is None:
        raise NotFound("book not found")
    text = row["raw_text"] or ""
    if not text.strip():
        raise InvalidInput("该书没有保存原文，无法重新分章（请重新导入）")
    return text


def _chapter_fp(chapters) -> str:
    joined = "|".join(ch.fp for ch in chapters)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def update_chapter_title(db: Database, book_id: str, idx: int, title: str) -> dict:
    title = (title or "").strip()
    if not title:
        raise InvalidInput("标题不能为空")
    row = db.query_one("SELECT idx FROM chapters WHERE book_id=? AND idx=?", (book_id, idx))
    if row is None:
        raise NotFound("chapter not found")
    db.execute("UPDATE chapters SET title=? WHERE book_id=? AND idx=?", (title, book_id, idx))
    return {"idx": idx, "title": title, "chapter_fp": _current_fp(db, book_id)}


def _current_fp(db: Database, book_id: str) -> str:
    row = db.query_one("SELECT chapter_fp FROM books WHERE id=?", (book_id,))
    return row["chapter_fp"] if row else ""


def resplit_book(
    db: Database,
    book_id: str,
    *,
    pattern: str | None = None,
    min_confidence: float | None = None,
    settings: Settings | None = None,
    llm: LlmGateway | None = None,
) -> ResplitResult:
    """用原文重新分章。``pattern`` 为空时走默认规则（必要时 LLM 兜底）。"""
    settings = settings or get_settings()
    text = _raw_text(db, book_id)

    if pattern:
        try:
            compiled = re.compile(pattern, re.MULTILINE)
        except re.error as exc:
            raise InvalidInput(f"正则非法: {exc}") from exc
        hits = [
            (m.start(), m.end(), m.group(0).strip())
            for m in compiled.finditer(text)
            if m.group(0).strip()
        ]
        if len(hits) < 2:
            raise InvalidInput("该正则只匹配到 1 处，至少需要 2 个章节标题")
        chapters = build_chapters_from_hits(text, hits, "manual", 1.0)
        split_rule, confidence = "manual", 1.0
    else:
        split = split_chapters(
            text, min_confidence=min_confidence if min_confidence is not None else settings.split_min_confidence
        )
        backend = (settings.split_backend or "auto").lower()
        want_llm = backend == "llm" or (backend == "auto" and split.needs_llm)
        if want_llm and llm is not None:
            proposal = propose_split(llm, text)
            if proposal is not None:
                chapters = build_llm_chapters(text, proposal)
                split_rule, confidence = "llm", 0.8
            else:
                chapters, split_rule, confidence = split.chapters, split.rule, split.confidence
        else:
            chapters, split_rule, confidence = split.chapters, split.rule, split.confidence

    if not chapters:
        raise InvalidInput("未能识别出任何章节")

    for spine, ch in enumerate(chapters):
        ch.idx = spine
        ch.spine_index = spine
        ch.nav_href = f"ch{spine + 1:04d}.xhtml"

    invalidated = _invalidate(db, book_id)
    new_fp = _chapter_fp(chapters)
    created = now_iso()

    with db.tx() as conn:
        for ch in chapters:
            conn.execute(
                "INSERT INTO chapters(book_id, idx, title, fp, spine_index, nav_href, char_start, char_end, "
                "body, split_source, confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    book_id,
                    ch.idx,
                    ch.title,
                    ch.fp,
                    ch.spine_index,
                    ch.nav_href,
                    ch.char_start,
                    ch.char_end,
                    ch.body,
                    ch.split_source,
                    ch.confidence,
                ),
            )
            conn.execute(
                "INSERT INTO chapter_coverage(book_id, chapter_idx, state, errors, updated_at) "
                "VALUES(?,?,'pending',0,?)",
                (book_id, ch.idx, created),
            )
        conn.execute(
            "UPDATE books SET chapter_fp=?, meta=json_set(meta, '$.split_rule', ?) WHERE id=?",
            (new_fp, split_rule, book_id),
        )
        conn.execute(
            "INSERT INTO analysis_tasks(book_id, state, cursor, total, message, updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(book_id) DO UPDATE SET state='queued', cursor=0, "
            "total=excluded.total, message='', updated_at=excluded.updated_at",
            (book_id, "queued", 0, len(chapters), "resplit", created),
        )
        conn.execute(
            "INSERT INTO reading_progress(book_id, progress_seq, progress_cfi, spoiler_guard, updated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(book_id) DO UPDATE SET progress_seq=1, progress_cfi='', "
            "updated_at=excluded.updated_at",
            (book_id, 1, "", 1, created),
        )

    paragraph_count, _next_seq = index_chapters(
        db, book_id, chapters, para_min_chars=settings.para_min_chars
    )

    result = ResplitResult(
        chapter_count=len(chapters),
        paragraph_count=paragraph_count,
        split_rule=split_rule,
        confidence=confidence,
        chapter_fp=new_fp,
        invalidated=invalidated,
    )
    if invalidated.get("notes"):
        result.warnings.append(
            f"{invalidated['notes']} 条笔记/书签的位置已变化（seq 与 CFI 重算），需要按章节指纹重新锚定"
        )
    if invalidated.get("facts"):
        result.warnings.append(
            f"{invalidated['facts']} 份章节事实已作废，重新分析后恢复"
        )
    return result


def _invalidate(db: Database, book_id: str) -> dict[str, int]:
    counts = {
        "facts": _count(db, "chapter_facts", book_id),
        "entities": _count(db, "entities", book_id),
        "relations": _count(db, "relations", book_id),
        "events": _count(db, "events", book_id),
        "summaries": _count(db, "chapter_summaries", book_id),
        "notes": _count(db, "notes", book_id),
        "paragraphs": _count(db, "paragraphs", book_id),
    }

    with db.tx() as conn:
        conn.execute(
            "DELETE FROM paragraphs_fts WHERE rowid IN (SELECT id FROM paragraphs WHERE book_id=?)",
            (book_id,),
        )
        for table in (
            "chapter_facts",
            "fact_rejects",
            "chapter_summaries",
            "events",
            "relations",
            "entity_aliases",
            "entities",
            "vectors",
            "chapter_coverage",
            "paragraphs",
            "chapters",
            "resolution_audit",
        ):
            conn.execute(f"DELETE FROM {table} WHERE book_id=?", (book_id,))
    return counts


def _count(db: Database, table: str, book_id: str) -> int:
    row = db.query_one(f"SELECT COUNT(*) AS c FROM {table} WHERE book_id=?", (book_id,))
    return int(row["c"]) if row else 0
