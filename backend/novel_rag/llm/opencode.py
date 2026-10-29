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
    custom: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label or self.name,
            "base_url": self.base_url,
            "models": self.models,
            "has_key": bool(self.api_key),
            "custom": self.custom,
        }

    def to_storage(self) -> dict:
        """落库格式（含 api_key，仅存本地库文件）。"""
        return {
            "name": self.name,
            "label": self.label or self.name,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "models": self.models,
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


def parse_custom(raw: str | dict | list | None) -> dict[str, ProviderSpec]:
    """解析界面添加的 provider（JSON 字符串或已解析对象）。

    支持两种结构：``[{name, base_url, api_key, models, label}, ...]``
    或 ``{name: {base_url, ...}}``；非法项直接跳过。
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, dict):
        if isinstance(raw.get("providers"), list):
            items: list = raw["providers"]
        else:
            items = [
                {**spec, "name": name} for name, spec in raw.items() if isinstance(spec, dict)
            ]
    elif isinstance(raw, list):
        items = raw
    else:
        return {}

    out: dict[str, ProviderSpec] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        base_url = str(item.get("base_url") or item.get("baseURL") or "").strip()
        if not name or not base_url:
            continue
        models = item.get("models") or []
        if isinstance(models, str):
            models = [m.strip() for m in re.split(r"[,\n]", models) if m.strip()]
        out[name] = ProviderSpec(
            name=name,
            base_url=base_url,
            api_key=str(item.get("api_key") or item.get("apiKey") or ""),
            models=[str(m) for m in models if str(m).strip()],
            label=str(item.get("label") or name),
            custom=True,
        )
    return out


def merged(
    path: str | Path | None = None, custom: str | dict | list | None = None
) -> dict[str, ProviderSpec]:
    """opencode 配置 + 界面添加的 provider；同名时以界面添加的为准。"""
    out = providers(path)
    out.update(parse_custom(custom))
    return out


def default_model(path: str | Path | None = None) -> str:
    return str(load_config(path).get("model") or "")


def resolve(
    provider_name: str | None = None,
    model: str | None = None,
    *,
    path: str | Path | None = None,
    custom: str | dict | list | None = None,
):
    """返回 ``(spec, model)``；找不到返回 ``(None, "")``。"""
    available = merged(path, custom)
    if not available:
        return None, ""
    spec = available.get(provider_name or "")
    if spec is None:
        spec = available.get("magpie") or next(iter(available.values()))
    if not model:
        if spec.models:
            model = spec.models[0]
        elif not spec.custom:
            # opencode 的默认 model 只适用于它自己的 provider
            model = default_model(path)
    return spec, model


def describe(
    path: str | Path | None = None, custom: str | dict | list | None = None
) -> dict:
    """给设置接口用：所有 provider 与模型。"""
    config_path = Path(path).expanduser() if path else next(
        (p for p in DEFAULT_PATHS if p.exists()), None
    )
    return {
        "config_path": str(config_path) if config_path else None,
        "default_model": default_model(path),
        "providers": [spec.to_dict() for spec in merged(path, custom).values()],
    }
