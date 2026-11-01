"""运行 A 股主板日线策略回测。

CSV 示例：
  python services/market/scripts/backtest_strategy.py --csv 600519.csv --symbol 600519.SH

数据湖示例（在 services/market 目录执行）：
  python scripts/backtest_strategy.py --from-lake --symbol 600519.SH
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

import pandas as pd

MARKET_ROOT = Path(__file__).resolve().parents[1]
if str(MARKET_ROOT) not in sys.path:
    sys.path.insert(0, str(MARKET_ROOT))

from app.lake import normalize_symbol, query_history  # noqa: E402
from app.strategy import StrategyConfig, normalize_bars, run_backtest  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A 股主板日线趋势突破策略（仅回测）")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", type=Path, help="包含日线 OHLC 的 CSV 文件")
    source.add_argument("--from-lake", action="store_true", help="从本机 CNEquity Parquet 数据湖读取")
    parser.add_argument("--symbol", required=True, help="沪深主板代码，例如 600519.SH 或 000858.SZ")
    parser.add_argument("--dataset", default="daily_bars", help="数据湖数据集名称，默认 daily_bars")
    parser.add_argument("--lake-root", type=Path,
                        default=Path(os.getenv("CNE_LAKE_ROOT", "D:/cnequity")),
                        help="数据湖目录；默认读取 CNE_LAKE_ROOT 或 D:/cnequity")
    parser.add_argument("--start", help="起始日 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日 YYYY-MM-DD")
    parser.add_argument("--cash", type=float, default=100_000.0, help="初始资金，默认 100000 元")
    parser.add_argument("--commission", type=float, default=0.00025, help="佣金比例，默认 0.025%%")
    parser.add_argument("--min-commission", type=float, default=5.0, help="单边最低佣金，默认 5 元")
    parser.add_argument("--stamp-duty", type=float, default=0.0005, help="卖出印花税比例，默认 0.05%%")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="单边滑点基点，默认 5")
    parser.add_argument("--risk", type=float, default=0.005, help="每笔 ATR 风险预算比例，默认 0.5%%")
    parser.add_argument("--max-position", type=float, default=0.20,
                        help="单只股票最大仓位比例，默认 20%%")
    parser.add_argument("--output", type=Path, default=Path("backtest_output"),
                        help="结果目录，默认 ./backtest_output")
    return parser


def _load_data(args: argparse.Namespace) -> pd.DataFrame:
    if args.csv:
        frame = normalize_bars(pd.read_csv(args.csv, encoding="utf-8-sig"))
        if args.start:
            frame = frame[frame["date"] >= pd.Timestamp(args.start)]
        if args.end:
            frame = frame[frame["date"] <= pd.Timestamp(args.end)]
        return frame

    result = query_history(
        args.lake_root,
        args.dataset,
        normalize_symbol(args.symbol),
        args.start,
        args.end,
        limit=20_000,
    )
    if not result["rows"]:
        raise ValueError(f"数据湖中没有 {args.symbol} 的历史数据")
    return pd.DataFrame(result["rows"])


def main() -> int:
    args = _parser().parse_args()
    config = StrategyConfig(
        initial_cash=args.cash,
        commission_rate=args.commission,
        min_commission=args.min_commission,
        stamp_duty_rate=args.stamp_duty,
        slippage_bps=args.slippage_bps,
        risk_per_trade=args.risk,
        max_position_pct=args.max_position,
    )
    try:
        bars = _load_data(args)
        result = run_backtest(bars, args.symbol, config)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"回测未完成：{exc}", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    safe_symbol = args.symbol.upper().replace(".", "_")
    metrics_path = args.output / f"{safe_symbol}_metrics.json"
    curve_path = args.output / f"{safe_symbol}_equity.csv"
    trades_path = args.output / f"{safe_symbol}_trades.csv"
    metrics_path.write_text(
        json.dumps(result.metrics, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    result.equity_curve.to_csv(curve_path, index=False, encoding="utf-8-sig")
    result.trades.to_csv(trades_path, index=False, encoding="utf-8-sig")

    metrics = result.metrics
    print(f"标的：{result.symbol}  |  区间：{metrics['data_start']} 至 {metrics['data_end']}")
    print(f"总收益：{metrics['total_return']:.2%}  |  最大回撤：{metrics['max_drawdown']:.2%}")
    print(f"年化收益：{_percent(metrics['cagr'])}  |  Sharpe：{metrics['sharpe_daily_252']}")
    print(f"已平仓交易：{metrics['closed_trades']}  |  胜率：{_percent(metrics['win_rate'])}")
    print(f"结果目录：{args.output.resolve()}")
    return 0


def _percent(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2%}"


if __name__ == "__main__":
    raise SystemExit(main())
