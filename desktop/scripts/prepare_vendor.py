#!/usr/bin/env python3
"""把 foliate-js 拉到 desktop/web/vendor（不入库）。

CI 与本地开发都跑这一个脚本，避免"仓库里塞第三方源码"。

实现上直接下载 GitHub 的 tarball 并按需解压，**不走 git clone**：
- 没有 ``.git`` 目录，省 4MB 且不会被 Windows 的只读属性卡住删除；
- 不产生临时克隆目录，也就不需要递归删目录；
- 只解压运行时需要的文件（``vendor/zip.js`` 是 EPUB 解压必需，
  ``vendor/pdfjs`` 13MB 只服务 PDF，直接跳过）。

    python3 desktop/scripts/prepare_vendor.py
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

REPO = "johnfactotum/foliate-js"
REF = "main"
TARBALL = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/{REF}"

REQUIRED = ["view.js", "epub.js", "paginator.js", "epubcfi.js", "vendor/zip.js"]
SKIP_DIRS = {("tests",), ("rollup",), ("vendor", "pdfjs")}
KEEP_EXT = {".js", ".css", ".html", ".json"}


def _wanted(parts: tuple[str, ...]) -> bool:
    if not parts:
        return False
    for skip in SKIP_DIRS:
        if parts[: len(skip)] == skip:
            return False
    return True


def _force_rmtree(path: Path) -> None:
    if not path.exists():
        return
    for root, _dirs, files in os.walk(path, topdown=False):
        for name in files:
            target = Path(root) / name
            try:
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
                target.unlink()
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def ensure_foliate(vendor: Path) -> Path:
    target = vendor / "foliate-js"
    if all((target / name).exists() for name in REQUIRED):
        print(f"foliate-js 已就绪: {target}")
        return target

    _force_rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    print(f"download {TARBALL}")
    with urllib.request.urlopen(TARBALL, timeout=180) as resp:
        blob = resp.read()
    print(f"  {len(blob) / 1024:.0f} KB")

    written = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts
            rel = parts[1:]  # 去掉 tarball 的顶层目录
            if not _wanted(rel):
                continue
            path = PurePosixPath(*rel)
            if path.suffix not in KEEP_EXT and path.name != "LICENSE":
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            dest = target / Path(*rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(handle.read())
            written += 1

    missing = [name for name in REQUIRED if not (target / name).exists()]
    if missing:
        raise RuntimeError(f"foliate-js 缺少必需文件: {missing}")

    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"foliate-js ready: {target} ({written} files, {size / 1024:.0f} KB)")
    return target


def main() -> int:
    web = Path(__file__).resolve().parents[1] / "web"
    vendor = web / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    ensure_foliate(vendor)
    return 0


if __name__ == "__main__":
    sys.exit(main())
