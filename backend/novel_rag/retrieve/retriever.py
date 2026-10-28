"""检索编排：三路召回 → 融合 → 双闸门 → 交给 ContextCompiler。

注意闸门顺序：先按 ``progress_seq`` 在**检索阶段就带上截止**（减少无效计算），
再在证据层做一次兜底过滤（防止结构化事实绕过截止）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db.conn import Database
from ..domain.models import CompiledContext, Evidence
from ..llm.embedding import EmbeddingProvider
from ..store import facts as fact_store
from . import lexical, semantic, structured
from .compiler import CompilerConfig, compile_context
from .fuse import merge
from .gates import GateResult, apply as apply_gates


@dataclass
class Retriever:
    db: Database
    embedder: EmbeddingProvider
    settings: Settings = field(default_factory=get_settings)

    def gather(
        self,
        book_id: str,
        question: str,
        *,
        progress_seq: int | None = None,
        spoiler_guard: bool = True,
    ) -> tuple[list[Evidence], GateResult, dict]:
        cutoff = progress_seq if spoiler_guard else None

        s_rows = structured.search(
            self.db, book_id, question, as_of_seq=cutoff, limit=self.settings.semantic_top_k
        )
        l_rows = lexical.search(
            self.db, book_id, question, up_to_seq=cutoff, limit=self.settings.lexical_top_k
        )
        v_rows = semantic.search(
            self.db,
            book_id,
            question,
            embedder=self.embedder,
            up_to_seq=cutoff,
            limit=self.settings.semantic_top_k,
        )

        merged = merge([s_rows, l_rows, v_rows])
        extracted = fact_store.extracted_chapters(self.db, book_id)
        gates = apply_gates(
            merged,
            progress_seq=progress_seq,
            spoiler_guard=spoiler_guard,
            extracted_chapters=extracted,
        )

        meta = {
            "routes": {
                "structured": len(s_rows),
                "lexical": len(l_rows),
                "semantic": len(v_rows),
            },
            "gates": gates.counts(),
            "progress_seq": progress_seq,
            "spoiler_guard": spoiler_guard,
        }
        return gates.kept, gates, meta

    def build_context(
        self,
        book_id: str,
        question: str,
        *,
        progress_seq: int | None = None,
        spoiler_guard: bool = True,
        budget_chars: int | None = None,
    ) -> tuple[CompiledContext, dict]:
        evidences, gates, meta = self.gather(
            book_id, question, progress_seq=progress_seq, spoiler_guard=spoiler_guard
        )
        coverage = fact_store.coverage_summary(self.db, book_id)
        compiled = compile_context(
            evidences,
            config=CompilerConfig(
                budget_chars=budget_chars or self.settings.context_budget_chars,
                min_chars=self.settings.min_evidence_chars,
            ),
            coverage=coverage,
        )
        meta["coverage"] = coverage
        meta["blocks"] = len(compiled.blocks)
        return compiled, meta
