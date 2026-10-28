#!/usr/bin/env python3
"""把 foliate-js 等前端依赖拉到 desktop/web/vendor（不入库）。

CI 与本地开发都跑这一个脚本，避免"仓库里塞 5MB 第三方源码"。

    python3 desktop/scripts/prepare_vendor.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

FOLIATE_REPO = "https://github.com/johnfactotum/foliate-js.git"
FOLIATE_REF = "main"


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def ensure_foliate(vendor: Path) -> Path:
    target = vendor / "foliate-js"
    if (target / "view.js").exists():
        print(f"foliate-js 已存在: {target}")
        return target

    vendor.mkdir(parents=True, exist_ok=True)
    tmp = vendor / ".foliate-js-tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    print(f"clone {FOLIATE_REPO} -> {tmp}")
    _run(["git", "clone", "--depth", "1", "--branch", FOLIATE_REF, FOLIATE_REPO, str(tmp)])
    tmp.rename(target)

    # 只保留运行时需要的东西：
    #   vendor/zip.js  —— EPUB 解压，必须保留（漏了它 epub 解析直接报 404）
    #   vendor/fflate.js —— MOBI 解压
    #   vendor/pdfjs/  —— 仅 PDF 用，13MB，直接丢掉
    for relative in ("tests", "rollup", "vendor/pdfjs"):
        shutil.rmtree(target / relative, ignore_errors=True)

    keep_ext = {".js", ".css", ".html", ".json"}
    for path in list(target.rglob("*")):
        if path.is_file() and path.suffix not in keep_ext and path.name != "LICENSE":
            path.unlink(missing_ok=True)

    required = ["view.js", "epub.js", "paginator.js", "vendor/zip.js"]
    missing = [r for r in required if not (target / r).exists()]
    if missing:
        raise RuntimeError(f"foliate-js 缺少必需文件: {missing}")

    print(f"foliate-js ready: {target}")
    return target


def main() -> int:
    here = Path(__file__).resolve()
    web = here.parents[1] / "web"
    vendor = web / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    ensure_foliate(vendor)
    return 0


if __name__ == "__main__":
    sys.exit(main())
