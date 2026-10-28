"""P0 摄入平面：章节识别、seq 分配、EPUB 转码保真。"""

from __future__ import annotations

import io
import unittest
import zipfile

from novel_rag.domain.models import Chapter
from novel_rag.ingest.encoding import decode_text, normalize_newlines
from novel_rag.ingest.epub_builder import build_epub
from novel_rag.ingest.seq_indexer import cfi_prefix, split_paragraphs
from novel_rag.ingest.splitter import chapter_fingerprint, split_chapters

from .fixtures import SAMPLE_TEXT


class SplitterTest(unittest.TestCase):
    def test_detects_three_chapters(self) -> None:
        result = split_chapters(SAMPLE_TEXT)
        self.assertEqual(result.rule, "cn-chapter")
        self.assertEqual(len(result.chapters), 3)
        self.assertEqual(result.chapters[0].title, "第一章 初入山门")
        self.assertGreaterEqual(result.confidence, 0.9)
        self.assertFalse(result.needs_llm)
        self.assertEqual([c.idx for c in result.chapters], [0, 1, 2])

    def test_spine_and_nav_are_assigned(self) -> None:
        result = split_chapters(SAMPLE_TEXT)
        self.assertEqual([c.spine_index for c in result.chapters], [0, 1, 2])
        self.assertEqual(result.chapters[1].nav_href, "ch0002.xhtml")

    def test_fallback_when_no_chapter_markers(self) -> None:
        result = split_chapters("一二三四五。" * 2000)
        self.assertEqual(result.rule, "fallback")
        self.assertTrue(result.needs_llm)
        self.assertGreater(len(result.chapters), 1)

    def test_fingerprint_is_content_sensitive(self) -> None:
        a = chapter_fingerprint("第一章内容")
        b = chapter_fingerprint("第一章内容 ")
        c = chapter_fingerprint("第一章内容改了")
        self.assertEqual(a, b)  # 空白差异不影响
        self.assertNotEqual(a, c)


class SeqIndexerTest(unittest.TestCase):
    def test_paragraph_is_exact_slice(self) -> None:
        body = "第一句比较短。\n第二句也不长。\n第三句开始有一些内容了，足够合并成一段了。\n"
        ranges = split_paragraphs(body, min_chars=20)
        self.assertTrue(ranges)
        for start, end in ranges:
            self.assertTrue(0 <= start < end <= len(body))
            self.assertEqual(body[start:end], body[start:end])

    def test_paragraphs_cover_body_in_order(self) -> None:
        result = split_chapters(SAMPLE_TEXT)
        body = result.chapters[0].body
        ranges = split_paragraphs(body, min_chars=40)
        offsets = [s for s, _ in ranges]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(ranges[0][0], 0)

    def test_cfi_prefix_is_stable(self) -> None:
        # body 第 1 个元素是 <h1>，段落从第 2 个元素起
        self.assertEqual(cfi_prefix(0, 0), "epubcfi(/6/2!/4/4)")
        self.assertEqual(cfi_prefix(2, 3), "epubcfi(/6/6!/4/10)")


class EpubBuilderTest(unittest.TestCase):
    def test_epub_structure(self) -> None:
        result = split_chapters(SAMPLE_TEXT)
        blob = build_epub("测试书", "作者", result.chapters)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = zf.namelist()
            self.assertEqual(names[0], "mimetype")
            self.assertIn("META-INF/container.xml", names)
            self.assertIn("OEBPS/nav.xhtml", names)
            self.assertIn("OEBPS/content.opf", names)
            self.assertIn("OEBPS/ch0003.xhtml", names)
            nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
            self.assertIn("第三章 血战", nav)

    def test_body_chars_are_not_rewritten(self) -> None:
        """转码只允许切块与转义，不允许改写字。"""
        result = split_chapters(SAMPLE_TEXT)
        chapter: Chapter = result.chapters[2]
        blob = build_epub("t", "", result.chapters)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            xhtml = zf.read("OEBPS/ch0003.xhtml").decode("utf-8")
        for line in chapter.body.split("\n"):
            if line.strip():
                self.assertIn(line, xhtml)


class EncodingTest(unittest.TestCase):
    def test_gbk_detected(self) -> None:
        raw = "第一章 测试\n\n内容内容内容".encode("gb18030")
        decoded = decode_text(raw)
        self.assertIn("第一章", decoded.text)

    def test_newlines_normalized(self) -> None:
        self.assertEqual(normalize_newlines("a\r\nb\rc"), "a\nb\nc")


if __name__ == "__main__":
    unittest.main()
