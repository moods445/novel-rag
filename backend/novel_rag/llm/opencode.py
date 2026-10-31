"""provider 定义解析。

应用里用到的 provider **只有两个来源**：

1. 设置页手动添加、存在本机数据库里的 provider（``custom_providers``，主力路径）；
2. 显式指定的本机 opencode 配置文件（``NOVEL_RAG_OPENCODE_CONFIG``）——
   仅用于本地测试 / 调试，**不会**去自动扫描 ``~/.config/opencode``。

第 2 种能认两种 schema（旧版 ``provider`` + ``options``，v2 ``providers`` + ``settings``）：

    "provider": {
      "magpie": {"options": {"baseURL": "...", "apiKey": "..."}, "models": {...}}
    }

本模块只做两件事：解析配置（兼容 jsonc 注释与尾逗号），
把 provider 翻译成 :class:`ProviderSpec`。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key: str = ""
    models: list[str] = field(default_factory=list)
    label: str = ""
    # True = 设置页添加的（应用内配置）；False = 本地测试用的 opencode 配置
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
    """读取显式指定的配置文件；没给路径就不读（不做任何默认路径探测）。"""
    if not path:
        return {}
    candidate = Path(path).expanduser()
    if not candidate.exists():
        return {}
    try:
        return json.loads(_strip_jsonc(candidate.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return {}


def _model_names(raw: dict) -> list[str]:
    """``models`` 可能是 ``{id: {...}}``（旧版）也可能是列表（新版/手写）。"""
    models = raw.get("models") or []
    if isinstance(models, dict):
        return [str(k) for k in models.keys()]
    if not isinstance(models, list):
        return []
    out: list[str] = []
    for item in models:
        if isinstance(item, dict):
            name = item.get("id") or item.get("name")
            if name:
                out.append(str(name))
        elif str(item).strip():
            out.append(str(item).strip())
    return out


def _spec_from(name: str, raw: dict) -> ProviderSpec | None:
    """从一条 provider 声明里取出 base_url / api_key / models。

    两种 schema 都要认，否则「明明配了却查不到」：
    - 旧版：``"provider": {"x": {"npm": ..., "options": {"baseURL": ...}}}``
    - v2  ：``"providers": {"x": {"package": ..., "settings": {"baseURL": ...}}}``
    """
    options = raw.get("options") if isinstance(raw.get("options"), dict) else {}
    settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    merged = {**settings, **options}  # options 优先（同名字段冲突时按旧版语义）
    base_url = str(
        merged.get("baseURL") or merged.get("baseUrl") or merged.get("base_url") or ""
    ).strip()
    if not base_url:
        return None
    return ProviderSpec(
        name=name,
        base_url=base_url,
        api_key=str(merged.get("apiKey") or merged.get("api_key") or ""),
        models=_model_names(raw),
        label=str(raw.get("name") or name),
    )


def providers(path: str | Path | None = None) -> dict[str, ProviderSpec]:
    """本地测试用的 opencode 配置里的 provider（``path`` 为空则返回空字典）。"""
    config = load_config(path)
    out: dict[str, ProviderSpec] = {}
    # 旧版用 provider，v2 用 providers；同名以先出现的为准
    for key in ("provider", "providers"):
        block = config.get(key)
        if not isinstance(block, dict):
            continue
        for name, raw in block.items():
            if not isinstance(raw, dict) or name in out:
                continue
            spec = _spec_from(str(name), raw)
            if spec is not None:
                out[str(name)] = spec
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
    """应用内 provider（``custom``）+ 可选的本地测试配置；同名时以应用内的为准。"""
    out = providers(path)
    out.update(parse_custom(custom))
    return out


def default_model(path: str | Path | None = None) -> str:
    """本地测试配置里声明的默认 model（没有就空串）。"""
    raw = load_config(path).get("model")
    if isinstance(raw, dict):  # v2 里可能写成 {"provider": ..., "id": ...}
        raw = raw.get("id") or raw.get("name")
    return str(raw or "")


def resolve(
    provider_name: str | None = None,
    model: str | None = None,
    *,
    path: str | Path | None = None,
    custom: str | dict | list | None = None,
):
    """返回 ``(spec, model)``；找不到就 ``(None, "")``（不再随便挑一个顶上）。"""
    available = merged(path, custom)
    spec = available.get(provider_name or "")
    if spec is None:
        return None, ""
    if not model:
        if spec.models:
            model = spec.models[0]
        elif not spec.custom:
            # opencode 的默认 model 只适用于它自己的 provider（本地测试路径）
            model = default_model(path)
    return spec, model


def describe(
    path: str | Path | None = None, custom: str | dict | list | None = None
) -> dict:
    """给设置接口用：所有 provider 与模型。"""
    config_path = Path(path).expanduser() if path else None
    return {
        "config_path": str(config_path) if config_path and config_path.exists() else None,
        "default_model": default_model(path),
        "providers": [spec.to_dict() for spec in merged(path, custom).values()],
    }
