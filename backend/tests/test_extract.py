"""P1 抽取的局部单元：预扫描候选名单 + JSON 解析容错。"""

from __future__ import annotations

import unittest

from novel_rag.extract.extractor import parse_json_object
from novel_rag.extract.prescan import dictionary_from, scan_names

from .fixtures import SAMPLE_TEXT


class PrescanTest(unittest.TestCase):
    def test_names_found_with_relaxed_threshold(self) -> None:
        names = dictionary_from(SAMPLE_TEXT)
        self.assertIn("林凡", names)
        self.assertIn("苏清月", names)

    def test_strict_threshold_can_be_empty_on_small_corpus(self) -> None:
        # 每个名字只出现一次，严格阈值返回空表 ——
        # 这正是 dictionary_from 需要自适应降级的原因。
        tiny = "张伟说道。李娜点头。王强问道。"
        self.assertEqual(scan_names(tiny, min_count=3), [])

    def test_contained_fragments_are_merged(self) -> None:
        # jieba 会把生僻名切成前缀，碎片必须被更长的那一个吸收
        from novel_rag.extract.ner import _drop_contained
        from collections import Counter

        result = _drop_contained(Counter({"苏清": 3, "苏清月": 2, "玄德": 50, "玄德公": 1}))
        self.assertNotIn("苏清", result)
        self.assertIn("苏清月", result)
        self.assertIn("玄德", result)

    def test_stopwords_filtered(self) -> None:
        names = dictionary_from("他们说。他们说道。我们问道。")
        self.assertNotIn("他们", names)
        self.assertNotIn("我们", names)


class JsonParseTest(unittest.TestCase):
    def test_plain_json(self) -> None:
        self.assertEqual(parse_json_object('{"a": 1}'), {"a": 1})

    def test_fenced_json(self) -> None:
        self.assertEqual(parse_json_object('```json\n{"a": 2}\n```'), {"a": 2})

    def test_json_with_prose(self) -> None:
        self.assertEqual(parse_json_object('好的，结果如下：{"a": 3} 以上。'), {"a": 3})

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(parse_json_object("完全不是 JSON"))
        self.assertIsNone(parse_json_object(""))
        self.assertIsNone(parse_json_object("[1,2,3]"))

    def test_truncated_json_is_repaired(self) -> None:
        """max_tokens 截断是最常见的失败形态，必须能救回来。"""
        raw = (
            '{"summary":"桃园结义","characters":[{"name":"刘备","aliases":["玄德"],'
            '"evidence":{"text":"刘备"}},{"name":"关羽","evide'
        )
        parsed = parse_json_object(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["summary"], "桃园结义")
        self.assertEqual(parsed["characters"][0]["name"], "刘备")

    def test_truncated_nested_arrays_repaired(self) -> None:
        raw = '{"events":[{"summary":"a"},{"summary":"b"}],"concepts":["x","y'
        parsed = parse_json_object(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(len(parsed["events"]), 2)

    def test_reasoning_content_json_still_parses(self) -> None:
        raw = '好的，结果如下：\n```json\n{"summary":"x","characters":[]}\n```\n以上。'
        parsed = parse_json_object(raw)
        assert parsed is not None
        self.assertEqual(parsed["summary"], "x")


if __name__ == "__main__":
    unittest.main()
