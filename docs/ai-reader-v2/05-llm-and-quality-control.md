# 05 · LLM 基础设施与质量控制

## 一句话概括

LLM 层被抽象成**同一组异步方法 + 明确的错误类型 + 可热切换的 provider**；质量控制不靠"调参祈祷"，而是靠**五个可独立开关的增强层**，每层关闭后行为与 v0.73 基线一致（NFR-3），便于做 A/B 与成本回退。

## Provider 矩阵

| 形态 | 标识 | 客户端 | 说明 |
| --- | --- | --- | --- |
| 本地推理 | `LLM_PROVIDER=ollama` | `infra/llm_client.LLMClient` | 默认 `qwen3:8b`；设置页提供 `qwen3:4b/8b/14b` 推荐档；全局信号量 = 1 |
| 云（OpenAI 兼容） | `LLM_PROVIDER=openai` | `infra/openai_client.OpenAICompatibleClient` | `base_url + /chat/completions`；共享信号量 = 3 |
| 云（Anthropic 原生） | `LLM_PROVIDER_FORMAT=anthropic` | `infra/anthropic_client.AnthropicClient` | 识别方式：provider id 为 `anthropic` 或 base_url 含 `anthropic.com` |

内置云预设（`api/routes/settings.py`）：`deepseek`、`minimax`、`qwen`、`moonshot`、`zhipu`、`siliconflow`，以及自定义 `base_url + model`。预设只提供默认 base_url 与模型清单，实际配置存 `app_settings`，API key 存系统钥匙串。

### 热切换

`config.update_cloud_config()` / `switch_to_ollama()` 会：

1. 更新模块级全局变量（`LLM_PROVIDER/API_KEY/BASE_URL/MODEL/FORMAT`）；
2. `_reset_llm_client()` 把 `llm_client._client` 置空；
3. 调用 `analysis_service.refresh_service_clients()` 让运行中的 service 下次取到新 client。

**不需要重启进程**。也因此 `get_scene_merge_enabled()`、`get_analysis_concurrency()` 写成函数而不是模块常量，读取时才判断当前 provider。

## 客户端契约

三个实现提供同名方法：

```python
async def generate(system, prompt, format=None, temperature=0.1,
                   max_tokens=4096, timeout=120, num_ctx=None
                   ) -> tuple[str | dict, LlmUsage]
async def generate_stream(...) -> AsyncIterator[str]
async def generate_with_tools(system, prompt, tools, ...) -> ...
```

- `format` 传 JSON Schema：Ollama 走原生 `format` 字段做约束解码，云走 `response_format={"type":"json_object"}`。
- 返回 `LlmUsage(prompt_tokens, completion_tokens, total_tokens, truncated, cache_hit_tokens, cache_miss_tokens)`。
- `truncated=True` 表示模型撞到输出上限，客户端做了 JSON 修补（`_repair_truncated_json`）。**消费方必须展示这个信号**——修补后的 JSON 语法合法，但尾部字段可能静默缺失。

### 错误体系

`infra/llm_client.py:126-160`：

| 异常 | 触发 | 上层处理 |
| --- | --- | --- |
| `LLMTimeoutError` | 请求超时 | `_classify_error` → `timeout`，章节标 failed |
| `LLMContextOverflowError` | Ollama 返回上下文溢出特征（携带 token 数） | `_call_with_overflow_shrink` 按比例缩短输入重试 |
| `LLMParseError` | JSON 无法解析 | `_classify_error` → `parse_error`，章节标 failed |
| `LLMError` | 其他 HTTP/协议错误 | 含审核关键词判 `content_policy`，否则 `http_error` |

错误分类集中在 `analysis_service._classify_error()`（`analysis_service.py:57-72`），`error_type` 取 `timeout / content_policy / http_error / parse_error`，非 `LLMError` 的统一落到 `unknown`，前端据此显示可操作提示。

### 并发控制

