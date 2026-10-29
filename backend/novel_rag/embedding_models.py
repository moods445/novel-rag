"""embedding 模型管理：状态查询 + 自动下载（带进度）。

同一份逻辑被三处复用：命令行脚本、HTTP 接口、启动时自动补齐。
只依赖标准库，下载进度存在进程内的 ``_downloads``，接口轮询即可。
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .net import USER_AGENT, build_opener

REPO = "Xenova/bge-small-zh-v1.5"
# 未显式配置下载源时：先直连官方，失败后自动换国内镜像（国内网络直连常常不可用）
DEFAULT_ENDPOINTS = ("https://huggingface.co", "https://hf-mirror.com")
VARIANTS = {
    "fp32": "onnx/model.onnx",
    "int8": "onnx/model_int8.onnx",
    "quantized": "onnx/model_quantized.onnx",
}
ASSETS = ["tokenizer.json", "config.json", "special_tokens_map.json", "vocab.txt"]
REQUIRED = ["model.onnx", "tokenizer.json"]


@dataclass(slots=True)
class DownloadState:
    state: str = "idle"  # idle | running | done | failed
    percent: int = 0
    downloaded: int = 0
    total: int = 0
    current: str = ""
    error: str = ""
    path: str = ""
    endpoint: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["downloaded_mb"] = round(self.downloaded / 1e6, 2)
        data["total_mb"] = round(self.total / 1e6, 2)
        return data


_downloads: dict[str, DownloadState] = {}
_lock = threading.Lock()


def model_status(dest: str | Path) -> dict:
    dest = Path(dest).expanduser()
    present = {name: (dest / name).stat().st_size if (dest / name).exists() else 0 for name in REQUIRED}
    installed = all(present.values())
    total_bytes = sum(f.stat().st_size for f in dest.glob("*") if f.is_file()) if dest.exists() else 0
    return {
        "dir": str(dest),
        "model_id": dest.name,
        "installed": installed,
        "missing": [name for name, size in present.items() if not size],
        "size_mb": round(total_bytes / 1e6, 1),
        "repo": REPO,
    }


def download_state(dest: str | Path) -> dict:
    key = str(Path(dest).expanduser())
    with _lock:
        state = _downloads.get(key)
    return state.to_dict() if state else DownloadState(path=key).to_dict()


def _set(dest: Path, **kwargs) -> None:
    with _lock:
        state = _downloads.get(str(dest))
        if state is None:
            state = DownloadState(path=str(dest))
            _downloads[str(dest)] = state
        for key, value in kwargs.items():
            setattr(state, key, value)
        state.updated_at = time.time()


def _endpoints(endpoint: str | None) -> list[str]:
    """下载源候选：显式配置只用一个；未配置则官方 + 国内镜像依次尝试。"""
    if endpoint and endpoint.strip():
        return [endpoint.strip().rstrip("/")]
    return list(DEFAULT_ENDPOINTS)


def _fetch(url: str, target: Path, dest: Path, *, opener, timeout: float = 30) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        _set(dest, current=target.name, downloaded=target.stat().st_size)
        return target.stat().st_size

    tmp = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(request, timeout=timeout) as resp, tmp.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        _set(dest, current=target.name, total=total, downloaded=0, percent=0)
        read = 0
        while chunk := resp.read(1 << 16):
            fh.write(chunk)
            read += len(chunk)
            _set(dest, downloaded=read, percent=int(read * 100 / total) if total else 0)
    tmp.replace(target)
    return target.stat().st_size


def _download_from(dest: Path, *, variant: str, endpoint: str, opener) -> None:
    model_path = dest / "model.onnx"
    if not (model_path.exists() and model_path.stat().st_size > 0):
        filename = VARIANTS.get(variant, VARIANTS["fp32"])
        tmp = dest / "model.onnx.tmp"
        _fetch(f"{endpoint}/{REPO}/resolve/main/{filename}", tmp, dest, opener=opener)
        # 统一命名为 model.onnx
        tmp.replace(model_path)
    errors: list[str] = []
    for asset in ASSETS:
        try:
            _fetch(f"{endpoint}/{REPO}/resolve/main/{asset}", dest / asset, dest, opener=opener)
        except Exception as exc:  # noqa: BLE001
            if asset in REQUIRED:
                # 缺 tokenizer 模型根本用不了，必须算这个下载源失败，换下一个
                raise RuntimeError(f"{asset}: {exc}") from exc
            errors.append(f"{asset}: {exc}")  # 其余可选文件，失败不影响推理
    missing = [
        name
        for name in REQUIRED
        if not (dest / name).exists() or (dest / name).stat().st_size == 0
    ]
    if missing:
        raise RuntimeError(
            f"缺少必要文件 {missing}" + (f"；可选文件失败: {errors}" if errors else "")
        )


def download(
    dest: str | Path,
    *,
    variant: str = "fp32",
    endpoint: str | None = None,
    proxy: str | None = None,
) -> dict:
    """同步下载。返回最终状态。

    下载源可显式指定；未指定时先直连官方，失败后自动回退国内镜像。
    """
    dest = Path(dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    candidates = _endpoints(endpoint)
    opener = build_opener(proxy)
    _set(dest, state="running", error="", percent=0, total=0, endpoint=candidates[0])

    errors: list[str] = []
    for candidate in candidates:
        try:
            _download_from(dest, variant=variant, endpoint=candidate, opener=opener)
            if not model_status(dest)["installed"]:
                raise RuntimeError(f"缺少必要文件: {model_status(dest)['missing']}")
        except Exception as exc:  # noqa: BLE001 - 换下一个下载源
            errors.append(f"{candidate}: {exc}")
            _set(dest, error="\n".join(errors), current="", endpoint=candidate)
            continue
        _set(dest, state="done", percent=100, current="", error="", endpoint=candidate)
        return download_state(dest)

    _set(dest, state="failed", error="\n".join(errors), current="")
    raise RuntimeError("模型下载失败：\n" + "\n".join(errors))


def download_async(
    dest: str | Path,
    *,
    variant: str = "fp32",
    endpoint: str | None = None,
    proxy: str | None = None,
) -> dict:
    dest_path = Path(dest).expanduser()
    status = model_status(dest_path)
    if status["installed"]:
        _set(dest_path, state="done", percent=100, error="")
        return download_state(dest_path)

    with _lock:
        current = _downloads.get(str(dest_path))
        if current is not None and current.state == "running":
            return current.to_dict()

    def worker() -> None:
        try:
            download(dest_path, variant=variant, endpoint=endpoint, proxy=proxy)
        except Exception:  # noqa: BLE001 - 状态里已记录
            pass

    _set(dest_path, state="running", percent=0, error="")
    threading.Thread(target=worker, name="embedding-download", daemon=True).start()
    return download_state(dest_path)


def ensure(
    dest: str | Path,
    *,
    variant: str = "fp32",
    endpoint: str | None = None,
    proxy: str | None = None,
) -> bool:
    """没有模型就下载（启动时调用）。返回是否可用。"""
    status = model_status(dest)
    if status["installed"]:
        return True
    download_async(dest, variant=variant, endpoint=endpoint, proxy=proxy)
    return False
