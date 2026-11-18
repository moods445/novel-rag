# TODO

- [x] 后端：`/api/ask` 支持多轮 `history`；`PUT /progress` 改为局部更新（不覆盖防剧透开关）
- [x] 前端：全局明暗主题切换（localStorage 持久化，阅读区同步换色）
- [x] 前端：阅读页右侧 chat 面板（多轮对话、引用跳转）
- [x] 前端：chat 面板内防剧透开关，与分析页同步并持久化
- [x] 验证：后端 unittest（127 通过）+ 浏览器联调（主题/对话/引用/防剧透持久化）
- [x] 阅读：默认改为连贯滚动排版（foliate `flow="scrolled"`），首屏从正文开始（`init({showTextStart})`）
- [x] 阅读：键盘 ←/→ 翻一屏、↑/↓ 分步滚动；正文 iframe 内同样生效，跨章自动交接
- [x] 阅读：边界保护（首章 ←、末章末尾 →/↓ 不再触发 foliate 越界 load 报错）
- [x] 阅读：右侧对话面板可隐藏/显示（工具栏按钮 + 面板 × 按钮，localStorage 持久化），隐藏后正文自动铺满且保持位置
- [x] 验证：网页端 playwright 联调（滚动/翻屏/iframe 焦点/边界/滚轮/面板隐藏与刷新后保持）+ 后端 unittest（128 通过）
- [x] Web 部署：后端 `NOVEL_RAG_WEB_ROOT` 同源托管静态前端（非 /api 走静态分支、目录穿越 404、index.html no-cache），`/api/**` 仍校验 Bearer
- [x] Web 部署：`NOVEL_RAG_DISABLE_AUTH` 显式关闭鉴权；前端同源 Web 模式 + token 记进 localStorage + hash 变更重载
- [x] 镜像：`Dockerfile`（python:3.12-slim、内置前端、非 root、HEALTHCHECK）、`.dockerignore`、`docker-compose.yml`（只 pull 不 build）
- [x] CI：`.github/workflows/docker.yml` 在 Actions 构建 amd64 推 ghcr + 启动烟测（本地禁止构建）
- [x] 验证：CI 跑通（run 40852721727 绿），本地 pull 镜像实测（healthy / 导入 121 章 / 同源 Web / 阅读键盘 / 对话面板隐藏），测完删除容器+卷+镜像
- [x] 后端：新增 `tests/test_web_static.py`（静态托管、穿越防护、/api 仍要 token），全套 134 通过
- [x] 阅读：键盘加固 —— 主文档 capture 绑键、iframe 焦点双绑、连按串行排队（不再被 foliate 换章锁丢键）、渲染器未就绪不吞键、新增 PgUp/PgDn/空格翻屏、无书时给提示
- [x] 阅读：换书先 `view.close()`，不再往 shadow DOM 里堆渲染器；load/relocate 只绑一次
- [x] 阅读：刷新安全 —— 书架/进度/防剧透都在服务端，前端记住上次的书，刷新后自动恢复书与 CFI 位置（滚动过程节流存 CFI）
- [x] 导入：书架上方进度条（读文件 → 编码 → 上传真实百分比 → 服务端分章不确定态 → 完成/失败），完成后写明章节数与可刷新说明
- [x] 验证：网页端 playwright 实测（`novels/三国演义.txt` 121 章）—— 导入进度四段、←→/↑↓/PgUp/PgDn/空格、iframe 内按键、连按 5 次不丢、刷新两次回到原位、重开同一本书
- [x] 文档：README 增「阅读页（键盘 / 刷新 / 导入进度）」，并说明 foliate sandbox 控制台警告无害

## 本轮（向量化页 / 日志页 / Docker 问答降级）

