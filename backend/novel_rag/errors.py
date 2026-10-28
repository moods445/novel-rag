"""领域错误。API 层把这些映射成 HTTP 状态码。"""

from __future__ import annotations


class NovelRagError(Exception):
    """所有领域错误的基类。"""


class NotFound(NovelRagError):
    pass


class Conflict(NovelRagError):
    pass


class InvalidInput(NovelRagError):
    pass


class ProviderError(NovelRagError):
    """LLM / embedding provider 调用失败。单章失败不应中断整条流水线。"""

    def __init__(self, message: str, *, kind: str = "http_error") -> None:
        super().__init__(message)
        self.kind = kind
