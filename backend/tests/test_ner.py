"""人名识别：三档后端 + 别名规则 + 碎片合并。"""

from __future__ import annotations

import unittest

from novel_rag.extract.ner import (
    CompositeNer,
    JiebaNer,
    RegexNer,
    build_ner,
    clean_name,
    is_plausible,
    scan_alias_pairs,
)


class CleanNameTest(unittest.TestCase):
    def test_strips_trailing_verb(self) -> None:
        self.assertEqual(clean_name("孔明大"), "孔明")
        self.assertEqual(clean_name("苏清月问"), "苏清月")
        self.assertEqual(clean_name("玄德惊"), "玄德")

    def test_keeps_legit_names(self) -> None:
        self.assertEqual(clean_name("曹操"), "曹操")
        self.assertEqual(clean_name("司马懿"), "司马懿")


class PlausibilityTest(unittest.TestCase):
    def test_rejects_place_and_collective(self) -> None:
        self.assertFalse(is_plausible("魏兵"))
        self.assertFalse(is_plausible("巨鹿郡"))
        self.assertFalse(is_plausible("众人"))
        self.assertFalse(is_plausible("左右"))

    def test_accepts_real_names(self) -> None:
        for name in ("曹操", "孔明", "司马懿", "关公", "献帝"):
            self.assertTrue(is_plausible(name), name)

    def test_rejects_single_char(self) -> None:
        self.assertFalse(is_plausible("操"))


class AliasRuleTest(unittest.TestCase):
    def test_extracts_courtesy_name(self) -> None:
        pairs = scan_alias_pairs("姓刘名备，字玄德。姓曹，名操，字孟德。")
        self.assertIn(("刘备", "玄德"), pairs)
        self.assertIn(("曹操", "孟德"), pairs)

    def test_no_alias_without_pattern(self) -> None:
        self.assertEqual(scan_alias_pairs("刘备与曹操煮酒论英雄。"), [])


class BackendTest(unittest.TestCase):
    TEXT = "曹操大喜，遂问计于荀彧。荀彧曰：可如此如此。夏侯惇领命而去。"

    def test_regex_backend_finds_names(self) -> None:
        result = RegexNer().scan(self.TEXT, min_count=1)
        names = {n for n, _ in result.names}
        self.assertTrue(names & {"曹操", "荀彧", "夏侯惇"})

    def test_composite_is_union_of_backends(self) -> None:
        composite = CompositeNer([RegexNer(), JiebaNer()])
        result = composite.scan(self.TEXT, min_count=1)
        names = {n for n, _ in result.names}
        self.assertIn("曹操", names)

    def test_composite_does_not_double_count(self) -> None:
        from collections import Counter

        class Fixed:
            def __init__(self, name: str, counts: dict[str, int]) -> None:
                self.name = name
                self._counts = counts

            def scan(self, text, *, min_count=2, top=60):
                from novel_rag.extract.ner import NerResult

                return NerResult(names=list(self._counts.items()))

        composite = CompositeNer(
            [Fixed("a", {"曹操": 3}), Fixed("b", {"曹操": 3})]  # type: ignore[list-item]
        )
        result = composite.scan("x", min_count=1)
        self.assertEqual(dict(result.names)["曹操"], 3)
        _ = Counter

    def test_build_ner_modes(self) -> None:
        self.assertIsInstance(build_ner("regex"), RegexNer)
        self.assertIsInstance(build_ner("jieba"), JiebaNer)
        self.assertIsNotNone(build_ner("auto"))

    def test_empty_text(self) -> None:
        self.assertEqual(build_ner("auto").scan("", min_count=1).names, [])


if __name__ == "__main__":
    unittest.main()