- [x] 后端：`GET /api/books/{id}/vectors` 按章节统计向量覆盖（已向量化段数、完成章数、下一章）；`POST /api/books/{id}/vectors/rebuild` 后台逐章重建（任务状态含当前章节）
- [x] 后端：`store/index.py` 新增 `vector_status` / `clear_vectors`（只统计当前 embedding 模型）
- [x] 后端：`GET /api/logs?source=backend|sidecar&lines=N` 读取日志尾部（限制 1–2000 行）
- [x] 后端：关键操作写日志（导入、重建、分析 start/pause/resume/retry、问答、设置、provider 增删）
- [x] 修复问答降级：区分「未配置模型」与「调用失败」，把真实错误写进回答前缀、`llm.error` 与日志
- [x] 前端：新增「向量」页（总进度条、已向量化/总段数、完成章数、当前/下一章、章节明细列表、整本重建）
- [x] 前端：新增「日志」页（backend/sidecar 切换、200–2000 行、自动刷新、级别着色）
- [x] 前端：设置页显示问答模型是否生效；对话/分析回答下方显示模型错误原因
- [x] Docker：compose 生成模板注入 `NOVEL_RAG_QUERY_*` / `NOVEL_RAG_EXTRACT_*` / `NOVEL_RAG_PROXY` / `NOVEL_RAG_LOG_LEVEL`
- [x] Docker：`deploy.sh` 新增 `--query-*` / `--extract-*`（extract 缺省复用 query），.env 保留手改变量，未配置问答模型时打印告警
- [x] 验证：后端 unittest（132 通过，不含 test_cli）；`bash -n deploy.sh`；浏览器实测向量页/日志页/设置页；curl 验证 121 章重建与问答降级文案
- [x] 测试：`test_qa` 增「provider 报错暴露真实原因」「向量状态按章节统计」，`test_web_static` 增日志尾部接口

## 本轮（问答模型诊断 / 向量续跑与暂停）

- [x] 后端：`effective_summary` 增加 `llm_detail.reason`，把「未生效」拆成具体原因（未选 provider / provider 不存在 / 没有模型名 / `NOVEL_RAG_QUERY_*` 只配了一半），设置页直接显示
- [x] 前端：模型选择改为「可选建议的输入框」（datalist），provider 没声明模型列表时也能直接填模型名
- [x] 后端：`POST /api/books/{id}/vectors/continue` 从数据库断点继续（只补未完成章节）；`/vectors/rebuild` 仍为清空重建
- [x] 后端：`POST /api/books/{id}/vectors/pause` 章节边界暂停；暂停标记写入 `extensions`，重启后仍生效
- [x] 后端：启动时 `resume_vector_builds()` 自动续跑所有未建完的书，运行中由 supervisor 每 60s 巡检；手动暂停的书跳过
- [x] 前端：向量页新增「继续构建」「暂停」按钮，状态展示 `pausing/paused`，按状态禁用按钮
- [x] 测试：新增 `tests/test_vectors.py`（继续只补缺章、重建全量、暂停/继续、重启自动续跑、暂停标记跨重启）；`test_settings` 新增 `LlmDiagnosticsTest`
- [x] 验证：后端 unittest 全套通过（含 test_cli 需非沙箱环境）
- [x] 文档：README 更新向量页说明与 `/vectors/continue`、`/vectors/pause` 接口

# 待确认

- [ ] 是否需要「翻页/滚动」排版模式切换开关（当前固定为连贯滚动）

## 本轮（provider 只走应用内配置，摘掉 opencode 依赖）

- [x] 后端：`llm/opencode.py` 不再自动探测 `~/.config/opencode/*` —— 不给路径就返回空；`load_config(None) == {}`
- [x] 后端：`opencode_config`（`NOVEL_RAG_OPENCODE_CONFIG`）降级为**仅本地测试**开关，显式指定才读
- [x] 后端：`resolve()` 去掉「找不到就顶上第一个（magpie）」的兜底，选中的 provider 不存在时如实报错
- [x] 后端：设置页/问答降级文案改为「应用内 provider 列表」（不再提 opencode 配置）
- [x] 前端：设置页说明改为「provider 只有设置页添加这一个来源（存本机数据库）」，模型列表提示至少填一个
- [x] 文档：README / architecture / 桌面说明 / compose 注释 / `deploy.sh --help` 同步
- [x] 测试：`test_no_default_path_discovery`、`test_missing_provider_is_not_silently_replaced`；全套 154 通过

## 本轮（问答模型「已配置却提示未配置」）

