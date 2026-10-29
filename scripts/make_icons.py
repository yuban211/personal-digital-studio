"""生成扩展图标（纯标准库 PNG，不引 Pillow）。

画一个简单的"起始页"图标：深色底 + 白色方块网格。
MV3 的 icons 必须是 PNG，SVG 不被接受。
"""
import struct
import zlib
from pathlib import Path

OUT = Path(r"D:\.211\起始页V2\src\icons")
OUT.mkdir(parents=True, exist_ok=True)

BG = (44, 44, 44)      # 深墨
FG = (250, 247, 242)   # 米白


def png(width, height, pixels):
    """pixels: list of rows, each row list of (r,g,b)"""
    raw = b""
    for row in pixels:
        raw += b"\x00" + b"".join(bytes(px) for px in row)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def make(size):
    """画图标：底色填充 + 中间一个白色圆角方块 + 一条搜索横线。"""
    px = []
    pad = max(1, size // 8)
    inner = size - pad * 2
    bar_top = pad + int(inner * 0.34)
    bar_bot = pad + int(inner * 0.52)
    bar_l = pad + int(inner * 0.18)
    bar_r = pad + int(inner * 0.82)

    for y in range(size):
        row = []
        for x in range(size):
            inside = pad <= x < size - pad and pad <= y < size - pad
            bar = bar_l <= x < bar_r and bar_top <= y < bar_bot
            row.append(FG if bar else (BG if inside else (250, 247, 242)))
        px.append(row)
    return png(size, size, px)


for s in (16, 48, 128):
    data = make(s)
    (OUT / f"icon{s}.png").write_bytes(data)
    print(f"icon{s}.png  {len(data)} bytes")

print("done ->", OUT)
