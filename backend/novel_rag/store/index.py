"""索引层：FTS5（trigram）+ 向量（按 model_id 分区）。

中文用 trigram tokenizer：unicode61 会把整句当一个 token，检索完全失效。
trigram 需要查询串 >= 3 字符，因此短查询（如两字人名）回退到 LIKE。
"""

from __future__ import annotations

import re

from ..db.conn import Database
from ..llm.embedding import EmbeddingProvider, to_blob, embed_query

_CJK = re.compile(r"[\u4e00-\u9fff]")


# ---------------------------------------------------------------- FTS


def rebuild_fts(db: Database, book_id: str) -> int:
    """重建某本书的 FTS 行（幂等）。"""
    rows = db.query("SELECT id, text FROM paragraphs WHERE book_id=?", (book_id,))
    db.execute(
        "DELETE FROM paragraphs_fts WHERE rowid IN (SELECT id FROM paragraphs WHERE book_id=?)", (book_id,)
    )
    for r in rows:
        db.execute("INSERT INTO paragraphs_fts(rowid, text) VALUES(?,?)", (r["id"], r["text"]))
    return len(rows)


def fts_search(
    db: Database, book_id: str, query: str, limit: int = 8, up_to_seq: int | None = None
) -> list[dict]:
    """全文检索。返回 ``seq, cfi, chapter_idx, text, rank``。"""
    q = query.strip()
    if not q:
        return []

    if len(q) >= 3 and _CJK.search(q):
        match = '"' + q.replace('"', " ") + '"'
        sql = (
            "SELECT p.seq, p.cfi, p.chapter_idx, p.text, bm25(paragraphs_fts) AS rank "
            "FROM paragraphs_fts f JOIN paragraphs p ON p.id=f.rowid "
            "WHERE paragraphs_fts MATCH ? AND p.book_id=? "
        )
        params: list = [match, book_id]
        if up_to_seq is not None:
            sql += "AND p.seq<=? "
            params.append(up_to_seq)
        sql += "ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            rows = db.query(sql, tuple(params))
        except Exception:  # trigram 查询串异常时退化为 LIKE
            rows = []
        if rows:
            return [dict(r) for r in rows]

    like = f"%{q}%"
    sql = "SELECT seq, cfi, chapter_idx, text, 0.0 AS rank FROM paragraphs WHERE book_id=? AND text LIKE ? "
    params = [book_id, like]
    if up_to_seq is not None:
        sql += "AND seq<=? "
        params.append(up_to_seq)
    sql += "ORDER BY seq LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.query(sql, tuple(params))]


# ---------------------------------------------------------------- 向量


def index_vectors(
    db: Database,
    book_id: str,
    embedder: EmbeddingProvider,
    *,
    chapter_idx: int | None = None,
    batch: int = 8,
) -> int:
    """建向量。``chapter_idx`` 非空时只处理该章（增量可用）。"""
    sql = "SELECT seq, text FROM paragraphs WHERE book_id=?"
    params: tuple = (book_id,)
    if chapter_idx is not None:
        sql += " AND chapter_idx=?"
        params = (book_id, chapter_idx)
    sql += " ORDER BY seq"
    rows = db.query(sql, params)
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        vecs = embedder.embed([r["text"] for r in chunk])
        with db.tx() as conn:
            for r, vec in zip(chunk, vecs):
                conn.execute(
                    "INSERT OR REPLACE INTO vectors(book_id, seq, model_id, dim, vec) VALUES(?,?,?,?,?)",
                    (book_id, r["seq"], embedder.model_id, embedder.dim, to_blob(vec)),
                )
        total += len(chunk)
    return total