- [x] 后端：`llm/opencode.py` 同时兼容两种配置 schema —— 旧版 `provider` + `options.baseURL`、新版 `providers` + `settings.baseURL`（模型列表支持 dict/list，`model` 支持字符串或对象），避免 v2 配置里 0 个 provider
- [x] 后端：抽出 `gateway.query_reason()` 作为「未生效原因」唯一来源，设置页 `effective_summary` 与问答降级文案共用（不再只给一句笼统的「未配置」）
- [x] 后端：问答抽取式兜底把具体原因（没选中 / provider 找不到 / 没模型名 / 环境变量只配一半）写进回答前缀
- [x] 后端：`POST /api/providers` 在当前没选中 provider 时自动选上新加的那个（含首个模型），不再「加了却没用上」
- [x] 前端：provider 下拉补「（未选择）」项，未选中时不再显示成第一个 provider；provider 无模型声明时模型名回退到 opencode 默认 `model` 并作为 datalist 建议
- [x] 测试：新增 `OpenCodeV2ConfigTest`（v2 解析 / resolve / 网关 / 不泄漏 key）与 3 个诊断用例（v2 生效、配置有但未选中、UI 新增自动选中）
- [x] 验证：本机 `~/.config/opencode/opencode.json`（v2 格式）现可解析出 `magpie` 并解析到模型；后端 unittest 153 通过

## 本轮（模型配置 CRUD）

- [x] 后端：`GET /api/providers/{name}` 单条详情；`PUT /api/providers/{name}` 修改（支持改名、增删模型）
- [x] 后端：Key 只回显脱敏提示 `api_key_hint`（如 `sk-a…3456`）；编辑不传 Key 保持原值，显式传空才清除
- [x] 后端：模型名去重（中英文逗号 / 换行）、长度与数量上限、base_url 完整校验
- [x] 后端：改名 / 模型列表缩短后同步 `llm_provider` / `llm_model`，不留下失效模型
- [x] 后端：本地测试配置（`NOVEL_RAG_OPENCODE_CONFIG`）里的 provider 显式只读（改 / 删报 400，不落库）
- [x] 前端：provider 列表显示 Base URL / Key 脱敏 / 模型列表，新增「编辑」「删除」操作
- [x] 前端：编辑回填表单、可改名、Key 留空保留、勾选「清除已保存的 Key」才置空，支持取消
- [x] 测试：新增 `ProviderCrudTest` / `ReadOnlyProviderTest` 与 `/api/providers` HTTP 路由用例，全套 165 通过
- [x] 验证：Playwright 实测新增 → 编辑 → 改名 → 清 Key → 取消 → 删除（控制台无报错）

## 本轮（阅读页章节下拉框选中后立即变空）

- [x] 前端：章节下拉框的选项值由 href 改为 spine 序号 —— foliate 解析后的 `OEBPS/ch0001.xhtml` 与后端 `nav_href`（`ch0001.xhtml`）不是同一个字符串，relocate 回填时匹配不到选项，选择框被清空
- [x] 前端：`goTo` 允许 target 为 0（首章），`goToChapter` / `goToCitation` 改用 `spine_index`，顺带修复上一章/下一章按钮因 href 不匹配而静默失败
- [x] 验证：浏览器实测《三国演义》—— 下拉框选第五十回后仍显示该章节、正文已切换；上一章/下一章按钮正常且下拉框同步不空白；控制台无报错

## 本轮（分析面板一直「还没有抽取到关系」）

- [x] 后端：`Pipeline.start` 在抽取模型可用时识别 `source=heuristic` 的章节，清掉其事实/事件/摘要并把游标回退，重抽后关系才会出现（离线跑完的缓存不再挡住「开始/继续」）
- [x] 后端：仍处于离线哨兵模式时不重抽，避免反复空跑
- [x] 前端：分析任务从非 done 变为 done 时自动刷新图谱，停在分析页也能看到新关系
- [x] 测试：新增「配置模型后重抽离线章节并生成关系」「模型仍离线时不重抽」；全套 167 通过

## 本轮（关系图独立成「图谱」功能页）

