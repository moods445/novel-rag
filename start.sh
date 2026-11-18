#!/usr/bin/env bash
# 本地阻塞式启动（前台运行，Ctrl-C 停止）：监听局域网，同源托管前端。
#   ./start.sh          # http://<局域网IP>:8000/
#   ./start.sh 9000     # 换端口
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8000}"

# foliate-js 是构建期拉取的（GitHub 原始码，MIT），缺失时先补上
if [ ! -d desktop/web/vendor/foliate-js ]; then
  echo "vendor 缺失，先拉取 foliate-js ..."
  python3 desktop/scripts/prepare_vendor.py
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "启动中：http://${IP:-127.0.0.1}:${PORT}/   (Ctrl-C 停止)"

# LD_LIBRARY_PATH 若被外层工具注入（例如 Trae 终端），会遮蔽系统 libsqlite3 导致导入失败
exec env -u LD_LIBRARY_PATH \
  PYTHONPATH=backend \
  NOVEL_RAG_WEB_ROOT=desktop/web \
  NOVEL_RAG_DISABLE_AUTH=1 \
  python3 -m novel_rag --host 0.0.0.0 --port "$PORT" --home "$PWD/.novel-rag"
