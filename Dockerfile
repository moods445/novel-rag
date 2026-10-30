# Web 部署镜像：Python 后端同时托管静态前端（同源，无 CORS、无第二进程）。
#
# 镜像**只在 GitHub Actions 里构建**（.github/workflows/docker.yml），
# 本地一律 `docker pull ghcr.io/moods445/novel-rag:latest`，不要 `docker build`。
#
# 运行：
#   docker run -d -p 8000:8000 -v novelrag-data:/data \
#     -e NOVEL_RAG_DISABLE_AUTH=1 ghcr.io/moods445/novel-rag:latest
#   浏览器打开 http://127.0.0.1:8000/
FROM python:3.12-slim AS runtime

# embed = 真·BGE 向量检索（onnxruntime，约 200MB）；ner = jieba 人名识别。
# 想减小镜像：--build-arg PIP_EXTRAS=ner
ARG PIP_EXTRAS="embed,ner"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NOVEL_RAG_HOME=/data \
    NOVEL_RAG_LOG_DIR=/data/logs \
    NOVEL_RAG_WEB_ROOT=/app/web

WORKDIR /app

# ---------------------------------------------------------------- 依赖层
# 只有 pyproject.toml 参与这一层的哈希：改 backend/novel_rag 或 desktop/web
# 都不会触发重装依赖（onnxruntime 等约 200MB，是整次构建最贵的一步）。
COPY backend/pyproject.toml /app/backend/pyproject.toml
RUN python -c "import os, pathlib, tomllib; \
p = tomllib.loads(pathlib.Path('/app/backend/pyproject.toml').read_text())['project']; \
ex = [e for e in os.environ.get('PIP_EXTRAS', '').split(',') if e]; \
print(*p['dependencies'], *[d for e in ex for d in p['optional-dependencies'][e]], sep='\n')" > /tmp/requirements.txt \
 && python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt

# ---------------------------------------------------------------- 前端 vendor 层
# foliate-js 不入库，构建期拉取（要有外网）；只留运行时需要的文件。
# 只依赖 prepare_vendor.py 本身，改前端代码不会重新下载。
COPY desktop/scripts/prepare_vendor.py /app/desktop/scripts/prepare_vendor.py
RUN python /app/desktop/scripts/prepare_vendor.py

# ---------------------------------------------------------------- 源码层
# 依赖已经装好，这里只装本地包（--no-deps）再组装静态目录：
# 日常改代码只重建这一层。
COPY backend/novel_rag /app/backend/novel_rag
COPY desktop/web /app/desktop/web
RUN python -m pip install --no-cache-dir --no-deps /app/backend \
 && mkdir -p /app/web \
 && cp -a /app/desktop/web/. /app/web/ \
 && rm -rf /app/desktop /app/backend

# ---------------------------------------------------------------- 运行用户
# 随机端口 + 单向 token 的原始设定只适用于桌面壳；Web 部署固定 8000 端口，
# 数据全部落在 /data（挂卷），进程以非 root 跑。
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data \
 && chown -R app:app /data /app
USER app

EXPOSE 8000
VOLUME ["/data"]

# 配了 token 就带上（/api/health 同样要鉴权）；没配则裸探活
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os, urllib.request; t = os.environ.get('NOVEL_RAG_TOKEN', ''); req = urllib.request.Request('http://127.0.0.1:8000/api/health', headers={'Authorization': 'Bearer ' + t} if t else {}); urllib.request.urlopen(req, timeout=4)"

CMD ["python", "-m", "novel_rag", "--host", "0.0.0.0", "--port", "8000", "--home", "/data"]
