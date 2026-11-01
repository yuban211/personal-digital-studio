"""把你的回测策略接到实盘模拟引擎：沪深主板日线突破 + ATR 移动止损。

规则来源：`services/market/app/strategy.py`（原文件不改，这里按同样口径重算指标）：

  入场  收盘价 > 前 20 日最高价
        且 MA20 > MA60、收盘 > MA60
        且 当日成交量 ≥ 前 20 日均量 × 1.2
        且 非 ST、上市超过 5 个交易日
  出场  收盘 < MA20，或收盘 < 3×ATR 移动止损线，或收盘 < 前 10 日最低
  仓位  权益 × 0.5% 风险预算 ÷ (3 × ATR) 得股数，再受单票 20% 上限与整手约束

与回测的**唯一差异**（无法回避）：
  回测是"收盘出信号、次日开盘成交"；实盘引擎按当前最新价即时成交。
  所以这里的信号会以现价成交，可能比回测价格好或差。要严格复现次日开盘价，
  得让引擎排队到次日 9:30 —— 当前引擎没有订单簿，故不做。

只在主板上选股（600/601/603/605 + 000/001/002/003），与你的策略口径一致。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

NAME = "主板突破 + ATR 止损（你的策略）"
DESCRIPTION = ("沪深主板：收盘破 20 日高、MA20>MA60、量比≥1.2 买入；"
               "跌破 MA20 / 前 10 日低 / 3×ATR 移动止损卖出。仓位按 0.5% 风险预算。")
PARAMS = {
    "risk_per_turn": 0.005,     # 单笔风险预算
    "max_position_pct": 0.20,   # 单票仓位上限
    "atr_stop_multiple": 3.0,
    "top_n": 5,
}

# ── 载入你的原始策略模块（只为复用代码判定与默认参数，不跑它的回测）──
_SRC = Path(__file__).resolve().parents[2] / "market" / "app" / "strategy.py"
_orig = None
try:
    if _SRC.exists():
        _spec = importlib.util.spec_from_file_location("user_breakout_src", _SRC)
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules["user_breakout_src"] = _mod
        _spec.loader.exec_module(_mod)
        _orig = _mod
except Exception:                      # 源文件缺失/依赖不全都不能拖垮服务
    _orig = None

DEFAULT_CFG = {
    "breakout_window": 20,
    "trend_fast_window": 20,
    "trend_slow_window": 60,
    "exit_window": 10,
    "atr_window": 14,
    "volume_window": 20,
    "volume_multiple": 1.2,
    "atr_stop_multiple": 3.0,
}
if _orig is not None:
    try:
        _c = _orig.StrategyConfig()
        DEFAULT_CFG.update({k: getattr(_c, k) for k in DEFAULT_CFG})
    except Exception:
        pass


def is_main_board(code: str) -> bool:
    if _orig is not None:
        try:
            return bool(_orig.is_main_board_symbol(code))
        except Exception:
            pass
    return code.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))


def _sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _atr(bars: list[dict[str, Any]], n: int) -> float | None:
    """与回测一致：真实波幅的 Wilder 平滑（等价 ewm alpha=1/n, min_periods=n）。"""
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        b, prev = bars[i], bars[i - 1]
        trs.append(max(b["high"] - b["low"],
                       abs(b["high"] - prev["close"]),
                       abs(b["low"] - prev["close"])))
    if len(trs) < n:
        return None
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def indicators(bars: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any] | None:
    """算出策略需要的那几个数，口径与回测代码一一对应。"""
    need = cfg["trend_slow_window"] + 2
    if len(bars) < max(need, cfg["volume_window"] + 2):
        return None
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vols = [b["vol"] for b in bars]

    fast = _sma(closes, cfg["trend_fast_window"])
    slow = _sma(closes, cfg["trend_slow_window"])
    prior_high = max(highs[-(cfg["breakout_window"] + 1):-1])
    prior_low = min(lows[-(cfg["exit_window"] + 1):-1])
    atr = _atr(bars, cfg["atr_window"])
    avg_vol = _sma(vols[-(cfg["volume_window"] + 1):-1], cfg["volume_window"])
    return {
        "close": closes[-1], "fast": fast, "slow": slow,
        "prior_high": prior_high, "prior_low": prior_low,
        "atr": atr, "avg_vol": avg_vol, "vol": vols[-1],
        "high_water": max(highs[-cfg["breakout_window"]:]),
    }


def select(ctx) -> list[dict[str, Any]]:
    cfg = dict(DEFAULT_CFG)
    for k in ("atr_stop_multiple",):
        if k in ctx.params:
            cfg[k] = float(ctx.params[k])
    top_n = int(ctx.params.get("top_n", 5))
    risk = float(ctx.params.get("risk_per_turn", PARAMS["risk_per_turn"]))
    cap = float(ctx.params.get("max_position_pct", PARAMS["max_position_pct"]))

    held = ctx.positions or {}
    signals: list[dict[str, Any]] = []

    # ── 卖出：持仓逐只按出场规则检查 ──
    for code, pos in held.items():
        bars = ctx.bars(code, 90)
        ind = indicators(bars, cfg)
        if not ind:
            continue
        price = ind["close"]
        reason = None
        if ind["fast"] and price < ind["fast"]:
            reason = "收盘跌破 MA%d" % cfg["trend_fast_window"]
        elif ind["atr"] and price < ind["high_water"] - cfg["atr_stop_multiple"] * ind["atr"]:
            reason = "触发 %g×ATR 移动止损" % cfg["atr_stop_multiple"]
        elif price < ind["prior_low"]:
            reason = "收盘跌破前 %d 日低点" % cfg["exit_window"]
        if reason:
            signals.append({"code": code, "side": "sell", "reason": reason})

    # ── 买入：突破 + 趋势 + 放量 ──
    hits = []
    for row in ctx.rows:
        code = row["code"]
        if not is_main_board(code) or "ST" in row["name"].upper() or row["price"] <= 0:
            continue
        if code in held:
            continue
        bars = ctx.bars(code, 90)
        ind = indicators(bars, cfg)
        if not ind or not ind["atr"] or ind["atr"] <= 0:
            continue
        # 复权口径校验：日线最后一根与实时价差太多，说明两者复权基准不一致
        # （除权日附近常见），这种标的直接跳过，别拿错价当突破。
        if row["price"] > 0 and abs(ind["close"] / row["price"] - 1) > 0.15:
            continue
        vol_ok = ind["avg_vol"] is None or ind["vol"] >= ind["avg_vol"] * cfg["volume_multiple"]
        entry = (
            ind["close"] > ind["prior_high"]
            and ind["fast"] is not None and ind["slow"] is not None
            and ind["fast"] > ind["slow"]
            and ind["close"] > ind["slow"]
            and vol_ok
        )
        if not entry:
            continue
        # 风险预算 → 股数 → 占净值比例（上限 20%）
        risk_shares = (ctx.equity * risk) / max(cfg["atr_stop_multiple"] * ind["atr"], 0.01)
        shares = int(risk_shares // 100 * 100)
        weight = min(cap, (shares * row["price"]) / ctx.equity) if ctx.equity else 0
        if weight <= 0.01:
            continue
        hits.append({
            "code": code, "side": "buy", "weight": round(weight, 4),
            "reason": f"破 {cfg['breakout_window']} 日高 {ind['prior_high']:.2f} · "
                      f"量比 {ind['vol'] / ind['avg_vol']:.1f}" if ind["avg_vol"] else "破 20 日高",
            "score": (ind["close"] / ind["prior_high"] - 1) if ind["prior_high"] else 0,
        })

    hits.sort(key=lambda x: -x["score"])
    for h in hits[:top_n]:
        h.pop("score", None)
        signals.append(h)
    return signals
