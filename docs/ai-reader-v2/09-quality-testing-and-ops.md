# 09 · 质量、测试与运维

## 一句话概括

V2 的工程重心明显偏向**可验证性**：57 个顶层后台脚本（另有 `evolve/` 17 个、`legacy/` 3 个）做评测/消融/迁移，117 个 pytest 文件覆盖抽取、地理、并发、迁移，CI 在**不装任何重依赖的裸环境**下跑全量单测。导出与备份是"用户数据可迁移"的兜底。

## 测试体系

### 后端（`backend/tests/`，117 个测试文件）

| 关注面 | 代表文件 | 验证什么 |
| --- | --- | --- |
| 抽取正确性 | `test_evidence_span`、`test_recall_pass`、`test_scene_merge`、`test_relation_dimensions_extraction`、`test_alias_contextual_filter` | 证据锚定、补漏、场景并入、关系维度、别名上下文过滤 |
| 幻觉与命名 | `test_hallucination_reviewer`、`test_canonical_grounding`、`test_name_resolution_audit` | 真假人物判定、实体归一、审计 |
| 空间/地点语义 | `test_location_relation_semantics`、`test_map_geo_artifacts`、`test_map_geo_coords_cache`、`test_map_geo_failed`、`test_geo_artifact_column_alignment` | 层级/拓扑分类、地理产物列对齐、失败缓存 |
| 世界结构 | `test_world_structure_*`、`test_location_relation_semantics` | 图层、传送门、覆盖合并 |
| 并发与任务 | `test_windowed_concurrency`、`test_analysis_passes_api`、`test_source_pass_*` | 窗口化顺序、二审独立性、成本 |
| 存储与迁移 | `test_status_migration`、`test_analysis_pass_store`、`test_analysis_latest_stats` | 幂等迁移、状态归一、统计 |
| 上下文预算 | `test_context_overflow`、`test_source_pass_context` | 溢出缩短、开关关闭时 prompt 不变 |

### 测试基建的坑（值得记录）

`tests/conftest.py:1-45` 的注释本身就是一份事故报告：

- **必须在导入 `src.infra.config` 之前**设置 `AI_READER_DATA_DIR`，因为该模块在 import 时解析数据目录；否则测试会读写开发者本机的 `~/.ai-reader-v2/data.db`。本机能跑、CI 上全红（`unable to open database file`），`main` 从 2026-09-23 起因此挂了 8 个用例；
- 用 `setdefault` 而非直接赋值，让显式导出的环境变量仍能生效；
- 测试库直接由 `_SCHEMA_SQL` + 手工补的 `_MIGRATION_SQL` 建全量 schema，避免"迁移函数只在真实 init_db 里跑"导致的字段缺失。

这类注释在仓库里出现频率很高，是"踩过坑就写下来"的文化。

### 标记

`pyproject.toml:85-87` 只有一个自定义 marker：`regression` —— 依赖 `EBOOK_SAMPLE_DIR` 与金标基线的回归集，CI 默认不跑。

### 前端（19 个测试文件）

`vitest` + `@testing-library/react` + `jsdom`，共 19 个文件，集中在最易回归的地方：

| 文件 | 验证 |
| --- | --- |
| `stores/analysisStore.test.ts` | WS 代际隔离、重连、消息校验 |
| `stores/chatStore.test.ts` | 流式 token 拼接、状态、结束 |
| `stores/mapDataStore.test.ts` | 图层切换、缓存 |
| `stores/novelStore.test.ts` / `readingStore.test.ts` / `entityCardStore.test.ts` | 列表、进度、实体抽屉 |
| `pages/GraphPage.test.tsx` | 图谱渲染与筛选 |
| `api/demoNovelMap.test.ts` | Demo 数据映射 |
| `components/shared/UploadDialog.test.tsx` / `AppendChaptersDialog.test.tsx`、`components/entity-cards/EntityCardDrawer.test.tsx`、`desktop/NovelCard.test.tsx` | 关键交互组件 |
| `lib/*.test.ts`（7 个：`novelPaths`、`paragraphWindow`、`saveExport`、`spatialLabels`、`specialSpace`、`systemFaq`、`useAuthedAsset`） | 海岸线/领土/标签/分页/导出/FAQ 等纯函数 |

