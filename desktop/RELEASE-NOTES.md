## 产物

| 平台 | 包名 | 说明 |
|---|---|---|
| macOS（Apple Silicon 与 Intel 请分别选择对应包） | `novelrag-macos-x86_64.zip` | 解压得到 `NovelRag.app`，需 macOS 12+（Intel/x86_64） |
| Windows x64 | `novelrag-windows-x64.zip` | 免安装，解压后运行 `NovelRag.exe` |

## 安装

**macOS**

1. 解压后把 `NovelRag.app` 拖进「应用程序」。
2. 首次打开若提示「无法验证开发者」：右键 →「打开」，或执行
   `xattr -dr com.apple.quarantine /Applications/NovelRag.app`。

**Windows**

1. 解压到任意目录（`NovelRag.exe` 与 `novel-rag-sidecar.exe` 必须放在一起）。
2. 直接运行 `NovelRag.exe`。
3. 需要系统已安装 Microsoft Edge WebView2 Runtime（Windows 10 1803+ 通常自带）。

## 首次启动

- 会在应用数据目录创建数据库，并**后台下载约 95MB 的 BGE 中文向量模型**
  （`bge-small-zh-v1.5`，ONNX）。下载期间即可阅读，只影响语义检索。
- 推理模型在「设置 → 推理模型」里添加 provider（Base URL / API Key / 模型名），
  保存在本机数据库，重启不丢。没有配置时退化为离线模式：抽取走本地规则，问答只做原文摘录，不联网。

## 数据位置

- macOS：`~/Library/Application Support/com.novelrag.desktop/`
- Windows：`%APPDATA%\com.novelrag.desktop\`

## 已知限制

- 仅提供 x86_64 产物；Apple Silicon 上通过 Rosetta 运行。
- 构建未做代码签名与公证，因此 macOS 需要上面的放行步骤。
