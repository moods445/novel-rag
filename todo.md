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
- [x] CI：`.github/workflows/docker.yml` 在 Actions 构建 amd64/arm64 推 ghcr + 启动烟测（本地禁止构建）
- [x] 验证：CI 跑通（run 40852721727 绿），本地 pull 镜像实测（healthy / 导入 121 章 / 同源 Web / 阅读键盘 / 对话面板隐藏），测完删除容器+卷+镜像
- [x] 后端：新增 `tests/test_web_static.py`（静态托管、穿越防护、/api 仍要 token），全套 134 通过

# 待确认

- [ ] 是否需要「翻页/滚动」排版模式切换开关（当前固定为连贯滚动）
