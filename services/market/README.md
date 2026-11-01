# 行情数据服务（历史回测 + 近秒级实时行情）

自托管 A 股数据平台。三层解耦：本地 Parquet 数据湖（历史）+ 通达信直连（实时）+ FastAPI 服务层。

---

## 一、架构

```
┌────────────────────────────────────────────────────────────┐
│ 采集层  collector.py                                        │
│  easy_tdx(MAC 协议, 7709)                                  │
│  · N 个 worker，各持独立连接，标的池轮转分片                │
│  · 自研测速选点：TCP 可达 → 真实协议握手校验 → 延迟排序      │
│  · 失败指数退避 + 节点轮转；异常转事件，不阻塞 API           │
└───────────────┬────────────────────────────────────────────┘
                │ publish(topic=quotes)
┌───────────────▼────────────────────────────────────────────┐
│ 事件总线  bus.py                                            │
│  memory（默认，零依赖）│ redis（多机/多进程时切）            │
└───────────────┬────────────────────────────────────────────┘
                │ subscribe
┌───────────────▼────────────────────────────────────────────┐
│ 服务层  api.py（FastAPI）                                   │
│  REST  /api/quotes  /api/history  /api/datasets  /api/status │
│  WS    /ws/quotes                                          │
│  降级语义：源挂 → 503 + code，前端不崩                       │
└───────────────┬────────────────────────────────────────────┘
                │ DuckDB 直读 Parquet（列裁剪、谓词下推）
┌───────────────▼────────────────────────────────────────────┐
│ 历史层  CNEquity 数据湖  CNE_LAKE_ROOT                      │
│  daily_bars / 复权因子 / 财报 / 指数成分 …（Parquet）        │
└────────────────────────────────────────────────────────────┘
```

---

## 二、事实核对：你给的方案里需要修正的 5 点

| # | 你的原话 | 实测结论 |
|---|---|---|
| 1 | `pip install easy_tdx` | **装不上**。easy-tdx 未发布到 PyPI（`pypi.org/pypi/easy-tdx/json` → 404）。必须从 GitHub 源码装：`pip install "easy-tdx @ git+https://github.com/Justin-ZS/easy_tdx.git"` |
| 2 | `pip install cnequity` → `cne init` → `cne run daily` | 正确 ✓。当前版本 **0.21.0**，Python ≥3.10。首次 `cne init` 是**小时级、GB 级**；`cne init --profile demo` 是 1 分钟演示湖；日常维护 `cne run daily`，体检 `cne check` |
| 3 | `TdxClient.from_best_host()` 自动测速选低延迟节点 | 机制存在 ✓，但**它会写 `~/.easy_tdx/config.json`**，容器内/非 root 用户直接抛 `PermissionError`（实测踩到），且内置 MAC 节点池只有 **3 个**。所以本服务自研测速选点：TCP 可达 → 真实协议握手校验 → 延迟排序 → 缓存到本地 `data/hosts.json` |
| 4 | 「easy_tdx 负责连接池」 | 它不负责连接池，只提供单连接 + 自动重连 + 跨节点故障转移。**连接池由本服务实现**：每 worker 独占连接、标的池轮转分片 |
| 5 | 「Redis Pub/Sub 解耦」 | 方向对，但 Redis 是额外故障点。做成**可插拔**：默认进程内广播（单实例零依赖），`MARKET_BUS=redis` 才切 Redis（多机/多进程时才有必要） |

### 协议选择的实测依据

easy-tdx 1.10.0 实测（2026-11-01，本机）：

| 协议 | 节点池 | 批量报价 | 结论 |
|---|---|---|---|
| MAC（默认） | 内置 3 + 标准池中 7 个实测兼容 | `get_stock_quotes()` **返回真实数据** | ✅ 用这个 |
| 标准 7709 | 52 | `get_security_quotes()` **对所有节点都返回空 DataFrame** | ❌ 不可用于行情（`get_security_count()` 正常，说明是接口而非连通性问题） |

节点延迟实测（TCP 握手，52 节点中 47 可达）：最快 14 ms，最慢 27 ms；**协议握手校验后剩下 7 个真正可用的 MAC 节点**，延迟 17–43 ms。

---

## 三、已验证的功能（本机实测，非纸面）

| 能力 | 验证方式 | 结果 |
|---|---|---|
| 实时行情 | `/api/quotes` | 4 只票全部取到（贵州茅台 1392.05 / 五粮液 74.70 / 宁德时代 232.92 / 中国平安 52.80），快照 age 0.13 s |
| 多 worker 合并 | 2 worker × 2 票 | 快照合并为 4 条，不互相覆盖 ✓ |
| 历史数据 | `/api/history?symbol=600519` | DuckDB 直读 Parquet 返回 3 行，`start` 过滤生效 ✓ |
| 数据集发现 | `/api/datasets` | 列出 `daily_bars`（parquet 文件数）✓ |
| WebSocket | `/ws/quotes` | 订阅端收到 `{"type":"quotes",...}` ✓ |
| 降级语义 | 数据湖不存在 / 无数据 | 返回 503 + `code`（`no_lake` / `no_data` / `stale`），不 500 ✓ |
| 启动不阻塞 | 启动 6 s 后 | 端口已可服务（节点测速在后台跑，不挡监听）✓ |
| 单元测试 | `pytest` | **19 passed** |

