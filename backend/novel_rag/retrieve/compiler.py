"""★ ContextCompiler —— 全系统唯一给 LLM 供料的出口（决策 #13）。

为什么排序与裁剪必须在这里：
    如果先裁剪再排序，裁剪会按「分数」掐掉中间章节，上下文就变成「跳着讲」；
    只有在同一模块里同时掌握「顺序」与「预算」，才能保证输出是一段
    时间上连续的叙述。

裁剪粒度是 **章节组**，不是单条证据：宁可少给一整章，也不给半章。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import CompiledContext, ContextBlock, Evidence

KIND_TAG = {
    "raw": "原文",
    "fact": "事实",
    "entity": "人物档案",
    "relation": "关系",
    "event": "事件",
}


@dataclass(slots=True)
class CompilerConfig:
    budget_chars: int = 12000
    min_chars: int = 12
    per_block_chars: int = 600
    max_blocks_per_chapter: int = 4


def compile_context(
    evidences: list[Evidence],
    *,
    config: CompilerConfig | None = None,
    coverage: dict | None = None,
) -> CompiledContext:
    cfg = config or CompilerConfig()
    # min_chars 只用于过滤过短的原文片段；结构化事实本身是原子陈述，
    # 即使只有两三个字（如人名档案）也必须保留。
    candidates = [
        e
        for e in evidences
        if e.text.strip() and (e.kind != "raw" or len(e.text.strip()) >= cfg.min_chars)
    ]

    # 1) 按章节分组；组内按 seq 升序，组间按「是否已校验 + 最高分」排序
    groups: dict[int, list[Evidence]] = {}
    for ev in candidates:
        groups.setdefault(ev.chapter_idx, []).append(ev)

    ranked_groups = sorted(
        groups.items(),
        key=lambda kv: (
            all(e.coverage != "extracted" for e in kv[1]),
            -max(e.score for e in kv[1]),
        ),
    )

    # 2) 以「章节组」为最小单位装预算
    selected: list[Evidence] = []
    used = 0
    dropped = 0
    for _chapter, items in ranked_groups:
        items.sort(key=lambda e: e.seq)
        trimmed = items[: cfg.max_blocks_per_chapter]
        dropped += len(items) - len(trimmed)
        group_chars = sum(min(len(e.text), cfg.per_block_chars) for e in trimmed)
        if selected and used + group_chars > cfg.budget_chars:
            dropped += len(trimmed)
            continue
        if not selected and group_chars > cfg.budget_chars:
            trimmed = trimmed[:1]
            group_chars = min(len(trimmed[0].text), cfg.per_block_chars) if trimmed else 0
        selected.extend(trimmed)
        used += group_chars

    # 3) 全局按 seq 重排 —— 这一步是时序还原的落地点
    selected.sort(key=lambda e: (e.seq, e.chapter_idx))

    # 4) 去重（同一 cfi 只保留一次）
    seen: set[tuple[str, str, int]] = set()
    blocks: list[ContextBlock] = []
    for ev in selected:
        marker = (ev.kind, ev.cfi, ev.seq)
        if marker in seen:
            dropped += 1
            continue
        seen.add(marker)
        text = ev.text.strip()
        if len(text) > cfg.per_block_chars:
            text = text[: cfg.per_block_chars] + "…"
        blocks.append(
            ContextBlock(
                seq=ev.seq,
                cfi=ev.cfi,
                chapter_idx=ev.chapter_idx,
                text=text,
                citation=len(blocks) + 1,
                source=ev.source,
                coverage=ev.coverage,
                kind=ev.kind,
                label=ev.label or KIND_TAG.get(ev.kind, ev.kind),
            )
        )

    rendered = _render(blocks)
    return CompiledContext(
        text=rendered,
        blocks=blocks,
        dropped=dropped,
        truncated=dropped > 0,
        coverage=coverage or {},
    )


def _render(blocks: list[ContextBlock]) -> str:
    lines: list[str] = []
    for b in blocks:
        where = f"第{b.chapter_idx + 1}章" if b.chapter_idx or b.kind == "raw" else "全书"
        trust = "" if b.coverage == "extracted" else "（未经抽取校验）"
        lines.append(f"[{b.citation}] {where} · {b.label}{trust}\n{b.text}")
    return "\n\n".join(lines)


def citation_payload(compiled: CompiledContext) -> list[dict]:
    """给前端的引用列表：``[n] → seq → cfi``。"""
    return [
        {
            "n": b.citation,
            "seq": b.seq,
            "cfi": b.cfi,
            "chapter_no": b.chapter_idx + 1 if b.kind == "raw" or b.chapter_idx else None,
            "label": b.label,
            "source": b.source,
            "coverage": b.coverage,
            "excerpt": b.text[:120],
        }
        for b in compiled.blocks
    ]
