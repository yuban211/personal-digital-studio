# 全 A 行情 · 模拟实盘（services/quant）

一个进程同时提供：全 A 实时行情、板块/指数、纸上交易账户、策略引擎、以及页面。

## 1. 运行

```bash
cd services
pip install -r quant/requirements.txt      # 只要 fastapi + uvicorn
PYTHONPATH=. python -m uvicorn quant.app.api:app --host 0.0.0.0 --port 9113
```

浏览器打开 <http://127.0.0.1:9113/>。
数据落在 `services/quant/data/quant.db`（可用 `QUANT_DATA_DIR` 改）。

Linux 一键：`bash services/quant/scripts/run.sh`

## 2. 目录

```
quant/
  app/
    settings.py   环境变量 → 配置对象（全部有默认值）
    feed.py       行情源：腾讯（主）+ 东财（备），统一字段 + TTL 缓存
    store.py      SQLite：账户/持仓/委托/成交/净值/日线缓存
    broker.py     纸上撮合：费用、T+1、整手、涨跌停
    strategy.py   策略基类 + 三个内置策略 + 目录自动加载
    engine.py     轮询引擎：行情 → 策略 → 委托 → 估值 → 记净值
    api.py        HTTP 接口 + 页面
  strategies/     你自己的策略放这里（.py 丢进去 10 秒内自动出现）
  web/            页面（原生 JS + canvas，无构建、无第三方库）
  tests/          14 个单测（撮合规则/代码映射/策略加载）
```

## 3. 写自己的策略

新建 `strategies/我的策略.py`：

```python
NAME = "我的策略"
DESCRIPTION = "一句话说明，页面会显示"
PARAMS = {"top_n": 3}

def select(ctx):
    picks = []
    for row in ctx.rows:                    # 已按成交额预筛的候选（默认前 300）
        bars = ctx.bars(row["code"], 40)    # 前复权日线，带内存 + 磁盘缓存
        if len(bars) < 21:
            continue
        closes = [b["close"] for b in bars]
        if closes[-1] > sum(closes[-20:]) / 20 and row["chg"] < 9:
            picks.append({"code": row["code"], "side": "buy",
                          "weight": 0.1,          # 可选，占净值比例
                          "reason": "站上 20 日线"})
    for code in list(ctx.positions):
        picks.append({"code": code, "side": "sell", "reason": "换仓"})
    return picks
```

### 已经接好的一个例子

`strategies/user_breakout.py` 就是把你写在 `services/market/app/strategy.py` 的
"主板 20 日突破 + ATR 移动止损"接进实盘引擎的适配器：

- 规则完全按你的口径（MA20>MA60、量比 ≥1.2、非 ST、3×ATR 止损、前 10 日低止损）
- 仓位按你的 0.5% 风险预算算股数，再受 20% 单票上限约束
- 会复用你原文件里的 `StrategyConfig` 与 `is_main_board_symbol`（原文件不改）
- **唯一差异**：回测是"收盘信号 → 次日开盘成交"，实盘引擎按最新价即时成交；
  要严格复现次日开盘价，得给引擎加订单队列，当前没有
- 页面策略下拉里显示为「主板突破 + ATR 止损（你的策略）」

`ctx` 可用：`rows` `positions` `equity` `params` `today` `bars(code, n)` `log(msg)`。
返回项：`code` `side` `weight`/`qty` `reason`。卖出不给数量 = 清空可卖。

## 4. 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `QUANT_PORT` | 9113 | 端口 |
| `QUANT_DATA_DIR` | `quant/data` | 数据库与缓存目录 |
| `QUANT_INIT_CASH` | 1000000 | 初始资金 |
| `QUANT_TICK` | 10 | 引擎轮询秒数 |
| `QUANT_SNAPSHOT_TTL` | 20 | 全 A 快照缓存秒数 |
| `QUANT_PREFILTER_TOP` | 300 | 策略只看成交额前 N 只 |
| `QUANT_POSITION_PCT` | 0.12 | 单票目标仓位占净值比 |
| `QUANT_MAX_POSITIONS` | 8 | 最大持仓数 |
| `QUANT_OFFHOURS` | 1 | 非交易时段是否也撮合（默认开，页面会标注） |
| `QUANT_ENGINE` | 1 | 0 = 只提供行情，不自动跑策略 |
| `QUANT_SLIPPAGE_BPS` | 5 | 滑点（万分之一为单位：5 = 0.05%） |

## 5. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/overview` | 指数、涨跌分布、板块、模式、快照年龄 |
| GET | `/api/list` | 全 A 分页：`page size sort order q filter` |
| GET | `/api/sectors` | 行业板块 |
| GET | `/api/quote/{code}` | 单只实时 + 当日分时 |
| GET | `/api/kline/{code}` | 日线（前复权） |
| GET | `/api/account` | 账户、持仓、净值曲线 |
| GET | `/api/orders` | 委托与成交 |
| GET/POST | `/api/strategies` `/api/strategy` | 策略列表 / 切换与风控参数 |
| POST | `/api/engine` | `{"action":"start|stop|tick"}` |
| POST | `/api/trade` | 手动下单 `{code, side, qty}` |
| POST | `/api/reset` | 重置账户（需 `confirm:true`） |

## 6. 撮合规则与已知简化

贴近真实：100 股整手、T+1、佣金万 2.5（最低 5 元）、过户费 0.001%、
卖出印花税 0.05%、涨跌停不成交、停牌不成交、滑点 5bp。

刻意简化（别当成回测平台用）：

- 不模拟集合竞价、盘口深度、排队，按最新价全额即时成交
- 不做除权除息的持仓调整，用前复权价估算市值
- 非交易时段默认允许成交（方便盘后调策略），页面顶部会明确标注
- 日线在当天内缓存，盘中不会随当日 K 线更新而刷新

## 7. 行情源说明

快照（全 A 列表）：腾讯排行 → 东财 `clist`；日线：腾讯 `fqkline` → 新浪 `getKLineData` → 东财 `kline`。

| 通道 | 承担 | 备注 |
| --- | --- | --- |
| 腾讯 | 快照 / 指数 / 板块 / 日线 / 分时 | 字段最全（换手、量比、市盈、主力净流入），限流宽松 |
| 新浪 | 日线 | 腾讯日线偶发 501 时接管，实测 0.7s / 只 |
| 东财 | 快照补充 / 日线 / 板块 | 字段更全但连续抓 56 页会被掐连接，只做兜底 |

- 每个通道都有熔断：连续失败 3 次冷却 5 分钟，避免每次都先撞一次墙
- `/api/overview` 的 `source` 字段给出当前快照来源，`snapshot_error` 给出失败原因
- 东财可用时会补上科创板 / 北交所；腾讯 `aStock` 榜覆盖沪深主板 + 创业板（约 4600 只）

## 8. 部署（雨云 / Caddy）

```caddy
handle_path /quant/* {
    reverse_proxy 127.0.0.1:9113
}
```

页面里的接口与静态资源都用相对路径（`api/`、`assets/`），
所以放在 `/quant/` 下不需要改前端代码。
