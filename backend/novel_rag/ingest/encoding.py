"""编码探测与换行归一。

小说 TXT 的现实：大量文件是 GBK/GB18030，少数是 UTF-16（带 BOM），
还有一部分 UTF-8 文件混入非法字节。策略是「从严到宽」逐个严格解码。
"""

from __future__ import annotations

from dataclasses import dataclass

# 顺序代表优先级：越靠前越可信
_CANDIDATES: tuple[str, ...] = ("utf-8", "utf-16", "gb18030", "big5", "latin-1")


@dataclass(slots=True)
class Decoded:
    text: str
    encoding: str


def decode_text(raw: bytes) -> Decoded:
    # BOM 优先
    if raw.startswith(b"\xef\xbb\xbf"):
        return Decoded(raw[3:].decode("utf-8", errors="replace"), "utf-8-sig")
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return Decoded(raw.decode("utf-16", errors="replace"), "utf-16")

    for enc in _CANDIDATES:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if _looks_like_mojibake(text):
            continue
        return Decoded(text, enc)
    return Decoded(raw.decode("utf-8", errors="replace"), "utf-8-replace")


def _looks_like_mojibake(text: str) -> bool:
    """误用编码的典型特征：大量替换字符或控制字符。"""
    if not text:
        return False
    sample = text[:4000]
    bad = sum(1 for ch in sample if ch == "\ufffd" or (ord(ch) < 9))
    return bad > max(4, len(sample) // 200)


def normalize_newlines(text: str) -> str:
    """统一换行。注意：这是导入阶段的一次性归一，之后段落必须是精确切片。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")
