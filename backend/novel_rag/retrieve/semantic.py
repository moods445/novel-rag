"""语义检索（第三路）：本地 embedding + 余弦。"""

from __future__ import annotations

from ..db.conn import Database
from ..domain.models import Evidence
from ..llm.embedding import EmbeddingProvider
from ..store import index as index_store
from .evidence import from_paragraph_rows


def search(
    db: Database,
    book_id: str,
    question: str,
    *,
    embedder: EmbeddingProvider,
    up_to_seq: int | None,
    limit: int = 8,
) -> list[Evidence]:
    rows = index_store.vector_search(
        db, book_id, embedder, question, limit=limit, up_to_seq=up_to_seq
    )
    return from_paragraph_rows(rows, kind="raw")
