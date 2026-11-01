"""策略层：内置三只 + 从 strategies/ 目录自动加载你自己的策略。

写自己的策略（不需要改这个文件）：
    # strategies/my_idea.py
    NAME = "我的想法"
    DESCRIPTION = "一句话说明"
    PARAMS = {"top_n": 3}

    def select(ctx):
        picks = []
        for row in ctx.rows:                     # 全A（按成交额预筛后的候选）
            bars = ctx.bars(row["code"], 40)     # 日线，带缓存
            if len(bars) < 21:
                continue
            ...
            picks.append({"code": row["code"], "side": "buy",
                          "weight": 0.1, "reason": "理由写这里"})
        return picks

    ctx 提供：rows / positions / equity / params / today / bars(code,n) / log(msg)
    返回项支持 weight（占净值比例）或 qty（股数），卖出可省略数量=清仓。

保存后 10 秒内自动出现在页面策略下拉里（引擎每轮都会扫描目录）。
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .settings import settings


@dataclass
class Context:
    rows: list[dict[str, Any]]
    positions: dict[str, dict[str, Any]]
    equity: float
    params: dict[str, Any] = field(default_factory=dict)
    today: str = ""
    bars_fn: Callable[[str, int], list[dict[str, Any]]] | None = None
    log_fn: Callable[[str], None] | None = None
    market_of: Callable[[str], int] | None = None

    def bars(self, code: str, n: int = 60) -> list[dict[str, Any]]:
        if not self.bars_fn:
            return []
        try:
            return self.bars_fn(code, n)
        except Exception:
            return []

    def log(self, msg: str) -> None:
        if self.log_fn:
            self.log_fn(str(msg)[:200])


class Strategy:
    key = "base"
    label = "基类"
    desc = ""
    params: dict[str, Any] = {}
    warmup = 25

    def select(self, ctx: Context) -> list[dict[str, Any]]:
        return []


# ═══════════════════════════════════════════════════════════════
# 内置策略
# ═══════════════════════════════════════════════════════════════

class Momentum(Strategy):
    key = "momentum"
    label = "20 日动量"
    desc = "20 日涨幅居前且站上 20 日均线，取前 N 名等权买入；跌破均线或动量转负则卖出。"
    params = {"top_n": 5, "min_amount": 3e8, "max_positions": 5}
    warmup = 25

    def select(self, ctx: Context) -> list[dict[str, Any]]:
        top_n = int(ctx.params.get("top_n", 5))
        min_amount = float(ctx.params.get("min_amount", 3e8))
        scored = []
        for row in ctx.rows:
            if row["amount"] < min_amount or "ST" in row["name"].upper():
                continue
            bars = ctx.bars(row["code"], 30)
            if len(bars) < 21:
                continue
            close = [b["close"] for b in bars]
            ma20 = sum(close[-20:]) / 20
            mom = close[-1] / close[-21] - 1
            if mom <= 0 or close[-1] < ma20:
                continue
            scored.append((mom, row, ma20))
        scored.sort(key=lambda x: -x[0])

        out = []
        for mom, row, _ma in scored[:top_n]:
            out.append({"code": row["code"], "side": "buy", "weight": None,
                        "reason": f"20日动量 {mom * 100:.1f}%"})

        # 已持仓但动量转弱的，明确卖出
        held = {code for code in ctx.positions}
        keep = {p["code"] for p in out}
        for row in ctx.rows:
            if row["code"] not in held or row["code"] in keep:
                continue
            bars = ctx.bars(row["code"], 30)
            if len(bars) >= 21:
                close = [b["close"] for b in bars]
                ma20 = sum(close[-20:]) / 20
                if close[-1] < ma20:
                    out.append({"code": row["code"], "side": "sell",
                                "reason": "跌破 20 日均线"})
        return out


class MaCross(Strategy):
    key = "ma_cross"
    label = "5/20 均线金叉"
    desc = "最近 3 个交易日出现 5 日上穿 20 日均线，且当日未涨停时买入；出现死叉卖出。"
    params = {"top_n": 5, "min_amount": 2e8}
    warmup = 30

    def select(self, ctx: Context) -> list[dict[str, Any]]:
        top_n = int(ctx.params.get("top_n", 5))
        min_amount = float(ctx.params.get("min_amount", 2e8))
        out = []
        for row in ctx.rows:
            if row["amount"] < min_amount or "ST" in row["name"].upper():
                continue
            bars = ctx.bars(row["code"], 40)
            if len(bars) < 25:
                continue
            close = [b["close"] for b in bars]
            ma = lambda arr, n, i: sum(arr[i - n:i]) / n
            crossed = False
            for i in range(len(close) - 3, len(close)):
                if i < 21:
                    continue
                fast_prev, slow_prev = ma(close, 5, i - 1), ma(close, 20, i - 1)
                fast_now, slow_now = ma(close, 5, i), ma(close, 20, i)
                if fast_prev <= slow_prev and fast_now > slow_now:
                    crossed = True
            if crossed and row["chg"] < 9.0:
                out.append({"code": row["code"], "side": "buy",
                            "reason": "MA5 上穿 MA20"})

        for code in list(ctx.positions):
            pos = ctx.positions[code]
            bars = ctx.bars(code, 40)
            if len(bars) < 25:
                continue
            close = [b["close"] for b in bars]
            fast = sum(close[-5:]) / 5
            slow = sum(close[-20:]) / 20
            if fast < slow and pos["qty"] > 0:
                out.append({"code": code, "side": "sell", "reason": "MA5 下穿 MA20"})

        buys = [p for p in out if p["side"] == "buy"][:top_n]
        sells = [p for p in out if p["side"] == "sell"]
        return sells + buys


class Breakout(Strategy):
    key = "breakout"
    label = "放量突破 20 日高"
    desc = "现价创 20 日新高、量比 > 1.2、涨幅在 2%~9.5% 之间时买入；跌破 10 日均线卖出。"
    params = {"top_n": 4, "min_amount": 1e8, "vol_ratio": 1.2}
    warmup = 25

    def select(self, ctx: Context) -> list[dict[str, Any]]:
        top_n = int(ctx.params.get("top_n", 4))
        vol_ratio = float(ctx.params.get("vol_ratio", 1.2))
        out = []
        for row in ctx.rows:
            if row["amount"] < float(ctx.params.get("min_amount", 1e8)):
                continue
            if "ST" in row["name"].upper() or not (2.0 <= row["chg"] <= 9.5):
                continue
            if row["vol_ratio"] < vol_ratio:
                continue
            bars = ctx.bars(row["code"], 30)
            if len(bars) < 21:
                continue
            high20 = max(b["high"] for b in bars[-21:-1])
            if row["price"] >= high20 * 0.995:
                out.append({"code": row["code"], "side": "buy",
                            "reason": f"突破 20 日高 {high20:.2f}"})

        for code in list(ctx.positions):
            bars = ctx.bars(code, 30)
            if len(bars) < 11:
                continue
            close = [b["close"] for b in bars]
            if close[-1] < sum(close[-10:]) / 10:
                out.append({"code": code, "side": "sell", "reason": "跌破 10 日均线"})
        return [p for p in out if p["side"] == "sell"] + [p for p in out if p["side"] == "buy"][:top_n]


BUILTIN: dict[str, Strategy] = {s.key: s for s in (Momentum(), MaCross(), Breakout())}


# ═══════════════════════════════════════════════════════════════
# 外部策略加载
# ═══════════════════════════════════════════════════════════════

class FuncStrategy(Strategy):
    """把"一个 select 函数"包装成策略对象。"""

    def __init__(self, key: str, label: str, desc: str, fn: Callable,
                 params: dict[str, Any]) -> None:
        self.key = key
        self.label = label
        self.desc = desc
        self.params = params or {}
        self._fn = fn

    def select(self, ctx: Context) -> list[dict[str, Any]]:
        return self._fn(ctx) or []


def load_external() -> dict[str, Strategy]:
    """扫描策略目录。单个文件出错只跳过它，不影响其它策略与服务本身。"""
    found: dict[str, Strategy] = {}
    d = settings.strategy_dir
    if not d.exists():
        return found
    for py in sorted(d.glob("*.py")):
        if py.name.startswith("_"):
            continue
        key = py.stem
        if key in BUILTIN:
            key = "u_" + key
        modname = f"quant_user_{py.stem}"
        try:
            spec = importlib.util.spec_from_file_location(modname, py)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[modname] = module
            spec.loader.exec_module(module)
        except Exception:
            sys.stderr.write(f"[strategy] 加载 {py.name} 失败\n{traceback.format_exc()}")
            continue

        fn = getattr(module, "select", None)
        cls = getattr(module, "STRATEGY", None)
        label = str(getattr(module, "NAME", py.stem))
        desc = str(getattr(module, "DESCRIPTION", ""))[:400]
        params = getattr(module, "PARAMS", {}) or {}
        if isinstance(params, dict) and not params:
            params = {}
        if cls is not None and hasattr(cls, "select"):
            try:
                inst = cls() if isinstance(cls, type) else cls
                inst.key = key
                inst.label = label or getattr(inst, "label", key)
                inst.desc = desc or getattr(inst, "desc", "")
                inst.params = params or getattr(inst, "params", {}) or {}
                found[key] = inst
            except Exception:
                sys.stderr.write(f"[strategy] 实例化 {py.name} 失败\n{traceback.format_exc()}")
        elif callable(fn):
            found[key] = FuncStrategy(key, label, desc, fn, params)
    return found


class Registry:
    """策略注册表。缓存一次，失败或新增文件时重建。"""

    def __init__(self) -> None:
        self._all: dict[str, Strategy] = {}
        self._stamp: float = 0.0
        self.reload()

    def reload(self) -> None:
        merged = dict(BUILTIN)
        merged.update(load_external())
        self._all = merged
        self._stamp = __import__("time").time()

    def refresh(self) -> None:
        """目录有新文件就重建（懒刷新，最多每 10 秒一次）。"""
        import time
        if time.time() - self._stamp < 10:
            return
        try:
            files = sorted(p.name for p in settings.strategy_dir.glob("*.py"))
            self._files = files
        except Exception:
            return
        self.reload()

    def all(self) -> dict[str, Strategy]:
        return self._all

    def get(self, key: str) -> Strategy:
        return self._all.get(key) or self._all["momentum"]

    def describe(self) -> list[dict[str, Any]]:
        return [{"key": s.key, "label": s.label, "desc": s.desc,
                 "params": s.params, "builtin": s.key in BUILTIN}
                for s in self._all.values()]


registry = Registry()
