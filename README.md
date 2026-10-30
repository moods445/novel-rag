# novel-rag

小说专用的 AI 阅读器：本地优先的「小说 → 可溯源知识库」。

功能架构与 24 项设计决策见 [docs/architecture.md](docs/architecture.md)。

## 目录

```
docs/
  architecture.md        收敛后的功能架构（决策表 + 六平面 + 契约 + 风险）
  summary.md             RAG 路线调研
  gihub同类型项目.md      竞品基线
  ai-reader-v2/          知识平面参考（Tauri + Python sidecar）
  anx-reader/            阅读平面参考（Flutter + foliate-js）
backend/
  novel_rag/
    ingest/    P0 摄入：编码探测、章节识别（规则/LLM）、seq 索引、TXT→EPUB、重新分章
    extract/   P1 抽取：人名识别(ner)、上下文、分段事实抽取、校验、归一+裁决、区间边、状态机
    store/     P2 存储：事实层读写、FTS5 + 向量索引
    retrieve/  P3 检索：三路召回 → 融合 → 双闸门 → ★ContextCompiler
    qa/        P4 消费：问答服务 + 5 个只读工具
    api/       P5/P6：本地 HTTP 接口（Bearer token）+ 设置/模型管理
    llm/       provider 网关、opencode 配置解析、BGE embedding
    settings_store.py / embedding_models.py  界面可改设置、模型下载
  scripts/     fetch_embedding_model.py
  tests/       107 个 unittest
```

```
desktop/                   Tauri 2 壳 + 静态前端
  src-tauri/src/main.rs    sidecar 生命周期 + backend_info
  web/js/{api,app,reader,analysis,settings}.js
  scripts/prepare_vendor.py
```

## 桌面端

Tauri 2 薄壳 + Python sidecar。壳只负责拉起 sidecar、读 `PORT:`、注入一次性 token，
业务全在后端 HTTP 接口上。

| 平台 | 支持范围 | 产物 |
|---|---|---|
| macOS | Intel，12.0+ | `novelrag-macos-x86_64.zip`（内含 `NovelRag.app`） |
| Windows | x64，免安装 | `novelrag-windows-x64.zip`（`NovelRag.exe` + sidecar） |

**本地不打包**，一律走 GitHub Actions（`.github/workflows/build-desktop.yml`）：

```bash
git tag v0.1.0 && git push origin v0.1.0     # 自动构建并发布 Release
# 或手动触发
gh workflow run build-desktop.yml
```

前端是纯静态页面（无 Vite/React），foliate-js 由 `desktop/scripts/prepare_vendor.py`
在构建前拉取（MIT）。本地调前端不需要 Rust：

```bash
python3 desktop/scripts/prepare_vendor.py
cd backend && NOVEL_RAG_TOKEN=dev python3 -m novel_rag --port 8801 &
python3 -m http.server 8088 --directory desktop/web
# 浏览器打开 http://127.0.0.1:8088/#port=8801&token=dev
```

## Web / Docker 部署

同一个容器里跑 Python 后端并**同源托管**静态前端（没有第二个 web server，也就没有 CORS）。
镜像**只在 GitHub Actions 上构建**（`.github/workflows/docker.yml` → `ghcr.io/moods445/novel-rag`），
本地禁止 `docker build`：

```bash
# 手动触发一次构建（推 master 涉及 backend/desktop/web/Dockerfile 时也会自动构建）
gh workflow run docker.yml
gh run watch

# 拉取（多架构：linux/amd64 + linux/arm64）
docker pull ghcr.io/moods445/novel-rag:latest

# 运行
docker run -d --name novel-rag -p 8000:8000 \
  -v novel-rag-data:/data \
  -e NOVEL_RAG_DISABLE_AUTH=1 \
  ghcr.io/moods445/novel-rag:latest
# 浏览器打开 http://127.0.0.1:8000/
```

**一键部署**（推荐）：`deploy.sh` 是**单文件部署**——把它单独拷到任何目录都能跑，
`docker-compose.yml` 和 `.env` 由它按参数现生成，然后拉镜像、起容器、等健康检查、打印访问地址：

```bash
./deploy.sh                          # 内网直接跑起来（免鉴权，8000 端口，数据在 ./data）
./deploy.sh --auth --port 9000       # 开鉴权（随机口令会打印出来）+ 换端口
./deploy.sh --data /srv/novel-rag    # 数据换个地方（默认就在脚本旁边的 ./data）
./deploy.sh --embed hash             # 跳过 100MB 向量模型下载
./deploy.sh status | logs | restart | down | purge

# 配问答模型（不配的话问答只返回原文摘录）
./deploy.sh --query-base-url https://api.openai.com/v1 \
            --query-api-key sk-xxx --query-model gpt-4o-mini

# 抽取想用另一个模型时再单独指定（不填则复用 --query-*）
./deploy.sh --query-base-url ... --query-model ... \
            --extract-base-url ... --extract-api-key ... --extract-model ...
```

