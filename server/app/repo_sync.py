"""从 GitHub 仓库拉取内容，同步到站点的项目与日志。

设计要点
--------
1. **只读拉取，不执行仓库代码。** 只解析仓库里的 Markdown/JSON，
   绝不 import 或运行拉下来的任何文件——否则仓库内容就能变成
   服务器上的任意代码执行。

2. **Token 不落库。** 只在请求时从请求体取，用完即弃。

3. **可回滚。** 同步前把旧内容快照到内存，出错即回滚。

4. **仓库格式约定**（两种都支持）：
   - ``content/projects/*.md``   → 项目
   - ``content/posts/*.md``      → 日志
   - 或者仓库根目录直接放 ``projects/`` ``posts/``

   Markdown 文件的 front-matter 决定标题、摘要、标签、状态。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

#: 请求超时（秒）
TIMEOUT = 20

#: 单个文件最大下载体积（字节）——防止拉进超大文件
MAX_FILE_BYTES = 512 * 1024

#: front-matter 解析：``---`` 包裹的简单 key: value 块
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


@dataclass
class SyncResult:
    """一次同步的结果。"""

    ok: bool
    message: str = ""
    #: 新增/更新的项目
    projects: list[dict[str, Any]] = field(default_factory=list)
    #: 新增/更新的日志
    posts: list[dict[str, Any]] = field(default_factory=list)
    #: 被跳过的文件及原因
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: 仓库里没被采用的文件
    unused: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"项目 {len(self.projects)} 条，日志 {len(self.posts)} 条，"
            f"跳过 {len(self.skipped)} 个"
        )


def _request(url: str, token: str, accept: str = "application/vnd.github+json") -> bytes:
    """带 token 的 GitHub API 请求。"""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            "User-Agent": "personal-digital-studio",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=TIMEOUT) as r:
        return r.read()


def list_repo_files(repo: str, branch: str, token: str, path: str = "") -> list[dict]:
    """列出仓库某个目录下的文件树。"""
    url = f"https://api.github.com/repos/{repo}/git/trees/{urllib.parse.quote(branch)}"
    if path:
        url += f":{urllib.parse.quote(path)}"
    data = json.loads(_request(url, token))
    if "tree" not in data:
        raise ValueError(f"仓库或分支不存在：{repo}@{branch}")
    return [e for e in data["tree"] if e.get("type") == "blob"]


def fetch_raw(repo: str, branch: str, token: str, path: str) -> str:
    """下载文件的原始内容。"""
    url = (
        f"https://raw.githubusercontent.com/{repo}/"
        f"{urllib.parse.quote(branch)}/{urllib.parse.quote(path)}"
    )
    data = _request(url, token, accept="application/vnd.github.raw")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"文件过大（>{MAX_FILE_BYTES // 1024}KB）：{path}")
    return data.decode("utf-8", "replace")


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """拆出 front-matter 和正文。

    只支持最简形式：``key: value``、字符串或用引号包住的值。
    不引入 YAML 依赖——离线环境装包麻烦，且这已经够用。
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip().strip('"').strip("'")
        if key:
            meta[key] = val
    return meta, text[m.end():]


def slugify(value: str) -> str:
    """生成 URL 友好的 slug（与 main.py 的规则保持一致）。"""
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9一-鿿]+", "-", value).strip("-")
    return value[:80] or "item"


def _first_paragraph(body: str) -> str:
    """取正文第一段作为摘要。"""
    for block in body.split("\n\n"):
        s = block.strip()
        if not s or s.startswith(("#", "```", "- ", "* ", ">", "|")):
            continue
        # 去掉行内标记
        s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
        s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
        s = re.sub(r"[*_`>#]", "", s).strip()
        if s:
            return s[:200]
    return ""


def parse_project(text: str, path: str) -> dict[str, Any]:
    """把一个 Markdown 文件解析成项目记录。"""
    meta, body = parse_front_matter(text)
    title = meta.get("title") or Path_title(path)
    return {
        "title": title,
        "slug": meta.get("slug") or slugify(title),
        "summary": meta.get("summary") or _first_paragraph(body),
        "body": body.strip(),
        "status": meta.get("status") or "规划中",
        "url": meta.get("url") or "",
        "repo_url": meta.get("repo") or meta.get("repo_url") or "",
        "featured": 1 if meta.get("featured", "").lower() in {"1", "true", "yes"} else 0,
    }


def parse_post(text: str, path: str) -> dict[str, Any]:
    """把一个 Markdown 文件解析成日志记录。"""
    meta, body = parse_front_matter(text)
    title = meta.get("title") or Path_title(path)
    return {
        "title": title,
        "slug": meta.get("slug") or slugify(title),
        "summary": meta.get("summary") or _first_paragraph(body),
        "body": body.strip(),
        "tags": meta.get("tags") or "",
        "published": 0 if meta.get("draft", "").lower() in {"1", "true", "yes"} else 1,
        "published_at": meta.get("date") or meta.get("published_at") or "",
    }


def Path_title(path: str) -> str:
    """从文件名取标题（去掉扩展名和日期前缀）。"""
    name = path.rsplit("/", 1)[-1]
    if name.lower().endswith(".md"):
        name = name[:-3]
    # 去掉形如 20260101- 的日期前缀
    name = re.sub(r"^\d{8}[-_]?", "", name)
    return name.replace("-", " ").replace("_", " ").strip() or "未命名"


#: 内容目录候选（按优先级）
_CONTENT_ROOTS = ("content", "", "docs/content")


def collect_content(repo: str, branch: str, token: str) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str]], list[str]
]:
    """从仓库收集项目和日志。

    返回 ``(projects, posts, skipped, unused)``。
    """
    projects: list[dict[str, Any]] = []
    posts: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()

    for root in _CONTENT_ROOTS:
        try:
            entries = list_repo_files(repo, branch, token, root)
        except Exception:
            continue
        if not entries:
            continue

        for e in entries:
            path = e.get("path", "")
            low = path.lower()
            if not low.endswith(".md"):
                continue
            seen.add(path)

            # 目录判定：projects/ 或 posts/
            if "/projects/" in f"/{low}" or low.startswith("projects/"):
                kind = "project"
            elif "/posts/" in f"/{low}" or low.startswith("posts/"):
                kind = "post"
            else:
                skipped.append((path, "不在 projects/ 或 posts/ 目录下"))
                continue

            try:
                text = fetch_raw(repo, branch, token, path)
            except Exception as exc:
                skipped.append((path, f"下载失败：{exc}"))
                continue

            try:
                item = parse_project(text, path) if kind == "project" else parse_post(text, path)
            except Exception as exc:
                skipped.append((path, f"解析失败：{exc}"))
                continue

            if not item.get("title"):
                skipped.append((path, "缺少标题"))
                continue

            (projects if kind == "project" else posts).append(item)

        # 一个 root 找到内容就停，避免根目录重复收集
        if projects or posts:
            break

    # 仓库里其他 Markdown（未采用）
    try:
        all_md = [
            e["path"] for e in list_repo_files(repo, branch, token, "")
            if e.get("path", "").lower().endswith(".md")
        ]
    except Exception:
        all_md = []
    unused = sorted(p for p in all_md if p not in seen)

    return projects, posts, skipped, unused


def check_repo(repo: str, branch: str, token: str) -> dict[str, Any]:
    """只检查仓库可达性和内容布局，不做修改。"""
    out: dict[str, Any] = {"ok": False, "repo": repo, "branch": branch}
    try:
        projects, posts, skipped, unused = collect_content(repo, branch, token)
    except Exception as exc:
        out["error"] = str(exc)
        return out
    out.update(
        ok=True,
        projects=len(projects),
        posts=len(posts),
        skipped=len(skipped),
        unused=len(unused),
        projectTitles=[p["title"] for p in projects],
        postTitles=[p["title"] for p in posts],
    )
    return out
