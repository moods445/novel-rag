# 桌面端（Tauri 2）

薄壳 + 本地 Python sidecar。壳只做三件事：拉起 sidecar、读 stdout 的 `PORT:`、
把端口与一次性 token 交给前端。业务全在后端，前端只调 HTTP。

## 结构

```
desktop/
├── src-tauri/
│   ├── src/main.rs          sidecar 生命周期 + backend_info command
│   ├── tauri.conf.json      窗口 / 打包 / externalBin
│   ├── capabilities/        只有 core:default（不用任何插件）
│   └── icons/               由 generate_icons.py 生成
├── web/
│   ├── index.html           四个平面：书架 / 阅读 / 分析 / 设置
│   ├── styles.css
│   ├── js/api.js            带 Bearer token 的 fetch 封装
│   ├── js/app.js            启动、书架、导航、导入
│   ├── js/reader.js         foliate-js 渲染 + CFI → seq 换算
│   ├── js/analysis.js       问答、引用跳转、任务、关系、时间线
│   ├── js/settings.js       模型选择 + 下载进度
│   └── vendor/foliate-js/   构建前拉取，不入库
└── scripts/prepare_vendor.py
```

## 本地开发

```bash
# 1. 前端依赖（foliate-js，MIT）
python3 desktop/scripts/prepare_vendor.py

# 2. 起后端（开发期壳会自动用 python -m novel_rag 拉起它，
#    也可以手动起，然后用浏览器调试）
cd backend && NOVEL_RAG_TOKEN=dev python3 -m novel_rag --port 8799

# 3. 浏览器里调试前端（不需要 Rust）
#    http://127.0.0.1:8000/desktop/web/#port=8799&token=dev
python3 -m http.server 8000
```

> 浏览器调试时 `window.__TAURI__` 不存在，`app.js` 会退化为从 URL hash 读端口与 token。

## 打包

**本地不打包。** 一律走 GitHub Actions（`.github/workflows/build-desktop.yml`）：

| 平台 | runner | 产物 |
|---|---|---|
| macOS Intel（12+） | `macos-13` | `novelrag-macos-x86_64.zip`（内含 `NovelRag.app`） |
| Windows x64 | `windows-latest` | `novelrag-windows-x64.zip`（免安装，exe + sidecar） |

推 tag 会自动发 Release：

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## 关键取舍

- **不用 tauri-plugin-shell**：直接 `std::process::Command`，少一层权限与 CI 变量；
  Windows 上加 `CREATE_NO_WINDOW` 避免弹出黑窗口。
- **不阻塞 UI**：端口由后台线程写入共享槽位，前端轮询 `backend_info`（最多 60s）。
- **便携优先**：sidecar 与主程序同目录，`current_exe().parent()` 定位；
  macOS 上该目录就是 `Contents/MacOS`。
- **静态前端无打包步骤**：没有 Vite/React，`frontendDist` 直接指向 `web/`，
  CI 少一个失败面。
- **Windows 不打安装包**：`tauri build --no-bundle` 后自行组 zip，
  `webviewInstallMode: skip`（要求系统已有 WebView2）。
- **macOS 用 ad-hoc 签名**（`signingIdentity: "-"`）：无证书也能过 Gatekeeper 的
  「已损坏」判定，用户仍需右键打开或去隔离属性。
