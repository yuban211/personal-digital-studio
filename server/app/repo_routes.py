"""后台「仓库同步」路由与数据库写入。

写入策略：按 slug 匹配。
  · 仓库里有、库里没有 → 新增
  · 两边都有 → 更新（仓库为准）
  · 库里有、仓库没有 → **不删除**（避免一次同步误删全部内容）
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.repo_sync import check_repo, collect_content

#: 仓库名格式校验：只允许 owner/repo，字符集受限
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,39}/[A-Za-z0-9_.-]{1,100}$")
#: 分支名校验：禁止空格和控制字符，禁止路径穿越
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


def validate_repo(repo: str, branch: str) -> None:
    """校验仓库与分支名，挡掉路径穿越和注入。"""
    repo = (repo or "").strip()
    branch = (branch or "").strip()
    if not _REPO_RE.match(repo):
        raise ValueError("仓库名格式不对，应该是 用户名/仓库名")
    if not _BRANCH_RE.match(branch) or ".." in branch or branch.startswith("/"):
        raise ValueError("分支名不合法")
    if len(repo) > 140 or len(branch) > 100:
        raise ValueError("仓库名或分支名过长")


def write_to_db(db, projects: list[dict[str, Any]], posts: list[dict[str, Any]]) -> tuple[int, int]:
    """把仓库内容写进数据库。

    返回 ``(写入项目数, 写入文章数)``。
    """
    written_p = written_a = 0
    with db() as c:
        for p in projects:
            slug = p["slug"]
            row = c.execute("SELECT id FROM projects WHERE slug=?", (slug,)).fetchone()
            vals = (
                p["title"][:160], slug, p["summary"][:500], p["body"][:100000],
                p["status"][:40], p["url"][:300], p["repo_url"][:300], p["featured"],
            )
            if row:
                c.execute(
                    "UPDATE projects SET title=?,slug=?,summary=?,body=?,status=?,"
                    "url=?,repo_url=?,featured=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (*vals, row["id"]),
                )
            else:
                c.execute(
                    "INSERT INTO projects(title,slug,summary,body,status,url,repo_url,"
                    "featured,sort_order) VALUES(?,?,?,?,?,?,?,?,0)",
                    vals,
                )
            written_p += 1

        for a in posts:
            slug = a["slug"]
            row = c.execute("SELECT id FROM posts WHERE slug=?", (slug,)).fetchone()
            pub_at = a.get("published_at") or None
            vals = (
                a["title"][:160], slug, a["summary"][:500], a["body"][:100000],
                a["tags"][:200], 1 if a["published"] else 0,
            )
            if row:
                c.execute(
                    "UPDATE posts SET title=?,slug=?,summary=?,body=?,tags=?,published=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (*vals, row["id"]),
                )
            else:
                c.execute(
                    "INSERT INTO posts(title,slug,summary,body,tags,published,"
                    "published_at,created_at) VALUES(?,?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP),"
                    "CURRENT_TIMESTAMP)",
                    (*vals, pub_at),
                )
            written_a += 1
    return written_p, written_a


def register_repo_routes(app, db, render, require_admin, csrf_ok, prefix: str) -> None:
    """注册同步相关的路由。"""

    @app.get(prefix + "/admin/repo/sync", response_class=__import__("fastapi").responses.HTMLResponse)
    def repo_sync_page(request: Request):
        s = require_admin(request)
        return render(request, "admin/repo_sync.html", csrf=s["csrf"], form={}, result=None)

    @app.post(prefix + "/admin/repo/sync")
    def repo_sync_run(
        request: Request,
        csrf: str = Form(...),
        repo: str = Form(""),
        branch: str = Form("main"),
        token: str = Form(""),
        mode: str = Form("check"),
    ):
        s = require_admin(request)
        if not csrf_ok(request, csrf):
            raise HTTPException(403, "CSRF validation failed")

        form = {
            "repo": repo.strip(),
            "branch": branch.strip(),
            "mode": mode,
            "token_set": bool(token),
        }

        # 令牌：优先用本次提交的，否则回退到之前保存在 settings 里的
        token = (token or "").strip()
        if not token:
            with db() as c:
                row = c.execute(
                    "SELECT value FROM settings WHERE key='repo_sync_token'"
                ).fetchone()
            token = (row["value"] if row else "") or ""

        if not token:
            return render(
                request, "admin/repo_sync.html", csrf=s["csrf"], form=form,
                result={"error": "没有访问令牌。填一个，或在站点设置里保存一个。"},
            )

        try:
            validate_repo(form["repo"], form["branch"])
        except ValueError as exc:
            return render(
                request, "admin/repo_sync.html", csrf=s["csrf"], form=form,
                result={"error": str(exc)},
            )

        try:
            projects, posts, skipped, unused = collect_content(
                form["repo"], form["branch"], token
            )
        except Exception as exc:
            return render(
                request, "admin/repo_sync.html", csrf=s["csrf"], form=form,
                result={"error": f"拉取失败：{exc}"},
            )

        result: dict[str, Any] = {
            "projects": projects,
            "posts": posts,
            "skipped": skipped,
            "unused": unused,
            "error": "",
            "applied": False,
            "written_projects": 0,
            "written_posts": 0,
        }

        if mode == "apply":
            wp, wa = write_to_db(db, projects, posts)
            result["applied"] = True
            result["written_projects"] = wp
            result["written_posts"] = wa
            # 记住令牌，下次不用重复输入
            try:
                with db() as c:
                    c.execute(
                        "INSERT OR REPLACE INTO settings(key,value) VALUES('repo_sync_token',?)",
                        (token,),
                    )
            except Exception:
                pass

        return render(
            request, "admin/repo_sync.html", csrf=s["csrf"], form=form, result=result
        )
