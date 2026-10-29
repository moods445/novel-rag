"""网络请求辅助：统一的代理处理。

代理为空时沿用系统环境变量（``http_proxy`` / ``https_proxy``）；
显式配置代理时覆盖环境变量，模型下载与 LLM 请求都走这里。
"""

from __future__ import annotations

import urllib.request


def build_opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    proxy = (proxy or "").strip()
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )
