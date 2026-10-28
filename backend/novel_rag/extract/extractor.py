"""逐章事实抽取（决策 #9：每章一份 ChapterFact）。

provider 返回哨兵或无法解析的文本时，退回**启发式抽取**并标记 ``source=heuristic``。
这条降级路径是刻意保留的：本地未配置云端 key 时整条流水线仍可跑通，
且 ``source`` 字段让下游能区分证据质量。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..domain.models import Chapter, ChapterFact, CharacterFact, EventFact, Paragraph
from ..llm.gateway import ECHO_SENTINEL, LlmGateway

SYSTEM = (
    "你是严谨的小说结构化抽取器。只输出一个 JSON 对象，不要输出解释或 markdown 代码块。"
    "所有事实必须能在给定原文中找到依据，evidence.text 必须是原文的连续片段。"
)

TEMPLATE = """请从下面这一章小说中抽取结构化事实。

输出 JSON schema：
{{
  "summary": "本章 60 字以内剧情摘要",
  "characters": [
    {{"name":"人物名","aliases":["本章出现的别名"],"kind":"person","description":"一句话",
      "evidence":{{"text":"原文连续片段"}}}}
  ],
  "relations": [
    {{"person_a":"A","person_b":"B","rel_type":"关系类型","polarity":0.0,
      "evidence":{{"text":"原文连续片段"}}}}
  ],
  "events": [
    {{"summary":"事件一句话","actors":["A","B"],"kind":"plot",
      "evidence":{{"text":"原文连续片段"}}}}
  ],
  "concepts": ["新出现的重要设定或名词"]
}}

要求：
1. evidence.text 必须逐字摘自原文，长度 10-60 字，用于回查定位。
2. 人物名使用本章最常用的称呼；别名只填本章确实出现过的。
3. 没有把握的条目宁可不填。

{context}

