"""seq 索引器：把章节切成段落并分配全书单调递增的 ``seq``。

这是「业务坐标系」的唯一生成点（决策 #8）。

保真约束（风险 #3）：段落必须是章节正文的**精确切片**。
因此段落文本一律用 ``body[start:end]`` 取，绝不做 strip/正则替换；
断言 ``body[off:off+len(text)] == text`` 在开发期强制校验。
"""

from __future__ import annotations

from ..db.conn import Database
from ..domain.models import Chapter, Paragraph


def cfi_prefix(chapter_spine: int, para_idx: int) -> str:
    """生成 CFI 前缀，语义是「spine 第 N 节的第 M 个 <p>」。

    与生成器保持一致：XHTML body 里第 1 个元素是 <h1>（步长 2），
    段落从第 2 个元素起（步长 4/6/8...），所以是 ``para_idx + 2``。
    这样 foliate-js 的 ``CFI.isCFI`` + ``resolveCFI`` 能直接定位到段落。
    """
    return f"epubcfi(/6/{2 * (chapter_spine + 1)}!/4/{2 * (para_idx + 2)})"


def _iter_lines(body: str):
    off = 0
    for line in body.split("\n"):
        yield off, line
        off += len(line) + 1


def split_paragraphs(body: str, *, min_chars: int = 40) -> list[tuple[int, int]]:
    """把正文切成段落区间 ``[(start, end), ...]``。

    短行（对话体常见的一句一段）向后合并，直到达到 ``min_chars``，
    这样向量检索的语义完整性更好。合并产生的段落内部可能含换行，
    但仍然是精确切片。
    """
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    acc = 0

    for off, line in _iter_lines(body):
        if not line.strip():
            continue
        if start is None:
            start = off
            acc = 0
        acc += len(line)
        if acc >= min_chars:
            ranges.append((start, off + len(line)))
            start = None
            acc = 0

    if start is not None:
        end = len(body.rstrip("\n"))
        if end > start:
            ranges.append((start, end))

    # 保真校验：开发期直接崩，生产期只会是零成本断言
    for s, e in ranges:
        assert 0 <= s < e <= len(body), (s, e, len(body))
    return ranges


def index_chapters(
    db: Database,
    book_id: str,
    chapters: list[Chapter],
    *,
    para_min_chars: int = 40,
    start_seq: int = 1,
) -> tuple[int, int]:
    """写入 segments + FTS。返回 ``(段落数, 下一个 seq)``。"""
    seq = start_seq
    total = 0
    conn = db.conn
    for chapter in chapters:
        for para_idx, (start, end) in enumerate(split_paragraphs(chapter.body, min_chars=para_min_chars)):
            text = chapter.body[start:end]
            cur = conn.execute(
                "INSERT INTO paragraphs(book_id, chapter_idx, seq, para_idx, char_off, cfi, text) "
                "VALUES(?,?,?,?,?,?,?)",
                (book_id, chapter.idx, seq, para_idx, start, cfi_prefix(chapter.spine_index, para_idx), text),
            )
            conn.execute(
                "INSERT INTO paragraphs_fts(rowid, text) VALUES(?, ?)",
                (cur.lastrowid, text),
            )
            seq += 1
            total += 1
    return total, seq


def paragraphs_of_chapter(db: Database, book_id: str, chapter_idx: int) -> list[Paragraph]:
    rows = db.query(
        "SELECT seq, chapter_idx, para_idx, char_off, cfi, text FROM paragraphs "
        "WHERE book_id=? AND chapter_idx=? ORDER BY para_idx",
        (book_id, chapter_idx),
    )
    return [Paragraph(r["seq"], r["chapter_idx"], r["para_idx"], r["char_off"], r["cfi"], r["text"]) for r in rows]


def paragraph_at_seq(db: Database, book_id: str, seq: int) -> Paragraph | None:
    row = db.query_one(
        "SELECT seq, chapter_idx, para_idx, char_off, cfi, text FROM paragraphs WHERE book_id=? AND seq=?",
        (book_id, seq),
    )
    if row is None:
        return None
    return Paragraph(row["seq"], row["chapter_idx"], row["para_idx"], row["char_off"], row["cfi"], row["text"])


def max_seq(db: Database, book_id: str) -> int:
    row = db.query_one("SELECT COALESCE(MAX(seq), 0) AS m FROM paragraphs WHERE book_id=?", (book_id,))
    return int(row["m"]) if row else 0
