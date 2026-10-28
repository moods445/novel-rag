"""把各检索器的原始结果统一成 ``Evidence``（架构第三节第 3 条接缝）。

所有检索器必须产出同一个结构，中间不允许出现裸字符串。
"""

from __future__ import annotations

from ..domain.models import Evidence


def from_paragraph_rows(rows: list[dict], *, kind: str = "raw", score_key: str = "score") -> list[Evidence]:
    out: list[Evidence] = []
    for r in rows:
        score = float(r.get(score_key) or 0.0)
        if score_key == "rank":
            score = -score  # bm25 越小越好
        out.append(
            Evidence(
                seq=int(r["seq"]),
                cfi=str(r.get("cfi") or ""),
                text=str(r.get("text") or ""),
                chapter_idx=int(r.get("chapter_idx") or 0),
                source="raw",
                coverage="unparsed",
                kind=kind,
                score=score,
            )
        )
    return out
