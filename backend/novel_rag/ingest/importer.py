"""导入编排：字节 → 文本 → 章节 → 段落(seq) → 落库。

幂等：同一份文本（sha256 + 章节指纹）重复导入会直接复用已有书籍记录。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import Settings, get_settings
from ..db.conn import Database
from ..domain.models import Book
from ..llm.gateway import LlmGateway
from .encoding import decode_text, normalize_newlines
from .llm_split import build_chapters, propose_split
from .seq_indexer import index_chapters, max_seq
from .splitter import SplitResult, split_chapters


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class ImportResult:
    book: Book
    chapter_count: int
    paragraph_count: int
    reused: bool
    split_rule: str
    confidence: float
    needs_llm: bool


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chapter_fp(chapters) -> str:
    joined = "|".join(ch.fp for ch in chapters)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def import_text(
    db: Database,
    *,
    text: str,
    title: str = "未命名",
    author: str = "",
    source_name: str = "",
    settings: Settings | None = None,
    encoding: str = "utf-8",
    llm: LlmGateway | None = None,
) -> ImportResult:
    settings = settings or get_settings()
    normalized = normalize_newlines(text)
    if not normalized.strip():
        raise ValueError("空文本")

    digest = _sha256(normalized)
    split = split_chapters(normalized, min_confidence=settings.split_min_confidence)

    # 章节识别后端可配（界面）：auto 只在规则置信度不足时动用 LLM，llm 则总是。
    backend = (settings.split_backend or "auto").lower()
    want_llm = backend == "llm" or (backend == "auto" and split.needs_llm)
    if want_llm and llm is not None:
        proposal = propose_split(llm, normalized)
        if proposal is not None:
            chapters = build_chapters(normalized, proposal)
            if len(chapters) >= 2:
                split = SplitResult(
                    chapters=chapters,
                    rule="llm",
                    confidence=0.8,
                    needs_llm=False,
                )

    if not split.chapters:
        raise ValueError("无法识别出任何章节")
    chapter_fp = _chapter_fp(split.chapters)

    existing = db.query_one(
        "SELECT * FROM books WHERE source_sha256=? AND chapter_fp=?", (digest, chapter_fp)
    )
    if existing is not None:
        book = _row_to_book(existing)
        row = db.query_one(
            "SELECT COUNT(*) AS c FROM paragraphs WHERE book_id=?", (book.id,)
        )
        return ImportResult(
            book=book,
            chapter_count=len(split.chapters),
            paragraph_count=int(row["c"]) if row else 0,
            reused=True,
            split_rule=split.rule,
            confidence=split.confidence,
            needs_llm=split.needs_llm,
        )

    book_id = uuid.uuid4().hex[:16]
    created = now_iso()

    with db.tx() as conn:
        conn.execute(
            "INSERT INTO books(id, title, author, source_name, source_sha256, chapter_fp, "
            "created_at, meta, raw_text) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                book_id,
                title.strip() or "未命名",
                author.strip(),
                source_name,
                digest,
                chapter_fp,
                created,
                '{"encoding": "%s", "split_rule": "%s"}' % (encoding, split.rule),
                normalized,
            ),
        )
        for ch in split.chapters:
            conn.execute(
                "INSERT INTO chapters(book_id, idx, title, fp, spine_index, nav_href, "
                "char_start, char_end, body, split_source, confidence) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
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

    paragraph_count, next_seq = index_chapters(
        db, book_id, split.chapters, para_min_chars=settings.para_min_chars
    )

    with db.tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reading_progress(book_id, progress_seq, progress_cfi, spoiler_guard, updated_at) "
            "VALUES(?,?,?,?,?)",
            (book_id, 1, "", 1 if settings.spoiler_guard_default else 0, created),
        )
        conn.execute(
            "INSERT OR REPLACE INTO analysis_tasks(book_id, state, cursor, total, message, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (book_id, "queued", 0, len(split.chapters), "", created),
        )

    book = Book(
        id=book_id,
        title=title.strip() or "未命名",
        author=author.strip(),
        source_name=source_name,
        source_sha256=digest,
        chapter_fp=chapter_fp,
        created_at=created,
    )
    return ImportResult(
        book=book,
        chapter_count=len(split.chapters),
        paragraph_count=paragraph_count,
        reused=False,
        split_rule=split.rule,
        confidence=split.confidence,
        needs_llm=split.needs_llm,
    )


def import_bytes(
    db: Database,
    raw: bytes,
    *,
    title: str = "未命名",
    author: str = "",
    source_name: str = "",
    settings: Settings | None = None,
    llm: LlmGateway | None = None,
) -> ImportResult:
    decoded = decode_text(raw)
    return import_text(
        db,
        text=decoded.text,
        title=title,
        author=author,
        source_name=source_name,
        settings=settings,
        encoding=decoded.encoding,
        llm=llm,
    )


def _row_to_book(row) -> Book:
    return Book(
        id=row["id"],
        title=row["title"],
        author=row["author"],
        source_name=row["source_name"],
        source_sha256=row["source_sha256"],
        chapter_fp=row["chapter_fp"],
        created_at=row["created_at"],
    )


def get_book(db: Database, book_id: str) -> Book | None:
    row = db.query_one("SELECT * FROM books WHERE id=?", (book_id,))
    return _row_to_book(row) if row else None


def list_books(db: Database) -> list[dict]:
    rows = db.query(
        "SELECT b.*, (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id) AS chapter_count, "
        "(SELECT COALESCE(MAX(p.seq),0) FROM paragraphs p WHERE p.book_id=b.id) AS max_seq "
        "FROM books b ORDER BY b.created_at DESC"
    )
    return [dict(r) for r in rows]


def book_shape(db: Database, book_id: str) -> dict:
    return {
        "max_seq": max_seq(db, book_id),
        "chapters": int(db.query_one("SELECT COUNT(*) AS c FROM chapters WHERE book_id=?", (book_id,))["c"]),
    }
