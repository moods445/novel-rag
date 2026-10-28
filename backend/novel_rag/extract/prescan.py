"""预扫描（兼容层）：保留原有 API，实现已迁到 ``ner``。

历史上这里是纯正则，现在由 ``ner.build_ner`` 决定后端：
jieba 词性标注（CPU 小模型）∪ 正则，并附带「姓X名Y字Z」别名对。
"""

from __future__ import annotations

from .ner import NerResult, build_ner, default_ner, scan_alias_pairs

__all__ = [
    "NerResult",
    "build_ner",
    "default_ner",
    "dictionary_from",
    "scan_alias_pairs",
    "scan_names",
    "scan_with_aliases",
]


def scan_names(text: str, *, min_count: int = 2, top: int = 40) -> list[tuple[str, int]]:
    """返回按出现次数排序的候选人名。"""
    return default_ner().scan(text, min_count=min_count, top=top).names


def dictionary_from(chapters_text: str, *, min_count: int = 2, top: int = 60) -> list[str]:
    """候选名单。语料太小（短篇/试读章）时降级为「出现即收」，否则会返回空表。"""
    result = default_ner().scan(chapters_text, min_count=min_count, top=top)
    if not result.names:
        result = default_ner().scan(chapters_text, min_count=1, top=top)
    return result.dictionary(top)


def scan_with_aliases(
    chapters_text: str, *, min_count: int = 2, top: int = 60
) -> NerResult:
    """一次拿到候选名单 + 别名对（流水线用这个）。"""
    return default_ner().scan(chapters_text, min_count=min_count, top=top)
