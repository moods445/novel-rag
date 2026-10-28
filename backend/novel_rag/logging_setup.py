"""日志落盘。

默认路径：``<NOVEL_RAG_HOME>/logs/backend.log``（可用 ``NOVEL_RAG_LOG_DIR`` 覆盖）。
桌面端还有第二个文件 ``logs/sidecar.log``，由 Rust 壳写入 sidecar 的
stdout/stderr——在 Python 还没来得及初始化日志就崩溃时（比如参数解析失败），
它是唯一的线索。

两级都要有，是因为踩过：桌面壳传了 ``--home`` 而 argparse 不认识，
进程立刻退出，接口和 backend.log 都不存在，只有 sidecar.log 里能看到
``unrecognized arguments``。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import Settings

_CONFIGURED = False


def setup_logging(settings: Settings, *, console: bool = True) -> Path:
    """初始化根 logger，返回日志文件路径。重复调用无副作用。"""
    global _CONFIGURED
    log_file = settings.log_file
    if _CONFIGURED:
        return log_file

    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if console:
        # 写 stderr：桌面壳会把它并进 sidecar.log
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    # 第三方库的噪声降级
    for noisy in ("jieba", "urllib3", "onnxruntime"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    logging.getLogger("novel_rag").info(
        "logging ready: file=%s level=%s python=%s", log_file, settings.log_level, sys.version.split()[0]
    )
    return log_file


def reset_logging() -> None:
    """测试用：清掉已注册的 handler。"""
    global _CONFIGURED
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
    _CONFIGURED = False


def log_paths(settings: Settings) -> dict[str, str]:
    """给设置接口与前端展示用。"""
    sidecar = settings.resolved_log_dir / "sidecar.log"
    return {
        "dir": str(settings.resolved_log_dir),
        "backend": str(settings.log_file),
        "sidecar": str(sidecar),
        "sidecar_exists": Path(sidecar).exists(),
        "env_log_dir": os.environ.get("NOVEL_RAG_LOG_DIR", ""),
    }
