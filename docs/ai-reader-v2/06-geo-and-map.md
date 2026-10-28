# 06 · 地理重建与地图子系统

## 一句话概括

地图子系统把"散落在各章的地点名 + 空间关系"变成两张图：**层级树（谁包含谁）** 和 **空间布局（画在哪）**。前者由**确定性 Edmonds 流水线**负责、可版本回滚；后者按世界类型在「真实地理投影 / 虚构分图层 / 纯层级散布」三种模式间自动选择，并缓存全部昂贵产物。

## 两条独立的问题

| 问题 | 输入 | 输出 | 负责人 |
| --- | --- | --- | --- |
| **层级**：地点之间的包含/上下位 | `locations.parent`、`spatial_relationships` 的 hierarchy 类证据、地名后缀、知识先验 | `location_parents` / `location_tiers` | `geo_skills` 流水线 |
| **布局**：每个地点画在画布哪里 | 层级 + topology 约束（方向/距离/相邻/路程） + 真实坐标 | `layout` 坐标 + 地貌产物 | `map_layout_service` + `visualization_service` |

两者解耦：布局失败不影响层级；层级变化会让布局缓存键失效并触发重算。

## 层级重建：`geo_skills` 流水线

入口 `build_default_orchestrator()`（`geo_skills/orchestrator.py:639`），顺序固定且单一实现，端点 `rebuild-hierarchy-v2` 与分析后自动重建共用：

```mermaid
flowchart LR
  V0["Snapshot v0<br/>从 WorldStructure 导入"] --> T["TierClassifier<br/>316 行"]
  T --> VB["VoteBuilder<br/>473 行"]
  VB --> KP["KnowledgePrior<br/>659 行"]
  KP --> ER["EdmondsResolver<br/>817 行"]
  ER --> AU["AuditorSkill<br/>174 行"]
  AU --> SN["SuffixNormalizer<br/>203 行"]
  SN --> EP["EntityPurifier<br/>208 行"]
  EP --> VN["Snapshot vN<br/>apply_to_world_structure"]
```

| 阶段 | 职责 | 是否用 LLM |
| --- | --- | --- |
| `tier` | 按地名后缀与语义推断层级（world/continent/kingdom/region/city/site/building） | 否 |
| `votes` | 从每章 facts 收集 parent 票、出现频次、首次出现章 | 否 |
| `prior` | 注入世界知识先验（真实地名、题材常识），给票加权 | 否（查内置先验表） |
| `edmonds` | 把 parent 关系当有向图求**最大生成树**（Edmonds/Chu-Liu），消除环与多父 | 否（networkx/scipy） |
| `auditor` | 入网门禁：剔除明显不该进层级的实体（人/物品/抽象概念），默认开启，可用 `evolve_param("auditor.enabled", False)` 关闭 | 否 |
| `suffix` | 合并地名变体（`乌斯藏国界 → 乌斯藏国`、`石头城 → 都中`），必须在 Edmonds 之后，否则会被后续投票覆盖 | 否 |
| `purify` | 最后净化实体（去掉误入的"人名地点"等），必须在 suffix 之后，否则归并可能把子节点挂回待剔除实体 | 否 |

**为什么是确定性流水线**：同一份 `WorldStructure` 重建两次必须得到同一棵树，否则用户无法判断"改了哪里"。LLM 只在 `SpatialCompletionAgent` 里补全缺失关系，不参与主重建。

### 快照与版本

`HierarchySnapshot`（`snapshot.py`）保存：`location_parents`、`location_tiers`、`parent_votes`（`Counter`）、`location_frequencies`、`chapter_settings`、`location_chapters`、`version`、`source`、`timestamp`、`novel_genre_hint`。每个 skill 成功即生成 `version+1` 的快照并写入 `snapshot_store`（与主库同文件、首次使用时惰性建 `hierarchy_snapshots` 表，`save/load_latest/load_version/list_versions/rollback`）。

两个关键语义：

- 默认 `run(fresh=False)` 从 `load_latest()` 继续"在上次结果上优化"；但连续两次 rebuild 会因起点不同产生约 50 条 parent 漂移。**`fresh=True` 强制从 `world_structures` 重新导入 v0**，用于可复现性验收（`repro_check`）和"重建"按钮的语义。
- `apply_to_world_structure()` 用**本次运行的最终快照**（`self._last_run_snapshot`），而不是 `load_latest()`。原因写在注释里：历史可能残留更高版本号（fresh 重写 v0–6 但旧链 v7+ 仍在），按 version DESC 取会把陈旧快照当最新，实测曾把已修复的"高老庄→灭法国"边回退。

### 空间补全

`services/spatial_completion_agent.py` 是唯一在 geo 链里用 LLM 的环节：

