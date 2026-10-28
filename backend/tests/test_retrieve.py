"""P3 检索与上下文平面：双闸门 + ContextCompiler 的时序还原与预算裁剪。"""

from __future__ import annotations

import unittest

from novel_rag.domain.models import Evidence
from novel_rag.retrieve.compiler import CompilerConfig, compile_context
from novel_rag.retrieve.gates import apply as apply_gates


def ev(seq: int, chapter: int, score: float, text: str = "", kind: str = "raw") -> Evidence:
    return Evidence(
        seq=seq,
        cfi=f"epubcfi(/6/{chapter * 2 + 2}!/4/2)",
        text=text or f"第{chapter + 1}章的第{seq}段内容，用来测试顺序还原是否生效。",
        chapter_idx=chapter,
        source="fact" if kind != "raw" else "raw",
        coverage="extracted" if kind != "raw" else "unparsed",
        kind=kind,
        score=score,
    )


class CompilerOrderTest(unittest.TestCase):
    def test_blocks_sorted_by_seq_not_by_score(self) -> None:
        evidences = [ev(5, 2, 0.9), ev(2, 1, 0.1), ev(8, 3, 0.8)]
        compiled = compile_context(evidences)
        self.assertEqual([b.seq for b in compiled.blocks], [2, 5, 8])
        self.assertEqual([b.citation for b in compiled.blocks], [1, 2, 3])

    def test_render_marks_chapter_and_citation(self) -> None:
        compiled = compile_context([ev(2, 1, 0.1)])
        self.assertIn("[1]", compiled.text)
        self.assertIn("第2章", compiled.text)

    def test_duplicate_evidence_deduped(self) -> None:
        a = ev(2, 1, 0.5)
        b = ev(2, 1, 0.4)
        compiled = compile_context([a, b])
        self.assertEqual(len(compiled.blocks), 1)
        self.assertGreaterEqual(compiled.dropped, 1)


class CompilerBudgetTest(unittest.TestCase):
    def test_budget_drops_whole_chapter_groups(self) -> None:
        evidences = [
            ev(1, 0, 0.9, "甲" * 100),
            ev(2, 0, 0.85, "乙" * 100),
            ev(3, 1, 0.1, "丙" * 100),
        ]
        cfg = CompilerConfig(budget_chars=150, per_block_chars=200)
        compiled = compile_context(evidences, config=cfg)
        chapters = {b.chapter_idx for b in compiled.blocks}
        self.assertEqual(len(chapters), 1, compiled.blocks)
        self.assertTrue(compiled.truncated)
        self.assertGreater(compiled.dropped, 0)

    def test_fact_evidence_not_filtered_by_min_chars(self) -> None:
        """人物档案只有两三个字，不能因为「太短」被丢掉。"""
        evidences = [ev(1, 0, 0.95, "林凡", kind="entity"), ev(2, 1, 0.3)]
        compiled = compile_context(evidences, config=CompilerConfig(min_chars=12))
        kinds = [b.kind for b in compiled.blocks]
        self.assertIn("entity", kinds)

    def test_budget_never_leaves_half_chapter(self) -> None:
        evidences = [ev(i, i // 2, 0.5, "內容" * 40) for i in range(1, 7)]
        cfg = CompilerConfig(budget_chars=200, per_block_chars=80)
        compiled = compile_context(evidences, config=cfg)
        by_chapter: dict[int, list[int]] = {}
        for b in compiled.blocks:
            by_chapter.setdefault(b.chapter_idx, []).append(b.seq)
        for _chapter, seqs in by_chapter.items():
            self.assertEqual(seqs, sorted(seqs))
        self.assertTrue(all(len(v) >= 1 for v in by_chapter.values()))


class GateTest(unittest.TestCase):
    def test_spoiler_gate_drops_future_evidence(self) -> None:
        evidences = [ev(1, 0, 0.5), ev(9, 4, 0.9)]
        result = apply_gates(
            evidences, progress_seq=3, spoiler_guard=True, extracted_chapters={0}
        )
        self.assertEqual([e.seq for e in result.kept], [1])
        self.assertEqual(result.dropped_spoiler, 1)

    def test_spoiler_gate_off_keeps_everything(self) -> None:
        evidences = [ev(1, 0, 0.5), ev(9, 4, 0.9)]
        result = apply_gates(
            evidences, progress_seq=3, spoiler_guard=False, extracted_chapters={0}
        )
        self.assertEqual(len(result.kept), 2)

    def test_coverage_gate_marks_unparsed_chapters(self) -> None:
        evidences = [ev(1, 0, 0.5), ev(2, 1, 0.6)]
        result = apply_gates(
            evidences, progress_seq=None, spoiler_guard=False, extracted_chapters={0}
        )
        coverage = {e.chapter_idx: e.coverage for e in result.kept}
        self.assertEqual(coverage[0], "extracted")
        self.assertEqual(coverage[1], "unparsed")
        # 已校验的排在前面
        self.assertEqual(result.kept[0].chapter_idx, 0)

    def test_entity_evidence_always_trusted(self) -> None:
        ent = ev(1, 0, 0.95, "人物档案", kind="entity")
        result = apply_gates(
            [ent], progress_seq=1, spoiler_guard=True, extracted_chapters=set()
        )
        self.assertEqual(result.kept[0].coverage, "extracted")
        self.assertEqual(result.kept[0].source, "fact")


if __name__ == "__main__":
    unittest.main()
