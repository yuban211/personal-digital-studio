"""HTTP 接口：一个进程同时提供 API 与页面。

页面挂在 /，静态资源挂在 /assets。全部同源，不需要 CORS。
反代若把 /quant/* 剥前缀转进来，这些路径依然成立。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .broker import broker, is_session, limit_pct
from .engine import OFFHOURS, engine
from .feed import feed
from .settings import settings
from .store import store
from .strategy import registry

app = FastAPI(title="全A模拟实盘", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/assets", StaticFiles(directory=str(settings.web_dir)), name="assets")


@app.middleware("http")
async def headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data:; "
                            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                            "connect-src 'self'; object-src 'none'; base-uri 'self'")
    return resp


@app.on_event("startup")
def _startup() -> None:
    settings.ensure_dirs()
    registry.reload()
    if settings.engine_enabled:
        engine.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    engine.stop()


# ═══════════════════════════════════════════════════════════════
# 页面
# ═══════════════════════════════════════════════════════════════

@app.get("/")
@app.get("/index.html")
def index():
    f = settings.web_dir / "index.html"
    if not f.exists():
        raise HTTPException(500, "index.html 缺失")
    return FileResponse(f, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    return {"ok": True, "rows": len(feed.get()), "age": feed.age,
            "engine": engine.state["running"]}


# ═══════════════════════════════════════════════════════════════
# 行情
# ═══════════════════════════════════════════════════════════════

def breadth(rows: list[dict[str, Any]]) -> dict[str, Any]:
    up = down = flat = lu = ld = 0
    amount = 0.0
    for r in rows:
        amount += r["amount"]
        chg = r["chg"]
        lim = limit_pct(r["code"], r["name"])
        if chg > 0:
            up += 1
        elif chg < 0:
            down += 1
        else:
            flat += 1
        if chg >= lim - 0.3 and r["price"] > 0:
            lu += 1
        if chg <= -lim + 0.3 and r["price"] > 0:
            ld += 1
    return {"up": up, "down": down, "flat": flat, "limit_up": lu, "limit_down": ld,
            "total": len(rows), "amount": round(amount, 2)}


@app.get("/api/overview")
def overview():
    rows = feed.get()
    try:
        idx = feed.indices()
    except Exception:
        idx = []
    try:
        sectors = feed.sectors(8)
    except Exception:
        sectors = []
    return {
        "ok": True,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "session" if is_session() else ("offhours" if OFFHOURS else "closed"),
        "indices": idx,
        "breadth": breadth(rows),
        "sectors": sectors,
        "snapshot_age": feed.age,
        "snapshot_error": feed.error,
        "source": feed.source,
    }


SORT_KEYS = {"chg": "chg", "amount": "amount", "turnover": "turnover", "vol_ratio": "vol_ratio",
             "price": "price", "pe": "pe", "mktcap": "mktcap", "main_flow": "main_flow",
             "code": "code"}


def match_filter(r: dict[str, Any], flt: str) -> bool:
    code, chg = r["code"], r["chg"]
    lim = limit_pct(code, r["name"])
    if flt == "up":
        return chg > 0
    if flt == "down":
        return chg < 0
    if flt == "limit_up":
        return chg >= lim - 0.3 and r["price"] > 0
    if flt == "limit_down":
        return chg <= -lim + 0.3 and r["price"] > 0
    if flt == "sh":
        return code.startswith("6")
    if flt == "sz":
        return code.startswith(("0", "3"))
    if flt == "bj":
        return code.startswith(("4", "8", "9"))
    if flt == "main":
        return code.startswith(("60", "000", "001", "002", "003"))
    if flt == "gem":
        return code.startswith(("300", "301"))
    if flt == "star":
        return code.startswith("688")
    return True


@app.get("/api/list")
def qlist(page: int = 1, size: int = 50, sort: str = "chg", order: str = "desc",
          q: str = "", filter: str = "all"):
    rows = feed.get()
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r["code"] or needle in r["name"].lower()]
    if filter != "all":
        rows = [r for r in rows if match_filter(r, filter)]

    key = SORT_KEYS.get(sort, "chg")
    rows = sorted(rows, key=lambda r: (r[key] if key != "code" else r["code"]),
                  reverse=(order != "asc"))
    size = max(1, min(size, 200))
    page = max(1, page)
    total = len(rows)
    start = (page - 1) * size
    page_rows = feed.enrich([dict(r) for r in rows[start:start + size]])
    return {
        "ok": True,
        "total": total,
        "page": page,
        "size": size,
        "pages": math.ceil(total / size) if total else 1,
        "rows": page_rows,
        "snapshot_age": feed.age,
        "source": feed.source,
    }


@app.get("/api/sectors")
def sectors(limit: int = 40):
    try:
        return {"ok": True, "sectors": feed.sectors(limit)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "sectors": []}, status_code=200)


@app.get("/api/quote/{code}")
def quote(code: str):
    row = feed.quote(code)
    if not row:
        raise HTTPException(404, "没有这只股票")
    try:
        trend = feed.trends(code, row["market"])
    except Exception:
        trend = {"points": [], "preclose": row["preclose"], "name": row["name"]}
    return {"ok": True, "quote": row, "trend": trend}


@app.get("/api/kline/{code}")
def kline(code: str, days: int = 120):
    row = feed.quote(code)
    market = row["market"] if row else (1 if code.startswith("6") else 0)
    try:
        bars = feed.kline(code, market, days)
    except Exception as e:
        return {"ok": False, "error": str(e), "bars": []}
    return {"ok": True, "code": code, "name": (row or {}).get("name", ""), "bars": bars[-days:]}


# ═══════════════════════════════════════════════════════════════
# 模拟盘
# ═══════════════════════════════════════════════════════════════

def _prices() -> dict[str, float]:
    return {r["code"]: r["price"] for r in feed.get() if r["price"] > 0}


@app.get("/api/account")
def account():
    acct = broker.account(_prices())
    return {"ok": True, **acct, "curve": store.equity_curve(240)}


@app.get("/api/orders")
def orders(limit: int = 40):
    return {"ok": True, "orders": store.orders(limit), "trades": store.trades(limit)}


@app.get("/api/strategies")
def strategies():
    return {"ok": True, "strategies": registry.describe(),
            "current": engine.state["strategy"], "params": engine.params()}


@app.post("/api/strategy")
async def set_strategy(request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(400, "请求体必须是对象")
    return {"ok": True, "params": engine.set_params(data)}


@app.post("/api/engine")
async def engine_ctl(request: Request):
    data = await request.json()
    action = (data or {}).get("action", "tick")
    if action == "start":
        engine.start()
    elif action == "stop":
        engine.stop()
    elif action == "tick":
        return {"ok": True, "result": engine.tick(run_strategy=bool((data or {}).get("run", True)))}
    return {"ok": True, "state": engine.state}


@app.get("/api/status")
def status():
    acct = broker.account(_prices())
    return {
        "ok": True,
        "engine": engine.state,
        "mode": "session" if is_session() else ("offhours" if OFFHOURS else "closed"),
        "snapshot_age": feed.age,
        "universe": len(feed.get()),
        "equity": acct["equity"],
        "pnl_pct": acct["pnl_pct"],
        "tick_seconds": settings.tick_seconds,
    }


@app.post("/api/trade")
async def trade(request: Request):
    data = await request.json()
    code = str((data or {}).get("code", "")).strip()
    side = str((data or {}).get("side", "buy")).lower()
    qty = int((data or {}).get("qty", 0) or 0)
    if not code:
        raise HTTPException(400, "缺少代码")
    res = broker.order(code, side, qty, feed.quote(code), src="manual", reason="手动")
    return {"ok": res.get("ok", False), "result": res}


@app.post("/api/reset")
async def reset(request: Request):
    data = await request.json()
    if not (data or {}).get("confirm"):
        raise HTTPException(400, "需要 confirm=true")
    store.reset()
    return {"ok": True}