- 找出"层级缺父节点 / 空间关系稀疏"的地点，让模型基于原文补 `parent` 与方向/距离；
- 分析后自动触发，`asyncio.wait_for(timeout=300)`，失败/超时只 warn；
- 必须排在层级重建之后：它读的是重建后的树。

## 布局：三种模式

`get_map_data()` 的最终 `layout_mode` 只可能是三者之一：

| 模式 | 触发 | 做法 |
| --- | --- | --- |
| `geographic` | 地点能被真实地理库解析到较高覆盖率；或用户在 UI 强制 | 经纬度 → Mercator 投影到画布；支持用户拖拽覆盖 lat/lng |
| `layered` | 存在多个世界图层（人间/仙界/秘境），或题材需要 | 每层独立求解，`compute_layered_layout` 生成；层间用 `Portal` 连接 |
| `hierarchy` | 兜底（无地理数据、层数不足、求解失败） | 纯层级 + 约束的能量优化布局 |

选择不是黑盒：响应里返回 `layout_reason`，枚举包括 `ok`、`manual_geographic`、`manual_layered`、`sublayer`、`realm_layers`、`geo_error`、`geo_unavailable`、`low_geo_coverage`、`no_geo_data`，前端原样展示"为什么用这张图"。

### 真实地理库

`geo_resolver.py`（2875 行）：

- 数据集：`cn`（GeoNames `CN.zip`）、`world`（`cities5000.zip`，约 5MB）；
- 中文维基索引 `zh_wiki_geo.sqlite`（随包 `.gz` 约 13MB）**优先于** GeoNames 别名：它由脚本人工整理，覆盖河流/山脉/区域，并修正错挂别名（注释举例：GeoNames 把"彼得堡"挂到加拿大 Peterborough）；SQLite 逐点查询、常驻内存为 0（完整字典约 400MB RSS，只在分析期用）；
- `AI_READER_GEO_DIR` 指向 Tauri resource 目录（`resources/geo/`），不塞进 onefile；
- `detect_geo_scope()` 按题材与地名特征自动选 `cn` / `world`；`geo_type` 判 `realistic/mixed/fantasy`；
- `auto_resolve()` 批量解析，找不到的用 `place_unresolved_geo_coords()` 相对已解析祖先/最大簇质心落点，而不是随机撒点。

### 层级/约束布局

`map_layout_service.py`（6006 行）核心：

1. `_compute_region_seeds` + `_lloyd_relax`：为每个区域生成种子并做 Lloyd 松弛；
2. `_layout_regions` → `_clip_polygon_to_canvas` → `_distort_polygon_edges`：Voronoi 区域边界 + 边缘扰动，`generate_voronoi_boundaries()` 输出 `region_boundaries`；
3. `_solve_layer` / `compute_layered_layout`：逐层求解，`_annotate_portals` 标注跨层通道；
4. 约束求解器（`LayoutSolver`，1572 行起）：
   - `parse_distance()` 解析"三天路程（步行）"等中文距离，`_parse_chinese_number` 支持中文数字；
   - 方向约束映射为 `north_of/south_of/...`，相反方向自动取反；
   - 能量函数 = 均匀铺开项（`_e_uniform_spread`）+ 叙事顺序项（`_e_narrative_order`）+ 约束项（`_energy`）；`progressive_solve()` 逐步加入地点；
   - 冲突检测 `_detect_and_remove_conflicts` 用 `_are_opposing` 判矛盾方向；
   - 非地理实体（天庭、地府、抽象空间）走 `_place_non_geographic`，不参与地理约束；
   - 用户拖拽坐标 `map_user_overrides` 作为锚点，层级锁定 `_load_locked_parents`。
5. `generate_terrain()`：基于 `opensimplex` 噪声 + 陆地掩膜生成地形图（PNG 落盘，响应给 `terrain_url`）。

### 地貌产物

| 产物 | 生成函数 | 表 |
| --- | --- | --- |
| 大陆/岛屿轮廓、海岸大陆架 | `generate_landmasses()`、`landmass_base_radius()` | `map_geo_artifacts.landmasses_json/shelves_json/shelf_depth_json` |
| 河流 | `generate_rivers()` / `_trace_river()` | `rivers_json` |
| 道路 | `generate_roads()` | `roads_json` |
| 真实坐标 | `geo_coords_json` | 同上 |
| 布局 | `_compute_or_load_layout` | `map_layouts` / `layer_layouts` |

缓存键是 `(novel_id, layer_id, chapter_hash)`。`chapter_hash` 随章节/层级变化，新增章节后旧产物自然不再命中。`map_layout_meta.geo_failed` 记录"地理生成已失败过"，避免每次请求都重试昂贵步骤。

## `get_map_data()`：一次请求的完整管线

`visualization_service.py:1602`（约 1400 行），按顺序：

