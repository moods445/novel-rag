"""网络请求辅助：统一代理、User-Agent 与 CA 证书。

- 代理为空时沿用系统环境变量（``http_proxy`` / ``https_proxy``）；显式配置时覆盖。
- 打包环境（PyInstaller / macOS）常常找不到系统 CA，优先用 certifi 证书包。
- 部分镜像（hf-mirror）会拒绝 Python 默认 User-Agent，统一发一个常见 UA。
"""

from __future__ import annotations

import ssl
import urllib.request

# 有些下载站按 UA 做拦截：urllib 默认的 "Python-urllib/3.x" 会被 hf-mirror 直接 403
USER_AGENT = "novel-rag/0.1 (+https://github.com/moods445/novel-rag)"


def _ssl_context() -> ssl.SSLContext:
    cafile = None
    try:
        import certifi  # 打包后由 --collect-data certifi 带上

        cafile = certifi.where()
    except ImportError:
        pass
    return ssl.create_default_context(cafile=cafile)


def build_opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [urllib.request.HTTPSHandler(context=_ssl_context())]
    proxy = (proxy or "").strip()
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def open_url(
    url: str,
    *,
    proxy: str | None = None,
    timeout: float = 120,
    headers: dict[str, str] | None = None,
):
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    return build_opener(proxy).open(request, timeout=timeout)
