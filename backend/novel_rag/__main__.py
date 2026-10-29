"""入口。

    python -m novel_rag                     # 随机端口，打印 PORT:<n> 与 LOG:<path>
    python -m novel_rag --port 8787         # 指定端口
    python -m novel_rag --home /data/.nr    # 指定数据目录（桌面壳注入）

桌面壳（Tauri）以 sidecar 方式拉起本进程，读取 stdout 的 ``PORT:<n>`` 后
把端口与 Bearer token 一起注入前端（架构决策 #1）。

**参数必须与壳对齐**：壳会传 ``--home <app_data>``。早期版本没有这个参数，
argparse 直接以 ``unrecognized arguments`` 退出，表现为「本地后端启动超时」，
且因为日志还没初始化，连 backend.log 都不会生成。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .api.server import build_app, serve
from .config import get_settings
from .logging_setup import log_paths, setup_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="novel-rag")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 表示随机端口（推荐）")
    parser.add_argument("--home", default=None, help="数据目录（桌面壳注入）")
    parser.add_argument("--db", default=None, help="覆盖数据库路径")
    parser.add_argument("--log-dir", default=None, help="覆盖日志目录")
    parser.add_argument("--print-token", action="store_true", help="启动后打印 token（壳用）")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.home:
        settings.home = Path(args.home).expanduser()
    if args.log_dir:
        settings.log_dir = args.log_dir
    settings.ensure_home()

    log_file = setup_logging(settings)
    logger = logging.getLogger("novel_rag")

    if not settings.auth_token and not settings.disable_auth:
        settings.auth_token = os.environ.get("NOVEL_RAG_TOKEN") or _random_token()
        os.environ["NOVEL_RAG_TOKEN"] = settings.auth_token
    if settings.disable_auth:
        logger.warning(
            "NOVEL_RAG_DISABLE_AUTH=1：/api 不校验 token，请只部署在受信网络或反向代理后面"
        )
    if args.log_dir:
        os.environ["NOVEL_RAG_LOG_DIR"] = settings.log_dir

    logger.info(
        "starting: home=%s db=%s port=%s host=%s",
        settings.home,
        args.db or settings.db_path,
        args.port,
        args.host,
    )

    try:
        app = build_app(settings=settings, db_path=args.db)
    except Exception:  # noqa: BLE001 - 启动失败必须留下完整堆栈
        logger.exception("build_app failed")
        print("ERROR:build_app_failed", flush=True)
        return 2

    paused = app.recover_tasks()
    if paused:
        logger.warning("resume paused tasks: %s", paused)

    try:
        httpd = serve(app, args.host, args.port)
    except Exception:  # noqa: BLE001
        logger.exception("listen failed on %s:%s", args.host, args.port)
        print("ERROR:listen_failed", flush=True)
        return 3

    port = httpd.server_address[1]
    # 这三行是壳的协议，必须只走 stdout 且立刻 flush
    print(f"PORT:{port}", flush=True)
    print(f"LOG:{log_file}", flush=True)
    if args.print_token:
        print(f"TOKEN:{settings.auth_token}", flush=True)
    if paused:
        print(f"PAUSED_TASKS:{json.dumps(paused)}", flush=True)
    logger.info("listening on http://%s:%s (logs: %s)", args.host, port, log_file)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("interrupted, shutting down")
    finally:
        httpd.server_close()
        app.db.close()
        logger.info("stopped")
    _ = log_paths(settings)  # 保持与设置接口同一份路径定义
    return 0


def _random_token() -> str:
    import secrets

    return secrets.token_urlsafe(24)


if __name__ == "__main__":
    sys.exit(main())
