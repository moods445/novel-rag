"""从 opencode 配置里读取 provider（默认 ``~/.config/opencode/opencode.json``）。

opencode 的 provider 声明是 OpenAI 兼容的：

    "provider": {
      "magpie": {
        "npm": "@ai-sdk/openai-compatible",
        "options": {"baseURL": "...", "apiKey": "..."},
        "models": {"qnaigc/qiniu/conatus-free": {"name": "..."}}
      }
    }

因此这里只做两件事：解析配置（兼容 jsonc 注释与尾逗号），
把 provider 翻译成 :class:`OpenAICompatProvider`。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATHS = (
    Path("~/.config/opencode/opencode.json").expanduser(),
    Path("~/.config/opencode/opencode.jsonc").expanduser(),
    Path("~/.opencode/opencode.json").expanduser(),
)


@dataclass(slots=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key: str = ""
    models: list[str] = field(default_factory=list)
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label or self.name,
            "base_url": self.base_url,
            "models": self.models,
            "has_key": bool(self.api_key),
        }


def _strip_jsonc(text: str) -> str:
    """去掉 // 与 /* */ 注释和尾逗号（配置里很常见）。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)(^|[^:])//.*$", r"\1", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def load_config(path: str | Path | None = None) -> dict:
    candidates = [Path(path).expanduser()] if path else list(DEFAULT_PATHS)
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            return json.loads(_strip_jsonc(candidate.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return {}


def providers(path: str | Path | None = None) -> dict[str, ProviderSpec]:
    config = load_config(path)
    out: dict[str, ProviderSpec] = {}
    for name, raw in (config.get("provider") or {}).items():
        if not isinstance(raw, dict):
            continue
        options = raw.get("options") or {}
        base_url = str(options.get("baseURL") or options.get("base_url") or "")
        if not base_url:
            continue
        out[name] = ProviderSpec(
            name=name,
            base_url=base_url,
            api_key=str(options.get("apiKey") or options.get("api_key") or ""),
            models=list((raw.get("models") or {}).keys()),
            label=str(raw.get("name") or name),
        )
    return out


def default_model(path: str | Path | None = None) -> str:
    return str(load_config(path).get("model") or "")


def resolve(
    provider_name: str | None = None,
    model: str | None = None,
    *,
    path: str | Path | None = None,
):
    """返回 ``(spec, model)``；找不到返回 ``(None, "")``。"""
    available = providers(path)
    if not available:
        return None, ""
    spec = available.get(provider_name or "")
    if spec is None:
        spec = available.get("magpie") or next(iter(available.values()))
    model = model or (spec.models[0] if spec.models else default_model(path))
    return spec, model


def describe(path: str | Path | None = None) -> dict:
    """给设置接口用：所有 provider 与模型。"""
    config_path = Path(path).expanduser() if path else next(
        (p for p in DEFAULT_PATHS if p.exists()), None
    )
    return {
        "config_path": str(config_path) if config_path else None,
        "default_model": default_model(path),
        "providers": [spec.to_dict() for spec in providers(path).values()],
    }