纯函数优先放 `lib/` 并配套测试，是前端可测性的主要手段——重型可视化组件本身几乎不测。

## CI 流水线

`.github/workflows/` 共 7 个：

| 工作流 | 触发 | 内容 |
| --- | --- | --- |
| `test.yml` | push/PR `main` | `secret-scan`（工作区 + 全历史）→ 后端 `uv sync`（**不装 embed extra**）→ `ruff check` → `pytest` → 前端 `npm ci` → `eslint` → `tsc -b` → `vitest run` |
| `build-desktop.yml` | `workflow_call`（**不是独立触发器**） | 可复用打包流水线：版本一致性校验（4 处版本号必须相同）→ PyInstaller 构建 sidecar → macOS/Windows 签名 → `tauri-action` 打包 → 按 `upload` 入参上传到 Release 或 artifact |
| `build-macos.yml` / `build-windows.yml` | `workflow_dispatch`（手动） | 以 `upload=artifact` 调用 `build-desktop.yml`，产出安装包供下载，不发布 |
| `release.yml` | `v*` 标签 | `create-release`（draft，`-(beta\|rc\|alpha)` → pre-release）→ 矩阵构建（仅 `aarch64-apple-darwin` + `x86_64-pc-windows-msvc`）→ `publish-release`（undraft）→ `mirror`（R2） |
| `mirror-downloads.yml` | `release:published` / `workflow_call` / `workflow_dispatch` | 把 tag 的 `*.dmg *.exe` 镜像到 Cloudflare R2 桶 `ai-reader-downloads` |
| `repo-hygiene.yml` | push `main` / 任意 PR | `scripts/check-repo-boundary.sh`：内部研究/试验产物不得出现在公开仓库 |

两个刻意的设计：

1. **裸环境跑测试**：CI 不装 `chromadb` / `sentence-transformers`，强制验证"没有重依赖时套件整体可过"。这直接对应 issue #80 的拆包决策，也防止开发者不小心把可选依赖变成硬依赖。
2. **密钥扫描进 CI**：`scripts/scan-secrets.sh --all` + `--history`，因为本项目要用户填 API key，仓库泄露风险高。

## 评测与实验脚本（`backend/scripts/`，顶层 57 个 .py）

这些脚本不是产品运行时的一部分，是**研究/质量工程的工具箱**。按用途分：

### 消融与对照

| 脚本 | 目的 |
| --- | --- |
| `ablation_study.py` | 端到端消融（各增强层开关对比） |
| `ablation_hierarchy.py` | 层级流水线各 skill 的贡献 |
| `m5_no_edmonds_ablation.py` | 去掉 Edmonds 解析的对照 |
| `pure_mwa_ablation.py` | 纯 MWA（多轮投票）基线 |
| `eval_relation_dimensions.py` | 关系维度增强的收益 |
| `zero_shot_baseline.py` / `single_shot_cot_baseline.py` / `stardust_cot_arm.py` / `stardust_voting_arm.py` | 不同 prompting 策略对照 |
| `graphrag_style_baseline.py` / `build_graphrag_comparison.py` | 与 GraphRAG 风格基线对比 |

### 质量度量

| 脚本 | 目的 |
| --- | --- |
| `smoke_extraction_quality.py` | 真实 LLM 端到端冒烟（单章全开关：抽取 → 校验 → 幻觉审查），产出填充率/证据定位率/成本报告 |
| `judge_extraction_faithfulness.py` | LLM-as-judge 忠实度打分 |
| `compute_iaa.py` | 人工标注一致性（IAA） |
| `quality_dashboard.py` / `quality_loop.py` | 质量看板与自动迭代闭环 |
| `measure_layout_quality.py` / `map_quality_audit.py` / `bench_terrain.py` | 地图布局与地形质量（`landmass_comparison.png` 是 `bench_terrain.py` 的产物，不是脚本） |
| `multi_seed_evaluate.py` / `bootstrap_ci.py` / `bootstrap_ci_result.json` | 多样本评估与置信区间 |
| `cross_llm_replication.py` + `cross_llm_replication_result.json` | 跨模型复现 |
| `verify_full_pipeline_metrics.py` | 全链路指标核对 |

