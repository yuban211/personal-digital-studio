"""示例策略：14 日 RSI 超卖反弹。

这个文件同时是"怎么写自己的策略"的模板：
    NAME / DESCRIPTION / PARAMS 三个常量给页面用
    select(ctx) 返回买卖清单

复制成 strategies/我的策略.py，改逻辑即可，不用重启服务（10 秒内自动加载）。
"""

NAME = "RSI 超卖反弹（示例）"
DESCRIPTION = "在成交额前 300 名里找 14 日 RSI 低于 32 且当日收红的标的，等权买入；RSI 回到 65 以上卖出。"
PARAMS = {"top_n": 3, "rsi_buy": 32, "rsi_sell": 65}


def rsi(closes, n=14):
    if len(closes) <= n:
        return 50.0
    gain = loss = 0.0
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gain += d
        else:
            loss -= d
    if loss == 0:
        return 100.0
    rs = (gain / n) / (loss / n)
    return 100 - 100 / (1 + rs)


def select(ctx):
    top_n = int(ctx.params.get("top_n", 3))
    buy_th = float(ctx.params.get("rsi_buy", 32))
    sell_th = float(ctx.params.get("rsi_sell", 65))

    picks = []
    for row in ctx.rows:
        if row["amount"] < 2e8 or "ST" in row["name"].upper():
            continue
        bars = ctx.bars(row["code"], 40)
        if len(bars) < 20:
            continue
        closes = [b["close"] for b in bars]
        value = rsi(closes)
        if value <= buy_th and row["chg"] > 0:
            picks.append({"code": row["code"], "side": "buy",
                          "reason": f"RSI {value:.0f} 超卖回升"})
    picks.sort(key=lambda p: p["reason"])
    buys = picks[:top_n]

    sells = []
    for code in list(ctx.positions):
        bars = ctx.bars(code, 40)
        if len(bars) < 20:
            continue
        value = rsi([b["close"] for b in bars])
        if value >= sell_th:
            sells.append({"code": code, "side": "sell", "reason": f"RSI {value:.0f} 偏高"})
    return sells + buys
