"""运行时设置（界面可改）：存库、可校验、可回退默认。

优先级：数据库覆盖 > 环境变量 > 代码默认。
界面提交的改动立刻写库并重建组件（网关 / embedding / 流水线），
不需要重启进程。
"""

from __future__ import annotations

import json
from typing import Any

from .config import Settings
from .db.conn import Database
from .errors import InvalidInput

# 允许界面修改的键，以及合法取值
ENUMS: dict[str, set[str]] = {
    "ner_backend": {"auto", "jieba", "regex"},
    "split_backend": {"auto", "rule", "llm"},
    "embedding_backend": {"auto", "bge", "hash"},
}
FREE_TEXT: dict[str, int] = {
    "embedding_model_id": 64,
    "llm_provider": 64,
    "llm_model": 128,
    "embedding_variant": 16,
    "embedding_dir": 512,
    # 模型下载源：空 = 自动（官方直连失败后换镜像）
    "embedding_endpoint": 512,
    # 网络代理：http/https 代理地址
    "network_proxy": 256,
    # 界面添加的 provider（JSON 列表，含 api_key，仅存本地库）
    "custom_providers": 20000,
}
ALLOWED = set(ENUMS) | set(FREE_TEXT)


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        # 上一次通过覆盖值写入内存的键；被删除时要回退默认值
        self._applied: set[str] = set()

    # ------------------------------------------------------------ 读写

    def get_all(self) -> dict[str, str]:
        rows = self.db.query("SELECT key, value FROM app_settings ORDER BY key")
        return {r["key"]: r["value"] for r in rows}

    def update(self, changes: dict[str, Any]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, value in (changes or {}).items():
            if key not in ALLOWED:
                raise InvalidInput(f"不支持的设置项: {key}")
            text = "" if value is None else str(value).strip()
            if text == "":
                cleaned[key] = ""  # 空值 = 清除覆盖，回退到默认
                continue
            if key in ENUMS and text not in ENUMS[key]:
                raise InvalidInput(f"{key} 只能是 {sorted(ENUMS[key])}")
            if key in FREE_TEXT and len(text) > FREE_TEXT[key]:
                raise InvalidInput(f"{key} 过长")
            cleaned[key] = text

        with self.db.tx() as conn:
            for key, text in cleaned.items():
                if text == "":
                    conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
                else:
                    conn.execute(
                        "INSERT INTO app_settings(key, value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, text),
                    )
        return self.get_all()

    def apply(self, settings: Settings) -> Settings:
        """把库里的覆盖值写回 Settings 实例（原地修改并返回）。

        被清除的覆盖项要回退到默认值（否则内存里会残留上一次的取值）。
        """
        overrides = self.get_all()
        defaults = Settings()
        for key in self._applied - set(overrides):
            if hasattr(settings, key):
                setattr(settings, key, getattr(defaults, key))
        for key, value in overrides.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        self._applied = set(overrides)
        return settings

    # ------------------------------------------------------------ 展示

    def describe(self, settings: Settings) -> dict:
        from . import embedding_models
        from .llm import opencode
        from .logging_setup import log_paths

        embed_status = embedding_models.model_status(settings.resolved_embed_dir)
        # provider 来自设置页（数据库）；只有本地测试显式指定 opencode 配置时才多一份
        providers = opencode.describe(settings.opencode_config, settings.custom_providers)
        # custom_providers 里含 api_key，不回显；providers 列表只暴露 has_key
        overrides = self.get_all()
        overrides.pop("custom_providers", None)

        return {
            "values": {
                "ner_backend": settings.ner_backend,
                "split_backend": settings.split_backend,
                "embedding_backend": settings.embedding_backend,
                "embedding_model_id": settings.embedding_model_id,
                "llm_provider": settings.llm_provider,
                "llm_model": settings.llm_model,
                "embedding_variant": settings.embedding_variant,
                "embedding_endpoint": settings.embedding_endpoint,
                "network_proxy": settings.network_proxy,
            },
            "overrides": overrides,
            "options": {
                "ner_backend": [
                    {"id": "auto", "label": "jieba + 正则（推荐）", "requires": ["jieba"]},
                    {"id": "jieba", "label": "jieba 词性标注", "requires": ["jieba"]},
                    {"id": "regex", "label": "纯正则（零依赖）", "requires": []},
                ],
                "split_backend": [
                    {"id": "auto", "label": "规则优先，置信度低时用 LLM"},
                    {"id": "rule", "label": "只用规则"},
                    {"id": "llm", "label": "总是用 LLM"},
                ],
                "embedding_backend": [
                    {
                        "id": "auto",
                        "label": "有模型就用 BGE，否则退回占位",
                        "installed": embed_status["installed"],
                    },
                    {"id": "bge", "label": "BGE-small-zh-v1.5（512 维）", "installed": embed_status["installed"]},
                    {"id": "hash", "label": "占位哈希（联调用）", "installed": True},
                ],
                "llm": providers,
                "embedding_endpoint": [
                    {"id": "", "label": "自动（先直连官方，失败自动换国内镜像）"},
                    {"id": "https://huggingface.co", "label": "HuggingFace 官方"},
                    {"id": "https://hf-mirror.com", "label": "国内镜像 hf-mirror.com"},
                ],
            },
            "embedding": {
                **embed_status,
                "endpoint": settings.embedding_endpoint,
                "download": embedding_models.download_state(settings.resolved_embed_dir),
            },
            "ner_runtime": _ner_runtime(),
            "logging": log_paths(settings),
        }


def _ner_runtime() -> dict:
    try:
        import jieba  # noqa: F401, PLC0415

        available = True
    except ImportError:
        available = False
    return {"jieba_available": available}


def dump(values: dict) -> str:
    return json.dumps(values, ensure_ascii=False)