### 可复现与审计

| 脚本 | 目的 |
| --- | --- |
| `repro_check.py` / `reproducibility_check.py` | 同一输入两次运行结果一致性（`fresh=True` 场景） |
| `trace_phase_determinism.py` | 阶段确定性追踪 |
| `audit_paper_numbers.py` | 论文数字与产物对账 |
| `audit_hardcoded_externals.py` | 硬编码外部依赖审计 |
| `probe_peer_suppression.py` / `probe_symbol_terrain_coupling.py` / `probe_terrain_field.py` | 针对性实验探针 |

### 数据与迁移

| 脚本 | 目的 |
| --- | --- |
| `generate_sample_data.py` / `synthesize_novel.py` | 造样例数据 |
| `bible_to_gold.py` / `build_shuihu_silver_sample.py` / `fill_sample_judgments.py` / `story_5_5_sample_180.py` | 金标/银标数据集构建 |
| `migrate_hierarchy_from_errata.py` / `migrate_phantom_parents.py` / `migrate_tier_inversions.py` | 历史数据修正迁移 |
| `rebuild_with_new_skills.py` / `hierarchy_iteration.py` / `version_refresh_trialrun.py` / `auto_resume_rerun.py` | 批量重跑与试跑 |
| `compile_prompts.py` | 把 `prompts/*.txt` 编译成打包用资产 |
| `bake_terrain_variant.py` | 地形变体预烘焙 |

`evolve/` 与 `legacy/` 子目录分别存放实验性技能演进与遗留脚本，与主流程隔离。

这些脚本共享同一套底座：直接 `get_connection()` 读真实库、复用 `geo_skills` / `extraction` 模块、输出 JSON 结果文件（如 `bootstrap_ci_result.json`）。**它们不保证向后兼容**——研究脚本的定位就是一次性证据，这点与产品代码的"旧 JSON 必须能反序列化"形成鲜明对比。

## 导出、备份与迁移

### 产品导出

两条**互不重叠**的导出路径，容易混淆，这里分开写：

| 能力 | 入口 | 支持格式 | 产物 |
| --- | --- | --- | --- |
| 分析数据包 | `GET /api/novels/{id}/export?format=…`（`export_import.py`） | `json`（默认）/ `air`（gzip JSON）/ `markdown`（逐章分析报告） | `export_service.export_novel()`；`.air` 格式版本 **6**，兼容读 1–6 |
| 系列圣经（单本书资料集） | `POST /api/novels/{id}/series-bible/export`（`series_bible.py`） | `markdown` / `docx` / `xlsx` / `pdf` | `series_bible_service.collect_data()` + `docx_renderer` / `xlsx_renderer` / `pdf_renderer` / `series_bible_renderer.render_markdown` |
| 会话导出 | `routes/chat.py` | Markdown | 单会话 / 全书所有会话 |
| 地图图片 | 前端 `saveExport.ts` → Rust `save_export` | PNG | 大文件不经 IPC |

`SeriesBibleRequest` 字段：`modules`（默认全部：characters/relations/locations/items/orgs/timeline）、`template`（`complete` / `author`）、`format`、`chapter_start`/`chapter_end`（章节范围后缀）、`export_all`（全量而非摘要，大书较慢）。`/series-bible/templates` 返回可选模板元信息。

**docx/xlsx/pdf 只存在于 series-bible 路由**，`/export` 路由不产出这些格式。`export_novel()` 会**预计算** graph / map / timeline / factions / encyclopedia / world_structure 一并打包（`_build_precomputed()`），因此 `.air` 在无后端环境也能被静态页面消费。若没有已分析章节，则跳过预计算并记 warning，不报错。

版本演进记录在 docstring 里：v5 新增 scenes/cost/quality/map_layouts/layer_layouts/conversations；v6 新增 `entity_overrides`（别名合并、拆分、改名、概念编辑、隐藏与改型）——理由写得很直白："不换机迁移则用户修正丢失"。

### 备份（整机数据）

`services/backup_service.py`：

- `export_all()` → `io.BytesIO`（全部小说 + 设置，不含 API key）；
- `preview_backup_import(data)` → 先给差异预览（哪些书新增/冲突），**再**由用户确认；
- `import_all(...)` → 按 id/hash 去重导入，可选覆盖。

