"""双闸门（决策 #6 / #21 / #23）。

闸门一 · 剧透：``progress_seq`` 之后的证据一律丢弃。
闸门二 · 覆盖度：按「该章是否已抽取」给证据打标并排序，
    已抽章的证据优先，未抽章的原文证据保留但标注「未经抽取校验」。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import Evidence


@dataclass(slots=True)
class GateResult:
    kept: list[Evidence] = field(default_factory=list)
    dropped_spoiler: int = 0
    extracted_chapters: set[int] = field(default_factory=set)

    def counts(self) -> dict[str, int]:
        extracted = sum(1 for e in self.kept if e.coverage == "extracted")
        return {
            "kept": len(self.kept),
            "extracted": extracted,
            "unparsed": len(self.kept) - extracted,
            "dropped_spoiler": self.dropped_spoiler,
        }


def apply(
    evidences: list[Evidence],
    *,
    progress_seq: int | None,
    spoiler_guard: bool,
    extracted_chapters: set[int],
) -> GateResult:
    result = GateResult(extracted_chapters=set(extracted_chapters))

    for ev in evidences:
        if spoiler_guard and progress_seq is not None and ev.seq > progress_seq and ev.seq > 0:
            result.dropped_spoiler += 1
            continue

        if ev.kind in {"entity", "relation", "event"}:
            ev.source = "fact"
            ev.coverage = "extracted"
        else:
            ev.source = "raw"
            ev.coverage = "extracted" if ev.chapter_idx in extracted_chapters else "unparsed"
        result.kept.append(ev)

    # 已校验的排在前面（同级内仍按分数）
    result.kept.sort(key=lambda e: (e.coverage != "extracted", -e.score, e.seq))
    return result