执行后脚本同目录会有三样东西（都已 gitignore 或可随时重生成）：
`docker-compose.yml`（生成物，手改会被下次执行覆盖）、`.env`（参数，下次沿用）、
`data/`（数据库、日志、embedding 模型，删容器不丢；脚本会把目录建好并放宽给容器内 uid 10001 写）。

仓库里也带了一份内容相同的 `docker-compose.yml`，不用脚本时可以直接：

```bash
docker compose pull && docker compose up -d
```

| tag | 触发 |
|---|---|
| `latest` | 默认分支最新一次构建 |
| `master` / `pr-N` | 分支 / PR 构建（PR 不推送） |
| `sha-<短 hash>` | 每次提交，可回溯 |
| `v1.2.3` | 打 tag 时 |

| 环境变量 | 说明 |
|---|---|
| `NOVEL_RAG_HOME` | 数据目录（镜像内 `/data`，宿主默认挂到 `./data`） |
| `NOVEL_RAG_WEB_ROOT` | 静态前端目录（镜像内 `/app/web`，已内置） |
| `NOVEL_RAG_TOKEN` | 开启鉴权；访问时用 `http://<host>:8000/#token=<token>`，首次会记进 localStorage |
| `NOVEL_RAG_DISABLE_AUTH` | `1` = 不校验 token。**只在受信网络 / 反向代理后面用** |
| `NOVEL_RAG_EMBED_BACKEND` | `auto`（缺模型后台下载，约 100MB）\| `hash`（占位向量，最省资源） |
| `NOVEL_RAG_PROXY` | 下载模型 / 调 LLM 走的代理 |
| `NOVEL_RAG_QUERY_{BASE_URL,API_KEY,MODEL}` | 问答模型（OpenAI 兼容）。容器里没有桌面端 opencode 配置，**不配就只能返回原文摘录** |
| `NOVEL_RAG_EXTRACT_{BASE_URL,API_KEY,MODEL}` | 抽取模型；不配时建议与查询用同一个端点 |
| `NOVEL_RAG_LLM_TIMEOUT` / `_QUERY` | 抽取 / 问答请求超时（秒），默认 240 / 120 |
| `NOVEL_RAG_LOG_LEVEL` | 日志级别（页面「日志」读取 `/data/logs/backend.log`） |

镜像内置 `HEALTHCHECK`（探 `/api/health`），`docker ps` 能看到 healthy。
首次启动会后台下载 BGE 向量模型；想跳过就用 `NOVEL_RAG_EMBED_BACKEND=hash`。

界面侧栏新增两个页面：

- **向量**：按章节显示向量化进度（已向量化段数、完成章数、当前/下一章、逐章明细），
  支持「继续构建」「暂停」「重建向量」。构建以数据库里已建好的段落为断点：
  服务重启或构建中断后会自动从断点续跑；暂停在章节边界生效（当前章节建完即停），
  暂停标记落库，重启后不会被自动续跑覆盖，点「继续构建」即可恢复。
- **日志**：直接查看容器内 `backend.log` 尾部（200–2000 行、级别着色、自动刷新）。

本地验证完记得删掉镜像和数据（不留在本机）：

```bash
./deploy.sh purge                                  # 删容器 + 数据
docker rmi ghcr.io/moods445/novel-rag:latest
rm -rf ./data                                      # 数据目录（默认在脚本旁边）
```

## 快速开始

零第三方依赖，Python ≥ 3.11 即可（依赖标准库 `sqlite3` 的 FTS5）。

```bash
cd backend
NOVEL_RAG_TOKEN=devtoken python3 -m novel_rag --port 8793
```

```bash
# 导入（导入后立即解锁，后台同时开始全量抽取）
curl -H 'Authorization: Bearer devtoken' -H 'Content-Type: application/json' \
  -d '{"path": "/path/to/novel.txt", "title": "书名"}' \
  http://127.0.0.1:8793/api/books

# 问答（progress_seq 与 spoiler_guard 控制防剧透）
curl -H 'Authorization: Bearer devtoken' -H 'Content-Type: application/json' \
  -d '{"book_id":"<id>","question":"林凡是谁","progress_seq":99,"spoiler_guard":false}' \
  http://127.0.0.1:8793/api/ask
```

## 阅读页（键盘 / 刷新 / 导入进度）

**键盘**（焦点在正文或页面上都行，点一下正文更稳）：

| 按键 | 行为 |
|---|---|
| `←` `→` / `PgUp` `PgDn` / `空格` | 翻一屏，到章尾自动交接下一章 |
| `↑` `↓` | 滚动约 1/6 屏，跨章连续 |

