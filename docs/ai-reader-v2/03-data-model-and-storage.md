# 03 · 数据模型与存储

## 一句话概括

存储是**"可重建事实 + 可重算派生数据 + 用户覆盖"三层结构**：`chapter_facts` / `pass_chapter_facts` 以"章"为粒度整体写入（重跑即整章替换，不做字段级增量合并）；`world_structures`、`map_*`、`layer_layouts`、`location_visuals` 是可整体重算的加工品；`*_overrides` 与 `app_settings` 是用户意志，优先级最高。

## 存储介质

| 介质 | 位置 | 内容 | 访问层 |
| --- | --- | --- | --- |
| SQLite（WAL） | `~/.ai-reader-v2/data.db` | `_SCHEMA_SQL` 里 22 张表 + geo_skills 首次使用时惰性创建的 `hierarchy_snapshots`（共 23 张） | `src/db/*_store.py`、`geo_skills/snapshot_store.py` |
| ChromaDB | `~/.ai-reader-v2/chroma` | 每本小说两个 collection：`{novel_id}_chapters`、`{novel_id}_entities`，cosine | `services/embedding_service.py` |
| 文件系统 | `~/.ai-reader-v2/`、导出目录 | `.air` 包、导出 docx/xlsx/pdf | `services/export_service.py`、`backup_service.py` |

SQLite 没有 ORM：`src/db/sqlite_db.py` 里是一段 `_SCHEMA_SQL`，各 store 手写 SQL + `aiosqlite`。所有表用 `CREATE TABLE IF NOT EXISTS` + 独立 `_migrate_*` 函数实现**幂等迁移**，没有版本号框架。

## 22 张 schema 表分组

> `sqlite_db.py::_SCHEMA_SQL` 里有 22 条 `CREATE TABLE IF NOT EXISTS`。另有 1 张不在 schema 里、由 `geo_skills/snapshot_store.py::_ensure_table()` 在首次使用时创建的 `hierarchy_snapshots`（层级快照版本链），所以运行期 DB 实际是 23 张表。

### A. 小说与章节（输入层）

| 表 | 关键列 | 说明 |
| --- | --- | --- |
| `novels` | `id`(TEXT PK)、`title`、`file_hash`、`total_chapters`、`total_words`、`prescan_status`、`is_sample`、`synopsis` | 一本书一行；`file_hash` 用于导入去重 |
| `chapters` | `id`(INTEGER PK)、`novel_id`、`chapter_num`、`volume_num/title`、`content`、`word_count`、`analysis_status`、`analyzed_at`、`UNIQUE(novel_id, chapter_num)` | 正文**全文入库**；`analysis_status` ∈ pending/…/completed/failed |
| `user_state` | `novel_id` PK、`last_chapter`、`scroll_position`、`chapter_range` | 阅读进度与"分析范围"记忆 |

### B. 事实层（按章整体替换）

| 表 | 关键列 | 说明 |
| --- | --- | --- |
| `chapter_facts` | `novel_id`、`chapter_id`、`fact_json`、`llm_model`、`extraction_ms`、`UNIQUE(novel_id, chapter_id)` | 每章一份 `ChapterFact` JSON；重跑用 `INSERT OR REPLACE` 覆盖 |
| `analysis_passes` | `id`、`kind`(默认 `source_pass`)、`model_name`、`status`、`chapter_start/end`、`config_json`、`history_json` | multi-pass 二审的"影子任务" |
| `pass_chapter_facts` | `pass_id`、`chapter_id`、`fact_json`、`status`、`error` | 二审结果独立存放，**主表零改动** |

`chapter_facts` 上的 `llm_model`、`extraction_ms`、`input_tokens/output_tokens/cost_*`、`is_truncated/segment_count/output_truncated` 等列由迁移补充，是"质量与成本可追溯"的基础。

### C. 世界结构（单文档 + 覆盖）

| 表 | 说明 |
| --- | --- |
| `world_structures` | 主键 `novel_id`，`structure_json` 存整份 `WorldStructure`；`source_chapters` 记录贡献章节；整文档 load-modify-save（因此存在 last-writer-wins 风险，见 [06](06-geo-and-map.md)） |
| `world_structure_overrides` | `(novel_id, override_type, override_key)` 唯一；用户手工改层级/图层/传送门 |
| `entity_overrides` | 同构，用户改实体（别名合并、类型、隐藏） |
| `entity_dictionary` | 预扫描 + 二审累积的实体词典：`name`、`entity_type`、`frequency`、`confidence`、`aliases`(JSON)、`source`、`sample_context` |

### D. 地图与布局（可重算派生）

| 表 | 说明 |
| --- | --- |
| `map_layouts` | `(novel_id, chapter_hash)` 主键；整书单层布局 |
| `layer_layouts` | `(novel_id, layer_id, chapter_hash)`；多图层（人间/仙界/秘境…）各自布局 |
| `map_geo_artifacts` | `landmasses/shelves/rivers/roads/geo_coords/shelf_depth` JSON，全部按 `chapter_hash` 缓存 |
| `map_layout_meta` | `geo_failed` 标志，避免反复重试昂贵的地理生成 |
| `map_user_overrides` | 用户拖拽后的坐标（`location_name, x, y`） |
| `location_visuals` | 从地点描述派生的地貌/地标属性，保留 `raw_json` 以量化枚举校验损失 |

`chapter_hash` 是**布局缓存键**：新增章节或层级变化后 hash 改变，旧缓存自然失效。

### E. 会话与问答应答

`conversations`（`id` TEXT PK、`novel_id`、`title`）与 `messages`（`role`、`content`、`sources_json`、按 `conversation_id, created_at` 索引）。`sources_json` 保存引用的章节号，支撑"可溯源"。