- [x] 后端：新增 `POST /api/books/{id}/graph/rebuild`（清空本书事实/实体/关系等抽取产物，游标归零后重新开跑）；`GET /graph` 增加 `model_ready`
- [x] 后端：`Pipeline.rebuild` + `LlmGateway.extract_ready`；新增重建单测
- [x] 前端：侧栏新增「图谱」页 `#view-graph`，支持选择书籍、刷新、开始抽取、暂停、继续、重建（确认弹窗）
- [x] 前端：新增 `graph.js` 力导向布局，SVG 画出实体/关系，按类型着色，悬停高亮邻居与关系并显示统计
- [x] 前端：分析页移除重复的「关系」列表，只保留时间线；图谱页任务完成自动刷新
- [x] 验证：临时服务（8001）浏览器实测 —— 图谱页 SVG 110 节点/153 连线、统计与按钮状态正常、无 JS 报错；分模块后端测试通过

## 本轮（图谱页交互约束）

- [x] 图谱页必须先手动选择书籍：下拉框增加「请选择书籍…」占位，不再自动落到第一本书；未选择时开始/暂停/继续/重建全部禁用并提示
- [x] 开始抽取后显示进度：状态行展示「抽取中 · 已完成/总章数（百分比）」，进度条按章节覆盖推进，运行中 1.5s 轮询
- [x] 重复点击幂等：前端 busy 互斥；后端 `Pipeline.start` 在 `running` 状态直接返回现状，不重置游标、不重开线程；抽取进行中禁止重建
- [x] 测试：新增「重复开始不改变游标」用例，pipeline 15 项通过
- [x] 验证：隔离数据（`/tmp` 拷贝）起 8001 临时服务，Playwright 实测 —— 未选书时四按钮全禁用并有提示；运行中状态行「抽取中 · 11/121（9%）」、开始/重建禁用、暂停可用；暂停态「已暂停（可点「继续」）· 11/121（9%）」、继续/重建可用；关系图 102 节点渲染、控制台无报错

## 本轮（抽取分段：默认 5000 字 + 均分避免尾段过短）

- [x] 分段算法：`a = ceil(s/n)`（段数向上取整），`b = ceil(s/a)`，按等距切点吸附换行，恰好 `a` 段且单段不超过 n；`s=10001/n=5000` 由 5000+5000+1 变为 3334+3334+3333
- [x] 默认分段字数改为 `5000`（原 1800），新增 `NOVEL_RAG_EXTRACT_SEGMENT_CHARS`，并接入设置页「抽取分段」输入（500–12000，留空回默认）
- [x] `SettingsStore` 新增整数设置校验/回退；流水线按 `settings.extract_segment_chars` 构造抽取器
- [x] 部署：compose/deploy 透传新变量；README 两处环境变量表补充
- [x] 测试：新增分段边界（整除/向上取整/换行保真/尾段不短）与整数设置校验；`test_extract`+`test_settings`+`test_pipeline` 共 86 项通过

## 本轮（关系抽取提速：章节级并发）

- [x] 后端：`Pipeline.run` 拆成「LLM 抽取」与「校验/归一/落库」两段 —— 抽取用 `ThreadPoolExecutor` 按窗口并发（默认 3），落库仍按章节顺序串行，关系区间/别名合并语义不变
- [x] 后端：新增 `NOVEL_RAG_EXTRACT_CONCURRENCY`（默认 3，`1` 为串行）；工作线程只做纯 LLM 调用，段落/上下文在主线程预读，避免 SQLite 跨线程访问
- [x] 后端：`LlmGateway` 用量计数加锁；暂停时丢弃预取结果、游标只推进到已完成章节；`max_chapters` 与跳章语义保持
- [x] 部署：`docker-compose.yml` / `deploy.sh` 透传 `NOVEL_RAG_EXTRACT_CONCURRENCY`；README 两处环境变量表补充
- [x] 测试：新增「抽取并发执行且落库保持顺序」用例；pipeline 16 项、除 CLI 外全套 149 项通过

## 镜像构建提速（拆分缓存层 / 去掉 arm64）

- [x] `Dockerfile`：拆回分层 —— 依赖层只依赖 `backend/pyproject.toml`，vendor 层只依赖 `prepare_vendor.py`，源码层才装本地包并组装静态目录（之前合并成单层，导致改一行源码就重装 onnxruntime 等依赖）
- [x] CI：只构建 `linux/amd64`，去掉 arm64 与 QEMU 模拟（部署机都是 x86，arm64 靠模拟要多花约 250s）
- [ ] 验证：push 后 GitHub Actions `build-image` 跑通（测试 + 镜像 + 烟测），并记录构建耗时