连按不会丢键（前端把每次翻屏串行排队；foliate-js 内部换章时有一把 100ms 的锁，
旧实现里锁住期间的按键会被静默丢掉）。焦点在输入框/下拉框里时不抢键。

**刷新页面是安全的**：书架、阅读位置（`progress_seq` + `progress_cfi`）、防剧透开关
都存在服务端 SQLite 里，刷新后会自动回到上次读的那本书和位置；后台建知识库的任务跑在
服务端线程上，刷新/关页面都不影响，回来继续看覆盖度即可。

**导入进度**：导入时书架上方有一条进度条，分四段 —— 读文件 → base64 编码 →
上传（真实百分比，来自 `XMLHttpRequest.upload`）→ 服务端分章（不确定态动画，
这一步是一次性同步解析，拿不到真实百分比）。完成/失败都会写明章节数与原因。

> 控制台里的 `An iframe which has both allow-scripts and allow-same-origin for its
> sandbox attribute can escape its sandboxing.` 是 foliate-js 给正文 iframe 设的
> sandbox（`desktop/web/vendor/foliate-js/paginator.js`）触发的浏览器提示，**不是错误、
> 不影响键盘与滚动**：`allow-scripts` 是 WebKit 下 iframe 内事件能工作的前提，
> `allow-same-origin` 是注入主题、取选区、绑键盘的前提，两者都不能去掉。

## 模型准备

三个"模型"各管一件事，都可以在界面（HTTP 接口）里切换：

| 用途 | 可选项 | 说明 |
|---|---|---|
| 人名识别 | `auto`(jieba∪正则) / `jieba` / `regex` | jieba 词性标注 `nr`，CPU、词典约 5MB；不装则自动退回正则 |
| 章节识别 | `auto`(规则+LLM兜底) / `rule` / `llm` | 规则命中率不足时才调 LLM，也可强制总是走 LLM |
| 语义检索 | `bge`(BGE-small-zh-v1.5) / `hash` / `auto` | ONNX + CPU；缺失时自动后台下载 |
| 推理 | opencode 配置 + 界面添加的任意 provider | 默认读 `~/.config/opencode/opencode.json`，也可在设置页手动添加 |

模型下载默认先直连 HuggingFace，失败后自动切换国内镜像（`hf-mirror.com`）；
下载源与网络代理都能在设置页修改，代理同时作用于 LLM 请求。

```bash
# 手动预下载 embedding（也可由后端启动时自动下载）
python3 backend/scripts/fetch_embedding_model.py --variant fp32
# 固定国内镜像
HF_ENDPOINT=https://hf-mirror.com python3 backend/scripts/fetch_embedding_model.py
# 走代理
python3 backend/scripts/fetch_embedding_model.py --proxy http://127.0.0.1:7890
```

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET/POST | `/api/books` | 书架 / 导入 |
| GET | `/api/books/{id}/chapters` | 目录（含章节指纹与 spine） |
| GET | `/api/books/{id}/epub` | 交给 foliate-js 渲染的 EPUB |
| GET | `/api/books/{id}/coverage` | 分析覆盖度（部分可用语义） |
| GET | `/api/books/{id}/vectors` | 向量化进度（按章节明细、当前/下一章、构建任务状态、暂停标记） |
| POST | `/api/books/{id}/vectors/continue` | 从断点继续构建（只补未完成章节） |
| POST | `/api/books/{id}/vectors/pause` | 暂停构建（当前章节完成后停止，可再继续） |
| POST | `/api/books/{id}/vectors/rebuild` | 后台整本重建向量（逐章推进） |
| GET | `/api/logs?source=backend\|sidecar&lines=500` | 读取日志尾部（1–2000 行） |
| GET/PUT | `/api/books/{id}/progress` | 阅读进度与防剧透开关 |
| GET | `/api/books/{id}/graph?as_of_seq=` | 关系图谱（物化到指定 seq） |
| GET | `/api/books/{id}/timeline` | 事件时间线 |
| GET | `/api/books/{id}/entity?name=` | 人物档案 |
| POST | `/api/analysis/{id}/start\|pause\|resume` | 任务状态机（start 可带 `{"max_chapters":3}` 试跑） |
| POST | `/api/analysis/{id}/retry` | 单章重跑 `{"chapter_idx":2}` |
| POST | `/api/ask` | 问答（唯一消费入口） |
| GET/PUT/DELETE | `/api/settings` | 切换 NER / 分章 / embedding / LLM（PUT 后组件热重建，无需重启） |
| GET | `/api/models` | 可选项清单（含各模型是否已安装） |
| GET/POST | `/api/models/embedding/download` | 模型下载状态 / 触发下载（可带 `endpoint`） |
| GET/POST | `/api/providers` | 合并后的 provider 清单 / 添加或更新自定义 provider |
| DELETE | `/api/providers/{name}` | 删除自定义 provider |
| PUT | `/api/books/{id}/chapters/{idx}` | 人工修改章节标题 |
| POST | `/api/books/{id}/resplit` | 用正则重新分章（会作废旧知识，返回失效清单） |
| GET | `/api/books/{id}/epub` | 生成 EPUB 供 foliate-js 渲染 |
| GET | `/api/books/{id}/audit` | 归一裁决与被拒事实审计 |

