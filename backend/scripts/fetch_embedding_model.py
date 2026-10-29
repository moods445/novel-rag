#!/usr/bin/env python3
"""下载 ONNX 版 BGE-small-zh-v1.5（默认到 ~/.novel-rag/models/bge-small-zh-v1.5）。

只依赖标准库。实现已迁到 ``novel_rag.embedding_models``，HTTP 接口与启动时
自动补齐共用同一份逻辑。支持 HF 官方与镜像（``HF_ENDPOINT``）。

    python3 scripts/fetch_embedding_model.py
    HF_ENDPOINT=https://hf-mirror.com python3 scripts/fetch_embedding_model.py
    python3 scripts/fetch_embedding_model.py --proxy http://127.0.0.1:7890
    python3 scripts/fetch_embedding_model.py --variant int8 --dest ./models/bge-small-zh-v1.5
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novel_rag import embedding_models  # noqa: E402
from novel_rag.embedding_models import REPO, VARIANTS  # noqa: E402,F401


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", default=str(Path("~/.novel-rag/models/bge-small-zh-v1.5").expanduser()))
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="fp32")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("HF_ENDPOINT", ""),
        help="下载源；留空则先直连官方，失败后自动换国内镜像",
    )
    parser.add_argument("--proxy", default=os.environ.get("NOVEL_RAG_PROXY", ""))
    args = parser.parse_args(argv)

    dest = Path(args.dest).expanduser()
    embedding_models.download(
        dest, variant=args.variant, endpoint=args.endpoint or None, proxy=args.proxy or None
    )
    status = embedding_models.model_status(dest)
    print(f"done  model dir = {dest} ({status['size_mb']} MB)")
    print("       export NOVEL_RAG_EMBED_DIR=" + str(dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
