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

import os
import secrets
import time

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
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
    # 静态资源挂载：/web 恒定可用（反代剥前缀时走这条），
    # 配了 APP_PREFIX 时再挂一份带前缀的（反代不剥前缀时走这条）。
    # 前端 index.html 统一用相对路径 web/xxx 引用，两种反代行为都能命中。
    app.mount("/web", StaticFiles(directory=str(web)), name="web")
    if cfg.prefix:
        app.mount(cfg.prefix + "/web", StaticFiles(directory=str(web)), name="web-prefixed")

    home = cfg.static_dir / "home"
    app.mount("/home", StaticFiles(directory=str(home)), name="home")
    if cfg.prefix:
        app.mount(cfg.prefix + "/home", StaticFiles(directory=str(home)), name="home-prefixed")

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
        f = home / "index.html"
        if not f.exists():
            # 新版页面缺失时退回旧版，宁可少功能也别白屏
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

    # 裸 /healthz 也留一条：启动脚本与反代探活两种写法都用得到，
    # 配了 APP_PREFIX 时避免探活误判为"启动失败"。
    if cfg.prefix:
        @app.get("/healthz")
        def healthz_root():
            return healthz()

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
    def backup(request: Request, conn=Depends(get_conn)):
        """导出全部配置，供前端下载备份。"""
        if not require_token(request):
            raise HTTPException(401)
        return JSONResponse({"ok": True,
                             "config": repo.SettingsRepo(conn).all()})

    # ══════════════════════════════════════════════════════════
    # 收藏夹 API（分类 + 站点）
    # ─────────────────────────────────────────────────────────
    # 鉴权：默认开放（单人自用）；设了 LIBRARY_TOKEN 才要求请求头带令牌。
    # 与 /api/config 分开的原因：配置里有敏感值，收藏夹只是网址，没必要上锁。
    # ══════════════════════════════════════════════════════════
    lib = APIRouter()

    def guard_library(request: Request) -> None:
        want = os.getenv("LIBRARY_TOKEN", "").strip()
        if not want:
            return
        got = request.headers.get("X-Library-Token", "")
        if not (got and secrets.compare_digest(got, want)):
            raise HTTPException(401, "收藏夹令牌无效")

    async def read_json(request: Request) -> dict:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "不是有效的 JSON")
        if not isinstance(payload, dict):
            raise HTTPException(400, "请求体必须是对象")
        return payload

    @lib.get("/api/library")
    def library(request: Request, conn=Depends(get_conn)):
        guard_library(request)
        return JSONResponse({
            "ok": True,
            "categories": repo.CategoryRepo(conn).list_all(),
            "bookmarks": repo.BookmarkRepo(conn).list_all(),
        })

    @lib.get("/api/categories")
    def list_categories(request: Request, conn=Depends(get_conn)):
        guard_library(request)
        return JSONResponse({"ok": True, "categories": repo.CategoryRepo(conn).list_all()})

    @lib.post("/api/categories")
    async def create_category(request: Request, conn=Depends(get_conn)):
        guard_library(request)
        data = await read_json(request)
        cid = repo.CategoryRepo(conn).create(data)
        return JSONResponse({"ok": True, "id": cid})

    @lib.put("/api/categories/{cid}")
    async def update_category(cid: int, request: Request, conn=Depends(get_conn)):
        guard_library(request)
        data = await read_json(request)
        ok = repo.CategoryRepo(conn).update(cid, data)
        if not ok:
            raise HTTPException(404, "分类不存在或没有可更新字段")
        return JSONResponse({"ok": True})

    @lib.delete("/api/categories/{cid}")
    def delete_category(cid: int, request: Request, conn=Depends(get_conn),
                        cascade: bool = False):
        guard_library(request)
        ok = repo.CategoryRepo(conn).delete(cid, cascade=cascade)
        if not ok:
            raise HTTPException(404, "分类不存在")
        return JSONResponse({"ok": True})

    @lib.post("/api/categories/reorder")
    async def reorder_categories(request: Request, conn=Depends(get_conn)):
        guard_library(request)
        data = await read_json(request)
        ids = [int(x) for x in data.get("ids", []) if str(x).isdigit()]
        return JSONResponse({"ok": True, "count": repo.CategoryRepo(conn).reorder(ids)})

    @lib.get("/api/bookmarks")
    def list_bookmarks(request: Request, conn=Depends(get_conn),
                       category_id: int | None = None):
        guard_library(request)
        return JSONResponse({"ok": True,
                             "bookmarks": repo.BookmarkRepo(conn).list_all(category_id)})

    @lib.post("/api/bookmarks")
    async def create_bookmark(request: Request, conn=Depends(get_conn)):
        guard_library(request)
        data = await read_json(request)
        try:
            bid = repo.BookmarkRepo(conn).create(data)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return JSONResponse({"ok": True, "id": bid})

    @lib.put("/api/bookmarks/{bid}")
    async def update_bookmark(bid: int, request: Request, conn=Depends(get_conn)):
        guard_library(request)
        data = await read_json(request)
        ok = repo.BookmarkRepo(conn).update(bid, data)
        if not ok:
            raise HTTPException(404, "收藏不存在或没有可更新字段")
        return JSONResponse({"ok": True})

    @lib.delete("/api/bookmarks/{bid}")
    def delete_bookmark(bid: int, request: Request, conn=Depends(get_conn)):
        guard_library(request)
        if not repo.BookmarkRepo(conn).delete(bid):
            raise HTTPException(404, "收藏不存在")
        return JSONResponse({"ok": True})

    @lib.post("/api/bookmarks/reorder")
    async def reorder_bookmarks(request: Request, conn=Depends(get_conn)):
        guard_library(request)
        data = await read_json(request)
        ids = [int(x) for x in data.get("ids", []) if str(x).isdigit()]
        return JSONResponse({"ok": True, "count": repo.BookmarkRepo(conn).reorder(ids)})

    @lib.post("/api/bookmarks/{bid}/move")
    async def move_bookmark(bid: int, request: Request, conn=Depends(get_conn)):
        """拖拽落点：把收藏移到目标分类的指定下标。"""
        guard_library(request)
        data = await read_json(request)
        cat = data.get("category_id")
        cat = int(cat) if cat not in (None, "", 0) else None
        pos = data.get("position")
        pos = int(pos) if pos not in (None, "") else None
        ok = repo.BookmarkRepo(conn).move(bid, cat, pos)
        if not ok:
            raise HTTPException(404, "收藏不存在")
        return JSONResponse({"ok": True})

    @lib.post("/api/bookmarks/{bid}/hit")
    def hit_bookmark(bid: int, request: Request, conn=Depends(get_conn)):
        guard_library(request)
        repo.BookmarkRepo(conn).hit(bid)
        return JSONResponse({"ok": True})

    @lib.get("/api/library/export")
    def export_library(request: Request, conn=Depends(get_conn)):
        """导出成可直接再导入的 JSON（分类含 id，导入时按名字对齐）。"""
        guard_library(request)
        data = repo.BookmarkRepo(conn).export_all()
        name_by_id = {c["id"]: c["name"] for c in data["categories"]}
        for b in data["bookmarks"]:
            b["category"] = name_by_id.get(b.get("category_id"))
        return JSONResponse({"ok": True, "version": 1, **data})

    @lib.post("/api/library/import")
    async def import_library(request: Request, conn=Depends(get_conn),
                             replace: bool = False):
        guard_library(request)
        data = await read_json(request)
        stats = repo.BookmarkRepo(conn).import_all(data, replace=replace)
        return JSONResponse({"ok": True, **stats})

    app.include_router(lib, prefix=cfg.prefix)
    if cfg.prefix:
        # 反代剥掉前缀时，请求到达这里已经是 /api/...
        app.include_router(lib, prefix="")

    return app


app = create_app()