def vector_search(
    db: Database,
    book_id: str,
    embedder: EmbeddingProvider,
    query: str,
    *,
    limit: int = 8,
    up_to_seq: int | None = None,
) -> list[dict]:
    """余弦相似度检索。MVP 阶段全量加载，书量大时应换 ANN 索引。"""
    sql = (
        "SELECT v.seq, v.vec, p.cfi, p.chapter_idx, p.text FROM vectors v "
        "JOIN paragraphs p ON p.book_id=v.book_id AND p.seq=v.seq "
        "WHERE v.book_id=? AND v.model_id=? "
    )
    params: list = [book_id, embedder.model_id]
    if up_to_seq is not None:
        sql += "AND v.seq<=? "
        params.append(up_to_seq)
    rows = db.query(sql, tuple(params))
    if not rows:
        return []

    qv = embed_query(embedder, query)
    scores = _dot_scores([r["vec"] for r in rows], qv)
    scored = [
        {
            "seq": r["seq"],
            "cfi": r["cfi"],
            "chapter_idx": r["chapter_idx"],
            "text": r["text"],
            "score": float(score),
        }
        for r, score in zip(rows, scores)
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def _dot_scores(blobs: list[bytes], query: list[float]) -> list[float]:
    """向量已 L2 归一，点积即余弦。有 numpy 时走矩阵乘，否则纯 Python 兜底。"""
    try:
        import numpy as np
    except ImportError:
        from ..llm.embedding import cosine, from_blob

        return [cosine(query, from_blob(b)) for b in blobs]

    mat = np.stack([np.frombuffer(b, dtype=np.float32) for b in blobs])
    q = np.asarray(query, dtype=np.float32)
    if mat.shape[1] != q.shape[0]:
        return [0.0] * len(blobs)
    return (mat @ q).tolist()


def vector_coverage(db: Database, book_id: str, model_id: str) -> dict:
    row = db.query_one(
        "SELECT COUNT(*) AS c FROM vectors WHERE book_id=? AND model_id=?", (book_id, model_id)
    )
    total = db.query_one("SELECT COUNT(*) AS c FROM paragraphs WHERE book_id=?", (book_id,))
    return {
        "indexed": int(row["c"]) if row else 0,
        "paragraphs": int(total["c"]) if total else 0,
        "model_id": model_id,
    }


def clear_vectors(db: Database, book_id: str) -> int:
    """删掉某本书的全部向量（重建/换模型时用）。"""
    cur = db.execute("DELETE FROM vectors WHERE book_id=?", (book_id,))
    return int(cur.rowcount or 0)


def vector_status(db: Database, book_id: str, model_id: str) -> dict:
    """按章节统计向量覆盖，返回明细与「进行到第几章」。

    只统计当前 ``model_id``：换 embedding 模型后旧向量不可复用
    （向量空间不通用），展示时必须反映真实可检索的覆盖。
    """
    rows = db.query(
        "SELECT c.idx, c.title, "
        "  COUNT(p.id) AS total, "
        "  SUM(CASE WHEN v.seq IS NOT NULL THEN 1 ELSE 0 END) AS indexed "
        "FROM chapters c "
        "LEFT JOIN paragraphs p ON p.book_id=c.book_id AND p.chapter_idx=c.idx "
        "LEFT JOIN vectors v ON v.book_id=p.book_id AND v.seq=p.seq AND v.model_id=? "
        "WHERE c.book_id=? GROUP BY c.idx ORDER BY c.idx",
        (model_id, book_id),
    )
    chapters: list[dict] = []
    indexed_total = 0
    paragraph_total = 0
    done_chapters = 0
    for row in rows:
        total = int(row["total"] or 0)
        indexed = int(row["indexed"] or 0)
        indexed_total += indexed
        paragraph_total += total
        percent = round(indexed / total * 100) if total else 100
        if total and indexed >= total:
            done_chapters += 1
        chapters.append(
            {
                "chapter_idx": int(row["idx"]),
                "chapter_no": int(row["idx"]) + 1,
                "title": row["title"],
                "total": total,
                "indexed": indexed,
                "percent": percent,
                "done": total == 0 or indexed >= total,
            }
        )

    next_chapter = next((c for c in chapters if not c["done"]), None)
    return {
        "model_id": model_id,
        "indexed": indexed_total,
        "paragraphs": paragraph_total,
        "percent": round(indexed_total / paragraph_total * 100) if paragraph_total else 0,
        "chapters_total": len(chapters),
        "chapters_done": done_chapters,
        "next_chapter_no": next_chapter["chapter_no"] if next_chapter else None,
        "next_chapter_idx": next_chapter["chapter_idx"] if next_chapter else None,
        "chapters": chapters,
    }