| 客户端 | 信号量 | 原因 |
| --- | --- | --- |
| Ollama | `Semaphore(1)` | 单 GPU 一次只处理一个请求；并发会造成超时雪崩 |
| OpenAI 兼容 / Anthropic | `Semaphore(3)` | 云端可并发；与 `ANALYSIS_CONCURRENCY` 上限 3 对齐 |

这是"窗口化并发最多 3 路"的底层约束来源（见 [04](04-analysis-pipeline.md)）。

## 上下文预算

`infra/context_budget.py` 把"8K → 128K"线性插值成一组参数，避免每个调用点各自拍脑袋：

| 参数 | 8K | 128K | 影响 |
| --- | --- | --- | --- |
| `max_chapter_len`（字符） | 8,000 | 50,000 | 超长章节截断阈值 |
| `retry_len` | 6,000 | 30,000 | 重试时缩短后的长度 |
| `segment_enabled` | false | true（≥32K 才开） | 长章分段抽取 |
| `context_max_chars` | 6,000 | 18,000 | 上下文摘要总长 |
| `char_limit` / `rel_limit` / `loc_limit` / `item_limit` | 30/20/30/15 | 60/40/80/30 | 各聚合段条目上限 |
| `max_hierarchy_chains` | 10 | 15 | 地点上行链条数 |
| `world_summary_chars` | 800 | 1,500 | 世界结构摘要长度 |
| `ws_max_tokens` / `ws_timeout` | 4096 / 120s | 8192 / 180s | 世界结构 agent |
| `hierarchy_timeout` | 90s | 120s | 层级复核 |

上下文窗口探测顺序（`detect_and_update_context_window`）：

1. Anthropic → 200,000；
2. 其他云 → 131,072；
3. Ollama → `POST /api/show` 读 `model_info.*.context_length`（回退解析 `num_ctx`），**上限压到 16,384**；理由写在注释里：模型可能声称 262K，但本地 KV cache 会拖垮生成速度；
4. 失败 → 8,192 保守值。

启动时探测一次（lifespan），设置页改模型后可重新触发。

## 五个质量开关

| 开关 | 默认 | 开启后做什么 | 关闭后 |
| --- | --- | --- | --- |
| `VOT_SPATIAL_ENABLED` | true | 抽取 prompt 注入"空间关系推理"指南，并要求模型输出 `spatial_relationships`；≤8K 窗口自动跳过 | prompt 与旧版一致，空间关系仅靠模型自发 |
| `RELATION_DIMENSIONS_ENABLED` | true | 关系增加 `polarity/rel_subtype/closeness`，并做枚举清洗 + 自一致性投票 | 不注入指南、不投票，解析行为回到维度升级前 |
| `EVIDENCE_GROUNDING_ENABLED` | true | few-shot 带 evidence、解析后做证据清洗、无法锚定的条目软剔除 | 与 v0.73 一致 |
| `RECALL_PASS_ENABLED` | true | 首遍后再跑一次"查漏"，补漏项标 `source="recall_pass"`；仅在首遍产出稀薄时触发（`RECALL_PASS_MIN_SIGNALS=2`） | 单章 LLM 调用量最多少一半 |
| `HALLUCINATION_REVIEW_ENABLED` | true | 规则层抓不住的疑似幻觉人物交给 LLM 判定，高置信剔除、低置信降级；决策写 JSONL 审计 | 只保留规则层 |

另有 `ENTITY_RESOLUTION_ENABLED`（实体消解）、`LLM_QUALITY_REVIEW`（聚合档案复核，默认 **false**）、`SCENE_MERGE_ENABLED`（场景并入主抽取，云端默认开）。

设计原则很明确：**每个开关都必须满足"关闭 = 旧行为"**，否则无法回答"这个增强到底值不值这个钱"。这条约束写在 `pyproject` 之外的代码注释里，并被 `tests/test_source_pass_context.py`、`tests/test_relation_dimensions_extraction.py` 等测试保护。

