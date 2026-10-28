"""章节识别（决策 #16：规则优先 + 置信度 + LLM 兜底 + 人工校正）。

规则库可扩展：新增一种排版格式只需在 ``RULES`` 里加一条。

设计约束：
- 标题行必须是「整行」匹配（``^`` + ``MULTILINE``），否则正文里出现「第三章」
  会被误判成标题。
- 标题行长度上限 ``MAX_TITLE_LEN``，进一步降低误判。
- 单条规则只命中一次不采信（噪声），至少 2 次。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..domain.models import Chapter

MAX_TITLE_LEN = 60
MIN_CHAPTER_CHARS = 20


@dataclass(slots=True, frozen=True)
class ChapterRule:
    name: str
    pattern: re.Pattern[str]
    weight: float


def _rule(name: str, pattern: str, weight: float) -> ChapterRule:
    return ChapterRule(name, re.compile(pattern, re.MULTILINE), weight)


CN_NUM = r"[一二三四五六七八九十百千零〇两0-9０-９]+"

RULES: tuple[ChapterRule, ...] = (
    _rule("cn-chapter", rf"^[ \t　]*第\s*{CN_NUM}\s*[章回节][ \t　]*.{{0,40}}$", 1.0),
    _rule("cn-volume", rf"^[ \t　]*第\s*{CN_NUM}\s*[卷部篇][ \t　]*.{{0,40}}$", 0.95),
    _rule("en-chapter", r"^[ \t]*chapter\s+[0-9ivxlcdm]+\b.{0,40}$", 1.0),
    _rule(
        "special",
        r"^[ \t　]*(?:楔子|序章|序言|引子|终章|尾声|后记|大结局|番外[一二三四五六七八九十0-9]*)[ \t　]*$",
        0.95,
    ),
    _rule("numeric", r"^[ \t　]*[0-9]{1,4}[、.．][ \t　]*\S.{0,40}$", 0.7),
)


@dataclass(slots=True)
class SplitResult:
    chapters: list[Chapter]
    rule: str
    confidence: float
    needs_llm: bool


def chapter_fingerprint(body: str) -> str:
    """章节指纹：内容变了就变，用于检测重分章导致的 CFI 失效（风险 #2）。"""
    normalized = re.sub(r"\s+", "", body)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _collect(text: str, rule: ChapterRule) -> list[tuple[int, int, str]]:
    hits: list[tuple[int, int, str]] = []
    for m in rule.pattern.finditer(text):
        line = m.group(0).strip()
        if not line or len(line) > MAX_TITLE_LEN:
            continue
        hits.append((m.start(), m.end(), line))
    return hits


def _confidence(count: int, weight: float, rule: str) -> float:
    base = weight if count >= 2 else weight * 0.55
    if rule == "special":
        base = weight
    if count >= 6:
        base = min(1.0, base + 0.05)
    return round(base, 3)


def split_chapters(
    text: str,
    *,
    min_confidence: float = 0.5,
    fallback_chunk: int = 4000,
) -> SplitResult:
    """返回切分结果。识别不出可信章节时退化为定长分节。"""
    best: tuple[float, int, ChapterRule, list[tuple[int, int, str]]] | None = None
    for rule in RULES:
        hits = _collect(text, rule)
        if not hits:
            continue
        score = _confidence(len(hits), rule.weight, rule.name)
        if best is None or score > best[0]:
            best = (score, len(hits), rule, hits)

    if best is not None and best[0] >= min_confidence:
        score, count, rule, hits = best
        chapters = _build_from_hits(text, hits, rule.name, score)
        if chapters:
            return SplitResult(chapters, rule.name, score, needs_llm=False)

    return SplitResult(_fallback_split(text, fallback_chunk), "fallback", 0.3, needs_llm=True)


def build_chapters_from_hits(
    text: str,
    hits: list[tuple[int, int, str]],
    rule_name: str,
    confidence: float,
) -> list[Chapter]:
    """从标题命中列表构造章节。规则路径与 LLM 路径共用这一个构造器。"""
    return _build_from_hits(text, hits, rule_name, confidence)


def _build_from_hits(
    text: str,
    hits: list[tuple[int, int, str]],
    rule_name: str,
    confidence: float,
) -> list[Chapter]:
    chapters: list[Chapter] = []

    # 前言：第一个标题之前若有大段正文，单独成章
    prefix_end = hits[0][0]
    preamble = text[:prefix_end]
    if len(preamble.strip()) >= MIN_CHAPTER_CHARS * 4:
        body = preamble.strip("\n")
        if body:
            offset = text.index(body) if body in text else 0
            chapters.append(
                Chapter(
                    idx=0,
                    title="开篇",
                    body=body,
                    char_start=offset,
                    char_end=offset + len(body),
                    fp=chapter_fingerprint(body),
                    split_source="preamble",
                    confidence=confidence,
                )
            )

    for i, (start, end, title) in enumerate(hits):
        body_start = end
        if body_start < len(text) and text[body_start] == "\n":
            body_start += 1
        body_end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        body = text[body_start:body_end].strip("\n")
        if len(body) < MIN_CHAPTER_CHARS:
            continue
        chapters.append(
            Chapter(
                idx=len(chapters),
                title=title,
                body=body,
                char_start=body_start,
                char_end=body_start + len(body),
                fp=chapter_fingerprint(body),
                split_source=f"rule:{rule_name}",
                confidence=confidence,
            )
        )

    for spine, ch in enumerate(chapters):
        ch.spine_index = spine
        ch.nav_href = f"ch{spine + 1:04d}.xhtml"

    return chapters


def _fallback_split(text: str, chunk: int) -> list[Chapter]:
    chapters: list[Chapter] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk)
        if end < n:
            newline = text.rfind("\n", start, end)
            if newline > start + chunk // 2:
                end = newline
        body = text[start:end].strip("\n")
        if body:
            chapters.append(
                Chapter(
                    idx=len(chapters),
                    title=f"第{len(chapters) + 1}节",
                    body=body,
                    char_start=start,
                    char_end=start + len(body),
                    fp=chapter_fingerprint(body),
                    split_source="fallback",
                    confidence=0.3,
                )
            )
        start = end
    for spine, ch in enumerate(chapters):
        ch.spine_index = spine
        ch.nav_href = f"ch{spine + 1:04d}.xhtml"
    return chapters


def detect_language_hint(text: str) -> str:
    sample = text[:5000]
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return "zh" if cjk > len(sample) * 0.2 else "other"
