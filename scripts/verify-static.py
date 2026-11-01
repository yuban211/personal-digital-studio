#!/usr/bin/env python3
"""静态资源路径自检（标准库，无需 fastapi/pytest 即可运行）。

为什么需要：
  V2 从"浏览器扩展"改成"网站"时，index.html 里的 style.css / app.js 还是裸相对路径，
  而 app.py 把静态文件挂在 /web 下 —— 上线后 CSS/JS 全 404，页面裸奔。
  这个脚本在打包前跑一遍，任何一处引用不存在就直接失败。

检查三件事：
  1. 每个页面里的本地 href/src，在两种反代行为下都必须落到真实文件
     （反代剥前缀 → 服务在 / ；反代不剥前缀 → 服务在 /studio/）
  2. app.py 必须同时挂载 /web、/home 以及带前缀的同名挂载
  3. 页面禁止 http(s) 外链资源 —— 起始页宣称"不加载第三方脚本/字体"，这条守着它

用法：python scripts/verify-static.py    # 退出码 0 = 通过
"""

from __future__ import annotations

import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "src" / "app.py"
STATIC = ROOT / "src" / "static"

#: (页面文件, 挂载前缀, 磁盘目录)
PAGES = [
    (STATIC / "web" / "index.html", "/web/", STATIC / "web"),
    (STATIC / "home" / "index.html", "/home/", STATIC / "home"),
]

#: 两种部署形态下浏览器的 base path
BASES = ["/", "/studio/"]

REF_RE = re.compile(r'(?:href|src)\s*=\s*"([^"]+)"')


def local_refs(html: str) -> tuple[list[str], list[str]]:
    """返回 (本地引用, 外链引用)。"""
    local, remote = [], []
    for ref in REF_RE.findall(html):
        if ref.startswith(("data:", "#", "mailto:", "javascript:")):
            continue
        if ref.startswith(("http://", "https://", "//")):
            remote.append(ref)
            continue
        local.append(ref)
    return local, remote


def resolve(ref: str, base: str) -> str:
    """把 HTML 里的相对引用按浏览器规则解析成绝对 URL 路径。"""
    return posixpath.normpath(posixpath.join(base, ref))


def to_file(url_path: str, mount: str, disk: Path, prefix: str = "") -> Path | None:
    """把 URL 路径映射回磁盘文件；不属于该静态挂载范围则返回 None。"""
    p = url_path
    if prefix and p.startswith(prefix + "/"):
        p = p[len(prefix):]
    if not p.startswith(mount):
        return None
    return disk / p[len(mount):]


def check_page(html_file: Path, mount: str, disk: Path) -> tuple[list[str], int]:
    problems: list[str] = []
    html = html_file.read_text(encoding="utf-8")
    refs, remote = local_refs(html)
    if not refs:
        problems.append(f"{html_file.name} 里没有找到任何本地资源引用，检查解析逻辑")
    for ref in remote:
        problems.append(f"{html_file.name} 引用了外链资源（应自托管）: {ref}")
    for base in BASES:
        prefix = base.rstrip("/")
        for ref in refs:
            url = resolve(ref, base)
            f = to_file(url, mount, disk, prefix)
            if f is None:
                # 页面之间的跳转（如 quant/）不属于静态挂载，跳过存在性检查
                continue
            if not f.is_file():
                problems.append(f"[{base}] {ref!r} -> {url} 文件不存在: {f}")
    return problems, len(refs)


def main() -> int:
    problems: list[str] = []
    total = 0

    for html_file, mount, disk in PAGES:
        if not html_file.exists():
            problems.append(f"缺页面: {html_file}")
            continue
        p, n = check_page(html_file, mount, disk)
        problems.extend(p)
        total += n

    app_src = APP.read_text(encoding="utf-8")
    for needle, why in (
        ('mount("/web"', 'app.py 缺少 mount("/web")'),
        ('cfg.prefix + "/web"', 'app.py 缺少带前缀的 /web 挂载（反代不剥前缀时会 404）'),
        ('mount("/home"', 'app.py 缺少 mount("/home")'),
        ('cfg.prefix + "/home"', 'app.py 缺少带前缀的 /home 挂载'),
    ):
        if needle not in app_src:
            problems.append(why)

    if problems:
        print("FAIL —— 静态资源自检未通过：")
        for p in problems:
            print("  -", p)
        return 1

    print(f"OK —— {total} 个本地引用，{len(PAGES)} 个页面 × {len(BASES)} 种 base path 全部命中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
