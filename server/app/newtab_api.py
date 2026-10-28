"""给浏览器扩展提供只读 JSON 接口。

为什么需要：
  扩展页面需要结构化数据（天气、项目、日志、图片）。
  解析 HTML 很脆弱（模板一改就坏），且浪费带宽。

设计原则：
  ★ 只读，公开可访问（不含任何私密数据）
  ★ 不暴露 admin 路径
  ★ 失败时返回明确的 ok:false，不抛 500
  ★ 带 CORS 头（扩展页面 origin 是 chrome-extension://）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.weather_mod import weather as weather_service

#: 允许跨域（扩展页面 origin 为 chrome-extension://）
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def build_payload(db, media_dir: Path, prefix: str, limit_projects: int = 8,
                  limit_posts: int = 8, limit_shots: int = 6) -> dict[str, Any]:
    """组装扩展需要的所有数据。"""
    # 项目
    with db() as c:
        projects = [
            {
                "title": r["title"],
                "slug": r["slug"],
                "summary": r["summary"] or "",
                "status": r["status"] or "",
                "url": r["url"] or "",
                "repoUrl": r["repo_url"] or "",
                "updatedAt": r["updated_at"] or "",
            }
            for r in c.execute(
                "SELECT title, slug, summary, status, url, repo_url, updated_at "
                "FROM projects ORDER BY featured DESC, sort_order ASC, updated_at DESC "
                "LIMIT ?", (limit_projects,)
            )
        ]
        posts = [
            {
                "title": r["title"],
                "slug": r["slug"],
                "summary": r["summary"] or "",
                "publishedAt": (r["published_at"] or r["created_at"] or "")[:10],
            }
            for r in c.execute(
                "SELECT title, slug, summary, published_at, created_at FROM posts "
                "WHERE published=1 ORDER BY published_at DESC, id DESC LIMIT ?",
                (limit_posts,)
            )
        ]

    # 天气
    try:
        wx = weather_service.current()
    except Exception:
        wx = None
    weather = None
    if wx:
        weather = {
            "city": wx.get("cityName", ""),
            "condition": wx.get("condition", ""),
            "temperature": wx.get("temperature"),
            "humidity": wx.get("humidity"),
            "precipProbability": wx.get("precipProbability"),
            "theme": wx.get("theme", "cloud"),
            "observedAt": wx.get("observedAt", ""),
            "isDemo": bool(wx.get("isDemo")),
            "stale": bool(wx.get("stale")),
        }

    # 图片
    shots: list[str] = []
    try:
        if media_dir.exists():
            files = sorted(
                (p for p in media_dir.iterdir()
                 if p.is_file() and re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|webp|gif)", p.name)),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:limit_shots]
            shots = [f"{prefix}/media/{f.name}" for f in files]
    except OSError:
        shots = []

    return {
        "ok": True,
        "projects": projects,
        "posts": posts,
        "weather": weather,
        "shots": shots,
    }


def register_newtab_api(app, db, media_dir: Path, prefix: str) -> None:
    """在 app 上注册 JSON 接口。"""

    @app.get(prefix + "/api/newtab.json")
    async def newtab_json(request: Request):
        return JSONResponse(build_payload(db, media_dir, prefix), headers=CORS_HEADERS)

    @app.options(prefix + "/api/newtab.json")
    async def newtab_json_options(request: Request):
        return Response(status_code=204, headers=CORS_HEADERS)
