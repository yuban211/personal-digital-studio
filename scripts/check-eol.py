#!/usr/bin/env python3
"""上传前行尾检查：仓库内的文本文件必须是纯 LF。

为什么需要这个断言：
    服务器是 Linux，`bash` 遇到行尾的 `\\r` 会把 `then\\r`、`{\\r` 当成命令名，
    报 `$'\\r': command not found`，整个启动脚本直接崩。
    2026-11-01 就因为这个把容器启动脚本改坏，实例反复起不来（已修复）。

    最容易踩的坑是 Windows 上的 Python：
        Path(p).write_text(s)          # ← newline=None，会把 \\n 全写成 \\r\\n
        Path(p).write_text(s, newline="\\n")   # ← 正确
    或者更保险：一律用 write_bytes / open(..., "wb") 处理要上传到 Linux 的文件。

用法：
    python scripts/check-eol.py            # 检查整个仓库
    python scripts/check-eol.py src scripts
    python scripts/check-eol.py path/to/file.sh
退出码 0 表示全为 LF，1 表示存在 CRLF / 单独 CR。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 只检查这些后缀；二进制（.whl/.png/.db/.tar.gz 等）直接跳过
TEXT_SUFFIXES = {
    ".py", ".sh", ".js", ".mjs", ".css", ".html", ".htm", ".json",
    ".md", ".txt", ".env", ".toml", ".yaml", ".yml", ".cfg", ".ini", "",
}
#: 这些目录不查
SKIP_DIRS = {".git", "__pycache__", "dist", "node_modules", ".pytest_cache", "venv", ".workbuddy"}
#: 这些文件名不查（Caddyfile 无后缀，单独放行；它由 start.tls.sh 在 Linux 上重建）
SKIP_NAMES = {"Caddyfile"}


def is_text(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return False
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    return True


def iter_files(targets: list[str]) -> list[Path]:
    out: list[Path] = []
    for t in targets:
        p = (ROOT / t) if not Path(t).is_absolute() else Path(t)
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and not (set(f.relative_to(p).parts) & SKIP_DIRS) and is_text(f):
                    out.append(f)
    return out


def main(argv: list[str]) -> int:
    targets = argv[1:] or ["."]
    files = iter_files(targets)
    bad: list[tuple[Path, int, int]] = []
    for f in files:
        try:
            b = f.read_bytes()
        except OSError:
            continue
        crlf = b.count(b"\r\n")
        cr = b.count(b"\r")
        if cr:
            bad.append((f, crlf, cr - crlf))

    checked = len(files)
    if not bad:
        print(f"OK —— {checked} 个文本文件全部为 LF 行尾")
        return 0

    print(f"✗ 发现 {len(bad)} 个文件行尾不是纯 LF（共检查 {checked} 个）：\n")
    for f, crlf, lone in bad:
        try:
            rel = f.relative_to(ROOT)
        except ValueError:
            rel = f
        extra = f"，另有 {lone} 个单独 CR" if lone else ""
        print(f"  {rel}  —— CRLF {crlf} 行{extra}")
    print(
        "\n修法（任选其一）：\n"
        "  python -c \"from pathlib import Path;p=Path('文件');p.write_bytes(p.read_bytes().replace(b'\\\\r\\\\n',b'\\\\n'))\"\n"
        "  sed -i 's/\\r$//' 文件\n"
        "上传时必须用二进制模式（WinSCP: put -transfer=binary），否则会被再次转成 CRLF。\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