`routes/backup.py` 对应三个端点。API key 始终不进备份包，恢复后需重新填写——这是安全边界。

### 数据导入

`POST /api/novels/import/preview` + `/confirm` 支持导入 `.air`：预览给出书目/章节数/冲突策略，确认后写库并重建缓存。桌面版另有 Rust 侧的 `preview_air_file` / `import_air_file`，把文件解到用户小说目录。`export_import.py` 与 `novels.py` 两个路由模块都涉及，前者管数据包，后者管原始 TXT。

## 可观测性

| 手段 | 位置 | 用途 |
| --- | --- | --- |
| 结构化日志 | 全后端 `logging.getLogger(__name__)` | 每章分析、每次 LLM 调用、每个后台任务都有日志 |
| LLM 调用明细 | `AI_READER_LLM_USAGE_LOG=/path.jsonl` | 阶段级 token/成本归因，默认关闭 |
| 成本记账 | `usage_events` 表 + `routes/usage.py` | 月度用量、预算、按阶段汇总 |
| 审计日志 | `routes/audit_log.py`（`GET /{novel_id}/audit-log`） | 幻觉判定等关键决策留痕 |
| 分析质量汇总 | `analysis_latest_stats` / `qualitySummary` | `is_truncated`、`segment_count`、失败类型占比 |
| sidecar 崩溃日志 | `~/.ai-reader-v2/sidecar-crash.log` | 桌面端启动失败诊断 |
| 版本 | `GET /api/health.version`、`frontend` 构建期注入 | 桌面端版本握手 |

## 版本与发布

- 四处版本号必须一致（`build-desktop.yml` 会做门禁，不一致直接失败）：`backend/pyproject.toml`、`frontend/package.json`、`frontend/src-tauri/tauri.conf.json`、`frontend/src-tauri/Cargo.toml`（当前均为 `0.85.0`），用 `scripts/bump-version.sh` 同步。
- 发布流程：`v*` tag → 创建 **draft** release → 矩阵构建（macOS ARM64 + Windows x64；Intel Mac 已放弃）→ 上传产物 → `publish-release`（undraft）→ `mirror` 把 `*.dmg *.exe` 推到 R2（官网 `/dl/<tag>/<file>` 依赖它）。
- tag 含 `beta`/`rc`/`alpha`（正则 `-(beta|rc|alpha)`）时标为 **pre-release**，走"体验版"渠道，与 stable Latest 分离。
- 桌面更新器指向 GitHub Release endpoint；`.air` 文件关联在安装包注册。

## 风险清单

| 风险 | 影响 | 现有缓解 |
| --- | --- | --- |
| `world_structures` 整文档 load-modify-save | 并发写 last-writer-wins | 后处理链强制串行；`apply_to_world_structure` 用本次运行快照而非 `load_latest` |
| 超长章节截断/分段 | 尾部事实缺失 | `is_truncated` / `segment_count` / `output_truncated` 上报前端 |
| 云端并发窗口 | 上下文落后 k−1 章 | 上限 3；本地 Ollama 强制串行 |
| 可选嵌入依赖缺失 | 语义召回静默降级 | 显式 warning + 前端提示；CI 裸环境守护 |
| 继承自研究阶段的脚本 | 与产品 schema 漂移 | 脚本不纳入产品路径，CI 不跑 |
| 两个 Tauri 工程并存 | 误改遗留配置 | 以 `frontend/src-tauri` 为准（文档已标注） |
| 超大模块 | 维护成本高 | 测试与注释密度高，但重构风险大 |

## 阅读建议

想快速建立心智模型，按这个顺序读代码：

1. `backend/src/models/chapter_fact.py` + `world_structure.py`（契约）
2. `backend/src/services/analysis_service.py` 的 `_run_loop_inner` 与 `_run_windowed`（主流程）
3. `backend/src/services/geo_skills/orchestrator.py:639`（确定性流水线长什么样）
4. `backend/src/services/visualization_service.py:1602`（`get_map_data` 的 9 步管线）
5. `frontend/src/api/sidecarBridge.ts` + `app/router.tsx` + `providers/context.tsx`（三形态如何归一）
