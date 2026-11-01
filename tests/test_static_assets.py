"""静态资源引用回归测试。

历史缺陷（2026-10-30 版）：index.html 用裸相对路径引 style.css / app.js，
而 app.py 把静态文件挂在 /web，上线即全 404。此用例锁死该行为。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "verify-static.py"


def test_all_static_refs_resolve():
    r = subprocess.run([sys.executable, str(SCRIPT)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"静态资源自检失败:\n{r.stdout}\n{r.stderr}"


def test_index_refs_are_web_prefixed():
    html = (ROOT / "src" / "static" / "web" / "index.html").read_text(encoding="utf-8")
    for name in ("style.css", "config.js", "search.js", "app.js"):
        assert f'href="web/{name}"' in html or f'src="web/{name}"' in html, \
            f"{name} 必须带 web/ 前缀"
        assert f'"{name}"' not in html, f"仍存在裸引用 {name}"
