"""历史数据湖查询：直接读 CNEquity 落地的 Parquet。

为什么不经过 cnequity 的 Python API：
  DuckDB 能直接对 Parquet 做列裁剪与谓词下推，零拷贝、零网络延迟，
  比"加载成 pandas 再筛"快一个量级。cnequity 仍作为可选的旁路
  （传了 CNE_CONFIG 且装了 cnequity 时优先用它取带复权/PIT 语义的版本）。

列名自适应：不同 CNEquity 版本的数据集列名不完全一致，这里按候选名探测，
探测不到就明确报错，不猜。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("market.lake")

SYMBOL_COLS = ("symbol", "code", "ts_code", "sec_code", "security_code", "stock_code")
DATE_COLS = ("date", "datetime", "trade_date", "trading_date", "dt", "bar_date")
OHLC = {
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c"),
    "volume": ("volume", "vol", "v"),
    "amount": ("amount", "turnover"),
    "pre_close": ("pre_close", "prev_close"),
    "adj_factor": ("adj_factor", "adjust_factor"),
}


class LakeError(RuntimeError):
    """数据湖不可用或数据集缺失。"""


def _duckdb():
    import duckdb
    return duckdb


def list_datasets(root: Path) -> list[dict[str, Any]]:
    """列出数据湖里的数据集目录（每个目录 = 一个数据集）。"""
    if not root.exists():
        return []
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        n = len(list(d.rglob("*.parquet")))
        if n:
            out.append({"name": d.name, "parquet_files": n})
    return out


def _pick(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def query_history(
    root: Path,
    dataset: str,
    symbols: list[str],
    start: str | None = None,
    end: str | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    """从数据湖查历史 K 线。返回 {dataset, columns, rows, truncated}。"""
    if not root.exists():
        raise LakeError(f"数据湖目录不存在：{root}")

    ds_dir = None
    for cand in root.iterdir():
        if cand.is_dir() and cand.name.lower() == dataset.lower():
            ds_dir = cand
            break
    if ds_dir is None:
        available = [d["name"] for d in list_datasets(root)]
        raise LakeError(f"数据集 {dataset!r} 不存在。可用：{available[:20]}")

    files = list(ds_dir.rglob("*.parquet"))
    if not files:
        raise LakeError(f"数据集 {dataset!r} 下没有 parquet 文件")

    glob = (ds_dir / "**" / "*.parquet").as_posix()
    con = _duckdb().connect(database=":memory:")
    try:
        desc = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{glob}', union_by_name=true)").fetchall()
        columns = [str(r[0]) for r in desc]
        sym_col = _pick(columns, SYMBOL_COLS)
        date_col = _pick(columns, DATE_COLS)
        if not sym_col:
            raise LakeError(f"数据集 {dataset!r} 缺少标的列（探测过 {SYMBOL_COLS}），实际列：{columns[:12]}")

        where = [f"{_quote_ident(sym_col)} IN ({', '.join(_sql_str(s) for s in symbols)})"]
        if date_col:
            if start:
                where.append(f"CAST({_quote_ident(date_col)} AS VARCHAR) >= {_sql_str(start)}")
            if end:
                where.append(f"CAST({_quote_ident(date_col)} AS VARCHAR) <= {_sql_str(end)}")
        order = f" ORDER BY {_quote_ident(date_col)}" if date_col else ""

        sql = (f"SELECT * FROM read_parquet('{glob}', union_by_name=true) "
               f"WHERE {' AND '.join(where)}{order} LIMIT {int(limit)}")
        rows = con.execute(sql).fetchdf().to_dict(orient="records")
        clean = [{k: (None if isinstance(v, float) and v != v else
                      (v.item() if hasattr(v, "item") else v))
                  for k, v in r.items()} for r in rows]
        return {"dataset": dataset, "columns": columns, "rows": clean,
                "truncated": len(clean) >= limit}
    finally:
        con.close()


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def normalize_symbol(symbol: str) -> list[str]:
    """'600519' → ['600519.SH','600519.SS','SH600519']，命中哪个用哪个。"""
    s = symbol.strip().upper()
    if not s:
        return []
    if "." in s:
        base, _, suf = s.partition(".")
        sfx = {"SH": "SH", "SS": "SH", "SZ": "SZ", "BJ": "BJ"}.get(suf, suf)
        return [f"{base}.{sfx}", f"{base}.SH" if sfx == "SH" else f"{base}.{sfx}", f"{sfx}{base}"]
    if s.isdigit() and len(s) == 6:
        m = {"6": "SH", "5": "SH", "0": "SZ", "3": "SZ", "8": "BJ", "4": "BJ"}.get(s[0], "SH")
        return [f"{s}.{m}", f"{s}.SH" if m == "SH" else f"{s}.{m}", f"{m}{s}"]
    return [s]