### F. 运维与设置

`app_settings`（KV，LLM 模式/模型/预算等）、`usage_events`（`event_type` + `metadata`，用于成本与审计）、`benchmark_records`（模型速度/质量基准）。另有 `analysis_tasks` 存分析任务状态与游标。

## `ChapterFact`：核心事实契约

`backend/src/models/chapter_fact.py:204`，每章一份：

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `characters` | `list[CharacterFact]` | 姓名、本章新别名、外貌、获得能力、出现地点、`source`(main/recall_pass) |
| `relationships` | `list[RelationshipFact]` | `person_a/b`、`relation_type`、`is_new`/`previous_type`、`evidence`；维度扩展 `polarity`/`rel_subtype`/`closeness`/`subtype_vote` |
| `locations` | `list[LocationFact]` | `name`、`type`、`parent`+`parent_evidence`、`peers`、`description`、`role`(setting/referenced/boundary) |
| `spatial_relationships` | `list[SpatialRelationship]` | 空间证据：`contains/located_in` 归为 **hierarchy**，`connects/route_to/adjacent/direction/distance/in_between/separated_by/travel_path` 归为 **topology**，旧值归 other |
| `item_events` | `list[ItemEventFact]` | 出现/获得/使用/赠予/消耗/丢失/损毁，含 `related`（物品间关系） |
| `org_events` | `list[OrgEventFact]` | 组织成员变动与组织间关系（盟友/敌对/从属/竞争），带 `evidence` |
| `events` | `list[EventFact]` | `summary`、`type`、`importance`、参与者、地点、`evidence` |
| `new_concepts` | `list[ConceptFact]` | 修炼体系/种族/货币等概念词条 |
| `world_declarations` | `list[WorldDeclaration]` | `region_division / layer_exists / portal / region_position` 四类世界声明，驱动世界结构 |

设计要点：

1. **向后兼容靠默认值**：所有后加字段都有默认值，旧 JSON 可直接反序列化（注释反复强调）。`AbilityGained` 还做了"LLM 返回字符串"的 `field_validator` 强转。
2. **证据内嵌**：关系、事件、组织事件、空间关系都带 `evidence` / `narrative_evidence`，这是"可溯源"和"幻觉抑制"的前提。
3. **空间关系分层**：`HIERARCHY_SPATIAL_RELATIONS` 是唯一事实源，只有 `contains/located_in` 能进层级树；`normalize_spatial_relation_type` 兼容 `north_of → direction` 等旧拼写。
4. **来源标记**：`source` 区分首遍与 recall pass，便于评估补漏贡献。

## `WorldStructure`：全书世界结构

`backend/src/models/world_structure.py:99`：

| 字段 | 说明 |
| --- | --- |
| `layers: list[MapLayer]` | 每个 `MapLayer` 有 `layer_id/name/layer_type/regions`，`LayerType` 枚举定义世界分层（如人间/仙界/秘境） |
| `portals: list[Portal]` | 跨层通道：`source_layer/source_location → target_layer/target_location`，双向标志、首次出现章节 |
| `location_region_map` | 地点 → 区域 |
| `location_layer_map` | 地点 → 图层 |
| `location_tiers` | 地点 → 层级等级（`LocationTier`） |
| `location_icons` | 地点 → 图标（`LocationIcon`） |
| `novel_genre_hint` | fantasy/wuxia/historical/urban/unknown，影响提示词与地图风格 |
| `spatial_scale` | `SpatialScale`，控制地图尺度 |

另有 `WorldRegion`（方位、区域类型、父区域）与 `WorldBuildingSignal`（信号类型、章节、原文摘录、置信度）。

`WorldStructureAgent`（3222 行）在分析过程中持续 load-modify-save 这份文档；`geo_skills` 流水线会产出**新版本快照**再 `apply_to_world_structure()` 写回（见 [06](06-geo-and-map.md)）。

## 派生读模型（不落库的部分）

以下不是表，而是请求时聚合：

- `EntityAggregator`：`aggregate_person/location/item/org` → `PersonProfile` / `LocationProfile` 等跨章档案，带进程内 LRU 缓存 + `invalidate_cache`。
- `visualization_service.get_graph_data / get_timeline_data / get_factions_data`：从 facts + overrides 组装前端视图。
- `get_map_data`：读 `world_structures` + 缓存布局 + `map_geo_artifacts`，必要时现算并写回缓存。

**缓存失效策略**：任何写 facts / world structure / overrides 的路径都会调用 `invalidate_*_cache`（如 `invalidate_layout_cache`、`invalidate_map_response_cache`）；缓存本身不持久化。

## 导入/导出格式

- `.air` = gzip 压缩的分析数据包，`export_service.CURRENT_FORMAT_VERSION = 6`，支持读 `1..6`（`export_service.py:11-12`）。版本不匹配时走兼容分支而非直接失败。
- 导出内容包括：小说元数据、章节、facts、实体聚合、世界结构、地图覆盖、布局、对话、预计算可视化数据。
- 桌面端 `.air` 与 Tauri 文件关联绑定（`application/x-air+gzip`），由 Rust 的 `preview_air_file` / `import_air_file` 处理，数据落到 `get_user_novels_dir()`（`lib.rs:307-599`）。

## 迁移与兼容约定

1. 新增列用 `ALTER TABLE ... ADD COLUMN` 包在 try/except 里（"列已存在"当成功）。
2. 状态值历史差异（如旧 `done` → 新 `completed`）由迁移函数统一归一，`test_status_migration.py` 覆盖。
3. 旧 JSON 一律靠 Pydantic 默认值兼容，禁止在模型里把可选字段改成必填。
4. `pass_chapter_facts` 与主表隔离，二审实验不会污染生产数据。
