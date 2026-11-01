# A 股主板日线策略

文件：`app/strategy.py`；命令行回测：`scripts/backtest_strategy.py`。

这是单只沪深主板股票的多头研究策略，不会连接券商，也不会发出真实委托。

## 策略规则

1. **入场**：收盘价突破前 20 个交易日最高价；20 日均线高于 60 日均线；当日成交量至少为前 20 日均量的 1.2 倍。ST 标记为真的交易日不产生买入信号。
2. **出场**：收盘跌破 20 日均线，或跌破持仓以来最高价减去 3 倍 ATR 的移动止损线；订单在下一交易日开盘执行。
3. **仓位**：每笔按账户权益的 0.5% 风险预算和 3 倍 ATR 估算股数，再受单票 20% 仓位上限、可用现金及 100 股买入单位限制。
4. **撮合**：信号在收盘后生成，最早下一交易日开盘成交；停牌时不成交；买入遇到全天封涨停时取消，卖出遇到全天封跌停时继续等待。这样不会用当日收盘信号回填当日成交，也自然遵守股票 T+1。
5. **成本**：默认单边佣金 0.025%、最低 5 元、卖出印花税 0.05%、单边滑点 5 个基点。佣金和滑点可按实际账户修改；默认佣金不是券商报价。

策略适用范围由证券代码判断：沪市 `600/601/603/605`、深市 `000/001/002/003`。科创板、创业板、北交所和 B 股会被拒绝。

## 运行

CSV 回测：

```powershell
python services/market/scripts/backtest_strategy.py --csv .\600519.csv --symbol 600519.SH --start 2018-01-01 --end 2026-10-31
```

读取已有 CNEquity 数据湖（在 `services/market` 目录执行）：

```powershell
python scripts/backtest_strategy.py --from-lake --symbol 600519.SH --dataset daily_bars --start 2018-01-01 --end 2026-10-31
```

可选参数包括 `--cash`、`--risk`、`--max-position`、`--commission`、`--min-commission`、`--stamp-duty`、`--slippage-bps`、`--lake-root` 和 `--output`。默认结果写入当前目录的 `backtest_output/`，包含指标 JSON、每日权益曲线 CSV 和已平仓交易 CSV。

CSV 至少需要 `date,open,high,low,close`；建议再提供 `volume,pre_close,is_st,listing_days`。也识别常见别名（例如 `trade_date,vol,prev_close` 和常见中文 OHLC 列名）。`is_st` 支持布尔值、0/1、`ST`、`*ST`、`是/否`。如果缺少 `is_st`，程序无法知道某天是否处于风险警示状态；回测结果应使用包含该字段的历史数据复核。

## A 股规则与数据边界

- 主板买入数量按 100 股整数倍，卖出数量按持仓全部卖出；当前交易规则见[上交所 2026 年交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml)和[深交所 2026 年交易规则](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)。
- 普通主板股票日涨跌幅按 10% 模拟。主板风险警示股票在 2026-07-06 前按 5%，自该日起按 10%；策略不新买入 ST，但历史持仓可能遇到状态变化。代码按交易日和 `is_st` 字段计算封板价。规则见[上交所规则修订说明](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml)和[深交所规则文本](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)。
- 卖出印花税默认 0.05%，自 2023-08-28 起证券交易印花税减半征收；见[财政部、税务总局公告](https://shanxi.chinatax.gov.cn/web/detail/sx-11400-545-1780448)。
- 回测应输入**未复权**日线 OHLC 和前收盘价，以便估算涨跌停价。当前实现不处理分红、送转股、停牌复牌细节、集合竞价队列、涨跌停排队量和退市股票的完整成交规则；日线 OHLC 也不能还原真实盘口成交。比较策略前，应使用覆盖退市股票的历史样本，并单独核对复权与公司行为。
- 输出指标不代表未来表现；应做样本外区间、不同市场阶段和交易成本敏感性分析。这里没有提供个性化投资建议。
