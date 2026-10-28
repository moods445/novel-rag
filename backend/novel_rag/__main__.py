"""入口。

    python -m novel_rag                # 监听随机端口，打印 PORT:<n>
    python -m novel_rag --port 8787    # 指定端口

桌面壳（Tauri）以 sidecar 方式拉起本进程，读取 stdout 的 ``PORT:<n>`` 后
把端口与 Bearer token 一起注入前端（架构决策 #1）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .api.server import build_app, serve
from .config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="novel-rag")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 表示随机端口（推荐）")
    parser.add_argument("--db", default=None, help="覆盖数据库路径")
    parser.add_argument("--print-token", action="store_true", help="启动后打印 token（壳用）")
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.ensure_home()
    if not settings.auth_token:
        settings.auth_token = os.environ.get("NOVEL_RAG_TOKEN") or _random_token()
        os.environ["NOVEL_RAG_TOKEN"] = settings.auth_token

    app = build_app(settings=settings, db_path=args.db)
    paused = app.recover_tasks()

    httpd = serve(app, args.host, args.port)
    port = httpd.server_address[1]
    print(f"PORT:{port}", flush=True)
    if args.print_token:
        print(f"TOKEN:{settings.auth_token}", flush=True)
    if paused:
        print(f"PAUSED_TASKS:{json.dumps(paused)}", flush=True)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.db.close()
    return 0


def _random_token() -> str:
    import secrets

    return secrets.token_urlsafe(24)


if __name__ == "__main__":
    sys.exit(main())
