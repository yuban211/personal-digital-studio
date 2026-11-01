"""A 股主板日线突破策略与保守的单标的回测器。

策略规则：
  - 收盘价突破前 N 日高点，且短均线在长均线上方时，下一交易日开盘买入；
  - 收盘跌破短均线或 ATR 移动止损线时，下一交易日开盘卖出；
  - 用 ATR 风险预算决定股数，并受单票仓位上限、现金和 100 股买入单位约束。

信号只使用当日收盘后已知的数据，成交最早发生在下一交易日开盘，避免前视偏差。
这是研究/回测代码，不会连接券商或发送真实委托。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Any

import pandas as pd


ST_LIMIT_CHANGE_DATE = date(2026, 7, 6)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class StrategyConfig:
    breakout_window: int = 20
    trend_fast_window: int = 20
    trend_slow_window: int = 60
    exit_window: int = 10
    atr_window: int = 14
    volume_window: int = 20
    volume_multiple: float = 1.2
    atr_stop_multiple: float = 3.0
    risk_per_trade: float = 0.005
    max_position_pct: float = 0.20
    lot_size: int = 100
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    slippage_bps: float = 5.0
    normal_limit_pct: float = 0.10
    initial_cash: float = 100_000.0

    def __post_init__(self) -> None:
        for name in ("breakout_window", "trend_fast_window", "trend_slow_window",
                     "exit_window", "atr_window", "volume_window", "lot_size"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} 必须大于 0")
        if self.trend_fast_window >= self.trend_slow_window:
            raise ValueError("trend_fast_window 必须小于 trend_slow_window")
        for name in ("risk_per_trade", "max_position_pct", "volume_multiple",
                     "atr_stop_multiple", "commission_rate", "min_commission",
                     "stamp_duty_rate", "slippage_bps", "normal_limit_pct", "initial_cash"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} 必须是有限的非负数")
        if not 0 < self.risk_per_trade <= 0.05:
            raise ValueError("risk_per_trade 应在 (0, 5%] 范围内")
        if not 0 < self.max_position_pct <= 1:
            raise ValueError("max_position_pct 应在 (0, 100%] 范围内")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash 必须大于 0")


@dataclass
class BacktestResult:
    symbol: str
    metrics: dict[str, Any]
    equity_curve: pd.DataFrame
    trades: pd.DataFrame


def is_main_board_symbol(symbol: str) -> bool:
    """只接受沪深主板代码；排除科创板、创业板、北交所和 B 股。"""
    raw = str(symbol or "").strip().upper()
    if "." in raw:
        code, suffix = raw.split(".", 1)
        exchange = {"SS": "SH", "SH": "SH", "SZ": "SZ"}.get(suffix)
    elif raw.startswith(("SH", "SZ")) and len(raw) == 8:
        exchange, code = raw[:2], raw[2:]
    else:
        code = raw
        exchange = "SH" if code.startswith(("600", "601", "603", "605")) else (
            "SZ" if code.startswith(("000", "001", "002", "003")) else None)
    if not code.isdigit() or len(code) != 6:
        return False
    if exchange == "SH":
        return code.startswith(("600", "601", "603", "605"))
    if exchange == "SZ":
        return code.startswith(("000", "001", "002", "003"))
    return False


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """规范 CSV/数据湖日线列名，保留主板交易回测所需字段。"""
    if frame.empty:
        raise ValueError("行情数据为空")
    aliases = {
        "date": ("date", "datetime", "trade_date", "trading_date", "dt", "日期"),
        "open": ("open", "o", "开盘"),
        "high": ("high", "h", "最高"),
        "low": ("low", "l", "最低"),
        "close": ("close", "c", "收盘"),
        "volume": ("volume", "vol", "v", "成交量"),
        "pre_close": ("pre_close", "prev_close", "昨收", "前收盘"),
        "is_st": ("is_st", "st", "risk_warning", "风险警示"),
        "listing_days": ("listing_days", "list_days", "上市交易日"),
    }
    lookup = {str(column).strip().lower(): column for column in frame.columns}
    rename: dict[Any, str] = {}
    for target, choices in aliases.items():
        if target in lookup:
            continue
        for choice in choices:
            if choice.lower() in lookup:
                rename[lookup[choice.lower()]] = target
                break
    data = frame.rename(columns=rename).copy()
    required = ("date", "open", "high", "low", "close")
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise ValueError(f"行情文件缺少必需列：{', '.join(missing)}")

    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "pre_close", "volume", "listing_days"):
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    if "is_st" in data.columns:
        data["is_st"] = data["is_st"].map(_to_bool)
    else:
        data["is_st"] = False

    data = data.dropna(subset=["date", "open", "high", "low", "close"])
    data = data.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    positive = (data[["open", "high", "low", "close"]] > 0).all(axis=1)
    coherent = (
        (data["low"] <= data[["open", "close"]].min(axis=1))
        & (data["high"] >= data[["open", "close"]].max(axis=1))
        & (data["high"] >= data["low"])
    )
    if not (positive & coherent).all():
        bad = int((~(positive & coherent)).sum())
        raise ValueError(f"发现 {bad} 行无效 OHLC 数据；请确认价格列为同一复权口径")
    if "volume" in data.columns:
        data["volume"] = data["volume"].fillna(0).clip(lower=0)
    if "pre_close" in data.columns:
        data.loc[data["pre_close"] <= 0, "pre_close"] = float("nan")
        data["pre_close"] = data["pre_close"].fillna(data["close"].shift(1))
    else:
        data["pre_close"] = data["close"].shift(1)
    return data


def run_backtest(
    bars: pd.DataFrame,
    symbol: str,
    config: StrategyConfig | None = None,
) -> BacktestResult:
    """对单只沪深主板股票运行日线回测。"""
    if not is_main_board_symbol(symbol):
        raise ValueError(f"{symbol!r} 不是沪深主板 A 股代码")
    cfg = config or StrategyConfig()
    data = normalize_bars(bars)
    if len(data) < cfg.trend_slow_window + 2:
        raise ValueError(f"至少需要 {cfg.trend_slow_window + 2} 个交易日的日线数据")

    high = data["high"]
    low = data["low"]
    close = data["close"]
    prev_close = data["pre_close"]
    data["sma_fast"] = close.rolling(cfg.trend_fast_window).mean()
    data["sma_slow"] = close.rolling(cfg.trend_slow_window).mean()
    data["prior_breakout_high"] = high.rolling(cfg.breakout_window).max().shift(1)
    data["prior_exit_low"] = low.rolling(cfg.exit_window).min().shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    data["atr"] = true_range.ewm(
        alpha=1 / cfg.atr_window, adjust=False, min_periods=cfg.atr_window
    ).mean()
    if "volume" in data.columns:
        data["prior_avg_volume"] = data["volume"].rolling(cfg.volume_window).mean().shift(1)
    else:
        data["prior_avg_volume"] = float("nan")

    cash = cfg.initial_cash
    shares = 0
    position: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    has_volume = "volume" in data.columns

    for index, row in data.iterrows():
        day = row["date"]
        active = not has_volume or float(row["volume"]) > 0

        # 前一收盘形成的订单只在本日开盘尝试成交。卖出被跌停/停牌挡住时继续排队；
        # 买入遇到封涨停时取消，不在后续交易日追单。
        if pending is not None and index > 0 and active:
            side = pending["side"]
            locked = _is_locked_limit(row, day, side, cfg)
            if side == "buy":
                if shares == 0 and not bool(row["is_st"]) and not locked:
                    fill = _slipped_price(float(row["open"]), buy=True, bps=cfg.slippage_bps)
                    atr = float(pending["atr"])
                    equity_at_open = cash + shares * float(row["open"])
                    risk_shares = _floor_lot(
                        equity_at_open * cfg.risk_per_trade
                        / max(cfg.atr_stop_multiple * atr, 0.01), cfg.lot_size)
                    cap_shares = _floor_lot(
                        equity_at_open * cfg.max_position_pct / fill, cfg.lot_size)
                    cash_shares = _floor_lot(
                        cash / (fill * (1 + cfg.commission_rate)), cfg.lot_size)
                    quantity = min(risk_shares, cap_shares, cash_shares)
                    while quantity >= cfg.lot_size:
                        notional = quantity * fill
                        fee = _commission(notional, cfg)
                        if notional + fee <= cash + 1e-8:
                            break
                        quantity -= cfg.lot_size
                    if quantity >= cfg.lot_size:
                        notional = quantity * fill
                        entry_fee = _commission(notional, cfg)
                        cash -= notional + entry_fee
                        shares = quantity
                        position = {
                            "entry_date": day,
                            "entry_price": fill,
                            "shares": quantity,
                            "entry_cost": notional + entry_fee,
                            "entry_fee": entry_fee,
                            "signal_date": pending["signal_date"],
                            "entry_index": int(index),
                            "high_water": fill,
                        }
                pending = None
            elif side == "sell" and shares > 0:
                if not locked:
                    fill = _slipped_price(float(row["open"]), buy=False, bps=cfg.slippage_bps)
                    notional = shares * fill
                    fee = _commission(notional, cfg)
                    stamp = notional * cfg.stamp_duty_rate
                    proceeds = notional - fee - stamp
                    cash += proceeds
                    assert position is not None
                    pnl = proceeds - float(position["entry_cost"])
                    trades.append({
                        "entry_date": _date_text(position["entry_date"]),
                        "exit_date": _date_text(day),
                        "signal_date": _date_text(position["signal_date"]),
                        "shares": shares,
                        "entry_price": round(float(position["entry_price"]), 4),
                        "exit_price": round(fill, 4),
                        "entry_fee": round(float(position["entry_fee"]), 2),
                        "exit_fee_and_tax": round(fee + stamp, 2),
                        "pnl_after_costs": round(pnl, 2),
                        "return_pct": round(pnl / float(position["entry_cost"]), 6),
                        "holding_days": int(index - position["entry_index"]),
                        "exit_reason": pending["reason"],
                    })
                    shares = 0
                    position = None
                    pending = None

        # 日线停牌时，当日买单视为过期；卖出意图保留到恢复交易后再执行。
        if pending is not None and pending["side"] == "buy" and not active:
            pending = None

        # 收盘估值；信号在本日收盘后产生，不能回填到本日成交。
        equity = cash + shares * float(row["close"])
        curve.append({
            "date": _date_text(day),
            "cash": round(cash, 2),
            "shares": shares,
            "close": float(row["close"]),
            "equity": round(equity, 2),
        })

        if not active:
            continue
        atr = row["atr"]
        if shares > 0:
            high_water = max(float(position["high_water"]), float(row["high"]))
            position["high_water"] = high_water
            stop_line = high_water - cfg.atr_stop_multiple * atr if pd.notna(atr) else float("nan")
            exit_reason = None
            if pd.notna(row["sma_fast"]) and float(row["close"]) < float(row["sma_fast"]):
                exit_reason = "收盘跌破短期均线"
            elif pd.notna(stop_line) and float(row["close"]) < float(stop_line):
                exit_reason = "收盘触发 ATR 移动止损"
            elif pd.notna(row["prior_exit_low"]) and float(row["close"]) < float(row["prior_exit_low"]):
                exit_reason = "收盘跌破前低"
            if exit_reason and (pending is None or pending["side"] != "sell"):
                pending = {"side": "sell", "reason": exit_reason, "signal_date": day}
        elif pending is None and pd.notna(atr) and pd.notna(row["prior_breakout_high"]):
            volume_ok = True
            if has_volume and pd.notna(row["prior_avg_volume"]):
                volume_ok = float(row["volume"]) >= float(row["prior_avg_volume"]) * cfg.volume_multiple
            entry_signal = (
                float(row["close"]) > float(row["prior_breakout_high"])
                and pd.notna(row["sma_fast"])
                and pd.notna(row["sma_slow"])
                and float(row["sma_fast"]) > float(row["sma_slow"])
                and float(row["close"]) > float(row["sma_slow"])
                and volume_ok
                and not bool(row["is_st"])
                and (pd.isna(row.get("listing_days")) or float(row["listing_days"]) > 5)
            )
            if entry_signal:
                pending = {"side": "buy", "atr": float(atr), "signal_date": day}

    equity_curve = pd.DataFrame(curve)
    trades_frame = pd.DataFrame(trades)
    metrics = _metrics(equity_curve, trades_frame, cfg.initial_cash, shares)
    metrics["config"] = asdict(cfg)
    metrics["data_start"] = _date_text(data.iloc[0]["date"])
    metrics["data_end"] = _date_text(data.iloc[-1]["date"])
    metrics["bars"] = int(len(data))
    metrics["open_shares_at_end"] = int(shares)
    return BacktestResult(symbol=str(symbol).upper(), metrics=metrics,
                          equity_curve=equity_curve, trades=trades_frame)


def _metrics(curve: pd.DataFrame, trades: pd.DataFrame,
             initial_cash: float, open_shares: int) -> dict[str, Any]:
    equity = curve["equity"].astype(float)
    daily_returns = equity.pct_change().replace([float("inf"), -float("inf")], float("nan")).dropna()
    final_equity = float(equity.iloc[-1]) if len(equity) else initial_cash
    elapsed_days = max(1, (pd.to_datetime(curve["date"].iloc[-1])
                           - pd.to_datetime(curve["date"].iloc[0])).days) if len(curve) > 1 else 0
    years = elapsed_days / 365.25
    cagr = (final_equity / initial_cash) ** (1 / years) - 1 if years > 0 and final_equity > 0 else None
    drawdown = equity / equity.cummax() - 1 if len(equity) else pd.Series(dtype=float)
    volatility = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
    sharpe = (float(daily_returns.mean()) / volatility * math.sqrt(252)
              if volatility > 0 else None)
    if not trades.empty:
        pnl = trades["pnl_after_costs"].astype(float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        profit_factor = float(wins.sum() / abs(losses.sum())) if len(losses) else (None if not len(wins) else "inf")
        win_rate = float((pnl > 0).mean())
        trade_count = int(len(trades))
    else:
        profit_factor = None
        win_rate = None
        trade_count = 0
    return {
        "initial_cash": round(initial_cash, 2),
        "final_equity": round(final_equity, 2),
        "total_return": round(final_equity / initial_cash - 1, 6),
        "cagr": round(cagr, 6) if cagr is not None else None,
        "max_drawdown": round(float(drawdown.min()), 6) if len(drawdown) else 0.0,
        "sharpe_daily_252": round(sharpe, 4) if sharpe is not None else None,
        "closed_trades": trade_count,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "profit_factor": profit_factor,
        "open_shares_at_end": int(open_shares),
    }


def _commission(notional: float, cfg: StrategyConfig) -> float:
    if notional <= 0:
        return 0.0
    return max(cfg.min_commission, notional * cfg.commission_rate)


def _floor_lot(shares: float, lot_size: int) -> int:
    if not math.isfinite(shares) or shares <= 0:
        return 0
    return int(shares // lot_size) * lot_size


def _slipped_price(open_price: float, buy: bool, bps: float) -> float:
    raw = open_price * (1 + (1 if buy else -1) * bps / 10_000)
    return float(Decimal(str(raw)).quantize(CENT, rounding=ROUND_HALF_UP))


def _round_cent(value: float) -> float:
    return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def _is_locked_limit(row: pd.Series, day: Any, side: str, cfg: StrategyConfig) -> bool:
    if "listing_days" in row.index and pd.notna(row["listing_days"]) and float(row["listing_days"]) <= 5:
        return False
    trade_day = pd.Timestamp(day).date()
    is_st = bool(row["is_st"])
    limit_pct = (0.05 if is_st and trade_day < ST_LIMIT_CHANGE_DATE else cfg.normal_limit_pct)
    previous_close = row["pre_close"]
    if pd.isna(previous_close) or float(previous_close) <= 0:
        return False
    limit_price = _round_cent(float(previous_close) * (1 + limit_pct if side == "buy" else 1 - limit_pct))
    prices = [float(row[name]) for name in ("open", "high", "low", "close")]
    return all(abs(price - limit_price) < 0.0051 for price in prices)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "st", "*st", "风险警示"}


def _date_text(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()