【第{chapter_no}章 {title}】
{body}
"""

MAX_CHAPTER_CHARS = 12000

# 单次调用的原文长度。超过就分段抽取再合并。
# 实测（magpie / conatus-free）：6000 字整章一次性抽取必然把 4096 token 全烧在
# 推理上、content 返回空；1800 字 + 3072 token 则 3/3 段全部成功。
SEGMENT_CHARS = 1800
SEGMENT_MAX_TOKENS = 3072


@dataclass(slots=True)
class ExtractionOutcome:
    fact: ChapterFact
    raw: str = ""
    error: str = ""
    segments: int = 1


class ChapterFactExtractor:
    def __init__(
        self,
        gateway: LlmGateway,
        *,
        max_attempts: int = 2,
        segment_chars: int = SEGMENT_CHARS,
        max_tokens: int = SEGMENT_MAX_TOKENS,
    ) -> None:
        self.gateway = gateway
        self.max_attempts = max(1, max_attempts)
        self.segment_chars = max(500, segment_chars)
        self.max_tokens = max_tokens

    def build_prompt(self, chapter: Chapter, context: str, *, body: str | None = None, part: tuple[int, int] | None = None) -> str:
        text = (body if body is not None else chapter.body)[:MAX_CHAPTER_CHARS]
        label = f"【第{chapter.idx + 1}章 {chapter.title}】"
        if part and part[1] > 1:
            label += f"（第 {part[0]}/{part[1]} 段，只从本段抽事实）"
        return TEMPLATE.format(context=context, chapter_no=chapter.idx + 1, title=chapter.title, body=text).replace(
            f"【第{chapter.idx + 1}章 {chapter.title}】", label
        )

    def extract(
        self,
        chapter: Chapter,
        *,
        context: str = "",
        paragraphs: list[Paragraph] | None = None,
        dictionary: list[str] | None = None,
        alias_map: dict[str, list[str]] | None = None,
    ) -> ExtractionOutcome:
        segments = split_segments(chapter.body, self.segment_chars)
        total = len(segments)
        facts: list[ChapterFact] = []
        errors: list[str] = []
        raws: list[str] = []

        for index, text in enumerate(segments, start=1):
            prompt = self.build_prompt(chapter, context, body=text, part=(index, total))
            fact, raw, error = self._extract_one(prompt, chapter)
            raws.append(raw)
            if error:
                errors.append(error)
                continue
            if fact is not None:
                facts.append(fact)

        if not facts:
            fact = heuristic_fact(chapter, paragraphs or [], dictionary or [], alias_map)
            return ExtractionOutcome(
                fact=fact, raw="\n---\n".join(raws)[:4000], error=errors[0] if errors else "parse_error"
            )

        merged = merge_facts(facts, chapter_idx=chapter.idx)
        return ExtractionOutcome(
            fact=merged,
            raw="\n---\n".join(raws)[:4000],
            error=";".join(errors)[:200],
            segments=total,
        )

    def _extract_one(
        self, prompt: str, chapter: Chapter
    ) -> tuple[ChapterFact | None, str, str]:
        """单段抽取。返回 ``(fact | None, raw, error)``。"""
        raw = ""
        error = ""

        # 超时与 5xx 值得重试一次；内容审核类错误重试也没用
        for attempt in range(self.max_attempts):
            try:
                raw = self.gateway.complete(
                    "extract", prompt, system=SYSTEM, max_tokens=self.max_tokens
                )
                error = ""
                break
            except Exception as exc:  # noqa: BLE001 - 单段失败不应中断整章
                error = str(exc)
                kind = getattr(exc, "kind", "http_error")
                if kind not in {"timeout", "http_error"} or attempt + 1 >= self.max_attempts:
                    break

        if error:
            return None, raw, error
        if raw == ECHO_SENTINEL:
            return None, raw, "echo"

        payload = parse_json_object(raw)

        # 解析失败再试一次：模型偶发返回 prose 或截断，重试成本远低于丢掉这一段
        if payload is None and self.max_attempts > 1:
            retry_prompt = prompt + "\n\n上一次输出不是合法 JSON。只输出一个 JSON 对象，不要任何说明文字。"
            try:
                raw = self.gateway.complete(
                    "extract", retry_prompt, system=SYSTEM, max_tokens=self.max_tokens
                )
                payload = parse_json_object(raw)
            except Exception as exc:  # noqa: BLE001
                return None, raw, str(exc)

        if payload is None:
            return None, raw, "parse_error"

        return ChapterFact.from_dict(payload, chapter_idx=chapter.idx, source="llm"), raw, ""


def split_segments(body: str, limit: int) -> list[str]:
    """按句子边界切段，保证每段不超过 ``limit`` 字符。"""
    if len(body) <= limit:
        return [body]
    segments: list[str] = []
    buffer: list[str] = []
    size = 0
    for line in body.split("\n"):
        if size + len(line) + 1 > limit and buffer:
            segments.append("\n".join(buffer))
            buffer, size = [], 0
        if len(line) > limit:  # 超长单行硬切
            for i in range(0, len(line), limit):
                segments.append(line[i : i + limit])
            continue
        buffer.append(line)
        size += len(line) + 1
    if buffer:
        segments.append("\n".join(buffer))
    return [s for s in segments if s.strip()] or [body]


def merge_facts(facts: list[ChapterFact], *, chapter_idx: int) -> ChapterFact:
    """把分段结果合并成一份 ChapterFact（人物/关系/事件/概念去重）。"""
    merged = ChapterFact(chapter_idx=chapter_idx, source="llm")
    summaries: list[str] = []
    characters: dict[str, CharacterFact] = {}
    relations: set[tuple[str, str, str]] = set()
    events: set[str] = set()
    concepts: set[str] = set()

    for fact in facts:
        if fact.summary and fact.summary not in summaries:
            summaries.append(fact.summary)
        for ch in fact.characters:
            if not ch.name:
                continue
            current = characters.get(ch.name)
            if current is None:
                characters[ch.name] = ch
            else:
                for alias in ch.aliases:
                    if alias not in current.aliases:
                        current.aliases.append(alias)
                if not current.evidence and ch.evidence:
                    current.evidence = ch.evidence
        for rel in fact.relations:
            key = (rel.person_a, rel.rel_type, rel.person_b)
            if key in relations:
                continue
            relations.add(key)
            merged.relations.append(rel)
        for ev in fact.events:
            if ev.summary and ev.summary not in events:
                events.add(ev.summary)
                merged.events.append(ev)
        for concept in fact.concepts:
            if concept not in concepts:
                concepts.add(concept)
                merged.concepts.append(concept)

    merged.characters = list(characters.values())
    merged.summary = "；".join(summaries)[:120]
    return merged


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(raw: str) -> dict | None:
    """尽最大努力解析模型输出。

    模型常见的三种坏习惯都在这处理：markdown 代码块、前后夹带说明文字、
    输出被 max_tokens 截断。最后一种靠「补括号」修复，比直接丢弃整章划算得多。
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    for candidate in (text, _slice_braces(text)):
        if not candidate:
            continue
        for attempt in (candidate, _repair_truncated(candidate)):
            if not attempt:
                continue
            try:
                data = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return None


