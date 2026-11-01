"""纸上撮合：把"信号"变成"账户变动"。

贴近 A 股真实规则的地方：
  · 买入 100 股整数倍，卖出可以清掉零股
  · T+1：当日买入次日才可卖
  · 佣金万 2.5（最低 5 元）+ 过户费 0.001% + 卖出印花税 0.05%
  · 涨跌停不成交、停牌不成交、现金不足不成交
  · 成交价 = 最新价 × (1 ± 滑点)

刻意简化的地方（写在 README 里，不假装精确）：
  · 不模拟集合竞价、不模拟档位深度、不排队，按现价即时全额成交
  · 不考虑除权除息的持仓调整（用前复权价估算市值）
"""

from __future__ import annotations

from datetime import datetime, time as dtime
from typing import Any

from .settings import settings
from .store import store


def limit_pct(code: str, name: str) -> float:
    """涨跌停幅度。ST 5%，创业板/科创板 20%，北交所 30%，其余 10%。"""
    if "ST" in name.upper():
        return 5.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("8", "4", "9")):
        return 30.0
    return 10.0


def is_session(now: datetime | None = None) -> bool:
    """是否在连续竞价时段（不含集合竞价与盘后）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:          # 周末
        return False
    t = now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))


def fees(side: str, amount: float) -> float:
    commission = max(settings.commission_min, amount * settings.commission_rate)
    transfer = amount * settings.transfer_rate
    stamp = amount * settings.stamp_rate if side == "sell" else 0.0
    return round(commission + transfer + stamp, 2)


class Broker:
    def __init__(self) -> None:
        self.store = store

    # ── 账户估值 ──
    def account(self, prices: dict[str, float]) -> dict[str, Any]:
        a = self.store.acct()
        positions = []
        mv = 0.0
        cost_total = 0.0
        for p in self.store.positions():
            price = prices.get(p["code"]) or p["cost"]
            value = price * p["qty"]
            cost_value = p["cost"] * p["qty"]
            mv += value
            cost_total += cost_value
            positions.append({
                "code": p["code"], "name": p["name"], "qty": p["qty"],
                "available": p["available"], "cost": round(p["cost"], 3),
                "price": round(price, 3),
                "value": round(value, 2),
                "pnl": round(value - cost_value, 2),
                "pnl_pct": round((value / cost_value - 1) * 100, 2) if cost_value else 0.0,
                "weight": 0.0,
            })
        total = a["cash"] + mv
        for p in positions:
            p["weight"] = round(p["value"] / total * 100, 2) if total else 0.0
        positions.sort(key=lambda x: -x["value"])
        return {
            "cash": round(a["cash"], 2),
            "initial": round(a["initial"], 2),
            "market_value": round(mv, 2),
            "equity": round(total, 2),
            "pnl": round(total - a["initial"], 2),
            "pnl_pct": round((total / a["initial"] - 1) * 100, 2) if a["initial"] else 0.0,
            "float_pnl": round(mv - cost_total, 2),
            "positions": positions,
            "position_count": len(positions),
            "created_at": a["created_at"],
        }

    # ── 下单 ──
    def order(self, code: str, side: str, qty: int, quote: dict[str, Any] | None,
              src: str = "manual", reason: str = "") -> dict[str, Any]:
        side = side.lower()
        qty = int(qty)
        name = (quote or {}).get("name", "")
        base = (quote or {}).get("price", 0.0)

        def reject(msg: str) -> dict[str, Any]:
            self.store.add_order(code, name, side, qty, base, "rejected", msg, src)
            return {"ok": False, "reason": msg, "code": code, "side": side, "qty": qty}

        if side not in ("buy", "sell"):
            return reject("方向必须是 buy/sell")
        if not quote or base <= 0:
            return reject("无有效行情（停牌或代码不存在）")
        if qty <= 0:
            return reject("数量必须为正")

        chg = float(quote.get("chg", 0.0))
        lim = limit_pct(code, name)
        # 留 0.2% 容差：涨停价成交在真实盘口也常有一两分钱空间
        if side == "buy" and chg >= lim - 0.2:
            return reject(f"涨停（{chg:.2f}%）无法买入")
        if side == "sell" and chg <= -lim + 0.2:
            return reject(f"跌停（{chg:.2f}%）无法卖出")

        pos = self.store.position(code)
        slip = settings.slippage_bps / 10000.0

        if side == "buy":
            if qty % 100:
                qty = qty // 100 * 100
                if qty <= 0:
                    return reject("A 股买入必须是 100 股整数倍")
            price = round(base * (1 + slip), 2)
            amount = price * qty
            fee = fees("buy", amount)
            if amount + fee > self.store.acct()["cash"] + 1e-6:
                return reject("可用资金不足")
            cash = self.store.acct()["cash"] - amount - fee
            new_qty = (pos["qty"] if pos else 0) + qty
            new_cost = ((pos["cost"] * pos["qty"] if pos else 0.0) + amount) / new_qty
            self.store.upsert_position(
                code, name, new_qty,
                (pos["available"] if pos else 0),   # T+1：今天买的今天不能卖
                new_cost,
            )
            self.store.set_cash(cash)
        else:
            if not pos or pos["qty"] <= 0:
                return reject("没有持仓")
            if qty > pos["available"]:
                return reject(f"可卖数量不足（T+1，可卖 {pos['available']} 股）")
            price = round(base * (1 - slip), 2)
            amount = price * qty
            fee = fees("sell", amount)
            self.store.set_cash(self.store.acct()["cash"] + amount - fee)
            left = pos["qty"] - qty
            self.store.upsert_position(code, name, left,
                                       max(0, pos["available"] - qty),
                                       pos["cost"] if left > 0 else 0.0)

        self.store.add_order(code, name, side, qty, price, "filled", reason, src)
        self.store.add_trade(code, name, side, qty, price, fee, amount, src)
        return {"ok": True, "code": code, "name": name, "side": side, "qty": qty,
                "price": price, "fee": fee, "amount": round(amount, 2)}


broker = Broker()
