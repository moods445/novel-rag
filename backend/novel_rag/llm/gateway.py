"""LLM 网关（决策 #11：抽取走云端强模型，查询走本地小模型）。

provider 抽象成 ``complete(prompt) -> str``。本地后端零依赖即可运行：
- ``EchoProvider``   ：返回哨兵，调用方走启发式兜底（离线链路验证）
- ``StubProvider``   ：测试用，返回预设文本
- ``OpenAICompatProvider``：真实端点（Ollama / vLLM / 云端兼容接口）

用量按 provider 名累加，供成本核算使用。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from ..config import Settings
from ..errors import ProviderError
from ..net import USER_AGENT, build_opener
from . import opencode

ECHO_SENTINEL = "__ECHO__"


class LlmProvider(Protocol):
    name: str

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str: ...


@dataclass(slots=True)
class EchoProvider:
    """离线哨兵。extractor 收到哨兵后改走启发式抽取。"""

    name: str = "echo"

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        return ECHO_SENTINEL


@dataclass(slots=True)
class StubProvider:
    """测试用固定输出。"""

    response: str = "{}"
    name: str = "stub"

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        return self.response


@dataclass(slots=True)
class OpenAICompatProvider:
    """OpenAI 兼容 /chat/completions（Ollama、vLLM、DashScope 兼容模式都适用）。"""

    base_url: str
    model: str
    api_key: str = ""
    name: str = "openai-compat"
    timeout: float = 120.0
    temperature: float = 0.2
    proxy: str = ""

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        if not self.base_url or not self.model:
            raise ProviderError("provider 未配置 base_url / model", kind="config")
        url = self.base_url.rstrip("/") + "/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": max_tokens,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with build_opener(self.proxy).open(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # 4xx/5xx
            raise ProviderError(f"HTTP {exc.code}", kind="http_error") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"网络失败: {exc}", kind="timeout") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("响应不是 JSON", kind="parse_error") from exc

        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("响应结构异常", kind="parse_error") from exc

        content = (message.get("content") or "").strip()
        if not content:
            # 有些推理模型把正文放在 reasoning_content 里（magpie 的 free 模型就是这样）
            content = (message.get("reasoning_content") or "").strip()
        return content


@dataclass(slots=True)
class LlmGateway:
    """按用途分档：``extract`` 用于抽取，``query`` 用于问答。"""

    extract: LlmProvider = field(default_factory=EchoProvider)
    query: LlmProvider = field(default_factory=EchoProvider)
    usage: dict[str, int] = field(default_factory=dict)

    def complete(
        self,
        purpose: str,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        provider = self.extract if purpose == "extract" else self.query
        self.usage[provider.name] = self.usage.get(provider.name, 0) + 1
        return provider.complete(prompt, system=system, max_tokens=max_tokens)


def query_reason(settings: Settings) -> str:
    """问答模型为什么没生效（设置页提示与问答降级共用同一套判断）。

    必须只给一条具体原因：用户「明明配了 provider」却看到笼统的「未配置」时，
    这里就是唯一能指出「没选中 / provider 找不到 / 没有模型名 / 环境变量只配一半」的地方。
    """
    if settings.query_base_url and not settings.query_model:
        return "已配置 NOVEL_RAG_QUERY_BASE_URL，但缺少 NOVEL_RAG_QUERY_MODEL"
    if settings.query_model and not settings.query_base_url:
        return "已配置 NOVEL_RAG_QUERY_MODEL，但缺少 NOVEL_RAG_QUERY_BASE_URL"

    available = opencode.merged(settings.opencode_config or None, settings.custom_providers or None)
    if not settings.llm_provider:
        if available:
            names = "、".join(list(available)[:3])
            return f"已经添加了 {len(available)} 个 provider（{names}），但还没选中，请在设置里选择"
        return "还没在设置里添加 provider，也没有配置 NOVEL_RAG_QUERY_* 环境变量"

    spec = available.get(settings.llm_provider)
    if spec is None:
        return f"provider「{settings.llm_provider}」在应用内的 provider 列表里找不到（可能已被删除）"
    # 模型名可能来自 provider 的 models 声明，也可能来自本地测试配置的默认 model
    _spec, model = opencode.resolve(
        settings.llm_provider,
        settings.llm_model or None,
        path=settings.opencode_config or None,
        custom=settings.custom_providers or None,
    )
    if not model:
        return f"provider「{settings.llm_provider}」没有可用模型：请在设置里选择或直接输入模型名"
    # 地址与模型都能解析出来：这里不该被调用（网关已生效），返回空以免误导
    return ""


def from_settings(settings: Settings) -> LlmGateway:
    """provider 解析顺序：

    1. ``NOVEL_RAG_EXTRACT_*`` / ``NOVEL_RAG_QUERY_*`` 显式配置（最高优先级）；
    2. 设置页里选中的 provider（``llm_provider`` + 应用内添加的 ``custom_providers``）；
    3. 显式的本地测试配置 ``NOVEL_RAG_OPENCODE_CONFIG``（默认不读任何文件）；
    4. 都没有 → 离线哨兵，抽取走启发式、问答走抽取式摘录。
    """
    extract: LlmProvider | None = None
    query: LlmProvider | None = None

    if settings.extract_base_url and settings.extract_model:
        extract = OpenAICompatProvider(
            base_url=settings.extract_base_url,
            model=settings.extract_model,
            api_key=settings.extract_api_key,
            name=f"extract:{settings.extract_model}",
            timeout=settings.llm_timeout,
            proxy=settings.network_proxy,
        )
    if settings.query_base_url and settings.query_model:
        query = OpenAICompatProvider(
            base_url=settings.query_base_url,
            model=settings.query_model,
            api_key=settings.query_api_key,
            name=f"query:{settings.query_model}",
            timeout=settings.llm_timeout_query,
            proxy=settings.network_proxy,
        )

    if (extract is None or query is None) and settings.llm_provider:
        spec, model = opencode.resolve(
            settings.llm_provider,
            settings.llm_model or None,
            path=settings.opencode_config or None,
            custom=settings.custom_providers or None,
        )
        if spec is not None and model:
            shared = OpenAICompatProvider(
                base_url=spec.base_url,
                model=model,
                api_key=spec.api_key,
                name=f"{spec.name}:{model}",
                timeout=settings.llm_timeout,
                proxy=settings.network_proxy,
            )
            extract = extract or shared
            query = query or OpenAICompatProvider(
                base_url=spec.base_url,
                model=model,
                api_key=spec.api_key,
                name=f"{spec.name}:{model}",
                timeout=settings.llm_timeout_query,
                proxy=settings.network_proxy,
            )

    return LlmGateway(
        extract=extract or EchoProvider(name="echo-extract"),
        query=query or EchoProvider(name="echo-query"),
    )
