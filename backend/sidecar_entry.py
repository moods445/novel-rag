#!/usr/bin/env python3
"""PyInstaller 入口：桌面包里的 sidecar 就是它的可执行文件。

启动后打印 ``PORT:<n>``，桌面壳读 stdout 拿到端口，再用注入的
``NOVEL_RAG_TOKEN`` 访问。
"""

from __future__ import annotations

import multiprocessing
import sys

from novel_rag.__main__ import main

if __name__ == "__main__":
    # PyInstaller onefile 下必须显式 freeze_support，否则子进程会递归启动
    multiprocessing.freeze_support()
    sys.exit(main())