def _slice_braces(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return text[text.find("{") :] if start >= 0 else ""
    return text[start : end + 1]


def _repair_truncated(text: str) -> str:
    """截断的 JSON：丢掉最后一个不完整字段，补齐未闭合的 } / ]。"""
    if not text.startswith("{"):
        return ""
    depth = 0
    stack: list[str] = []
    in_string = False
    escaped = False
    last_safe = -1
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
            depth += 1
        elif ch in "}]":
            if stack:
                stack.pop()
            depth -= 1
        elif ch == "," and depth >= 1:
            last_safe = i
        if depth == 0 and i > 0:
            return ""  # 已经闭合，不需要修复

    if not stack:
        return ""
    body = text[: last_safe + 1] if last_safe > 0 else text[: text.rfind(",") + 1]
    if not body:
        return ""
    body = body.rstrip().rstrip(",")
    return body + "".join(reversed(stack))


_SENTENCE_END = re.compile(r"[。！？!?]")


def heuristic_fact(
    chapter: Chapter,
    paragraphs: list[Paragraph],
    dictionary: list[str],
    alias_map: dict[str, list[str]] | None = None,
) -> ChapterFact:
    """离线兜底：只用 NER 词典 + 别名表，产出一份最小可用事实。

    明确不伪造关系——宁可少，不可错（决策 #21）。
    """
    body = chapter.body
    alias_map = alias_map or {}
    characters: list[CharacterFact] = []
    seen: set[str] = set()

    # 正名优先：先处理带别名的正名，确保实体以正式名字建立
    for canonical in alias_map:
        if canonical in seen or canonical not in body:
            continue
        aliases = [a for a in alias_map.get(canonical, []) if a in body]
        _append_character(body, characters, seen, canonical, aliases)

    for name in dictionary:
        if name in seen or name not in body:
            continue
        aliases = [a for a in alias_map.get(name, []) if a in body]
        _append_character(body, characters, seen, name, aliases)

    summary = _summarize(body)
    first_para = paragraphs[0] if paragraphs else None
    events: list[EventFact] = []
    if summary and first_para is not None:
        events.append(
            EventFact(
                summary=f"{chapter.title}：{summary}",
                actors=[c.name for c in characters[:3]],
                evidence={"text": first_para.text[:40]},
            )
        )

    return ChapterFact(
        chapter_idx=chapter.idx,
        summary=summary,
        characters=characters,
        events=events,
        source="heuristic",
    )


def _append_character(
    body: str,
    characters: list[CharacterFact],
    seen: set[str],
    name: str,
    aliases: list[str],
) -> None:
    seen.add(name)
    for alias in aliases:
        seen.add(alias)
    evidence = _first_snippet(body, name)
    characters.append(
        CharacterFact(
            name=name,
            aliases=[a for a in aliases if a != name],
            kind="person",
            evidence={"text": evidence} if evidence else None,
        )
    )


def _first_snippet(body: str, needle: str, width: int = 30) -> str:
    pos = body.find(needle)
    if pos < 0:
        return ""
    start = max(0, pos - 5)
    return body[start : start + width].strip()


def _summarize(body: str, limit: int = 60) -> str:
    flat = re.sub(r"\s+", "", body)
    if not flat:
        return ""
    m = _SENTENCE_END.search(flat, 20)
    if m and m.end() <= limit + 20:
        return flat[: m.end()]
    return flat[:limit]
