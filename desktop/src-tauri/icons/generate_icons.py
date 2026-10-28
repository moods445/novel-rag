#!/usr/bin/env python3
"""生成桌面端图标（纯标准库，无 Pillow 依赖）。

产出：32/128/128@2x/256/512/1024 PNG + icon.ico + icon.icns。
图形语义：深色圆角底 + 一本翻开的书 + 青色书脊。

    python3 desktop/src-tauri/icons/generate_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 1024
BG = (14, 17, 22, 255)
PAGE = (245, 247, 250, 255)
SPINE = (45, 212, 191, 255)
LINE = (148, 163, 184, 255)
SHADOW = (2, 6, 23, 90)


def _rounded_alpha(x: int, y: int, x0: int, y0: int, x1: int, y1: int, r: int) -> bool:
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def render(size: int = SIZE) -> list[list[tuple[int, int, int, int]]]:
    """返回 RGBA 像素矩阵。用整体缩放来保证各尺寸视觉一致。"""
    img = [[(0, 0, 0, 0)] * size for _ in range(size)]
    k = size / SIZE

    def blit(x: int, y: int, color: tuple[int, int, int, int]) -> None:
        if 0 <= x < size and 0 <= y < size:
            img[y][x] = color

    def fill_round(x0: int, y0: int, x1: int, y1: int, r: int, color) -> None:
        sx0, sy0, sx1, sy1, sr = (
            int(x0 * k),
            int(y0 * k),
            int(x1 * k),
            int(y1 * k),
            max(1, int(r * k)),
        )
        for y in range(sy0, sy1 + 1):
            for x in range(sx0, sx1 + 1):
                if _rounded_alpha(x, y, sx0, sy0, sx1, sy1, sr):
                    blit(x, y, color)

    # 底色
    fill_round(32, 32, 992, 992, 200, BG)
    # 书页
    fill_round(252, 196, 772, 828, 36, PAGE)
    # 书脊
    fill_round(252, 196, 336, 828, 36, SPINE)
    # 文字行
    for index, top in enumerate((320, 430, 540, 650)):
        right = 700 if index != 3 else 590
        fill_round(392, top, right, top + 44, 20, LINE)
    # 右下角强调点
    fill_round(796, 748, 900, 852, 52, SPINE)

    return img


def png_bytes(img: list[list[tuple[int, int, int, int]]]) -> bytes:
    height = len(img)
    width = len(img[0])
    raw = bytearray()
    for row in img:
        raw.append(0)  # filter type 0
        for r, g, b, a in row:
            raw.extend((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def resize(img: list[list[tuple[int, int, int, int]]], size: int) -> list[list[tuple[int, int, int, int]]]:
    """盒式采样缩放，足够做图标。"""
    src = len(img)
    out: list[list[tuple[int, int, int, int]]] = []
    step = src / size
    for y in range(size):
        row = []
        sy = int(y * step)
        for x in range(size):
            sx = int(x * step)
            row.append(img[sy][sx])
        out.append(row)
    return out


def write_ico(path: Path, pngs: dict[int, bytes]) -> None:
    sizes = sorted(pngs)
    entries = b""
    offset = 6 + 16 * len(sizes)
    body = b""
    for size in sizes:
        data = pngs[size]
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(data),
            offset + len(body),
        )
        body += data
    path.write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + entries + body)


def write_icns(path: Path, pngs: dict[int, bytes]) -> None:
    mapping = {16: b"icp4", 32: b"icp5", 64: b"icp6", 128: b"ic07", 256: b"ic08", 512: b"ic09", 1024: b"ic10"}
    body = b""
    for size, tag in mapping.items():
        if size not in pngs:
            continue
        data = pngs[size]
        body += tag + struct.pack(">I", len(data) + 8) + data
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> int:
    here = Path(__file__).resolve().parent
    base = render(SIZE)
    variants = {s: png_bytes(resize(base, s)) for s in (16, 32, 48, 64, 128, 256, 512, 1024)}

    (here / "icon.png").write_bytes(variants[1024])
    (here / "32x32.png").write_bytes(variants[32])
    (here / "128x128.png").write_bytes(variants[128])
    (here / "128x128@2x.png").write_bytes(variants[256])
    (here / "256x256.png").write_bytes(variants[256])
    ico_sizes = [s for s in (16, 32, 48, 64, 128, 256) if s in variants]
    for size in list(ico_sizes):
        if size not in variants:
            variants[size] = png_bytes(resize(base, size))
    write_ico(here / "icon.ico", {s: variants[s] for s in ico_sizes})
    write_icns(here / "icon.icns", variants)
    print("icons written to", here)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
