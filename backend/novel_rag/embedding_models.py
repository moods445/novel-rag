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

REPO = "Xenova/bge-small-zh-v1.5"
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


def _fetch(url: str, target: Path, dest: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        _set(dest, current=target.name, downloaded=target.stat().st_size)
        return target.stat().st_size

    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        _set(dest, current=target.name, total=total, downloaded=0, percent=0)
        read = 0
        while chunk := resp.read(1 << 16):
            fh.write(chunk)
            read += len(chunk)
            _set(dest, downloaded=read, percent=int(read * 100 / total) if total else 0)
    tmp.replace(target)
    return target.stat().st_size


def download(dest: str | Path, *, variant: str = "fp32", endpoint: str | None = None) -> dict:
    """同步下载。返回最终状态。"""
    dest = Path(dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    endpoint = (endpoint or "https://huggingface.co").rstrip("/")
    _set(dest, state="running", error="", percent=0, total=0)
    try:
        filename = VARIANTS.get(variant, VARIANTS["fp32"])
        _fetch(f"{endpoint}/{REPO}/resolve/main/{filename}", dest / "model.onnx.tmp", dest)
        # 统一命名为 model.onnx
        (dest / "model.onnx.tmp").replace(dest / "model.onnx")
        for asset in ASSETS:
            try:
                _fetch(f"{endpoint}/{REPO}/resolve/main/{asset}", dest / asset, dest)
            except (urllib.error.URLError, TimeoutError):
                continue  # 可选文件，失败不影响推理
        _set(dest, state="done", percent=100, current="")
    except Exception as exc:  # noqa: BLE001
        _set(dest, state="failed", error=str(exc), current="")
        raise
    return download_state(dest)


def download_async(dest: str | Path, *, variant: str = "fp32", endpoint: str | None = None) -> dict:
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
            download(dest_path, variant=variant, endpoint=endpoint)
        except Exception:  # noqa: BLE001 - 状态里已记录
            pass

    _set(dest_path, state="running", percent=0, error="")
    threading.Thread(target=worker, name="embedding-download", daemon=True).start()
    return download_state(dest_path)


def ensure(dest: str | Path, *, variant: str = "fp32", endpoint: str | None = None) -> bool:
    """没有模型就下载（启动时调用）。返回是否可用。"""
    status = model_status(dest)
    if status["installed"]:
        return True
    download_async(dest, variant=variant, endpoint=endpoint)
    return False
