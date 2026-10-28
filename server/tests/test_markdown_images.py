"""safe_markdown 的图片语法与安全性测试。

重点：图片语法可用；恶意 URL 被拒绝；原有行为不回退。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_vendor"))

from app.security import safe_markdown, safe_image_url  # noqa: E402


def test_image_markdown_renders():
    html = safe_markdown("![截图](/studio/media/abc.png)")
    assert "<img" in html
    assert 'src="/studio/media/abc.png"' in html
    assert 'alt="截图"' in html


def test_image_with_title_text():
    html = safe_markdown("图 ![a](/studio/media/x.jpg) 后面")
    assert "<img" in html
    assert "后面" in html


def test_image_https_allowed():
    html = safe_markdown("![x](https://example.com/a.png)")
    assert 'src="https://example.com/a.png"' in html


def test_javascript_url_rejected():
    """javascript: 图片必须被拒绝，只留 alt 文本。"""
    html = safe_markdown("![x](javascript:alert(1))")
    assert "<img" not in html
    assert "javascript:" not in html


def test_data_url_rejected():
    html = safe_markdown("![x](data:text/html;base64,PHNjcmlwdD4=)")
    assert "<img" not in html
    assert "data:" not in html


def test_protocol_relative_rejected():
    html = safe_markdown("![x](//evil.example/a.png)")
    assert "<img" not in html


def test_traversal_rejected():
    html = safe_markdown("![x](/studio/../../etc/passwd)")
    assert "<img" not in html


def test_raw_html_still_escaped():
    html = safe_markdown("<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_link_still_works():
    html = safe_markdown("[站](https://example.com)")
    assert 'href="https://example.com"' in html


def test_image_not_confused_with_link():
    """图片语法必须先于链接语法处理，否则 [alt](url) 会被链接吃掉。"""
    html = safe_markdown("![alt](/studio/media/a.png)")
    assert "<img" in html
    assert "<a " not in html


def test_code_block_escapes_image_syntax():
    html = safe_markdown("```\n![x](/studio/media/a.png)\n```")
    assert "<img" not in html
    assert "&lt;img" in html or "![x]" in html


def test_safe_image_url_direct():
    assert safe_image_url("/studio/media/a.png") is True
    assert safe_image_url("https://a.com/b.png") is True
    assert safe_image_url("javascript:alert(1)") is False
    assert safe_image_url("data:text/html,x") is False
    assert safe_image_url("//evil/a.png") is False
    assert safe_image_url("") is False
    assert safe_image_url("/a/../../b") is False


def test_existing_features_intact():
    """回归：标题、列表、代码块、粗体。"""
    html = safe_markdown("# 标题\n\n- 一\n- 二\n\n**粗**\n\n`code`")
    assert "<h1>标题</h1>" in html
    assert "<ul>" in html and "<li>一</li>" in html
    assert "<strong>粗</strong>" in html
    assert "<code>code</code>" in html
