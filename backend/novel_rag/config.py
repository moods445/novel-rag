"""全局配置。所有可调项集中在此，避免散落在各模块。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    """运行时配置。数据默认不出机（架构决策 #1）。"""

    home: Path = field(default_factory=lambda: Path(os.environ.get("NOVEL_RAG_HOME", "~/.novel-rag")).expanduser())

    # 鉴权：本地后端仍然要求 Bearer token（架构决策 #1）
    auth_token: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_TOKEN", ""))
    # 显式关闭鉴权（仅限受信网络 / 反代后面；Web 部署想省掉 URL 里的 token 时用）
    disable_auth: bool = field(default_factory=lambda: _env_bool("NOVEL_RAG_DISABLE_AUTH", False))

    # Web 部署：同源托管静态前端（Docker 镜像里指向 /app/web）；留空则只提供 /api
    web_root: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_WEB_ROOT", ""))

    # 抽取（云端强模型）
    extract_base_url: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_EXTRACT_BASE_URL", ""))
    extract_api_key: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_EXTRACT_API_KEY", ""))
    extract_model: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_EXTRACT_MODEL", ""))

    # 查询（本地小模型）
    query_base_url: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_QUERY_BASE_URL", ""))
    query_api_key: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_QUERY_API_KEY", ""))
    query_model: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_QUERY_MODEL", ""))

    # embedding：首版只跑本地（架构决策 #12）
    # backend: auto（有模型就用真模型）| bge | hash
    embedding_backend: str = field(
        default_factory=lambda: os.environ.get("NOVEL_RAG_EMBED_BACKEND", "auto").lower()
    )
    embedding_dir: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_EMBED_DIR", ""))
    embedding_model_id: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_EMBED_MODEL", ""))
    embedding_dim: int = field(default_factory=lambda: _env_int("NOVEL_RAG_EMBED_DIM", 0))
    # 单批编码条数（BGE 在 CPU 上 batch=16 吞吐最好）
    embedding_batch: int = field(default_factory=lambda: _env_int("NOVEL_RAG_EMBED_BATCH", 16))

    # 日志：默认 <home>/logs，可用 NOVEL_RAG_LOG_DIR 覆盖
    log_dir: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_LOG_DIR", ""))
    log_level: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_LOG_LEVEL", "INFO").upper())

    # 人名识别后端：auto（jieba ∪ 正则）| jieba | regex
    ner_backend: str = field(
        default_factory=lambda: os.environ.get("NOVEL_RAG_NER_BACKEND", "auto").lower()
    )
    # 章节识别后端：auto（规则优先 + LLM 兜底）| rule | llm
    split_backend: str = field(
        default_factory=lambda: os.environ.get("NOVEL_RAG_SPLIT_BACKEND", "auto").lower()
    )

    # 模型下载变体：fp32 | int8 | quantized
    embedding_variant: str = field(
        default_factory=lambda: os.environ.get("NOVEL_RAG_EMBED_VARIANT", "fp32")
    )
    # 模型下载源：空 = 自动（先直连官方，失败后换国内镜像）；也可显式填 URL
    embedding_endpoint: str = field(
        default_factory=lambda: os.environ.get("NOVEL_RAG_HF_ENDPOINT", os.environ.get("HF_ENDPOINT", ""))
    )

    # 网络代理：http/https 代理地址，空 = 直连（仍遵循系统 http_proxy / https_proxy）
    network_proxy: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_PROXY", ""))

    # LLM：留空则走具体的 EXTRACT_/QUERY_ 配置；填写则用设置页里选中的 provider
    llm_provider: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_LLM_PROVIDER", ""))
    llm_model: str = field(default_factory=lambda: os.environ.get("NOVEL_RAG_LLM_MODEL", ""))
    # 设置页添加的 provider（JSON 列表，存本机数据库）——应用里唯一的常规 provider 来源
    custom_providers: str = field(
        default_factory=lambda: os.environ.get("NOVEL_RAG_CUSTOM_PROVIDERS", "")
    )
    # 仅本地测试/调试：显式指定 opencode 配置文件；留空 = 不读、不探测任何默认路径
    opencode_config: str = field(
        default_factory=lambda: os.environ.get("NOVEL_RAG_OPENCODE_CONFIG", "")
    )
    # 抽取是长 prompt + 长 JSON，必须给足超时；查询要快
    llm_timeout: float = field(default_factory=lambda: float(os.environ.get("NOVEL_RAG_LLM_TIMEOUT", "240")))
    llm_timeout_query: float = field(
        default_factory=lambda: float(os.environ.get("NOVEL_RAG_LLM_TIMEOUT_QUERY", "120"))
    )

    # 上下文预算：ContextCompiler 的裁剪上限
    context_budget_chars: int = field(default_factory=lambda: _env_int("NOVEL_RAG_CONTEXT_BUDGET", 12000))
    # 单段证据最小保留长度，过短的不进上下文
    min_evidence_chars: int = field(default_factory=lambda: _env_int("NOVEL_RAG_MIN_EVIDENCE", 12))
    # 语义召回条数
    semantic_top_k: int = field(default_factory=lambda: _env_int("NOVEL_RAG_SEMANTIC_TOP_K", 8))
    # 词法召回条数
    lexical_top_k: int = field(default_factory=lambda: _env_int("NOVEL_RAG_LEXICAL_TOP_K", 8)
                               )

    # 章节识别
    split_min_confidence: float = field(default_factory=lambda: float(os.environ.get("NOVEL_RAG_SPLIT_MIN_CONF", "0.5")))
    # 段落合并阈值：短行合并成段，避免对话体被切成一句一段
    para_min_chars: int = field(default_factory=lambda: _env_int("NOVEL_RAG_PARA_MIN_CHARS", 40))

    # 质量开关（默认全开；关闭后行为退回基线）
    evidence_grounding: bool = field(default_factory=lambda: _env_bool("NOVEL_RAG_EVIDENCE_GROUNDING", True))
    spoiler_guard_default: bool = field(default_factory=lambda: _env_bool("NOVEL_RAG_SPOILER_GUARD", True))

    @property
    def db_path(self) -> Path:
        return self.home / "novel-rag.db"

    @property
    def resolved_log_dir(self) -> Path:
        if self.log_dir:
            return Path(self.log_dir).expanduser()
        return self.home / "logs"

    @property
    def log_file(self) -> Path:
        return self.resolved_log_dir / "backend.log"

    @property
    def resolved_embed_dir(self) -> Path:
        if self.embedding_dir:
            return Path(self.embedding_dir).expanduser()
        return self.home / "models" / "bge-small-zh-v1.5"

    @property
    def web_root_path(self) -> Path | None:
        """静态前端根目录；未配置或不存在时返回 None（只提供 /api）。"""
        if not self.web_root:
            return None
        path = Path(self.web_root).expanduser()
        return path.resolve() if path.is_dir() else None

    def apply_overrides(self, overrides: dict) -> "Settings":
        for key, value in (overrides or {}).items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self

    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        return self.home


_settings: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    global _settings
    if _settings is None or reload:
        _settings = Settings()
    return _settings
