"""LLM 兜底章节识别（决策 #16 的后半句）。

只在规则库置信度不足时启用：

1. 先筛出「短行」候选（标题一定是短行，正文段落通常不是），把候选清单
   连同行号发给模型；
2. 模型返回标题行号 → 用与规则完全相同的构造器生成章节，
   因此两条路径产出的 ``Chapter`` 结构没有差异；
3. 结果落库时 ``split_source='llm'``，置信度记 0.8，仍可人工校正。

这样做的代价是一次额外调用，收益是脏排版文件不会退化成定长切块。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..domain.models import Chapter
from ..llm.gateway import LlmGateway
from .splitter import build_chapters_from_hits, chapter_fingerprint

SPLIT_SYSTEM = (
    "你是小说排版分析器。只输出一个 JSON 对象，不要解释，不要 markdown 代码块。"
)

TEMPLATE = """下面是一本小说中所有「短行」的行号与内容（行号从 1 开始，可能有省略）。

请找出其中真正属于**章节标题**的行号，输出 JSON：
{{"title_lines": [3, 88, 143], "pattern": "可选的正则表达式"}}

判定标准：
1. 标题通常包含「第X章 / 第X回 / 第X卷 / 第X节」，或整行是「楔子 / 序章 / 引子 / 尾声 / 番外」；
2. 标题行一般不超过 30 个字，且不以句号、引号结尾；
3. 正文里的短句（对话、单句描写）**不是**标题，不要选；
4. 至少要给出 2 个行号，且必须严格递增。

行清单：
{lines}
"""

MAX_CANDIDATES = 300
CANDIDATE_MAX_CHARS = 40


@dataclass(slots=True)
class SplitProposal:
    line_numbers: list[int]
    pattern: str
    raw: str


def _line_index(text: str) -> list[tuple[int, int, str]]:
    """返回 ``[(start, end, line), ...]``，索引 0 = 第 1 行。"""
    out: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.split("\n"):
        out.append((offset, offset + len(line), line))
        offset += len(line) + 1
    return out


def _candidates(lines: list[tuple[int, int, str]]) -> list[tuple[int, str]]:
    picked: list[tuple[int, str]] = []
    for idx, (_start, _end, line) in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or len(stripped) > CANDIDATE_MAX_CHARS:
            continue
        picked.append((idx, stripped))
        if len(picked) >= MAX_CANDIDATES:
            break
    return picked


def propose_split(gateway: LlmGateway | None, text: str, *, purpose: str = "extract") -> SplitProposal | None:
    if gateway is None:
        return None
    lines = _line_index(text)
    candidates = _candidates(lines)
    if len(candidates) < 2:
        return None

    listing = "\n".join(f"{idx}: {line}" for idx, line in candidates)
    prompt = TEMPLATE.format(lines=listing)
    try:
        raw = gateway.complete(purpose, prompt, system=SPLIT_SYSTEM, max_tokens=1024)
    except Exception:  # noqa: BLE001 - 兜底失败就继续用规则结果
        return None

    payload = _parse(raw)
    if payload is None:
        return None
    numbers = [int(n) for n in payload.get("title_lines") or [] if isinstance(n, (int, float))]
    numbers = sorted({n for n in numbers if 1 <= n <= len(lines)})
    if len(numbers) < 2:
        return None
    return SplitProposal(line_numbers=numbers, pattern=str(payload.get("pattern") or ""), raw=raw)


def build_chapters(text: str, proposal: SplitProposal, *, confidence: float = 0.8) -> list[Chapter]:
    lines = _line_index(text)
    hits: list[tuple[int, int, str]] = []
    for number in proposal.line_numbers:
        start, end, line = lines[number - 1]
        title = line.strip()
        if title:
            hits.append((start, end, title))
    return build_chapters_from_hits(text, hits, "llm", confidence)


def _parse(raw: str) -> dict | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.lstrip("json").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def verify_no_duplicate_fp(chapters: list[Chapter]) -> bool:
    fps = [chapter_fingerprint(ch.body) for ch in chapters]
    return len(fps) == len(set(fps))
