"""行情源：双通道（腾讯为主，东财为备），统一成一套数据结构。

为什么是双通道：
  实测东财 push2 在短时间内被连续抓 56 页后会直接掐连接（Remote end closed），
  单一数据源等于把整个服务的可用性押在别人的限流策略上。
  腾讯的排行/日线/分时接口同样公开、无鉴权、限流宽松，字段还更全
  （换手、量比、市盈、主力净流入一次给齐），正合适做主力通道；
  东财则在需要"科创板 + 北交所全量"时做补充。

统一字段（rows）：
  code market name price chg chg_amt vol amount amplitude turnover
  pe vol_ratio high low open preclose mktcap floatcap pb main_flow main_pct

所有请求只用标准库 urllib；失败重试一次；快照带 TTL 缓存。
"""

from __future__ import annotations

import json
import math
from datetime import datetime
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .settings import settings
from .store import store

QT = "https://qt.gtimg.cn/q="
RANK = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
PTRANK = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank"
KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
MINUTE = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"

PUSH2 = "https://push2.eastmoney.com/api/qt"
PUSH2HIS = "https://push2his.eastmoney.com/api/qt"
FS_ALL = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
EM_FIELDS = ("f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
             "f20,f21,f23,f62,f184")

INDEX_CODES = ["sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000905"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REFERER = "https://stockapp.finance.qq.com/"


# ═══════════════════════════════════════════════════════════════
# 基础工具
# ═══════════════════════════════════════════════════════════════

def _num(v: Any) -> float:
    if v in (None, "", "-", "null"):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _get_text(url: str, encoding: str = "utf-8", referer: str = REFERER,
              timeout: float | None = None) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": referer, "Accept": "*/*",
    })
    last: Exception | None = None
    for attempt in (0, 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout or settings.http_timeout) as r:
                return r.read().decode(encoding, errors="replace")
        except Exception as e:
            last = e
            if attempt == 0:
                time.sleep(0.25)
    raise RuntimeError(f"行情请求失败: {last}")


def _get_json(url: str, referer: str = REFERER) -> dict[str, Any]:
    return json.loads(_get_text(url, referer=referer))


def split_code(raw: str) -> tuple[str, int]:
    """'sz300030' → ('300030', 0)；'sh600519' → ('600519', 1)。"""
    v = raw.strip().lower()
    if v[:2] in ("sh", "sz", "bj"):
        return v[2:], {"sh": 1, "sz": 0, "bj": 0}[v[:2]]
    code = v
    if code.startswith("6"):
        return code, 1
    return code, 0


def join_code(code: str, market: int) -> str:
    if market == 1:
        return "sh" + code
    if code.startswith(("4", "8", "9")) and not code.startswith("9"):
        return "bj" + code
    return "sz" + code


# ═══════════════════════════════════════════════════════════════
# 腾讯通道（主力）
# ═══════════════════════════════════════════════════════════════

class Tencent:
    name = "腾讯"

    # ── 全A排行 ──
    @staticmethod
    def _rank_page(offset: int, count: int = 200, board: str = "aStock") -> dict[str, Any]:
        qs = urllib.parse.urlencode({
            "board_code": board, "sort_type": "PriceRatio", "direct": "down",
            "offset": offset, "count": count,
        })
        return _get_json(f"{RANK}?{qs}")

    def snapshot(self) -> list[dict[str, Any]]:
        first = self._rank_page(0)
        data = first.get("data") or {}
        total = int(data.get("total") or 0)
        items = list(data.get("rank_list") or [])
        offsets = list(range(200, total, 200))
        if offsets:
            with ThreadPoolExecutor(max_workers=settings.fetch_workers) as pool:
                for page in pool.map(lambda o: self._rank_page(o), offsets):
                    items.extend((page.get("data") or {}).get("rank_list") or [])
        rows = []
        for it in items:
            code, market = split_code(str(it.get("code", "")))
            if not code:
                continue
            rows.append({
                "code": code,
                "market": market,
                "name": it.get("name", "") or "",
                "price": _num(it.get("zxj")),
                "chg": _num(it.get("zdf")),
                "chg_amt": _num(it.get("zd")),
                "vol": _num(it.get("volume")),
                "amount": _num(it.get("turnover")) * 1e4,     # 万元 → 元
                "amplitude": _num(it.get("zf")),
                "turnover": _num(it.get("hsl")),
                "pe": _num(it.get("pe_ttm")),
                "vol_ratio": _num(it.get("lb")),
                "high": 0.0, "low": 0.0, "open": 0.0, "preclose": 0.0,
                "mktcap": _num(it.get("zsz")) * 1e8,
                "floatcap": _num(it.get("ltsz")) * 1e8,
                "pb": _num(it.get("pn")),
                # zljlr 本身就是"主力净流入"，单位万元（实测：涨停股 +0.99 亿 / 大票 -3.5 亿）
                "main_flow": round(_num(it.get("zljlr")) * 1e4, 2),
                "main_pct": 0.0,
            })
        rows.sort(key=lambda r: r["amount"], reverse=True)
        return rows

    # ── 单页补充：最高/最低/今开/昨收（排行接口不返回）──
    def enrich(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        codes = [join_code(r["code"], r["market"]) for r in rows]
        try:
            text = _get_text(QT + ",".join(codes), encoding="gbk")
        except Exception:
            return
        by_code: dict[str, list[str]] = {}
        for line in text.split(";"):
            line = line.strip()
            if not line.startswith("v_"):
                continue
            head, _, payload = line.partition("=")
            parts = payload.strip('"').split("~")
            if len(parts) < 6:
                continue
            by_code[head[2:]] = parts
        for r in rows:
            p = by_code.get(join_code(r["code"], r["market"]))
            if not p:
                continue
            if not r["price"]:
                r["price"] = _num(p[3])
            r["preclose"] = _num(p[4])
            r["open"] = _num(p[5])
            if len(p) > 33:
                r["high"] = _num(p[33])
                r["low"] = _num(p[34])
            if len(p) > 47 and not r["vol_ratio"]:
                r["vol_ratio"] = _num(p[47])

    # ── 指数 ──
    def indices(self) -> list[dict[str, Any]]:
        text = _get_text(QT + ",".join(INDEX_CODES), encoding="gbk")
        out = []
        for line in text.split(";"):
            line = line.strip()
            if not line.startswith("v_"):
                continue
            parts = line.partition("=")[2].strip('"').split("~")
            if len(parts) < 32:
                continue
            out.append({
                "code": parts[2],
                "name": parts[1],
                "price": _num(parts[3]),
                "chg": _num(parts[32]) if len(parts) > 32 else _num(parts[31]),
                "chg_amt": _num(parts[31]),
                "amount": _num(parts[37]) * 1e4 if len(parts) > 37 else 0.0,
            })
        return out

    # ── 行业板块 ──
    def sectors(self, limit: int = 40) -> list[dict[str, Any]]:
        qs = urllib.parse.urlencode({
            "board_type": "hy", "sort_type": "PriceRatio", "direct": "down",
            "offset": 0, "count": max(limit, 20),
        })
        data = _get_json(f"{PTRANK}?{qs}")
        out = []
        for it in ((data.get("data") or {}).get("rank_list") or []):
            leader = it.get("lzg") or {}
            out.append({
                "code": str(it.get("code", "")),
                "name": it.get("name", ""),
                "chg": _num(it.get("zdf")),
                "amount": _num(it.get("turnover")) * 1e4,
                "main_flow": round(_num(it.get("zljlr")) * 1e4, 2),
                "up": 0, "down": 0,
                "leader": leader.get("name", ""),
                "leader_chg": _num(leader.get("zdf")),
                "leader_code": split_code(str(leader.get("code", "")))[0],
            })
        return out[:limit]

    # ── 日线（前复权）──
    def kline(self, code: str, market: int, days: int = 120) -> list[dict[str, Any]]:
        symbol = join_code(code, market)
        url = f"{KLINE}?param={symbol},day,,,{max(days, 30)},qfq"
        data = _get_json(url)
        node = (data.get("data") or {}).get(symbol) or {}
        raw = node.get("qfqday") or node.get("day") or []
        out = []
        prev = None
        for item in raw:
            if len(item) < 6:
                continue
            close = _num(item[2])
            out.append({
                "date": item[0],
                "open": _num(item[1]),
                "close": close,
                "high": _num(item[3]),
                "low": _num(item[4]),
                "vol": _num(item[5]),
                "amount": 0.0,
                "chg": round((close / prev - 1) * 100, 2) if prev else 0.0,
            })
            prev = close or prev
        return out[-days:]

    # ── 当日分时 ──
    def trends(self, code: str, market: int) -> dict[str, Any]:
        symbol = join_code(code, market)
        data = _get_json(f"{MINUTE}?code={symbol}")
        root = (data.get("data") or {}).get(symbol) or {}
        node = root.get("data") or {}
        raw = node.get("data") or []
        # 昨收在 qt 快照里：qt[symbol][4]
        qt = (root.get("qt") or {}).get(symbol) or []
        preclose = _num(qt[4]) if len(qt) > 4 else 0.0
        points = []
        prev_vol = 0.0
        for line in raw:
            parts = line.split(" ")
            if len(parts) < 4:
                continue
            t = parts[0]
            price = _num(parts[1])
            cum_vol = _num(parts[2])          # 累计成交量（手）
            cum_amount = _num(parts[3])       # 累计成交额（元）
            avg = (cum_amount / (cum_vol * 100)) if cum_vol > 0 else price
            points.append({
                "t": f"{t[:2]}:{t[2:]}",
                "price": price,
                "avg": round(avg, 3),
                "vol": max(0.0, cum_vol - prev_vol),   # 分钟增量，画量柱用
            })
            prev_vol = cum_vol
        return {"code": code, "name": node.get("name", ""), "preclose": preclose,
                "points": points}


# ═══════════════════════════════════════════════════════════════
# 东财通道（补充：科创板/北交所全量，字段更全）
# ═══════════════════════════════════════════════════════════════

class Eastmoney:
    name = "东财"

    @staticmethod
    def _page(pn: int) -> dict[str, Any]:
        qs = urllib.parse.urlencode({
            "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": FS_ALL, "fields": EM_FIELDS,
        })
        return _get_json(f"{PUSH2}/clist/get?{qs}", referer="https://quote.eastmoney.com/")

    def snapshot(self) -> list[dict[str, Any]]:
        first = self._page(1)
        data = first.get("data") or {}
        total = int(data.get("total") or 0)
        items = list(data.get("diff") or [])
        pages = max(1, math.ceil(total / 100))
        if pages > 1:
            with ThreadPoolExecutor(max_workers=settings.fetch_workers) as pool:
                for page in pool.map(self._page, range(2, pages + 1)):
                    items.extend((page.get("data") or {}).get("diff") or [])
        rows = []
        for d in items:
            if not d.get("f12"):
                continue
            rows.append({
                "code": str(d["f12"]),
                "market": int(d.get("f13") or 0),
                "name": d.get("f14", "") or "",
                "price": _num(d.get("f2")),
                "chg": _num(d.get("f3")),
                "chg_amt": _num(d.get("f4")),
                "vol": _num(d.get("f5")),
                "amount": _num(d.get("f6")),
                "amplitude": _num(d.get("f7")),
                "turnover": _num(d.get("f8")),
                "pe": _num(d.get("f9")),
                "vol_ratio": _num(d.get("f10")),
                "high": _num(d.get("f15")),
                "low": _num(d.get("f16")),
                "open": _num(d.get("f17")),
                "preclose": _num(d.get("f18")),
                "mktcap": _num(d.get("f20")),
                "floatcap": _num(d.get("f21")),
                "pb": _num(d.get("f23")),
                "main_flow": _num(d.get("f62")),
                "main_pct": _num(d.get("f184")),
            })
        rows.sort(key=lambda r: r["amount"], reverse=True)
        return rows

    def enrich(self, rows: list[dict[str, Any]]) -> None:
        return  # 东财字段本身就完整

    def indices(self) -> list[dict[str, Any]]:
        qs = urllib.parse.urlencode({
            "secids": "1.000001,0.399001,0.399006,1.000688,1.000300,1.000905",
            "fltt": 2, "invt": 2, "fields": "f1,f2,f3,f4,f6,f12,f13,f14,f18",
        })
        data = _get_json(f"{PUSH2}/ulist.np/get?{qs}", referer="https://quote.eastmoney.com/")
        out = []
        for d in ((data.get("data") or {}).get("diff") or []):
            out.append({
                "code": str(d.get("f12", "")), "name": d.get("f14", ""),
                "price": _num(d.get("f2")), "chg": _num(d.get("f3")),
                "chg_amt": _num(d.get("f4")), "amount": _num(d.get("f6")),
            })
        return out

    def sectors(self, limit: int = 40) -> list[dict[str, Any]]:
        qs = urllib.parse.urlencode({
            "pn": 1, "pz": max(limit, 20), "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": "m:90+t:2",
            "fields": "f2,f3,f6,f12,f14,f62,f104,f105,f128,f136,f140",
        })
        data = _get_json(f"{PUSH2}/clist/get?{qs}", referer="https://quote.eastmoney.com/")
        out = []
        for d in ((data.get("data") or {}).get("diff") or []):
            out.append({
                "code": str(d.get("f12", "")), "name": d.get("f14", ""),
                "chg": _num(d.get("f3")), "amount": _num(d.get("f6")),
                "main_flow": _num(d.get("f62")),
                "up": int(_num(d.get("f104"))), "down": int(_num(d.get("f105"))),
                "leader": d.get("f128", "") or "", "leader_chg": _num(d.get("f136")),
                "leader_code": d.get("f140", "") or "",
            })
        return out[:limit]

    def kline(self, code: str, market: int, days: int = 120) -> list[dict[str, Any]]:
        qs = urllib.parse.urlencode({
            "secid": f"{1 if market == 1 else 0}.{code}", "klt": 101, "fqt": 1,
            "beg": 0, "end": 20500101, "lmt": days,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        })
        data = _get_json(f"{PUSH2HIS}/stock/kline/get?{qs}", referer="https://quote.eastmoney.com/")
        out = []
        for line in ((data.get("data") or {}).get("klines") or []):
            p = line.split(",")
            if len(p) < 7:
                continue
            out.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                        "high": float(p[3]), "low": float(p[4]), "vol": float(p[5]),
                        "amount": float(p[6]), "chg": float(p[8]) if len(p) > 8 else 0.0})
        return out[-days:]

    def trends(self, code: str, market: int) -> dict[str, Any]:
        qs = urllib.parse.urlencode({
            "secid": f"{1 if market == 1 else 0}.{code}", "ndays": 1, "iscr": 0,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        })
        data = _get_json(f"{PUSH2}/stock/trends2/get?{qs}", referer="https://quote.eastmoney.com/")
        d = data.get("data") or {}
        points = []
        for line in d.get("trends") or []:
            p = line.split(",")
            if len(p) < 6:
                continue
            points.append({"t": p[0][-5:], "price": float(p[2]),
                           "avg": float(p[7]) if len(p) > 7 and p[7] not in ("-", "") else float(p[2]),
                           "vol": float(p[5])})
        return {"code": code, "name": d.get("name", ""),
                "preclose": _num(d.get("preClose")), "points": points}



# ═══════════════════════════════════════════════════════════════
# 新浪通道（日线备用：腾讯 kline 偶发 501 时接管）
# ═══════════════════════════════════════════════════════════════

class Sina:
    name = "新浪"
    BASE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_MarketData.getKLineData")

    def kline(self, code: str, market: int, days: int = 120) -> list[dict[str, Any]]:
        symbol = join_code(code, market)
        url = f"{self.BASE}?symbol={symbol}&scale=240&ma=no&datalen={days}"
        text = _get_text(url, referer="https://finance.sina.com.cn/")
        data = json.loads(text) if text.strip().startswith("[") else []
        out = []
        prev = None
        for item in data:
            close = _num(item.get("close"))
            out.append({
                "date": str(item.get("day", ""))[:10],
                "open": _num(item.get("open")),
                "close": close,
                "high": _num(item.get("high")),
                "low": _num(item.get("low")),
                "vol": _num(item.get("volume")) / 100.0,      # 股 → 手，与腾讯口径一致
                "amount": 0.0,
                "chg": round((close / prev - 1) * 100, 2) if prev else 0.0,
            })
            prev = close or prev
        return out[-days:]


# ═══════════════════════════════════════════════════════════════
# 门面：缓存 + 自动切换 + 状态
# ═══════════════════════════════════════════════════════════════

class Cache:
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._d: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            hit = self._d.get(key)
            if hit and hit[0] > time.time():
                return hit[1]
        return None

    def put(self, key: str, value: Any) -> Any:
        with self._lock:
            self._d[key] = (time.time() + self.ttl, value)
        return value


class Feed:
    def __init__(self) -> None:
        self.tencent = Tencent()
        self.eastmoney = Eastmoney()
        self.sina = Sina()
        self._fail: dict[str, int] = {}
        self._cool_until: dict[str, float] = {}
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._by_code: dict[str, dict[str, Any]] = {}
        self._ts = 0.0
        self._error = ""
        self._source = ""
        self._idx = Cache(settings.quote_ttl)
        self._sec = Cache(max(settings.quote_ttl, 15))
        self._kline = Cache(settings.kline_ttl)
        self._minute = Cache(settings.quote_ttl)

    # ── 通道健康度 ──
    def _usable(self, name: str) -> bool:
        return time.time() >= self._cool_until.get(name, 0.0)

    def _mark_ok(self, name: str) -> None:
        self._fail[name] = 0

    def _mark_bad(self, name: str) -> None:
        n = self._fail.get(name, 0) + 1
        self._fail[name] = n
        if n >= 3:
            self._cool_until[name] = time.time() + 300   # 冷却 5 分钟
            self._fail[name] = 0

    # ── 快照 ──
    def _fetch_snapshot(self) -> tuple[list[dict[str, Any]], str]:
        errors = []
        for src in (self.tencent, self.eastmoney):
            if not self._usable(src.name):
                continue
            try:
                rows = src.snapshot()
                self._mark_ok(src.name)
                if rows:
                    # 东财能补上科创板/北交所，腾讯覆盖沪深主板+创业板
                    if src is self.tencent:
                        try:
                            extra = {r["code"] for r in rows}
                            for r in self.eastmoney.snapshot():
                                if r["code"] not in extra:
                                    rows.append(r)
                        except Exception:
                            pass
                        rows.sort(key=lambda r: r["amount"], reverse=True)
                    return rows, src.name
            except Exception as e:
                self._mark_bad(src.name)
                errors.append(f"{src.name}: {e}")
        self._error = " | ".join(errors) or "无可用行情源"
        return [], ""

    def get(self, force: bool = False) -> list[dict[str, Any]]:
        if self._rows and not force and (time.time() - self._ts) < settings.snapshot_ttl:
            return self._rows
        with self._lock:
            if self._rows and not force and (time.time() - self._ts) < settings.snapshot_ttl:
                return self._rows
            rows, source = self._fetch_snapshot()
            if rows:
                self._rows = rows
                self._by_code = {r["code"]: r for r in rows}
                self._source = source
                self._ts = time.time()
                self._error = ""
            return self._rows

    @property
    def age(self) -> float:
        return round(time.time() - self._ts, 1) if self._ts else -1

    @property
    def error(self) -> str:
        return self._error

    @property
    def source(self) -> str:
        return self._source

    @property
    def total(self) -> int:
        return len(self._rows)

    def by_code(self) -> dict[str, dict[str, Any]]:
        return self._by_code

    # ── 单只 ──
    def quote(self, code: str) -> dict[str, Any] | None:
        row = self._by_code.get(code)
        if row:
            return row
        self.get()
        return self._by_code.get(code)

    # ── 指数 / 板块 ──
    def indices(self) -> list[dict[str, Any]]:
        hit = self._idx.get("i")
        if hit is not None:
            return hit
        for src in (self.tencent, self.eastmoney):
            try:
                rows = src.indices()
                if rows:
                    return self._idx.put("i", rows)
            except Exception:
                continue
        return []

    def sectors(self, limit: int = 40) -> list[dict[str, Any]]:
        # 缓存永远存"全量"，按需切片；否则先来一个 limit=8 的调用会把缓存写小
        hit = self._sec.get("s")
        if hit is None:
            for src in (self.tencent, self.eastmoney):
                try:
                    rows = src.sectors(40)
                    if rows:
                        hit = self._sec.put("s", rows)
                        break
                except Exception:
                    continue
        return (hit or [])[:limit]

    # ── 日线 / 分时 ──
    def kline(self, code: str, market: int, days: int = 120) -> list[dict[str, Any]]:
        key = f"k:{code}:{days}"
        hit = self._kline.get(key)
        if hit is not None:
            return hit
        # 磁盘缓存：同一交易日只抓一次，重启也不用重拉 3000 只
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            raw = store.kline_get(code, today)
            if raw:
                bars = json.loads(raw)
                if len(bars) >= days:          # 缓存不够长就重抓，别拿 60 根凑 90 根
                    return self._kline.put(key, bars[-days:])
        except Exception:
            pass
        # 顺序：腾讯（字段全、最快）→ 新浪（限流宽松）→ 东财（同源限流最紧）
        for src in (self.tencent, self.sina, self.eastmoney):
            if not self._usable(src.name):
                continue
            try:
                bars = src.kline(code, market, days)
                if bars:
                    self._mark_ok(src.name)
                    try:
                        store.kline_put(code, today, json.dumps(bars, ensure_ascii=False))
                    except Exception:
                        pass
                    return self._kline.put(key, bars)
            except Exception:
                self._mark_bad(src.name)
                continue
        return []

    def trends(self, code: str, market: int) -> dict[str, Any]:
        key = f"t:{code}"
        hit = self._minute.get(key)
        if hit is not None:
            return hit
        for src in (self.tencent, self.eastmoney):
            try:
                data = src.trends(code, market)
                if data.get("points"):
                    return self._minute.put(key, data)
            except Exception:
                continue
        return {"code": code, "name": "", "preclose": 0.0, "points": []}

    def enrich(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """给当前页补 最高/最低/今开/昨收（排行接口没有这几个字段）。"""
        try:
            self.tencent.enrich(rows)
        except Exception:
            pass
        return rows


feed = Feed()
