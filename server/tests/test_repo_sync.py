"""仓库同步模块的测试（不触网，只测纯函数与解析逻辑）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_vendor"))

from app.repo_sync import (  # noqa: E402
    _first_paragraph,
    parse_front_matter,
    parse_post,
    parse_project,
    slugify,
)


def test_front_matter_split():
    meta, body = parse_front_matter(
        "---\ntitle: 测试\nstatus: 已上线\n---\n\n正文内容\n"
    )
    assert meta["title"] == "测试"
    assert meta["status"] == "已上线"
    assert body.strip() == "正文内容"


def test_front_matter_quotes_stripped():
    meta, _ = parse_front_matter('---\ntitle: "带引号"\n---\nx')
    assert meta["title"] == "带引号"


def test_no_front_matter():
    meta, body = parse_front_matter("纯正文")
    assert meta == {}
    assert body == "纯正文"


def test_front_matter_ignores_comments_and_blanks():
    meta, _ = parse_front_matter("---\n# 注释\n\ntitle: X\n---\nbody")
    assert meta == {"title": "X"}


def test_parse_project_full():
    text = (
        "---\n"
        "title: Garden Action\n"
        "slug: garden-action\n"
        "summary: 园艺助手\n"
        "status: 已上线\n"
        "url: https://example.com\n"
        "repo: https://github.com/x/y\n"
        "featured: true\n"
        "---\n\n"
        "## 介绍\n\n这是正文。\n"
    )
    p = parse_project(text, "content/projects/garden.md")
    assert p["title"] == "Garden Action"
    assert p["slug"] == "garden-action"
    assert p["status"] == "已上线"
    assert p["url"] == "https://example.com"
    assert p["repo_url"] == "https://github.com/x/y"
    assert p["featured"] == 1
    assert "## 介绍" in p["body"]


def test_parse_project_defaults():
    p = parse_project("随便写点东西", "content/projects/foo.md")
    assert p["title"] == "foo"
    assert p["status"] == "规划中"
    assert p["featured"] == 0


def test_parse_project_summary_from_body():
    p = parse_project(
        "---\ntitle: T\n---\n\n第一段话，用来当摘要。\n\n第二段。\n",
        "content/projects/x.md",
    )
    assert p["summary"] == "第一段话，用来当摘要。"


def test_parse_post_draft():
    p = parse_post("---\ntitle: 草稿\ndraft: true\n---\n正文", "content/posts/d.md")
    assert p["published"] == 0
    assert p["title"] == "草稿"


def test_parse_post_published_by_default():
    p = parse_post("---\ntitle: 正式\n---\n正文", "content/posts/p.md")
    assert p["published"] == 1


def test_parse_post_date():
    p = parse_post("---\ntitle: T\ndate: 2026-01-02\n---\nx", "content/posts/x.md")
    assert p["published_at"] == "2026-01-02"


def test_parse_post_tags():
    p = parse_post("---\ntitle: T\ntags: a, b, c\n---\nx", "content/posts/x.md")
    assert p["tags"] == "a, b, c"


def test_first_paragraph_skips_headings_and_lists():
    body = "# 标题\n\n- 列表项\n\n真正的段落\n"
    assert _first_paragraph(body) == "真正的段落"


def test_first_paragraph_strips_inline_markup():
    body = "看这个 [链接](https://a.com) 和 ![图](/x.png) 很好"
    out = _first_paragraph(body)
    assert "](https" not in out
    assert "![" not in out


def test_first_paragraph_empty():
    assert _first_paragraph("") == ""
    assert _first_paragraph("# 只有标题") == ""


def test_slugify_chinese_kept():
    assert slugify("中文标题") == "中文标题"


def test_slugify_spaces_and_symbols():
    assert slugify("Hello World!") == "hello-world"


def test_slugify_empty_fallback():
    assert slugify("!!!") == "item"


def test_filename_title_strips_date_prefix():
    from app.repo_sync import Path_title
    assert Path_title("content/posts/20260101-my-note.md") == "my note"


def test_filename_title_strips_extension():
    from app.repo_sync import Path_title
    assert Path_title("content/posts/simple.md") == "simple"


# ── 路径分类（同步时判定项目/文章）────────────────────────────


def test_classify_content_paths():
    from app.repo_sync import _classify
    assert _classify("content/projects/a.md")[0] == "project"
    assert _classify("content/posts/b.md")[0] == "post"
    assert _classify("projects/c.md")[0] == "project"
    assert _classify("posts/d.md")[0] == "post"


def test_classify_relative_path_strips_prefix():
    from app.repo_sync import _classify
    kind, rel = _classify("content/posts/my-note.md")
    assert kind == "post"
    assert rel == "my-note.md"


def test_classify_ignores_non_content():
    from app.repo_sync import _classify
    assert _classify("README.md")[0] is None
    assert _classify("docs/guide.md")[0] is None
    assert _classify("server/app/main.py")[0] is None


def test_classify_nested_uses_last_segment():
    """content/posts/ 里的 posts 才算数，目录名更深也要找对。"""
    from app.repo_sync import _classify
    assert _classify("a/b/projects/x.md")[0] == "project"
    assert _classify("a/b/posts/x.md")[0] == "post"


def test_classify_same_relative_name_dedupes():
    """content/posts/x.md 与 posts/x.md 应判定为同一份内容。"""
    from app.repo_sync import _classify
    k1, r1 = _classify("content/posts/x.md")
    k2, r2 = _classify("posts/x.md")
    assert (k1, r1) == (k2, r2) == ("post", "x.md")


def test_list_repo_files_uses_recursive():
    """git/trees 默认不递归，必须显式加 recursive=1，否则子目录里的文件拿不到。"""
    import inspect

    from app.repo_sync import list_repo_files
    src = inspect.getsource(list_repo_files)
    assert "recursive=1" in src


def test_fetch_raw_uses_contents_api():
    """raw.githubusercontent.com 在国内常被重置，必须走 api.github.com。"""
    import inspect

    from app.repo_sync import fetch_raw
    src = inspect.getsource(fetch_raw)
    # 去掉 docstring 再看代码体，避免注释里的域名造成误判
    code = src.split('"""')[0] + src.split('"""')[-1]
    assert "raw.githubusercontent.com" not in code
    assert "api.github.com" in code


def test_repo_sync_imports_base64():
    """base64 是 Contents API 解码所必需。"""
    import app.repo_sync as m
    assert hasattr(m, "base64")
