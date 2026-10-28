"""融合器：三路检索结果去重合并。

同一段原文可能被词法与语义同时命中，按 ``kind + cfi + text`` 去重，
保留较高分数，并记录命中来源数量作为置信微调。
"""

from __future__ import annotations

from ..domain.models import Evidence


def merge(groups: list[list[Evidence]]) -> list[Evidence]:
    best: dict[tuple, Evidence] = {}
    hits: dict[tuple, int] = {}
    for group in groups:
        for ev in group:
            key = ev.key()
            hits[key] = hits.get(key, 0) + 1
            current = best.get(key)
            if current is None or ev.score > current.score:
                best[key] = ev

    merged: list[Evidence] = []
    for key, ev in best.items():
        if hits[key] > 1:
            ev.score = min(1.0, ev.score + 0.05 * (hits[key] - 1))
        merged.append(ev)
    merged.sort(key=lambda e: e.score, reverse=True)
    return merged
