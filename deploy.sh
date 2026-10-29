#!/usr/bin/env bash
#
# 一键部署 novel-rag（Docker Compose）。
# 镜像只在 GitHub Actions 里构建并推到 ghcr.io，这个脚本只 pull，不 build。
#
# 用法：
#   ./deploy.sh                          拉最新镜像、后台启动、等健康检查通过
#   ./deploy.sh --port 9000              换宿主端口
#   ./deploy.sh --auth                   开启鉴权（没给 --token 就随机生成并打印）
#   ./deploy.sh --token mypass --auth    指定口令，访问用 /#token=mypass
#   ./deploy.sh --data /srv/novel-rag    数据放宿主目录（bind mount）
#   ./deploy.sh --embed hash             跳过 100MB 向量模型下载，最省资源
#   ./deploy.sh status | logs | restart | down | purge
#
# 参数会写进同目录的 .env（compose 自动读），下次直接 ./deploy.sh 沿用上次设置。
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"
SERVICE="novel-rag"
DEFAULT_IMAGE="ghcr.io/moods445/novel-rag:latest"
DEFAULT_DATA="novel-rag-data"

# ---------------------------------------------------------------- 输出
info() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf '警告: %s\n' "$*" >&2; }
die() { printf '错误: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
用法: ./deploy.sh [动作] [选项]

动作（默认 up）:
  up         拉取镜像并后台启动，等健康检查通过（默认）
  restart    重启容器
  status     看容器与健康状态
  logs       跟随日志（Ctrl-C 退出）
  down       停止并删除容器（数据卷保留）
  purge      停止并删除容器 + 数据卷

选项:
  -p, --port N       宿主端口，默认 8000
  -t, --token S      访问口令（隐含开启鉴权）
      --auth         开启鉴权；没给 --token 会随机生成并打印
      --no-auth      关闭鉴权（默认，仅限内网/反代后面）
  -d, --data PATH    数据目录，默认命名卷 novel-rag-data；给路径则 bind mount
      --embed MODE   向量检索：auto（默认）/ hash（最省资源）/ bge
      --image IMG    换镜像地址，默认 ghcr.io/moods445/novel-rag:latest
  -h, --help         显示这段帮助

例子:
  ./deploy.sh                       # 内网直接跑起来
  ./deploy.sh --auth --port 9000    # 开鉴权 + 换端口
  ./deploy.sh --data /srv/nr        # 数据放宿主目录
EOF
}

# ---------------------------------------------------------------- 参数
ACTION="up"
arg_port=""
arg_token=""
arg_auth=""          # 1 = 开启鉴权 / 0 = 免鉴权 / 空 = 沿用 .env
arg_data=""
arg_embed=""
arg_image=""

while [ $# -gt 0 ]; do
  case "$1" in
    up|down|restart|logs|status|purge) ACTION="$1"; shift ;;
    -p|--port)  arg_port="${2:-}";  shift 2 ;;
    -t|--token) arg_token="${2:-}"; arg_auth=1; shift 2 ;;
    --auth)     arg_auth=1; shift ;;
    --no-auth)  arg_auth=0; shift ;;
    -d|--data)  arg_data="${2:-}"; shift 2 ;;
    --embed)    arg_embed="${2:-}"; shift 2 ;;
    --image)    arg_image="${2:-}"; shift 2 ;;
    -h|--help)  usage; exit 0 ;;
    *) die "不认识的参数：$1（--help 看用法）" ;;
  esac
done

# ---------------------------------------------------------------- 环境自检
command -v docker >/dev/null 2>&1 || die "没装 docker"
docker info >/dev/null 2>&1 || die "docker 守护进程没起来（或当前用户没有权限）"
[ -f "$COMPOSE_FILE" ] || die "找不到 $COMPOSE_FILE（请在仓库根目录运行）"

if docker compose version >/dev/null 2>&1; then
  compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
  compose() { docker-compose "$@"; }
else
  die "缺少 docker compose（v2 或 docker-compose 都行）"
fi

# 纪律：镜像一律来自 CI，禁止本地构建
if grep -qE '^[[:space:]]*build:' "$COMPOSE_FILE"; then
  die "$COMPOSE_FILE 里出现了 build:，本项目禁止本地构建镜像"
fi

env_get() { # key -> .env 里的值（没有就空）
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | head -n1 | sed -e 's/^"//' -e 's/"$//'
}

gen_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 12
  else
    od -An -tx1 -N12 /dev/urandom | tr -d ' \n'
  fi
}

# 参数 > .env > 默认值
PORT="${arg_port:-$(env_get NOVEL_RAG_PORT)}";             PORT="${PORT:-8000}"
TOKEN="${arg_token:-$(env_get NOVEL_RAG_TOKEN)}"
DATA="${arg_data:-$(env_get NOVEL_RAG_DATA)}";             DATA="${DATA:-$DEFAULT_DATA}"
EMBED="${arg_embed:-$(env_get NOVEL_RAG_EMBED_BACKEND)}";  EMBED="${EMBED:-auto}"
IMAGE="${arg_image:-$(env_get NOVEL_RAG_IMAGE)}";          IMAGE="${IMAGE:-$DEFAULT_IMAGE}"

case "$arg_auth" in
  1) DISABLE_AUTH=0 ;;
  0) DISABLE_AUTH=1 ;;
  *) DISABLE_AUTH="$(env_get NOVEL_RAG_DISABLE_AUTH)"; DISABLE_AUTH="${DISABLE_AUTH:-1}" ;;
