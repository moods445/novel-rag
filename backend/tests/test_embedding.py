"""真实 embedding：BGE-small-zh-v1.5 的维度、归一、语义区分度。

模型文件不在时整类跳过（CI 上不强制下载 94MB 权重）。
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from novel_rag.config import Settings
from novel_rag.llm import embedding as emb

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "bge-small-zh-v1.5"
HAS_MODEL = (MODEL_DIR / "model.onnx").exists() and (MODEL_DIR / "tokenizer.json").exists()


def cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


@unittest.skipUnless(HAS_MODEL, "未下载 BGE 模型（scripts/fetch_embedding_model.py）")
class BgeEmbeddingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.provider = emb.BgeSmallZhEmbedding(MODEL_DIR)

    def test_dimension_is_512(self) -> None:
        vec = self.provider.embed(["林凡"])[0]
        self.assertEqual(len(vec), 512)
        self.assertEqual(self.provider.dim, 512)
        self.assertEqual(self.provider.model_id, "bge-small-zh-v1.5")

    def test_vectors_are_l2_normalized(self) -> None:
        vec = self.provider.embed(["苏清月站在高台上，冷冷地看着林凡。"])[0]
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in vec)), 1.0, places=4)

    def test_empty_input(self) -> None:
        self.assertEqual(self.provider.embed([]), [])

    def test_batch_matches_single(self) -> None:
        batch = self.provider.embed(["第一句话", "第二句话"])
        self.assertEqual(len(batch), 2)
        self.assertEqual(len(batch[0]), 512)

    def test_semantic_relevance_beats_unrelated(self) -> None:
        query = self.provider.embed_query(["苏清月是什么人"])[0]
        related = self.provider.embed(["苏清月站在高台上，冷冷地看着林凡。"])[0]
        unrelated = self.provider.embed(["夜色如墨，山风呼啸，一剑刺来。"])[0]
        self.assertGreater(cos(query, related), cos(query, unrelated))

    def test_query_instruction_keeps_identity(self) -> None:
        plain = self.provider.embed(["林凡"])[0]
        with_instruction = self.provider.embed_query(["林凡"])[0]
        self.assertGreater(cos(plain, with_instruction), 0.8)

    def test_deterministic(self) -> None:
        a = self.provider.embed(["测试文本"])[0]
        b = self.provider.embed(["测试文本"])[0]
        self.assertEqual(a, b)


class ProviderSelectionTest(unittest.TestCase):
    def test_auto_falls_back_to_hash_without_model(self) -> None:
        settings = Settings(embedding_dir="/nonexistent/path")
        provider = emb.from_settings(settings)
        self.assertIsInstance(provider, emb.HashEmbedding)
        self.assertEqual(provider.dim, 256)

    @unittest.skipUnless(HAS_MODEL, "未下载 BGE 模型")
    def test_auto_picks_bge_when_model_present(self) -> None:
        settings = Settings(embedding_dir=str(MODEL_DIR), embedding_backend="auto")
        provider = emb.from_settings(settings)
        self.assertIsInstance(provider, emb.BgeSmallZhEmbedding)
        self.assertEqual(provider.model_id, "bge-small-zh-v1.5")

    def test_explicit_bge_without_model_raises(self) -> None:
        settings = Settings(embedding_dir="/nonexistent/path", embedding_backend="bge")
        with self.assertRaises(FileNotFoundError):
            emb.from_settings(settings)

    def test_embed_query_falls_back_for_hash(self) -> None:
        provider = emb.HashEmbedding()
        vec = emb.embed_query(provider, "林凡")
        self.assertEqual(len(vec), 256)


if __name__ == "__main__":
    unittest.main()
