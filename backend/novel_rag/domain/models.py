"""核心数据契约（架构第四节）。

设计要点：
- ``Evidence`` 是唯一的证据单元，检索/闸门/编译/引用渲染共用。
- ``Paragraph.seq`` 是业务坐标系，``Paragraph.cfi`` 是渲染坐标系（双轴，决策 #8）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _s(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


@dataclass(slots=True)
class Book:
    id: str
    title: str
    author: str
    source_name: str
    source_sha256: str
    chapter_fp: str
    created_at: str = ""


@dataclass(slots=True)
class Chapter:
    idx: int
    title: str
    body: str
    char_start: int
    char_end: int
    fp: str
    spine_index: int = 0
    nav_href: str = ""
    split_source: str = "rule"
    confidence: float = 1.0

    @property
    def text_length(self) -> int:
        return len(self.body)


@dataclass(slots=True)
class Paragraph:
    seq: int
    chapter_idx: int
    para_idx: int
    char_off: int
    cfi: str
    text: str


@dataclass(slots=True)
class Evidence:
    """统一证据单元。``source`` 区分事实层与原文层（决策 #23）。"""

    seq: int
    cfi: str
    text: str
    chapter_idx: int = 0
    source: str = "raw"  # fact | raw
    coverage: str = "unparsed"  # extracted | unparsed
    kind: str = "raw"  # raw | fact | entity | relation | event
    score: float = 0.0
    label: str = ""

    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.cfi, self.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "cfi": self.cfi,
            "text": self.text,
            "chapter_idx": self.chapter_idx,
            "source": self.source,
            "coverage": self.coverage,
            "kind": self.kind,
            "score": round(self.score, 4),
            "label": self.label,
        }


@dataclass(slots=True)
class CharacterFact:
    name: str
    aliases: list[str] = field(default_factory=list)
    kind: str = "person"
    evidence: dict[str, Any] | None = None
    description: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CharacterFact":
        return cls(
            name=_s(raw.get("name")),
            aliases=[_s(a) for a in (raw.get("aliases") or []) if _s(a)],
            kind=_s(raw.get("kind"), "person") or "person",
            evidence=raw.get("evidence") if isinstance(raw.get("evidence"), dict) else None,
            description=_s(raw.get("description")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "kind": self.kind,
            "evidence": self.evidence,
            "description": self.description,
        }


@dataclass(slots=True)
class RelationFact:
    person_a: str
    person_b: str
    rel_type: str
    evidence: dict[str, Any] | None = None
    polarity: float = 0.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RelationFact":
        try:
            polarity = float(raw.get("polarity") or 0.0)
        except (TypeError, ValueError):
            polarity = 0.0
        return cls(
            person_a=_s(raw.get("person_a")),
            person_b=_s(raw.get("person_b")),
            rel_type=_s(raw.get("rel_type")),
            evidence=raw.get("evidence") if isinstance(raw.get("evidence"), dict) else None,
            polarity=polarity,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_a": self.person_a,
            "person_b": self.person_b,
            "rel_type": self.rel_type,
            "polarity": self.polarity,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class EventFact:
    summary: str
    actors: list[str] = field(default_factory=list)
    kind: str = "plot"
    evidence: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EventFact":
        return cls(
            summary=_s(raw.get("summary")),
            actors=[_s(a) for a in (raw.get("actors") or []) if _s(a)],
            kind=_s(raw.get("kind"), "plot") or "plot",
            evidence=raw.get("evidence") if isinstance(raw.get("evidence"), dict) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "actors": list(self.actors),
            "kind": self.kind,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class ChapterFact:
    """每章一份事实（决策 #9）。这是知识平面的地基契约。"""

    chapter_idx: int
    summary: str = ""
    characters: list[CharacterFact] = field(default_factory=list)
    relations: list[RelationFact] = field(default_factory=list)
    events: list[EventFact] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    source: str = "llm"  # llm | heuristic
    rejected: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, chapter_idx: int, source: str = "llm") -> "ChapterFact":
        return cls(
            chapter_idx=chapter_idx,
            summary=_s(raw.get("summary")),
            characters=[CharacterFact.from_dict(x) for x in (raw.get("characters") or []) if isinstance(x, dict)],
            relations=[RelationFact.from_dict(x) for x in (raw.get("relations") or []) if isinstance(x, dict)],
            events=[EventFact.from_dict(x) for x in (raw.get("events") or []) if isinstance(x, dict)],
            concepts=[_s(c) for c in (raw.get("concepts") or []) if _s(c)],
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_idx": self.chapter_idx,
            "summary": self.summary,
            "characters": [c.to_dict() for c in self.characters],
            "relations": [r.to_dict() for r in self.relations],
            "events": [e.to_dict() for e in self.events],
            "concepts": list(self.concepts),
            "source": self.source,
            "rejected": self.rejected,
        }


@dataclass(slots=True)
class TemporalEdge:
    """区间边（决策 #19）。``valid_to_seq`` 延迟确定，故表必须 append-only + version。"""

    a_entity: str
    b_entity: str
    rel_type: str
    valid_from_seq: int
    valid_to_seq: int | None = None
    version: int = 1
    evidence_seq: int | None = None
    evidence_cfi: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "a_entity": self.a_entity,
            "b_entity": self.b_entity,
            "rel_type": self.rel_type,
            "valid_from_seq": self.valid_from_seq,
            "valid_to_seq": self.valid_to_seq,
            "version": self.version,
            "evidence_seq": self.evidence_seq,
            "evidence_cfi": self.evidence_cfi,
        }


@dataclass(slots=True)
class ContextBlock:
    """编译后的一块上下文：一段连续原文或一条事实陈述。"""

    seq: int
    cfi: str
    chapter_idx: int
    text: str
    citation: int
    source: str = "raw"
    coverage: str = "unparsed"
    kind: str = "raw"
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "cfi": self.cfi,
            "chapter_idx": self.chapter_idx,
            "text": self.text,
            "citation": self.citation,
            "source": self.source,
            "coverage": self.coverage,
            "kind": self.kind,
            "label": self.label,
        }


@dataclass(slots=True)
class CompiledContext:
    """ContextCompiler 的输出。全系统唯一的 LLM 供料格式。"""

    text: str
    blocks: list[ContextBlock] = field(default_factory=list)
    dropped: int = 0
    truncated: bool = False
    coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "blocks": [b.to_dict() for b in self.blocks],
            "dropped": self.dropped,
            "truncated": self.truncated,
            "coverage": self.coverage,
        }