## 本轮（关系图改用 relation-graph 云图）

- [x] 依赖：`desktop/scripts/prepare_vendor.py` 新增 `ensure_relation_graph`，构建期拉取 `relation-graph-vue3@2.2.11` + `vue@3.5.44` + `screenfull@5.2.0` + `html2canvas@1.4.1` 到 `desktop/web/vendor/relation-graph/`（UMD，仓里不入库）
- [x] 前端：`graph.js` 自写 SVG 力导向换成 relation-graph 组件，运行时按序注入 4 个 UMD 脚本（组件要求全局 `Vue`/`screenfull`/`html2canvas`）
- [x] 前端：`fixed` 布局 + 自算坐标（向日葵螺旋，`CLOUD_PACK=0.8` 按已占面积累加半径，高度数人物落中心且不被挤扁），不再依赖不稳定的力导向
- [x] 前端：取景绕开组件 `zoomToFit`（它只统计未裁剪节点，反复调用会越取越小），按节点坐标算整图尺寸 → `setZoom` 等比缩放 → 下一帧按实际 DOM 外框 `setCanvasOffset` 居中
- [x] 前端：节点改成 HTML 气泡（按类型上色、度数角标、hover 放大），`hideNodeContentByZoom: false` 避免缩小后只剩空白；连线 `opacity .55 / lineWidth 1.5`，颜色走 CSS 变量随明暗主题切换
- [x] 前端：最多 110 个节点 / 400 条连线（按度数取头部），画布高度提到 `calc(100vh - 270px)`，让气泡半径与字号（12–26px）够看清
- [x] 交互：节点自由拖拽、空白处拖拽平移、滚轮缩放、点击节点/连线写 `#graph-detail` 明细；轮询刷新用 `dataKey` 去重，拖好的位置不会被弹回去
- [x] 验证：Playwright 实测（《三国演义》121 章）—— 110 节点 / 250 连线、`outside=0` 无越界、滚轮缩放 67→79px、拖拽 1014→1110 且 6s 轮询后位置保持、点击曹操显示「60 条关系」明细、切走再切回不变、暗/亮主题连线 `rgb(127,140,163)` / `rgb(182,193,211)`、控制台无报错

## 本轮（云图人物筛选：上限 / 根人物 / 孤立人物）

- [x] 前端：图谱页左侧新增「云图设置」面板（最多人物、显示孤立人物、根人物多选），`grid-template-areas` 让关系网络跨两行
- [x] 前端：默认最多 30 个人物（可配 3–200，localStorage 记 `novel-rag.graph.maxNodes`），改数值/开关/根人物即时重画（复用上次数据，不重新请求）
- [x] 前端：根人物（必展示）按书记住 `novel-rag.graph.roots:<bookId>`；未选时默认取度数最高的 3 个并在下拉框里勾上
- [x] 前端：选人改为「根人物 → 一跳 → 两跳 …」的广度优先，填满上限即停；根人物排在螺旋最前，落在圆心附近
- [x] 前端：与根人物不连通的人物算孤立（含度数为 0 的实体），默认不显示；开关打开时追加到外圈、同样受上限约束，虚线圈 + 压暗区分
- [x] 前端：间距常数改为 `CLOUD_PACK=0.45`（原 0.8 中心大节点会叠住），取景允许放大到 1.3 倍，图例显示「N 节点 · M 关系 · 孤立 K · 共 X 人」
- [x] 验证：Playwright 实测《三国演义》—— 默认 30 节点/47 连线、上限 10 → 10 节点、开孤立 → 10+10 节点「孤立 10」、换根人物（吕布）以吕布为中心重排、改设置后刷新页面仍保持（上限 40/孤立开/3 个根人物）、拖拽 1224→1308 正常、亮色主题连线正常、控制台无报错

## 本轮（关系图页改为左右两栏并美化）