---

## 四、目录结构

```
services/market/
├── app/
│   ├── settings.py      集中配置（env 驱动，带默认值与类型收敛）
│   ├── bus.py           事件总线：MemoryBus / RedisBus 可插拔
│   ├── collector.py     采集器：测速选点 + 连接池 + 分片 + 退避
│   ├── lake.py          历史查询：DuckDB 直读 Parquet，列名自适应
│   └── api.py           FastAPI：REST + WS + 降级语义
├── scripts/
│   ├── probe_tdx.py     节点延迟实测（部署第一步）
│   ├── install-easy-tdx.sh
│   ├── lake.sh          CNEquity 建湖/增量/体检封装
│   └── run-api.sh
├── deploy/market.service   systemd unit
├── config.example.env
├── requirements.txt
└── tests/test_service.py
```

---

## 五、部署（Linux 自建机）

```bash
# 1. 依赖
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
bash scripts/install-easy-tdx.sh          # GitHub 装不上就传本地 zip 路径

# 2. 配置
cp config.example.env config.env && vi config.env
#   至少设 TDX_SYMBOLS、CNE_LAKE_ROOT；公网暴露务必设 API_TOKEN

# 3. 节点连通性体检（先测，别直接上）
python scripts/probe_tdx.py --protocol mac
#    全不通 → 先解决出网/端口 7709，别往下走

# 4. 历史数据湖（另开一台/低峰跑，首次数小时）
CNE_LAKE_ROOT=/data/cnequity bash scripts/lake.sh init
CNE_LAKE_ROOT=/data/cnequity bash scripts/lake.sh check

# 5. 跑服务
bash scripts/run-api.sh config.env

# 6. 装成 systemd 服务
sudo cp deploy/market.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now market
```

### 每日维护

```cron
30 16 * * 1-5 cd /opt/market-service && . venv/bin/activate && bash scripts/lake.sh daily >> /var/log/market-lake.log 2>&1
```

---

## 六、风险与降级（照你的要求逐条落实）

| 风险 | 措施 | 表现 |
|---|---|---|
| 免费节点波动 | 自研测速 + 协议握手校验 + 排名缓存 1 小时；失败按延迟顺序轮转节点 | `/api/status` 里 `worker_errors` 可见；`/api/quotes` 返回 503 + `code:"stale"`，不返回过期价 |
| 采集进程卡死 | 阻塞型 SDK 调用放线程池；采集与 API 进程内分任务，API 不等采集 | API 始终在线 |
| 队列被慢消费者撑爆 | 内存总线队列有界，满了丢最旧 | 实时行情允许丢帧，不允许拖垮采集 |
| 数据湖未建 / 损坏 | `/api/history` 探测不到就返回 503 + `no_lake` + 提示命令 | 前端弹提示，不白屏 |
| 单机故障 | 总线切 Redis（`MARKET_BUS=redis`），采集器与 API 可分机部署 | — |
| 数据对不上账 | `cne check` 每日体检；`/api/history` 的数据自带 `source`/`data_version` 字段 | 定期与东方财富/同花顺对数 |

---

## 七、接口

```
GET /healthz                     存活探针，永远 200
GET /api/status                  总线模式、采集延迟、错误数、数据湖清单
GET /api/quotes                  最新行情快照（陈旧则 503 + code=stale）
GET /api/history?symbol=&dataset=daily_bars&start=&end=&limit=
GET /api/datasets                数据湖可用数据集
WS  /ws/quotes                   实时推送 {"type":"quotes","ts":...,"quotes":[...]}
```

鉴权：`API_TOKEN` 非空时，REST 需 `?token=` 或 `X-API-Token` 头，WS 需 `?token=`。

---

## 八、待办

- [ ] 接入起始页前端（自选股行情卡片），目前服务与前端尚未联通
- [ ] Redis 模式的端到端验证（内存模式已验证）
- [ ] 与东方财富/同花顺的数值对数脚本（`scripts/verify_quotes.py` 待写）
- [ ] CNEquity 真实数据湖的建库（demo profile 已验证接口，full profile 未在本机跑）

## 九、A 股主板策略回测

单标的日线趋势突破策略和回测命令见 [STRATEGY.md](STRATEGY.md)。支持 CSV 或本地 CNEquity 数据湖，输出交易记录、每日权益曲线和收益/回撤指标；仅用于研究，不连接券商。

```bash
python services/market/scripts/backtest_strategy.py --from-lake --symbol 600519.SH --start 2018-01-01
```