## 提示词资产

`extraction/prompts/` 是纯文本资产目录，`prompt_registry.py` 负责加载：

| 文件 | 用途 |
| --- | --- |
| `extraction_system.txt` | 主抽取系统提示（含 `{context}`、`{genre_context}`、各类注入标记） |
| `extraction_examples.json` | few-shot 示例 |
| `vot_spatial_guide.txt` | 空间推理指南 |
| `relation_dimensions_guide.txt` | 关系维度指南 |
| `evidence_grounding_guide.txt` | 证据锚定指南 |
| `scene_system.txt` / `scene_merge_guide.txt` | 场景抽取 / 并入指南 |
| `source_pass_system.txt` | 二审系统提示 |
| `world_structure_update.txt` / `hierarchy_*.txt` / `macro_skeleton.txt` / `spatial_completion.txt` | 世界结构、层级、宏观骨架、空间补全 |

`get_prompt(name)` 优先读目录内 `.txt`，并提供编译后的回退（便于 PyInstaller 打包）；`get_prompt_json` 读 JSON 资产。

## 成本可观测

### 计量

- 每次调用返回 `LlmUsage`，含缓存命中拆分（DeepSeek 的 `prompt_cache_hit_tokens` 等）。
- `cost_service.compute_cost_from_usage()` 按 `(缓存命中价 × hit + 输入价 × miss + 输出价 × completion) / 1M` 算 USD，再乘 7.2 得 CNY。
- 模型定价表 `_PRICING` 按精确匹配 → 前缀匹配 → 保守默认 `(0.50, 1.50)`；缓存倍率默认 0.26，仅对已知支持的模型族生效。
- 分析过程中按章累计，广播 `cost` 字段：`total_cost_cny`、`estimated_remaining_cny`、`monthly_used_cny`、`monthly_budget_cny`（默认预算 50 元）。

### 预估

`estimate_analysis_cost()` 用固定假设做**分析前报价**：

| 常量 | 值 |
| --- | --- |
| 中文字符 → token | 1.5 |
| 每次调用的固定 prompt 开销 | 47,000 tokens（系统提示 ~10KB + few-shot ~21KB） |
| 每章平均输出 | 4,000 tokens |
| 预扫描 | 25,000 输入 / 5,000 输出 |
| 每章上下文摘要 | ~2,000 字符 |

这些常量直接来自成本基线分析（注释引用 `docs/analysis/analysis-cost-baseline-2026-10.md`）：输出占支出 58%，固定 prompt 占每章输入约 52%。

### 归因

`llm_stage("extraction" | "validation" | "scene" | "qa" | ...)` 用 `ContextVar` 给调用打阶段标签；设置 `AI_READER_LLM_USAGE_LOG=/path/file.jsonl` 后每次调用追加一行 JSON。默认关闭，正常使用零 I/O 开销。

## 成本与质量的取舍

| 选择 | 收益 | 代价 |
| --- | --- | --- |
| 开启 recall pass | 少漏人物/事件 | 单章 LLM 调用最多 ×2；已用"稀薄才触发"缓解 |
| 开启关系子类型投票（N=3） | 子类型更稳 | 每章多 2 次轻量调用 |
| 云端 `ANALYSIS_CONCURRENCY=3` | 吞吐约 2–3× | 上下文最多落后 2 章，抽取精度略降 |
| 场景并入主抽取 | 每章省一次整章重发 | 分段/截断章无法合并，只能独立调用 |
| 超长章节截断/分段 | 避免上下文溢出 | `is_truncated` / `segment_count` 必须作为质量标志暴露给前端 |

## 密钥管理

`infra/secret_store.py` 优先写系统钥匙串（`keyring`，服务名固定），失败回退到本地文件加密/受限权限方案；`load_api_key` 在启动恢复设置时调用，明文 key 不写入 SQLite。设置页保存/删除走同一组函数，保证三端一致。
