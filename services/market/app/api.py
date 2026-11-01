"""服务层：FastAPI（REST + WebSocket）。

分层约定：
  采集器只管往总线推数据，不管谁在消费；API 只管读总线 + 查数据湖。
  两边通过 EventBus 解耦 —— 行情源抽风时，API 依然在线（返回降级状态，而不是 500 崩掉）。

对外接口：
  GET  /healthz              存活探针（永远 200，带各子系统状态）
  GET  /api/status           详细状态：总线模式、数据湖、采集器延迟与错误数
  GET  /api/quotes           最近一次行情快照（陈旧则 503）
  GET  /api/history          历史回测数据（走数据湖 Parquet）
  GET  /api/datasets         数据湖可用数据集清单
  WS   /ws/quotes            实时行情推送（断线不影响采集）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .bus import TOPIC_QUOTES, create_bus
from .collector import QuoteCollector, Snapshot
from .lake import LakeError, list_datasets, normalize_symbol, query_history
from .settings import Settings, load_settings

# easy_tdx 默认把配置写到 ~/.easy_tdx，容器内 / 非 root 用户常常写不了（PermissionError）。
# 指到服务自己的数据目录，避免踩这个坑。
os.environ.setdefault(
    "EASY_TDX_CONFIG_DIR",
    str(Path(os.getenv("MARKET_DATA_DIR", "./data")) / "easy_tdx"))

log = logging.getLogger("market.api")


def _check_token(request: Request, settings: Settings, query_token: str | None = None) -> None:
    """有 API_TOKEN 就必须带对；留空＝不鉴权（内网自用场景）。"""
    if not settings.api_token:
        return
    got = request.headers.get("X-API-Token") or query_token or ""
    import secrets
    if not secrets.compare_digest(got, settings.api_token):
        raise HTTPException(401, "无效的 API token")


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or load_settings()
    snapshot = Snapshot()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.bus = create_bus(s.bus_mode, s.redis_url, s.bus_queue_size)
        app.state.collector = QuoteCollector(s, app.state.bus, snapshot)
        await app.state.collector.start()
        try:
            yield
        finally:
            await app.state.collector.stop()
            await app.state.bus.close()

    app = FastAPI(title="行情数据服务", version="1.0", lifespan=lifespan, docs_url="/docs")

    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    async def healthz():
        return {
            "ok": True,
            "bus": s.bus_mode,
            "collector": {
                "symbols": len(s.symbols),
                "workers": s.workers,
                "last_ts": snapshot.ts,
                "age": round(snapshot.age(), 2) if snapshot.ts else None,
                "errors": snapshot.errors,
                "last_error": snapshot.last_error,
            },
            "lake": {"root": str(s.lake_root), "available": s.lake_available},
        }

    @app.get("/api/status")
    async def status(request: Request, token: str | None = Query(None)):
        _check_token(request, s, token)
        datasets = list_datasets(s.lake_root) if s.lake_available else []
        return {
            "ok": True,
            "bus": s.bus_mode,
            "protocol": s.protocol,
            "poll_interval": s.poll_interval,
            "symbols": list(s.symbols),
            "snapshot": {
                "ts": snapshot.ts,
                "age": round(snapshot.age(), 2) if snapshot.ts else None,
                "count": len(snapshot.quotes),
                "errors": snapshot.errors,
                "last_error": snapshot.last_error,
                "worker_errors": snapshot.worker_errors,
            },
            "lake": {"root": str(s.lake_root), "datasets": datasets},
        }

    @app.get("/api/quotes")
    async def quotes(request: Request, token: str | None = Query(None)):
        _check_token(request, s, token)
        if not snapshot.ts:
            return JSONResponse(status_code=503, content={
                "error": "尚无行情数据，采集器可能正在连接行情节点",
                "code": "no_data", "last_error": snapshot.last_error})
        age = snapshot.age()
        if age > s.stale_after:
            return JSONResponse(status_code=503, content={
                "error": f"行情快照已陈旧（{age:.1f}s > {s.stale_after:.0f}s），采集端可能掉线",
                "code": "stale", "age": round(age, 2), "last_error": snapshot.last_error})
        return {"ok": True, "ts": snapshot.ts, "age": round(age, 2), "quotes": snapshot.quotes}

    @app.get("/api/datasets")
    async def datasets(request: Request, token: str | None = Query(None)):
        _check_token(request, s, token)
        if not s.lake_available:
            return JSONResponse(status_code=503, content={
                "error": f"数据湖不存在：{s.lake_root}", "code": "no_lake"})
        return {"ok": True, "root": str(s.lake_root), "datasets": list_datasets(s.lake_root)}

    @app.get("/api/history")
    async def history(request: Request,
                      symbol: str = Query(..., description="600519 或 600519.SH"),
                      dataset: str = Query("daily_bars"),
                      start: str | None = Query(None, description="YYYY-MM-DD"),
                      end: str | None = Query(None, description="YYYY-MM-DD"),
                      limit: int = Query(2000, ge=1, le=20000),
                      token: str | None = Query(None)):
        _check_token(request, s, token)
        if not s.lake_available:
            return JSONResponse(status_code=503, content={
                "error": f"数据湖不存在：{s.lake_root}（先跑 cne init 建湖）", "code": "no_lake"})
        try:
            result = await asyncio.to_thread(
                query_history, s.lake_root, dataset,
                normalize_symbol(symbol), start, end, limit)
        except LakeError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc), "code": "lake_error"})
        except Exception as exc:                     # noqa: BLE001
            log.exception("历史查询失败")
            return JSONResponse(status_code=500, content={
                "error": f"{type(exc).__name__}: {exc}", "code": "internal"})
        return {"ok": True, **result}

    @app.websocket("/ws/quotes")
    async def ws_quotes(websocket: WebSocket, token: str | None = Query(None)):
        if s.api_token:
            import secrets
            if not secrets.compare_digest(token or "", s.api_token):
                await websocket.close(code=4401)
                return
        await websocket.accept()
        bus = websocket.app.state.bus
        try:
            async for message in bus.subscribe(TOPIC_QUOTES):
                await websocket.send_text(json.dumps(message, ensure_ascii=False, default=str))
        except WebSocketDisconnect:
            log.info("WebSocket 客户端断开")
        except Exception:
            log.exception("WebSocket 推送异常")
            try:
                await websocket.send_text(json.dumps(
                    {"type": "status", "state": "degraded", "ts": time.time()},
                    ensure_ascii=False))
            except Exception:
                pass

    return app


app = create_app()


def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run("app.api:app", host=s.host, port=s.port, log_level=os.getenv("UVICORN_LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