- [x] 布局：改为「左侧设置栏 316px + 右侧关系网络自适应」两栏，`grid-template-areas` 换成 `aside.graph-sidebar` + `panel.graph-network`，1080px 以下自动堆叠（画布在上、设置在下）
- [x] 布局：整页高度锁在 `calc(100vh - 137px)`（视图区内边距 + 页头），两栏等高铺满、1440/1366/1280 均不出现滚动条
- [x] 设置栏：任务卡固定高度，云图设置卡吃满剩余高度，根人物多选列表跟着伸缩（1440 下约 10 行、768 高屏幕自动压到 3 行）
- [x] 美化：卡片标题加细分隔线、图例做成小胶囊、多选项改为淡青底 + 加粗（明暗主题都清晰）、孤立人物虚线圈、画布圆角 12px
- [x] 精简：把根人物的长说明收进 `title` 悬浮提示，卡片里只留一行「Ctrl / Cmd / Shift 多选根人物」，短屏不再溢出
- [x] 验证：1440×900 / 1366×768 / 1280×800 两栏均等高（763 / 631 / 663），`viewScroll=false`、画布 812×658；1024 宽度自动堆叠；暗/亮主题截图正常；设置、拖拽（1183→1267）、刷新后设置保持、控制台无报错

## 本轮（根人物改为可搜索多选 + 上限即时生效）

- [x] 前端：根人物从原生 `<select multiple>` 换成自建选择器（已选胶囊 + 搜索框 + 可点击列表），支持关键字过滤、点一次选中/再点一次取消、点胶囊上的 × 移除
- [x] 前端：候选列表不再只收 `degree>0`，收录全部实体并按连接度降序（行尾显示度数）；一次最多 80 行，超出提示「还有 N 人，输入关键字查找」；未手动选时默认的 3 人用虚线勾选框标注为「自动」
- [x] 前端：上限输入改成 `input` + 350ms 防抖即时生效，`change`/失焦再做 3–200 夹取回填，解决「输入后不失焦看起来没效果」
- [x] 前端：手选的根人物即使当前 0 条关系也保留（不再被 `degree>0` 过滤掉）；`renderEmpty` 同步清空候选/搜索/胶囊；换书清搜索词，根人物仍按书记住
- [x] 验证：Playwright 实测《三国演义》—— 候选 80 行（共 520 人）、搜索「曹」→14 行、点选曹操 → 胶囊「曹操 ×」并重画关系、再点取消回到自动、多选曹操+刘备、输入 8 不失焦即变 8 节点、刷新后胶囊与上限保持、亮/暗主题截图正常、控制台无报错

## 本轮（每个节点添加下探功能）

- [x] 前端：每个气泡右下角新增「＋」按钮，点击把该人物的**一跳直接关系人**补进画布（不受「最多人物」上限），按钮变「−」可收起这一层；图例显示「下探 N」，页头出现「收起下探」一键还原
- [x] 前端：下探为会话级状态（换书 / 改上限 / 切孤立 / 换根人物自动清空），不写 localStorage；下探补入的节点仍受 200 硬上限保护
- [x] 前端：下探保持原位 —— `state.posCache` 记住上一版坐标（含用户手动拖动），布局签名 `posSig` 变化才清缓存；新节点从外圈按黄金角铺开并做碰撞避让，已有节点不重排
- [x] 前端：`＋/−` 按钮在画布捕获阶段拦截 `pointerdown/mousedown/click`，命中后 `stopPropagation`，不会误触节点点击或拖拽；鼠标悬停放大、`opacity` 提升
- [x] 前端：气泡几何修正 —— `.rel-node.cloud-node` 改 `position:relative`、`.cloud-bubble` 改 `position:absolute; inset:0`（此前内部容器被内容压扁，气泡一直是椭圆），按钮定位 `right:12%/bottom:12%`
- [x] 验证：Playwright 实测《三国演义》—— 曹操（60 条关系，其中 28 已显示）按钮 title「下探：展开 32 个直接关系人」，点击后 30→62 节点 / 47→111 关系、图例「下探 32」、`收起下探`出现且节点明细未被覆盖；点「−」回到 30 节点；`收起下探`清空；二级下探 62→66 / 62→78 正常
- [x] 验证：坐标保持 —— 3 点距离比值下探前后均为 `1.726`（仅整体缩放/居中变化），原有 9 对节点矩形相交在下探后仍是同样 9 对；气泡实测 70×70、`border-radius:50%`、按钮 15×15 在圆内且与名字矩形无重叠；控制台无报错
