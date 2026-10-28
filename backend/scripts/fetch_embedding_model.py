#!/usr/bin/env python3
"""下载 ONNX 版 BGE-small-zh-v1.5（默认到 ~/.novel-rag/models/bge-small-zh-v1.5）。

只依赖标准库。实现已迁到 ``novel_rag.embedding_models``，HTTP 接口与启动时
自动补齐共用同一份逻辑。支持 HF 官方与镜像（``HF_ENDPOINT``）。

    python3 scripts/fetch_embedding_model.py
    HF_ENDPOINT=https://hf-mirror.com python3 scripts/fetch_embedding_model.py
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


def _download(repo: str, filename: str, dest: Path, endpoint: str) -> None:
    url = f"{endpoint.rstrip('/')}/{repo}/resolve/main/{filename}"
    target = dest / Path(filename).name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        print(f"skip  {target} ({target.stat().st_size / 1e6:.1f} MB)")
        return
    print(f"fetch {url}")
    with urllib.request.urlopen(url, timeout=120) as resp, target.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        read = 0
        while chunk := resp.read(1 << 16):
            fh.write(chunk)
            read += len(chunk)
            if total:
                pct = read * 100 // total
                print(f"\r  {pct:3d}%  {read / 1e6:.1f}/{total / 1e6:.1f} MB", end="", flush=True)
    print(f"\n  -> {target} ({target.stat().st_size / 1e6:.1f} MB)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", default=str(Path("~/.novel-rag/models/bge-small-zh-v1.5").expanduser()))
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="fp32")
    parser.add_argument("--endpoint", default=os.environ.get("HF_ENDPOINT", "https://huggingface.co"))
    args = parser.parse_args(argv)

    dest = Path(args.dest).expanduser()
    state = embedding_models.download(dest, variant=args.variant, endpoint=args.endpoint)
    print(f"done  model dir = {dest} ({state['total_mb']} MB)")
    print("       export NOVEL_RAG_EMBED_DIR=" + str(dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
