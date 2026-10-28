# 01 · 技术栈与工程结构

## 一句话概括

后端是 **Python 3.10+ / FastAPI 单体**（147 个文件、61,639 行），前端是 **React 19 + TypeScript 5.9 + Vite 8 SPA**（160 个文件、43,753 行），桌面分发用 **Tauri 2** 把 Web 产物与 PyInstaller 打出的 Python sidecar 装进同一个安装包。

## 运行时基线

| 项 | 值 | 来源 |
| --- | --- | --- |
| 产品版本 | `0.85.0` | `frontend/package.json`、`backend/pyproject.toml`、`frontend/src-tauri/tauri.conf.json` |
| 后端包名 | `ai-reader-v2-backend` | `backend/pyproject.toml:2` |
| Python 约束 | `>=3.10`，ruff target `py310` | `backend/pyproject.toml:6,56` |
| 前端包管理器 | npm（`package-lock.json`） | `frontend/` |
| Node 基线 | 官方 README 标注 22+ | `README.md` |
| 桌面壳 | Tauri 2，identifier `cc.ai-reader.desktop`，productName `AI Reader` | `frontend/src-tauri/tauri.conf.json` |
| 许可证 | AGPL v3 | `README.md` |
| 测试 | 官方宣称 1764 后端 / 112 前端 | `README.md:105` |

## 后端依赖分层

`backend/pyproject.toml` 的依赖分成三层，这是理解打包行为的关键。

### 核心运行时（默认安装）

| 包 | 用途 | 主要使用点 |
| --- | --- | --- |
| `fastapi` + `uvicorn[standard]` | HTTP / WebSocket 服务 | `src/api/main.py`、各 `routes/*` |
| `aiosqlite` | 异步 SQLite 访问 | `src/db/sqlite_db.py` 与全部 store |
| `pydantic>=2` | 全部领域模型 | `src/models/*` |
| `httpx` | LLM 与 Ollama API 调用 | `src/infra/*_client.py` |
| `websockets` | WS 运行时 | `src/api/websocket/*` |
| `python-multipart` | 文件上传 | `routes/novels.py` |
| `pillow` / `opensimplex` | 图像与噪声地形生成 | `map_layout_service.py`、`utils/spatial_*` |
| `jieba` | 中文分词、人名发现 | `extraction/entity_pre_scanner.py` |
| `keyring` | 系统钥匙串存 API key | `src/infra/secret_store.py` |
| `python-docx` / `openpyxl` / `reportlab` / `matplotlib` | 导出 docx/xlsx/pdf/图表 | `services/export_*`、`series_bible_renderer.py` |
| `anthropic` | Anthropic 原生客户端 | `src/infra/anthropic_client.py` |
| `scipy` / `numpy` / `networkx` | 图算法、布局、拓扑指标 | `geo_skills/edmonds_resolver.py`、`utils/topology_metrics.py` |
| `python-louvain` | 社区发现（Louvain）；`src/` 内无调用，实际用在 `backend/scripts/graphrag_style_baseline.py`（`community_louvain.best_partition`） | `pyproject.toml:24` |

### 可选 extra（`issue #80` 拆分）

| extra | 内容 | 说明 |
| --- | --- | --- |
| `embed` | `chromadb` | 桌面 sidecar 用，仅带 ONNX MiniLM 回退向量，不含 torch |
| `embed-torch` | `chromadb` + `sentence-transformers` | 本地开发完整语义检索（BGE 中文模型，数 GB） |

未安装 extra 时 `embedding_service._import_chromadb()` 抛出可操作的中文提示；RAG 会自动跳过语义召回，其余路径仍可用（见 [07](07-query-and-rag.md)）。

### 开发组

`pytest` + `pytest-asyncio`（`asyncio_mode = "strict"`）、`ruff`、`pyinstaller`、`opencc`（简繁转换，测试与数据清洗用）。

## 前端依赖分层

### UI 与状态

| 包 | 用途 |
| --- | --- |
| `react@19` / `react-dom@19` | 视图层 |
| `react-router-dom@7` | 路由（`src/app/router.tsx`） |
| `zustand@5` | 全部客户端状态（`src/stores/*`，20 个文件含测试） |
| `radix-ui` / `lucide-react` | 无头组件与图标 |
| `tailwindcss@4` + `@tailwindcss/vite` | 样式；无 PostCSS 配置文件 |
| `class-variance-authority` / `clsx` / `tailwind-merge` | 组件变体与类名合并 |
| `@tanstack/react-virtual` | 长列表虚拟化（章节、时间线） |

