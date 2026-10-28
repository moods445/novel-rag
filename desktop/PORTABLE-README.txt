Novel RAG · 便携版（Windows）
============================

启动
----
1. 保持 NovelRag.exe 与 novel-rag-sidecar.exe 在同一个文件夹里（缺一不可）。
2. 双击 NovelRag.exe。

首次启动
--------
- 会在 %APPDATA%\com.novelrag.desktop 下创建数据目录，并后台下载
  约 95MB 的 BGE 中文向量模型（用于语义检索）。下载期间即可阅读，
  只是语义检索要等模型就绪。
- 若提示缺少 WebView2：安装 Microsoft Edge WebView2 Runtime
  （Windows 10 1803+ / Windows 11 通常已自带）。

数据位置
--------
%APPDATA%\com.novelrag.desktop\novel-rag.db    书籍与知识库
%APPDATA%\com.novelrag.desktop\models\...      向量模型

导入小说
--------
书架 → 导入小说，支持 TXT（自动识别 UTF-8 / GB18030 / UTF-16）。
导入后立即可以阅读与提问，知识库在后台逐章建立。

推理模型
--------
默认读取 %USERPROFILE%\.config\opencode\opencode.json 里的 provider。
没有配置时退化为离线模式：抽取走本地规则，问答只做原文摘录，不联网。
