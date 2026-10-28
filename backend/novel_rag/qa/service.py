"""问答服务（P4 消费平面）。

硬约束：
1. 空上下文**不调 LLM**，直接返回固定文案（避免凭空生成）。
2. 回答必须带引用：模型没给 ``[n]`` 时，服务端强制补一节「依据」。
3. 覆盖度必须回传给前端，让用户知道这次回答有多少底料是未校验的。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db.conn import Database
from ..llm.embedding import EmbeddingProvider
from ..llm.gateway import ECHO_SENTINEL, LlmGateway
from ..retrieve.compiler import citation_payload
from ..retrieve.retriever import Retriever

SYSTEM = (
    "你是小说知识库的问答助手。只能依据给定资料回答，不得编造。"
    "每个结论后面必须标注来源编号，格式为 [1]、[2]。"
    "如果资料不足以回答，直接说明缺少哪部分信息。"
)

PROMPT = """请依据下面的资料回答问题。

【用户问题】
{question}

【资料】（已按原文顺序排列，编号即引用号）
{context}

要求：
1. 用中文回答，简洁准确。
2. 每条结论后用 [编号] 标注依据。
3. 资料标注「未经抽取校验」的部分，若被引用需说明其可信度较低。
"""

NO_CONTEXT_ANSWER = (
    "在当前可检索范围内没有找到与该问题相关的资料。"
    "可能是本书还未完成分析，或问题涉及的内容在当前阅读进度之后（防剧透已开启）。"
)


@dataclass
class QaService:
    db: Database
    gateway: LlmGateway
    embedder: EmbeddingProvider
    settings: Settings = field(default_factory=get_settings)

    def ask(
        self,
        book_id: str,
        question: str,
        *,
        progress_seq: int | None = None,
        spoiler_guard: bool | None = None,
        budget_chars: int | None = None,
    ) -> dict:
        question = (question or "").strip()
        if not question:
            raise ValueError("空问题")

        progress, guard = self._resolve_progress(book_id, progress_seq, spoiler_guard)

        retriever = Retriever(self.db, self.embedder, self.settings)
        compiled, meta = retriever.build_context(
            book_id,
            question,
            progress_seq=progress,
            spoiler_guard=guard,
            budget_chars=budget_chars,
        )

        base = {
            "question": question,
            "citations": citation_payload(compiled),
            "coverage": compiled.coverage,
            "meta": meta,
            "truncated": compiled.truncated,
            "dropped": compiled.dropped,
            "progress_seq": progress,
            "spoiler_guard": guard,
        }

        if not compiled.blocks:
            return {**base, "answer": NO_CONTEXT_ANSWER, "degraded": "no_context"}

        answer = ""
        error = ""
        try:
            answer = self.gateway.complete(
                "query",
                PROMPT.format(question=question, context=compiled.text),
                system=SYSTEM,
                max_tokens=1024,
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)

        if not answer or answer == ECHO_SENTINEL:
            answer = self._extractive_answer(question, compiled)
            degraded = "extractive"
        else:
            degraded = ""

        answer = self._ensure_citations(answer, compiled)
        return {
            **base,
            "answer": answer,
            "degraded": degraded or error,
            "context_preview": compiled.text[:400],
        }

    # ------------------------------------------------------------ 内部

    def _resolve_progress(
        self, book_id: str, progress_seq: int | None, spoiler_guard: bool | None
    ) -> tuple[int | None, bool]:
        row = self.db.query_one("SELECT * FROM reading_progress WHERE book_id=?", (book_id,))
        if progress_seq is None:
            progress_seq = int(row["progress_seq"]) if row else None
        if spoiler_guard is None:
            spoiler_guard = bool(row["spoiler_guard"]) if row else self.settings.spoiler_guard_default
        return progress_seq, spoiler_guard

    def _extractive_answer(self, question: str, compiled) -> str:
        """本地模型不可用时的抽取式兜底：只做引用拼接，绝不生成新内容。"""
        head = compiled.blocks[:3]
        lines = [f"（未能调用本地模型，以下为原文摘录，未做归纳）"]
        for b in head:
            where = f"第{b.chapter_idx + 1}章"
            lines.append(f"- {where}：{b.text[:80]} [{b.citation}]")
        return "\n".join(lines)

    def _ensure_citations(self, answer: str, compiled) -> str:
        if any(f"[{b.citation}]" in answer for b in compiled.blocks):
            return answer
        appendix = [
            "",
            "依据：",
            *[
                f"[{b.citation}] 第{b.chapter_idx + 1}章 · {b.text[:60]}"
                for b in compiled.blocks[:5]
            ],
        ]
        return answer + "\n".join(appendix)


def ensure_citation_text(answer: str, compiled) -> bool:
    """给测试与审计用：回答是否带引用。"""
    return any(f"[{b.citation}]" in answer for b in compiled.blocks)