### 可视化

| 包 | 用途 |
| --- | --- |
| `react-force-graph-2d` | 人物关系力导向图（`GraphPage.tsx`） |
| `leaflet` + `react-leaflet@5` | 真实地理底图 |
| `d3-delaunay` / `d3-shape` / `d3-drag` / `d3-selection` / `d3-transition` / `d3-zoom` | 虚构地图的轮廓、领土、交互 |
| `roughjs` | 手绘风地形/海岸线笔触 |
| `pako` | `.air` / `json.gz` 解压 |
| `react-markdown` | 问答与百科正文渲染 |

### Tauri 侧

`@tauri-apps/api` + `plugin-shell`（sidecar 启停）+ `plugin-store`（本地设置）+ `plugin-dialog` / `plugin-notification`；`withGlobalTauri: true` 使 `window.__TAURI__` 可直接被前端探测。

## 两个 Tauri 工程

仓库里有两套 Rust 壳，**只有一套是 v0.85.0 的发布链路**。

| 维度 | `frontend/src-tauri/`（生效） | `src-tauri/`（遗留） |
| --- | --- | --- |
| productName | `AI Reader` | `AI Reader V2` |
| version | `0.85.0` | — |
| `build.beforeBuildCommand` | `npm run build`（在 frontend 内） | `cd ../frontend && npm run build` |
| `externalBin` | `binaries/ai-reader-sidecar` | `binaries/ai-reader-backend` |
| resources | `resources/novels/**/*`、`resources/geo/zh_wiki_geo.sqlite`、`zh_geonames.tsv` | 仅图标与二进制 |
| 文件关联 | `.air`（`application/x-air+gzip`） | — |
| 更新器 | 注释中出现 GitHub release endpoint 配置 | 同上 |
| CSP | `connect-src` 放行 `localhost:*` / `ws://localhost:*` / CartoCDN 底图 | 较宽松 |
| capabilities | `core:default` + `store/dialog/notification`（`capabilities/default.json`） | — |

判定依据：`frontend/package.json` 的 `build:desktop = tauri build` 在 frontend 目录执行，Tauri CLI 默认读取 `src-tauri/`，即 `frontend/src-tauri/`。根目录那份是早期结构，sidecar 名与资源清单都已过时。

### sidecar 打包

`backend/build_sidecar.py` + `backend/ai-reader-sidecar.spec` 用 PyInstaller 产出 `ai-reader-sidecar`，再被 Tauri 作为 `externalBin` 随包安装；地理数据**不打进 onefile**，而是作为 App resource 通过 `AI_READER_GEO_DIR` 传给子进程（`frontend/src-tauri/src/lib.rs:85-93` 有明确注释：避免每次启动把 ~30MB SQLite 解压到临时目录）。

## 工程约定

### 后端

- 目录职责严格分层：`api` 只做参数解析与响应，`services` 编排，`extraction` 纯抽取/校验，`db` 只碰 SQL，`infra` 只做外部适配。
- `src/api/main.py` 里所有 router 显式 `include_router`（23 个 REST 模块 → 25 次注册，`chapters` / `annotations` 各暴露两个 router；另有 2 个 WS router），没有自动发现。
- ruff 只开 `E4/E7/E9/F/I/UP/B/SIM/RUF`，显式忽略 `RUF001-003`（中文全角标点）与 `SIM102/105`，理由写在 `pyproject.toml:62-76`。
- 测试用 `pytest`，`asyncio_mode=strict`，`regression` marker 依赖 `EBOOK_SAMPLE_DIR` 金标数据。

### 前端

- 脚本：`dev` / `build`（`tsc -b && vite build`）/ `lint`（eslint 9 flat config）/ `preview` / `build:demo`（demo 模式产出到独立落地页目录）/ `tauri` / `build:desktop`。
- dev server 固定 `5173`，`/api` 代理到 `http://localhost:8000`、`/ws` 代理到 `ws://localhost:8000`（`vite.config.ts:83-90`），因此 Web 模式无需 token。
- 构建期注入版本号与 demo 标志；`TAURI_ENV_PLATFORM` 影响 target 与 HMR 配置。
- 测试：`vitest` + `@testing-library/react` + `jsdom`；现有测试集中在 store 与 `GraphPage`。
