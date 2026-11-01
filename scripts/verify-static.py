#!/usr/bin/env python3
"""静态资源路径自检（标准库，无需 fastapi/pytest 即可运行）。

为什么需要：
  V2 从"浏览器扩展"改成"网站"时，index.html 里的 style.css / app.js 还是裸相对路径，
  而 app.py 把静态文件挂在 /web 下 —— 上线后 CSS/JS 全 404，页面裸奔。
  这个脚本在打包前跑一遍，任何一处引用不存在就直接失败。

检查两件事：
  1. index.html 里所有本地 href/src，在两种反代行为下都必须落到真实文件
     （反代剥前缀 → 服务在 / ；反代不剥前缀 → 服务在 /studio/）
  2. app.py 必须同时挂载 /web 与带前缀的 /web

用法：python scripts/verify-static.py    # 退出码 0 = 通过
"""

from __future__ import annotations

import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "src" / "static" / "web" / "index.html"
APP = ROOT / "src" / "app.py"
STATIC = ROOT / "src" / "static"
WEB_DIR = STATIC / "web"          # mount("/web") 指向的磁盘目录

#: 两种部署形态下浏览器的 base path
BASES = ["/", "/studio/"]

REF_RE = re.compile(r'(?:href|src)\s*=\s*"([^"]+)"')


def local_refs(html: str) -> list[str]:
    out = []
    for ref in REF_RE.findall(html):
        if ref.startswith(("http://", "https://", "//", "data:", "#", "mailto:")):
            continue
        out.append(ref)
    return out


def resolve(ref: str, base: str) -> str:
    """把 HTML 里的相对引用按浏览器规则解析成绝对 URL 路径。"""
    return posixpath.normpath(posixpath.join(base, ref))


def to_file(url_path: str, prefix: str = "") -> Path | None:
    """把 URL 路径映射回磁盘文件；不属于静态挂载范围则返回 None。"""
    p = url_path
    if prefix and p.startswith(prefix + "/"):
        p = p[len(prefix):]
    if not p.startswith("/web/"):
        return None
    return WEB_DIR / p[len("/web/"):]


def main() -> int:
    problems: list[str] = []

    html = HTML.read_text(encoding="utf-8")
    refs = local_refs(html)
    if not refs:
        problems.append("index.html 里没有找到任何本地资源引用，检查解析逻辑")

    for base in BASES:
        prefix = base.rstrip("/")          # "/studio/" -> "/studio"
        for ref in refs:
            url = resolve(ref, base)
            f = to_file(url, prefix)
            if f is None:
                problems.append(
                    f"[{base}] {ref!r} -> {url} 不在 /web 挂载范围，必然 404")
            elif not f.is_file():
                problems.append(f"[{base}] {ref!r} -> {url} 文件不存在: {f}")

    app_src = APP.read_text(encoding="utf-8")
    if 'mount("/web"' not in app_src:
        problems.append('app.py 缺少 mount("/web")')
    if 'cfg.prefix + "/web"' not in app_src:
        problems.append('app.py 缺少带前缀的 /web 挂载（反代不剥前缀时会 404）')

    if problems:
        print("FAIL —— 静态资源自检未通过：")
        for p in problems:
            print("  -", p)
        return 1

    print(f"OK —— {len(refs)} 个本地引用，在 {len(BASES)} 种 base path 下全部命中")
    for base in BASES:
        print(f"  {base}: " + ", ".join(resolve(r, base) for r in refs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