## 配置（环境变量）

| 变量 | 说明 |
|---|---|
| `NOVEL_RAG_HOME` | 数据目录，默认 `~/.novel-rag`（桌面端为 app_data 目录） |
| `NOVEL_RAG_LOG_DIR` | 日志目录，默认 `<home>/logs` |
| `NOVEL_RAG_LOG_LEVEL` | 日志级别，默认 `INFO` |
| `NOVEL_RAG_TOKEN` | Bearer token，空则随机生成并在启动时打印 |
| `NOVEL_RAG_LLM_PROVIDER` / `NOVEL_RAG_LLM_MODEL` | 从 opencode 配置解析 provider，如 `magpie` / `qnaigc/qiniu/conatus-free` |
| `NOVEL_RAG_OPENCODE_CONFIG` | opencode 配置文件路径（默认 `~/.config/opencode/opencode.json`） |
| `NOVEL_RAG_PROXY` | 网络代理（`http://host:port`），作用于模型下载与 LLM 请求 |
| `NOVEL_RAG_HF_ENDPOINT` / `HF_ENDPOINT` | 模型下载源；留空则官方直连失败后自动换国内镜像 |
| `NOVEL_RAG_CUSTOM_PROVIDERS` | 界面添加的 provider（JSON），一般由设置页维护 |
| `NOVEL_RAG_EXTRACT_{BASE_URL,API_KEY,MODEL}` | 覆盖式直连（优先级高于 opencode） |
| `NOVEL_RAG_QUERY_{BASE_URL,API_KEY,MODEL}` | 同上，用于查询 |
| `NOVEL_RAG_NER_BACKEND` | `auto` / `jieba` / `regex` |
| `NOVEL_RAG_SPLIT_BACKEND` | `auto` / `rule` / `llm` |
| `NOVEL_RAG_EMBED_BACKEND` / `_DIR` / `_VARIANT` | `auto` / `bge` / `hash`，模型目录，`fp32`/`int8` |
| `NOVEL_RAG_LLM_TIMEOUT` | 抽取超时（默认 240s），查询单独 120s |
| `NOVEL_RAG_CONTEXT_BUDGET` | ContextCompiler 上下文预算（字符） |

未配置 LLM 时整条链路仍可跑通：抽取退回启发式（`source=heuristic`），
问答退回抽取式摘录（`degraded=extractive`）。

## 日志

| 文件 | 写入方 | 内容 |
|---|---|---|
| `<home>/logs/backend.log` | Python（轮转 5MB×3） | 启动参数、监听端口、分析任务进度、API 500 堆栈 |
| `<home>/logs/sidecar.log` | Rust 壳 | sidecar 的 stdout/stderr 原文（含启动期崩溃） |

桌面端默认位置：

- macOS：`~/Library/Application Support/com.novelrag.desktop/logs/`
- Windows：`%APPDATA%\com.novelrag.desktop\logs\`

> 两个文件都必要：Python 的日志要等 `setup_logging` 跑起来才存在，
> 参数解析失败这类**启动前**崩溃只会出现在 `sidecar.log` 里。

`GET /api/settings` 的 `logging` 字段会返回完整路径，界面左下角也会直接显示。

## 测试

```bash
cd backend && python3 -m unittest discover -s tests -t .
```

## 已知实现细节

- **LLM 抽取必须分段**：6000 字整章一次性抽取时，推理型模型会把 token 预算全烧在
  `reasoning_content` 里、`content` 返回空。实测 1800 字/段 + 3072 token 时 3/3 段成功，
  200 字以下的小章节则一次搞定。分段结果按人物/关系/事件去重合并。
- **JSON 容错**：支持 markdown 代码块、前后夹带说明、以及被 `max_tokens` 截断后补括号修复。
- **人名识别**：jieba `nr` 在《三国演义》上召回远超正则（曹操 934 / 孔明 831 / 玄德 685），
  再叠加「姓X名Y字Z」规则得到 77 组高精度别名对（刘备↔玄德），离线也能正确合并实体。
- **jieba 只在整本书上跑一次**，结果缓存在 `extensions` 表，暂停恢复不重算（606K 字约 42s）。

## 尚未实现

- 图形化的关系图谱（当前是列表 + 时间线；地图/势力仍是预留扩展槽）
- Windows ARM64 / macOS Apple Silicon 产物（矩阵里只跑 Intel 与 x64）
