"""Embedding 服务（决策 #12：索引按 model_id 分区）。

两个 provider：
- ``BgeSmallZhEmbedding``：真实模型，BGE-small-zh-v1.5（ONNX + tokenizers），512 维，
  CLS pooling + L2 归一；查询侧自动加 BGE 官方检索指令前缀。
- ``HashEmbedding``：确定性占位。没有 onnxruntime 或模型文件时自动降级，
  保证整条链路零依赖可跑（但检索质量仅供联调）。

两者实现同一个 Protocol，切换只改配置；向量表以 ``model_id`` 分区，互不污染。
"""

from __future__ import annotations

import array
import hashlib
import math
import re
from pathlib import Path
from typing import Protocol

from ..config import Settings

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z]+|[0-9]+")


class EmbeddingProvider(Protocol):
    model_id: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


# ---------------------------------------------------------------- 占位实现


class HashEmbedding:
    """确定性离线占位 embedding（字符 bigram 哈希）。"""

    def __init__(self, model_id: str = "hash-bigram-256", dim: int = 256) -> None:
        self.model_id = model_id
        self.dim = dim

    def _tokens(self, text: str) -> list[str]:
        chars = _TOKEN_RE.findall(text)
        grams = list(chars)
        grams += [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
        return grams

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for gram in self._tokens(text):
            h = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


# ---------------------------------------------------------------- 真实模型

BGE_MODEL_ID = "bge-small-zh-v1.5"
BGE_DIM = 512

# BGE 官方建议：检索型查询加指令前缀，文档侧不加。
BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class BgeSmallZhEmbedding:
    """BGE-small-zh-v1.5（ONNX Runtime）。

    模型目录需要包含 ``model.onnx`` 与 ``tokenizer.json``，
    可用 ``scripts/fetch_embedding_model.py`` 下载。
    """

    def __init__(
        self,
        model_dir: str | Path,
        *,
        model_id: str = BGE_MODEL_ID,
        dim: int = BGE_DIM,
        max_length: int = 512,
        intra_threads: int = 0,
    ) -> None:
        self.model_dir = Path(model_dir).expanduser()
        self.model_id = model_id
        self.dim = dim
        self.max_length = max_length
        self.intra_threads = intra_threads
        self._session = None
        self._tokenizer = None

    # -- 懒加载：import 本模块不应付出加载 94MB 模型的代价
    def _ensure(self) -> None:
        if self._session is not None:
            return
        model_path = self.model_dir / "model.onnx"
        tok_path = self.model_dir / "tokenizer.json"
        if not model_path.exists() or not tok_path.exists():
            raise FileNotFoundError(f"缺少模型文件: {model_path} / {tok_path}")

        import onnxruntime as ort  # noqa: PLC0415
        from tokenizers import Tokenizer  # noqa: PLC0415

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if self.intra_threads:
            opts.intra_op_num_threads = self.intra_threads

        self._session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = Tokenizer.from_file(str(tok_path))
        self._tokenizer.enable_truncation(max_length=self.max_length)
        self.dim = int(self._session.get_outputs()[0].shape[-1])
        self._verify()

    def _verify(self) -> None:
        vec = self._run(["测试"], prefix=False)[0]
        if len(vec) != self.dim:
            raise RuntimeError(f"embedding 维度不一致: {len(vec)} != {self.dim}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._run(texts, prefix=False)

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        return self._run(texts, prefix=True)

    def _run(self, texts: list[str], *, prefix: bool) -> list[list[float]]:
        if not texts:
            return []
        self._ensure()
        import numpy as np  # noqa: PLC0415

        payload = [BGE_QUERY_INSTRUCTION + t if prefix else t for t in texts]
        encodings = self._tokenizer.encode_batch(payload)  # type: ignore[union-attr]
        max_len = max(len(e.ids) for e in encodings)
        n = len(encodings)

        input_ids = np.zeros((n, max_len), dtype=np.int64)
        attention = np.zeros((n, max_len), dtype=np.int64)
        type_ids = np.zeros((n, max_len), dtype=np.int64)
        for row, enc in enumerate(encodings):
            length = len(enc.ids)
            input_ids[row, :length] = enc.ids
            attention[row, :length] = enc.attention_mask
            type_ids[row, :length] = enc.type_ids

        hidden = self._session.run(  # type: ignore[union-attr]
            None,
            {"input_ids": input_ids, "attention_mask": attention, "token_type_ids": type_ids},
        )[0]
        # BGE 用 [CLS] 向量做句表示
        pooled = hidden[:, 0, :].astype(np.float32)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (pooled / norms).tolist()


# ---------------------------------------------------------------- 工具函数


def to_blob(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def from_blob(blob: bytes) -> list[float]:
    arr = array.array("f")
    arr.frombytes(blob)
    return arr.tolist()


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def embed_query(provider: EmbeddingProvider, text: str) -> list[float]:
    """查询侧编码。BGE 需要指令前缀，占位实现没有这个方法。"""
    fn = getattr(provider, "embed_query", None)
    if callable(fn):
        return fn([text])[0]
    return provider.embed([text])[0]


def from_settings(settings: Settings) -> EmbeddingProvider:
    """按配置选 provider。``auto`` 时能用真模型就用真模型。"""
    backend = settings.embedding_backend
    model_dir = settings.resolved_embed_dir

    if backend in ("auto", "bge"):
        ready = (model_dir / "model.onnx").exists() and (model_dir / "tokenizer.json").exists()
        if ready:
            try:
                return BgeSmallZhEmbedding(
                    model_dir,
                    model_id=settings.embedding_model_id or BGE_MODEL_ID,
                    dim=settings.embedding_dim or BGE_DIM,
                )
            except ImportError:
                if backend == "bge":
                    raise
        elif backend == "bge":
            raise FileNotFoundError(
                f"未找到模型 {model_dir}，先运行 scripts/fetch_embedding_model.py"
            )

    return HashEmbedding(
        model_id=settings.embedding_model_id or "hash-bigram-256",
        dim=settings.embedding_dim or 256,
    )
