"""词法检索（第二路）：FTS5 trigram + LIKE 兜底。"""

from __future__ import annotations

from ..db.conn import Database
from ..domain.models import Evidence
from ..store import index as index_store
from .evidence import from_paragraph_rows


def search(
    db: Database,
    book_id: str,
    question: str,
    *,
    up_to_seq: int | None,
    limit: int = 8,
) -> list[Evidence]:
    rows = index_store.fts_search(db, book_id, question, limit=limit, up_to_seq=up_to_seq)
    if not rows:
        # 长问句可能整串匹配不到，退化为关键词逐个检索
        for word in _tokens(question):
            rows = index_store.fts_search(db, book_id, word, limit=limit, up_to_seq=up_to_seq)
            if rows:
                break
    evidences = from_paragraph_rows(rows, kind="raw", score_key="rank")
    # bm25 归一：rank 越小越好，转成正向分数
    for ev in evidences:
        ev.score = 0.5 + min(0.4, ev.score)
    return evidences


def _tokens(question: str) -> list[str]:
    cleaned = question.strip()
    parts = [p for p in cleaned.replace("？", " ").replace("，", " ").split() if p]
    if len(parts) > 1:
        return sorted(parts, key=len, reverse=True)
    return [cleaned[i : i + 3] for i in range(0, max(0, len(cleaned) - 3), 3)][:4]
