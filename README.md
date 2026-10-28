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

## 模型准备

三个"模型"各管一件事，都可以在界面（HTTP 接口）里切换：

| 用途 | 可选项 | 说明 |
|---|---|---|
| 人名识别 | `auto`(jieba∪正则) / `jieba` / `regex` | jieba 词性标注 `nr`，CPU、词典约 5MB；不装则自动退回正则 |
| 章节识别 | `auto`(规则+LLM兜底) / `rule` / `llm` | 规则命中率不足时才调 LLM，也可强制总是走 LLM |
| 语义检索 | `bge`(BGE-small-zh-v1.5) / `hash` / `auto` | ONNX + CPU；缺失时自动后台下载 |
| 推理 | opencode 配置里的任意 provider | 默认读 `~/.config/opencode/opencode.json` |

```bash
# 手动预下载 embedding（也可由后端启动时自动下载）
python3 backend/scripts/fetch_embedding_model.py --variant fp32
# 国内镜像
HF_ENDPOINT=https://hf-mirror.com python3 backend/scripts/fetch_embedding_model.py
```

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET/POST | `/api/books` | 书架 / 导入 |
| GET | `/api/books/{id}/chapters` | 目录（含章节指纹与 spine） |
| GET | `/api/books/{id}/epub` | 交给 foliate-js 渲染的 EPUB |
| GET | `/api/books/{id}/coverage` | 分析覆盖度（部分可用语义） |
| GET/PUT | `/api/books/{id}/progress` | 阅读进度与防剧透开关 |
| GET | `/api/books/{id}/graph?as_of_seq=` | 关系图谱（物化到指定 seq） |
| GET | `/api/books/{id}/timeline` | 事件时间线 |
| GET | `/api/books/{id}/entity?name=` | 人物档案 |
| POST | `/api/analysis/{id}/start\|pause\|resume` | 任务状态机（start 可带 `{"max_chapters":3}` 试跑） |
| POST | `/api/analysis/{id}/retry` | 单章重跑 `{"chapter_idx":2}` |
| POST | `/api/ask` | 问答（唯一消费入口） |
| GET/PUT/DELETE | `/api/settings` | 切换 NER / 分章 / embedding / LLM（PUT 后组件热重建，无需重启） |
| GET | `/api/models` | 可选项清单（含各模型是否已安装） |
| GET/POST | `/api/models/embedding/download` | 模型下载状态 / 触发下载 |
| PUT | `/api/books/{id}/chapters/{idx}` | 人工修改章节标题 |
| POST | `/api/books/{id}/resplit` | 用正则重新分章（会作废旧知识，返回失效清单） |
| GET | `/api/books/{id}/epub` | 生成 EPUB 供 foliate-js 渲染 |
| GET | `/api/books/{id}/audit` | 归一裁决与被拒事实审计 |

## 配置（环境变量）

| 变量 | 说明 |
|---|---|
| `NOVEL_RAG_HOME` | 数据目录，默认 `~/.novel-rag` |
| `NOVEL_RAG_TOKEN` | Bearer token，空则随机生成并在启动时打印 |
| `NOVEL_RAG_LLM_PROVIDER` / `NOVEL_RAG_LLM_MODEL` | 从 opencode 配置解析 provider，如 `magpie` / `qnaigc/qiniu/conatus-free` |
| `NOVEL_RAG_OPENCODE_CONFIG` | opencode 配置文件路径（默认 `~/.config/opencode/opencode.json`） |
| `NOVEL_RAG_EXTRACT_{BASE_URL,API_KEY,MODEL}` | 覆盖式直连（优先级高于 opencode） |
| `NOVEL_RAG_QUERY_{BASE_URL,API_KEY,MODEL}` | 同上，用于查询 |
| `NOVEL_RAG_NER_BACKEND` | `auto` / `jieba` / `regex` |
| `NOVEL_RAG_SPLIT_BACKEND` | `auto` / `rule` / `llm` |
| `NOVEL_RAG_EMBED_BACKEND` / `_DIR` / `_VARIANT` | `auto` / `bge` / `hash`，模型目录，`fp32`/`int8` |
| `NOVEL_RAG_LLM_TIMEOUT` | 抽取超时（默认 240s），查询单独 120s |
| `NOVEL_RAG_CONTEXT_BUDGET` | ContextCompiler 上下文预算（字符） |

未配置 LLM 时整条链路仍可跑通：抽取退回启发式（`source=heuristic`），
问答退回抽取式摘录（`degraded=extractive`）。

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
