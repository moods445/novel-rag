"""AI 小说阅读器后端。

六个功能平面（见 docs/architecture.md）：
    ingest    P0 摄入
    extract   P1 抽取（后台长任务）
    store     P2 存储与索引
    retrieve  P3 检索与上下文（ContextCompiler 是唯一 LLM 出口）
    qa        P4 消费
    api       P5/P6 对外契约与支撑
"""

__version__ = "0.1.0"
