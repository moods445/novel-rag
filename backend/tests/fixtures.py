"""测试夹具：一本三章的样例小说 + 可按章节应答的 provider。"""

from __future__ import annotations

import json
import re

SAMPLE_TEXT = """第一章 初入山门

林凡站在山门前，抬头望向云雾深处的石阶。
"你就是新来的弟子？"苏清月问道。
林凡点头，眼中闪过一丝坚定。

第二章 试炼开始

试炼场上人声鼎沸，众人围成一圈。
苏清月站在高台上，冷冷地看着林凡。
林凡握紧手中的短剑，深吸一口气。

第三章 血战

夜色如墨，山风呼啸。
苏清月突然出手，一剑刺向林凡。
林凡侧身避开，反手一击。
"""

CHAPTER_PAYLOADS: dict[int, dict] = {
    0: {
        "summary": "林凡初入山门，遇见苏清月。",
        "characters": [
            {
                "name": "林凡",
                "aliases": [],
                "kind": "person",
                "description": "新入门的弟子",
                "evidence": {"text": "林凡站在山门前"},
            },
            {
                "name": "苏清月",
                "aliases": ["苏师姐"],
                "kind": "person",
                "description": "山门中的师姐",
                "evidence": {"text": "苏清月问道"},
            },
        ],
        "relations": [
            {
                "person_a": "林凡",
                "person_b": "苏清月",
                "rel_type": "同门",
                "evidence": {"text": "你就是新来的弟子"},
            }
        ],
        "events": [
            {
                "summary": "林凡来到山门，被苏清月询问",
                "actors": ["林凡", "苏清月"],
                "evidence": {"text": "林凡站在山门前"},
            }
        ],
        "concepts": ["山门"],
    },
    1: {
        "summary": "试炼开始，林凡与苏清月同场。",
        "characters": [
            {"name": "林凡", "evidence": {"text": "林凡握紧手中的短剑"}},
            {"name": "苏清月", "evidence": {"text": "苏清月站在高台上"}},
        ],
        "relations": [],
        "events": [
            {
                "summary": "试炼场上众人聚集",
                "actors": ["林凡"],
                "evidence": {"text": "试炼场上人声鼎沸"},
            }
        ],
    },
    2: {
        "summary": "苏清月出手，林凡反击。",
        "characters": [
            {"name": "林凡", "evidence": {"text": "林凡侧身避开"}},
            {"name": "苏清月", "evidence": {"text": "苏清月突然出手"}},
        ],
        "relations": [
            {
                "person_a": "苏清月",
                "person_b": "林凡",
                "rel_type": "敌对",
                "evidence": {"text": "一剑刺向林凡"},
            }
        ],
        "events": [
            {
                "summary": "苏清月夜袭林凡",
                "actors": ["苏清月", "林凡"],
                "evidence": {"text": "夜色如墨"},
            }
        ],
    },
}


class ScriptedProvider:
    """按「【第N章」识别章节，返回对应 JSON。"""

    name = "scripted"

    def __init__(self, payloads: dict[int, dict] | None = None) -> None:
        self.payloads = payloads if payloads is not None else CHAPTER_PAYLOADS
        self.calls = 0

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        self.calls += 1
        m = re.search(r"【第(\d+)章", prompt)
        idx = int(m.group(1)) - 1 if m else 0
        return json.dumps(self.payloads.get(idx, {}), ensure_ascii=False)


class FixedProvider:
    name = "fixed"

    def __init__(self, text: str) -> None:
        self.text = text

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        return self.text
