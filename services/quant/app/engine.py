"""引擎：定时把"行情 → 策略 → 委托 → 账户"这条链路跑一遍。

节奏（默认，可用环境变量调）：
    QUANT_TICK=10          每 10 秒一轮
    QUANT_SNAPSHOT_TTL=20  全A快照最多 20 秒一次真实抓取
    QUANT_KLINE_TTL=900    日线 15 分钟缓存（日线一天只需要一次）

两档模式：
    session   连续竞价时段（9:30-11:30 / 13:00-15:00）—— 真正的盘中模拟
    offhours  非交易时段 —— 用最新收盘价撮合，并明确标注"非交易时段模拟"
非交易时段也允许成交，是为了让你半夜调策略时能立刻看到结果；
想严格只在盘中成交，设 QUANT_OFFHOURS=0。
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import traceback
from datetime import datetime
from typing import Any

from .broker import broker, is_session
from .feed import feed
from .settings import settings
from .store import store
from .strategy import Context, registry

OFFHOURS = os.getenv("QUANT_OFFHOURS", "1").strip() not in ("0", "false", "no")

#: 风控：默认止损 -8%，止盈 +25%（可在页面改，落在 meta 里）
DEFAULT_RISK = {"stop_loss": -8.0, "take_profit": 25.0, "max_positions": 8,
                "position_pct": 0.12, "enabled": True}


class Engine:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.state: dict[str, Any] = {
            "running": False,
            "last_tick": "",
            "last_error": "",
            "signals": [],
            "executed": [],
            "mode": "offhours",
            "strategy": self._get("strategy", "momentum"),
            "ticks": 0,
        }
        self._last_equity_ts = 0.0

    # ── 配置读写 ──
    @staticmethod
    def _get(key: str, default: str) -> str:
        return store.meta_get(key, default)

    def params(self) -> dict[str, Any]:
        out = dict(DEFAULT_RISK)
        for k in DEFAULT_RISK:
            raw = store.meta_get("risk_" + k, "")
            if raw != "":
                try:
                    out[k] = float(raw) if isinstance(DEFAULT_RISK[k], float) else int(raw)
                except ValueError:
                    pass
        out["strategy"] = self.state["strategy"]
        return out

    def set_params(self, patch: dict[str, Any]) -> dict[str, Any]:
        for k, v in patch.items():
            if k == "strategy":
                if v in registry.all():
                    self.state["strategy"] = str(v)
                    store.meta_set("strategy", str(v))
                continue
            if k in DEFAULT_RISK:
                store.meta_set("risk_" + k, str(v))
        return self.params()

    # ── 生命周期 ──
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="quant-engine", daemon=True)
        self._thread.start()
        self.state["running"] = True

    def stop(self) -> None:
        self._stop.set()
        self.state["running"] = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:                       # 一轮失败不打死线程
                self.state["last_error"] = f"{type(e).__name__}: {e}"
                traceback.print_exc()
            self._stop.wait(settings.tick_seconds)

    # ── 主流程 ──
    def tick(self, run_strategy: bool = True, src: str = "strategy") -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "reason": "上一轮还在跑"}
        try:
            return self._tick(run_strategy, src)
        finally:
            self._lock.release()

    def _tick(self, run_strategy: bool, src: str) -> dict[str, Any]:
        store.maybe_new_day()
        registry.refresh()
        rows = feed.get()
        if not rows:
            self.state["last_error"] = "快照为空（行情源不可用）"
            return {"ok": False, "reason": self.state["last_error"]}

        prices = {r["code"]: r["price"] for r in rows if r["price"] > 0}
        acct = broker.account(prices)
        self.state["last_tick"] = datetime.now().strftime("%H:%M:%S")
        self.state["ticks"] += 1

        live = is_session()
        self.state["mode"] = "session" if live else ("offhours" if OFFHOURS else "closed")

        risk = self.params()
        executed: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []

        can_trade = risk.get("enabled", 1) and (live or OFFHOURS)
        rows_by_code = {r["code"]: r for r in rows}

        # ── 1. 风控优先：止损 / 止盈 ──
        if can_trade:
            for p in acct["positions"]:
                if p["available"] <= 0:
                    continue
                if p["pnl_pct"] <= risk["stop_loss"]:
                    signals.append({"code": p["code"], "name": p["name"], "side": "sell",
                                    "reason": f"止损 {p['pnl_pct']:.1f}%"})
                elif p["pnl_pct"] >= risk["take_profit"]:
                    signals.append({"code": p["code"], "name": p["name"], "side": "sell",
                                    "reason": f"止盈 {p['pnl_pct']:.1f}%"})

        # ── 2. 策略信号 ──
        if run_strategy and can_trade:
            strat = registry.get(self.state["strategy"])
            candidates = self._candidates(rows)
            self._ensure_warm(candidates)
            ctx = Context(
                rows=candidates,
                positions={p["code"]: p for p in acct["positions"]},
                equity=acct["equity"],
                params=strat.params,
                today=datetime.now().strftime("%Y-%m-%d"),
                bars_fn=lambda code, n: feed.kline(code, self._market(code, rows_by_code), n),
                log_fn=lambda m: None,
                market_of=lambda c: self._market(c, rows_by_code),
            )
            try:
                raw = strat.select(ctx) or []
            except Exception as e:
                self.state["last_error"] = f"策略 {strat.key} 异常: {e}"
                raw = []
            for s in raw:
                if not isinstance(s, dict) or not s.get("code"):
                    continue
                row = rows_by_code.get(str(s["code"]))
                signals.append({
                    "code": str(s["code"]),
                    "name": (row or {}).get("name", ""),
                    "side": str(s.get("side", "buy")),
                    "weight": s.get("weight"),
                    "qty": s.get("qty"),
                    "reason": str(s.get("reason", ""))[:80],
                })

        # ── 3. 去重 + 执行 ──
        seen: set[tuple[str, str]] = set()
        deduped = []
        for s in signals:
            k = (s["code"], s["side"])
            if k in seen:
                continue
            seen.add(k)
            deduped.append(s)
        signals = deduped[:30]

        if can_trade:
            held = {p["code"]: p for p in acct["positions"]}
            # 先卖后买，释放资金
            for s in [x for x in signals if x["side"] == "sell"]:
                pos = held.get(s["code"])
                if not pos or pos["available"] <= 0:
                    continue
                res = broker.order(s["code"], "sell", pos["available"],
                                   rows_by_code.get(s["code"]), src=src, reason=s["reason"])
                if res.get("ok"):
                    s["price"] = res["price"]
                    executed.append(res)
                else:
                    s["rejected"] = res.get("reason")

            equity_after = broker.account(prices)["equity"]
            cash = store.acct()["cash"]
            held_count = len(store.positions())
            max_pos = int(risk["max_positions"])
            for s in [x for x in signals if x["side"] == "buy"]:
                if s["code"] in {p["code"] for p in store.positions()}:
                    continue
                if held_count >= max_pos:
                    s["rejected"] = f"持仓已达上限 {max_pos}"
                    continue
                row = rows_by_code.get(s["code"])
                if not row or row["price"] <= 0:
                    s["rejected"] = "无行情"
                    continue
                budget = equity_after * float(risk["position_pct"])
                if s.get("weight"):
                    budget = equity_after * float(s["weight"])
                budget = min(budget, cash * 0.98)
                qty = s.get("qty") or int(budget / row["price"] // 100 * 100)
                if qty < 100:
                    s["rejected"] = "资金不足一手"
                    continue
                res = broker.order(s["code"], "buy", qty, row, src=src, reason=s["reason"])
                if res.get("ok"):
                    s["price"] = res["price"]
                    cash -= res["amount"] + res["fee"]
                    held_count += 1
                    executed.append(res)
                else:
                    s["rejected"] = res.get("reason")

        self.state["signals"] = signals
        self.state["executed"] = executed

        # ── 4. 记净值（每分钟一条，避免曲线被高频刷成锯齿）──
        acct = broker.account(prices)
        now = time.time()
        if now - self._last_equity_ts > 60 or not store.equity_curve(1):
            store.put_equity(acct["equity"], acct["cash"], acct["market_value"], acct["pnl"])
            self._last_equity_ts = now

        return {"ok": True, "mode": self.state["mode"], "strategy": self.state["strategy"],
                "signals": len(signals), "executed": len(executed), "equity": acct["equity"]}

    # ── 辅助 ──
    @staticmethod
    def _market(code: str, rows_by_code: dict[str, dict[str, Any]]) -> int:
        row = rows_by_code.get(code)
        return int(row["market"]) if row else (1 if code.startswith("6") else 0)

    @staticmethod
    def _candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按成交额预筛，避免对 5900 只逐个拉日线把上游打爆。"""
        out = []
        for r in rows[: settings.prefilter_top]:
            if r["price"] <= 0 or "ST" in r["name"].upper():
                continue
            out.append(r)
        return out

    def _ensure_warm(self, candidates: list[dict[str, Any]]) -> None:
        """后台预热日线，不阻塞本轮。

        串行拉 300 只日线要 50 秒以上，一轮就废了；这里用后台线程并发拉，
        本轮策略先用已有缓存算，缺数据的标的自然跳过，下一轮就补齐。
        日线结果落 SQLite，同一天重启也不用重拉。
        """
        th = getattr(self, "_warm_thread", None)
        if th and th.is_alive():
            return
        if not candidates:
            return
        self.state["warm"] = {"total": len(candidates), "done": 0, "running": True}

        def run() -> None:
            done = 0
            lock = threading.Lock()

            def one(row: dict[str, Any]) -> None:
                nonlocal done
                try:
                    feed.kline(row["code"], int(row["market"]), 120)
                except Exception:
                    pass
                with lock:
                    done += 1
                    if done % 10 == 0 or done == len(candidates):
                        self.state["warm"] = {"total": len(candidates), "done": done, "running": True}

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(one, candidates))
            self.state["warm"] = {"total": len(candidates), "done": done, "running": False}

        self._warm_thread = threading.Thread(target=run, name="quant-warm", daemon=True)
        self._warm_thread.start()


engine = Engine()