1. **取事实**：`_load_facts_in_range()` 只加载请求章节范围内的事实；`get_analyzed_range()` 给出已分析区间；
2. **名字归一**：`canonicalize_map_names()` + `entity_overrides`，保证图上名字与图谱一致；
3. **约束清洗**：`_clean_spatial_constraints` 去掉自环、hierarchy 与 topology 混用、无效端点；
4. **约束增强**：
   - `_inject_cardinal_name_priors`：从地名含"东/南/西/北"推断方向（低置信、`source_type=name_prior`）；
   - `_enhance_constraints`：补 travel sequence、反向边、传递闭包；
   - 海陆语义：`_is_water_location`、`_snap_sea_orphans_to_land`、`_snap_sea_bodies_to_water`、`_relocate_misplaced_seas`、`_separate_merged_continents`、`_continent_subtree_members`——这些是"海不能在内陆、大陆不能粘一起"的规则修复；
5. **图层过滤**：`_allowed_map_layers` / `_is_geographic_map_location`；有"海域/天界"层时 `_has_sea_realm_layer` / `_prefers_fantasy_layers` 决定是否强制 layered；
6. **布局**：先读 `layer_layouts` 缓存；无则 `_compute_or_load_layout()` 调用求解器并写回；用户覆盖最后叠加；
7. **地理产物**：`map_geo_artifacts` 命中则直接用，否则现算并写入；
8. **质量审计**：`utils/layout_metrics.py` 算 `layout_quality`；`map_plausibility.audit_map_layout()` 只读审计语义荒谬（三块大陆堆一点、海在陆上、王都离首都 3000px），失败绝不影响响应；
9. **组装响应**（`visualization_service.py:2949-2992`）：

```text
locations, layout, layout_mode, layout_reason, layout_choice,
quality_metrics, layout_quality, plausibility, geo_coverage,
spatial_constraints, trajectories, region_boundaries, portals,
landmasses, shelves, shelf_depth, rivers, roads, terrain_url,
revealed_location_names, spatial_scale, layer_spatial_scales,
canvas_size, geography_context, location_conflicts,
max_mention_count, suggested_min_mentions, space_theme,
[geo_coords], [world_structure, layer_layouts]
```

未指定 `layer_id` 时额外返回 `world_structure` 摘要与所有层的 `layer_layouts`；指定层则只返回该层并省略 `terrain_url`（各层共用地形）。

## 视觉派生：地点长什么样

`services/location_visual_extractor.py` + `location_visual_store`：从地点描述让 LLM 推断 `terrain`（地貌性格）与 `landmark`（地标类型），写 `location_visuals`，并保留 `raw_json` 以量化枚举校验损失。前端 `lib/terrainHints.ts`（1725 行）把这些标签映射到纹理/色调/笔触；`coastlineGenerator.ts`、`hullTerritoryGenerator.ts`、`territoryGenerator.ts`、`labelAnnealing.ts`、`spatialLabels.ts` 负责轮廓、领土、标签防重叠。

## 前端渲染

| 文件 | 职责 |
| --- | --- |
| `pages/MapPage.tsx`（1440 行） | 图层切换、章节范围、地理/分层模式选择、质量面板、地点详情 |
| `components/visualization/NovelMap.tsx`（3518 行） | 主渲染器：Leaflet 底图或自定义 Canvas，海陆、河流、道路、区域边界、标签、交互 |
| `components/visualization/GeoMap.tsx` | 真实地理模式（Leaflet + 经纬度） |
| `components/visualization/MapLayerTabs.tsx` | 图层页签 |
| `components/visualization/GeographyPanel.tsx` / `MapQualityPanel.tsx` | 覆盖率、冲突、plausibility 展示 |
| `components/visualization/WorldStructureEditor.tsx` | 手工编辑层级/图层/传送门 |
| `stores/mapDataStore.ts` | 地图数据缓存、当前层、加载状态 |
| `lib/mapRenderer/` | 渲染原语（海岸线、地形提示、标签布局） |

前端**不重算布局**：只消费后端 `layout` 坐标与产物，所有拖拽通过 `POST` 覆盖接口写回 `map_user_overrides`，再触发失效。

## 可解释性设计

地图是"算法结果不可解释"的重灾区，V2 的处理方式值得单独记一笔：

1. 每个响应带 `layout_mode` + `layout_reason`，UI 直说"为什么是这张图"；
2. `marker` 级信息：每个地点带 `mention_count`、出现章节、`source`（层级/方向/拓扑），可回溯到证据；
3. `plausibility` 是只读审计，列出违规项而不擅自修改；
4. 用户覆盖优先于所有自动结果，且覆盖本身可撤销（`map_user_overrides` 单行删除）；
5. 快照可回滚（`snapshot_store.rollback`），重建不是单向操作。
