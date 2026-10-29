"""起始页 V2 应用装配。

与 V1 的区别：
  V1 —— 服务端渲染 16 个模板 + 后台管理 + 仓库同步，业务很重
  V2 —— 只做两件事：
        1. 返回一个静态起始页（前端逻辑全部在浏览器）
        2. 提供一个配置存取 API（可选，用于跨设备同步）

  模板引擎、后台、Markdown 渲染全部去掉 —— 起始页不需要服务端渲染。

保留的 V1 基础框架：
  config.py  集中配置
  db.py      SQLite + 迁移 + 备份
  repo.py    SQL 集中
  security.py 会话/CSRF（这里只用到签名，做轻量鉴权）
"""

from __future__ import annotations

import secrets
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import repo
import security
from config import Config, load_config
from db import init_db, open_db

WEB_DIR = None  # 在 create_app 里绑定


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    init_db(cfg.db_path)

    app = FastAPI(title="起始页", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = cfg
    app.state.sessions = {}

    web = cfg.static_dir / "web"
    app.mount("/web", StaticFiles(directory=str(web)), name="web")

    def get_conn():
        with open_db(cfg.db_path) as c:
            yield c

    def require_token(request: Request):
        """轻量鉴权：可选的同步令牌。

        不设 SYNC_TOKEN 时，API 完全开放（单人自用场景可接受）；
        设了就必须带对，防止别人改你的配置。
        """
        want = cfg.sync_token
        if not want:
            return True
        got = request.headers.get("X-Sync-Token", "")
        return bool(got) and secrets.compare_digest(got, want)

    # ── 安全头 ──
    @app.middleware("http")
    async def security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Content-Security-Policy", (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self' https://api.open-meteo.com "
            "https://geocoding-api.open-meteo.com; object-src 'none'; base-uri 'self'"
        ))
        return resp

    # ── 页面 ──
    @app.get("/")
    @app.get(cfg.prefix + "/")
    def index():
        f = web / "index.html"
        if not f.exists():
            raise HTTPException(500, "index.html 缺失")
        return FileResponse(f, media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico")
    def favicon():
        f = cfg.static_dir / "icons" / "icon48.png"
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f, media_type="image/png")

    @app.get(cfg.prefix + "/healthz")
    def healthz():
        return {"ok": True, "prefix": cfg.prefix, "db": str(cfg.db_path)}

    # ── 配置同步 API（可选）──
    # 前端把配置存 localStorage；这里只做"可选的跨设备同步"。
    @app.get(cfg.prefix + "/api/config")
    def get_config(request: Request, conn=Depends(get_conn)):
        if not require_token(request):
            raise HTTPException(401)
        return JSONResponse({"ok": True,
                             "config": repo.SettingsRepo(conn).all()})

    @app.post(cfg.prefix + "/api/config")
    async def save_config(request: Request, conn=Depends(get_conn)):
        if not require_token(request):
            raise HTTPException(401)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "不是有效的 JSON")
        if not isinstance(payload, dict):
            raise HTTPException(400, "配置必须是对象")
        clean = {str(k)[:60]: str(v)[:2000] for k, v in payload.items()}
        repo.SettingsRepo(conn).set_many(clean)
        return JSONResponse({"ok": True, "saved": len(clean)})

    @app.get(cfg.prefix + "/api/backup")
    def backup(conn=Depends(get_conn)):
        """导出全部配置，供前端下载备份。"""
        return JSONResponse({"ok": True,
                             "config": repo.SettingsRepo(conn).all()})

    return app


app = create_app()
