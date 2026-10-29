"""V2 起始页网站 —— 前端结构测试。

检查历史踩过的坑：
  - HTML 标签是否平衡（V1 曾缺闭合标签导致布局错乱）
  - 是否引入外部 CDN（本机所有 CDN 不可达）
  - 引用的文件是否都存在
  - 无硬编码密钥
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "src" / "static" / "web"
ICONS = ROOT / "src" / "static" / "icons"


def read(name):
    return (WEB / name).read_text(encoding="utf-8")


class TestHtmlStructure:
    def test_web_dir_exists(self):
        assert WEB.is_dir(), f"前端目录不存在: {WEB}"
        assert (WEB / "index.html").exists()

    def test_tags_balanced(self):
        """标签必须配对 —— V1 曾因缺闭合标签导致布局错乱。"""
        html = read("index.html")
        void = {"meta", "link", "br", "hr", "img", "input", "source"}
        stack = []
        for m in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*?)(/?)>", html):
            closing, tag, attrs, self_close = m.groups()
            tag = tag.lower()
            if tag in void or self_close or tag == "!doctype":
                continue
            if closing:
                if not stack or stack[-1] != tag:
                    pytest.fail(
                        f"标签不匹配: </{tag}>，栈顶为 {stack[-1] if stack else '空'}")
                stack.pop()
            else:
                stack.append(tag)
        assert not stack, f"未闭合的标签: {stack}"

    def test_referenced_files_exist(self):
        html = read("index.html")
        refs = re.findall(r'(?:href|src)="([^"]+)"', html)
        local = [r for r in refs if not r.startswith(("http", "#", "data:"))]
        assert local, "没有任何本地引用"
        for r in local:
            assert (WEB / r).exists(), f"引用了不存在的文件: {r}"

    def test_no_external_cdn(self):
        """★ 关键：本机所有 CDN 不可达，任何外部资源引用都会让页面残缺。"""
        html = read("index.html")
        ext = re.findall(r'(?:href|src)="(https?://[^"]+)"', html)
        assert not ext, f"index.html 引用了外部资源: {ext}"

        for f in ("style.css", "app.js", "config.js", "search.js"):
            src = read(f)
            urls = re.findall(r'https?://[^\s"\'\)]+', src)
            # 允许搜索引擎 URL 与天气 API（运行时请求，不是资源加载）
            resource_like = [u for u in urls
                             if re.search(r'\.(css|js|woff2?|ttf|png|jpg|svg)(?:$|\?)', u)]
            assert not resource_like, f"{f} 引用了外部资源: {resource_like}"

    def test_no_css_import(self):
        assert "@import" not in read("style.css")

    def test_search_input_autofocus(self):
        """打开就该能直接输入。"""
        assert "autofocus" in read("index.html")

    def test_icons_exist(self):
        for s in (16, 48, 128):
            assert (ICONS / f"icon{s}.png").exists(), f"图标缺失 icon{s}.png"


class TestNoSecrets:
    def test_no_hardcoded_tokens(self):
        for f in WEB.glob("*.js"):
            t = f.read_text(encoding="utf-8")
            for pat in (r"ghp_[A-Za-z0-9]{20,}", r"sk-[A-Za-z0-9]{20,}",
                        r"api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9]{16,}"):
                assert not re.search(pat, t), f"{f.name} 含疑似密钥"
