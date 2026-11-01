"""标的代码解析。

单独成模块的原因：采集器（collector）和自选池（watchlist）都要用它，
放在 collector 里会形成 watchlist → collector → watchlist 的循环导入。
"""

from __future__ import annotations

#: 市场代码前缀 → 通达信 Market 枚举值（SZ=0, SH=1, BJ=2）
PREFIX_MARKET = {
    "6": 1,   # 上海
    "5": 1,   # 沪基金
    "0": 0,   # 深圳
    "3": 0,   # 深基金
    "2": 0,   # 深B
    "8": 2,   # 北交所
    "4": 2,   # 新三板
}

MARKET_NAME = {0: "SZ", 1: "SH", 2: "BJ"}


def parse_symbol(symbol: str) -> tuple[int, str] | None:
    """'600519.SH' / 'SH600519' / '600519' → (market, code)。无法识别返回 None。"""
    s = str(symbol).strip().upper()
    if not s:
        return None
    if "." in s:
        code, _, suffix = s.partition(".")
        suffix = suffix.strip()
        market = {"SH": 1, "SS": 1, "SZ": 0, "BJ": 2}.get(suffix)
        if market is None or not code:
            return None
        return market, code
    if s.startswith(("SH", "SZ", "BJ")) and len(s) > 2:
        market = {"SH": 1, "SZ": 0, "BJ": 2}[s[:2]]
        return market, s[2:]
    if s.isdigit() and len(s) == 6:
        market = PREFIX_MARKET.get(s[0])
        return None if market is None else (market, s)
    return None


def canonical(symbol: str) -> str | None:
    """规范成 'CODE.MKT'，无法识别返回 None。"""
    parsed = parse_symbol(symbol)
    if not parsed:
        return None
    market, code = parsed
    return f"{code}.{MARKET_NAME[market]}"