esac

case "$PORT" in
  ''|*[!0-9]*) die "端口必须是数字：$PORT" ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  die "端口超出范围：$PORT"
fi

case "$EMBED" in
  auto|hash|bge) ;;
  *) die "--embed 只能是 auto | hash | bge" ;;
esac

if [ "$DISABLE_AUTH" = "0" ] && [ -z "$TOKEN" ]; then
  TOKEN="$(gen_token)"
  [ -n "$TOKEN" ] || die "生成随机口令失败，请用 --token 指定"
  info "已生成访问口令：$TOKEN"
fi

# bind mount 的数据目录先建好（容器内是 uid 10001）
case "$DATA" in
  /*|.*|*/*)
    case "$DATA" in
      /*) DATA_ABS="$DATA" ;;
      *)  DATA_ABS="$(pwd)/${DATA#./}" ;;
    esac
    mkdir -p "$DATA_ABS" || die "建不了数据目录：$DATA_ABS"
    if [ "$(id -u)" = "0" ]; then
      chown -R 10001:10001 "$DATA_ABS" 2>/dev/null || true
    else
      warn "bind mount 的数据目录由容器内 uid 10001 写入，若启动报权限错误：sudo chown -R 10001:10001 $DATA_ABS"
    fi
    DATA="$DATA_ABS"
    ;;
esac

write_env() {
  umask 077
  cat > "$ENV_FILE" <<EOF
# 由 deploy.sh 生成；改这里等价于改 deploy.sh 的参数
NOVEL_RAG_IMAGE=$IMAGE
NOVEL_RAG_PORT=$PORT
NOVEL_RAG_DATA=$DATA
NOVEL_RAG_DISABLE_AUTH=$DISABLE_AUTH
NOVEL_RAG_TOKEN=$TOKEN
NOVEL_RAG_EMBED_BACKEND=$EMBED
EOF
  umask 022
}

# shell 里残留的同名变量会盖掉 .env，交给 compose 之前先清掉
clear_env() {
  unset NOVEL_RAG_IMAGE NOVEL_RAG_PORT NOVEL_RAG_DATA \
        NOVEL_RAG_DISABLE_AUTH NOVEL_RAG_TOKEN NOVEL_RAG_EMBED_BACKEND \
        NOVEL_RAG_CONTAINER 2>/dev/null || true
}

lan_ip() {
  hostname -I 2>/dev/null | awk '{print $1}'
}

print_url() {
  local ip
  ip="$(lan_ip || true)"
  info ""
  info "访问地址："
  if [ "$DISABLE_AUTH" = "0" ]; then
    info "  本机   http://127.0.0.1:$PORT/#token=$TOKEN"
    if [ -n "$ip" ]; then info "  局域网 http://$ip:$PORT/#token=$TOKEN"; fi
  else
    info "  本机   http://127.0.0.1:$PORT/"
    if [ -n "$ip" ]; then info "  局域网 http://$ip:$PORT/"; fi
    info ""
    info "（当前免鉴权，只适合内网或反代后面；要口令就 ./deploy.sh --auth）"
  fi
  info ""
  info "数据：$DATA"
  info "日志：./deploy.sh logs"
  info "停止：./deploy.sh down"
}

wait_healthy() {
  local cid status i
  cid="$(compose ps -q "$SERVICE" 2>/dev/null || true)"
  if [ -z "$cid" ]; then
    compose logs --tail 40 "$SERVICE" || true
    die "容器没起来"
  fi

  status="unknown"
  for i in $(seq 1 45); do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
    case "$status" in
      healthy)
        info "健康检查通过"
        return 0
        ;;
      exited|dead)
        break
        ;;
      running)
        # 没有 HEALTHCHECK 的镜像：跑起来几秒就当成功
        if [ "$i" -ge 3 ]; then
          info "容器已运行"
          return 0
        fi
        ;;
    esac
    sleep 2
  done

  warn "容器 90s 内没有变成 healthy（当前：$status）"
  compose logs --tail 40 "$SERVICE" || true
  die "启动失败"
}

do_up() {
  step "拉取镜像 $IMAGE（只 pull，不 build）"
  if ! compose pull "$SERVICE"; then
    die "拉镜像失败；仓库是私有包的话先登录：docker login ghcr.io -u <用户名>"
  fi

  step "启动容器"
  compose up -d --remove-orphans

  step "等待健康检查"
  wait_healthy

  print_url
}

clear_env

case "$ACTION" in
  up)
    write_env
    do_up
    ;;
  restart)
    write_env
    step "重启容器"
    compose restart "$SERVICE"
    wait_healthy
    print_url
    ;;
  status)
    compose ps
    cid="$(compose ps -q "$SERVICE" 2>/dev/null || true)"
    if [ -n "$cid" ]; then
      info ""
      info "health: $(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
    fi
    ;;
  logs)
    compose logs -f --tail 200 "$SERVICE"
    ;;
  down)
    step "停止并删除容器（数据卷保留）"
    compose down
    info "数据还在：$DATA"
    info "要连数据一起删：./deploy.sh purge"
    ;;
  purge)
    step "停止、删除容器并删除数据卷"
    compose down -v
    case "$DATA" in
      /*) info "bind mount 目录不会自动删，需要的话手动 rm -rf $DATA" ;;
      *)  info "数据卷已删除：$DATA" ;;
    esac
    ;;
esac
