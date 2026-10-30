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

## 镜像构建提速（拆分缓存层 / 去掉 arm64）

- [x] `Dockerfile`：拆回分层 —— 依赖层只依赖 `backend/pyproject.toml`，vendor 层只依赖 `prepare_vendor.py`，源码层才装本地包并组装静态目录（之前合并成单层，导致改一行源码就重装 onnxruntime 等依赖）
- [x] CI：只构建 `linux/amd64`，去掉 arm64 与 QEMU 模拟（部署机都是 x86，arm64 靠模拟要多花约 250s）
- [ ] 验证：push 后 GitHub Actions `build-image` 跑通（测试 + 镜像 + 烟测），并记录构建耗时
