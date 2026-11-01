# 起始页 V2 · 个人数字工作室

自己用的浏览器起始页，外加一个行情服务和一个量化台。**纯网站形态**（不是浏览器扩展），后端只做两件事：返回静态页、提供本地 JSON API；前端逻辑全在浏览器里跑，零构建。

**线上**：<https://yuban211.duckdns.org:59684/studio/>

> **分支说明**：本仓库的 `main` 是**上一代**实现（FastAPI `server/` + 浏览器扩展 + `content/` 文章，2026-10-28）。V2 代码在这个 **`startpage-v2`** 分支上，两代并列存在、互不覆盖——`main` 的 `content/` 是后台「仓库同步」的内容源，动它会丢文章。

---

## 三个模块

| 模块 | 路径 | 端口 | 说明 |
|---|---|---|---|
| 起始页 | `/studio/` | 9103 | limestart 风格首页：搜索、快捷方式、收藏夹分类、便笺、壁纸、实时行情卡片 |
| 行情服务 | `/studio/market/*` | 9111 | 通达信节点采集 A 股实时行情，REST + WebSocket |
| 量化台 | `/studio/quant/*` | 9113 | 全 A 股列表、模拟撮合、策略引擎（策略文件热加载） |

三者独立进程、独立数据库，通过 HTTP 互相调用，没有共享内存或消息队列。

## 请求怎么走

```
浏览器 ──TLS:59684──> Caddy ──handle──> uvicorn:9103  起始页
                        ├──handle_path──> uvicorn:9111  行情（剥前缀）
                        └──handle_path──> uvicorn:9113  量化（剥前缀）
```

**前缀是硬约束**：`/studio/*` 走 `handle`（**不剥**前缀），所以起始页必须自己带 `/studio`，即 `APP_PREFIX="/studio"`；行情和量化走 `handle_path`（剥前缀），它们的路由里不带 `/studio`。

## 技术栈

- FastAPI + uvicorn，**单 worker**（内存态在进程内，多 worker 会各算各的）
- 标准库 `sqlite3`，WAL 模式 + `PRAGMA user_version` 做迁移，不引 ORM
- 前端原生 JS，**零构建、零第三方 CDN**：本机 jsdelivr / unpkg / cdnjs / npmmirror 全部不可达，任何新加的前端库必须本地化，`scripts/verify-static.py` 会拦截外链
- 运行时依赖只有 3 个，见 `requirements.txt`

## 目录结构

```
src/                   起始页应用（app.py 装配路由，repo.py 数据访问，db.py 迁移）
  static/home/         ★ 线上在用的青柠式页面
  static/web/          旧版页面，仅作兜底（app.py 找不到 home/index.html 才回退）
services/market/       行情服务：采集器、总线、自选池、策略回测
services/quant/        量化台：撮合引擎、行情 feed、策略加载
tests/                 起始页后端测试 + 前端断言
scripts/               运维脚本（证书续期、静态自检、打包、启动）
docs/                  PRD、设计与运维文档
```

两套前端页面容易踩：`static/home/` 是线上那套，改 `static/web/` 线上不会有任何变化。

## 本地跑

```bash
python -m pip install -r requirements.txt

export APP_PREFIX="/studio"        # 与 Caddy 的 handle 对齐；若改 handle_path 则设为 ""
export APP_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
export DATA_DIR="./data"
export PYTHONPATH="src"

uvicorn app:app --host 127.0.0.1 --port 9103 --workers 1
```

打开 <http://127.0.0.1:9103/studio/>，`/healthz` 应返回 `{"ok":true,...}`。

`APP_SECRET` 缺失时 `src/config.py` 直接拒绝启动；`scripts/startpage.env` 是本地真实配置（含密钥，已 gitignore），模板见 `scripts/startpage.env.example`。

## 测试

**必须在各自目录跑**：`services/market/app/__init__.py` 会抢占顶层 `app` 包名，在仓库根跑 pytest 会 `ImportError: cannot import name 'create_app'`。

```bash
# 起始页（基准 52 passed）
PYTHONPATH=src python -m pytest tests/ -q

# 行情（基准 41 passed）
cd services/market && PYTHONPATH=. python -m pytest tests/ -q

# 量化（基准 14 passed）
cd services/quant && PYTHONPATH=.. python -m pytest tests/ -q

# 静态资源自检：外链拦截 + 引用是否都落到真实文件
python scripts/verify-static.py
```

## 部署要点

1. **CRLF 铁律**：任何 `.sh` / `.py` 上传前必须是 LF。Windows 上曾因 CRLF 打坏启动脚本，代价是整站宕机。
2. 代码传服务器 `/workspace/startpage`，面板重启实例生效。
3. **Caddyfile 每次开机被 `start.tls.sh` 用 heredoc 重新生成**，端口写死在脚本里；只改磁盘上的 Caddyfile，一重启就被覆盖回旧值。改路由必须同时改 `start.tls.sh` 的 heredoc 段。
4. 就绪判据（容器内）：

```sh
curl -s http://127.0.0.1:9103/healthz   # {"ok":true,"prefix":"/studio",...}
curl -s http://127.0.0.1:9111/healthz   # {"ok":true,"collector":{...}}
curl -s http://127.0.0.1:9113/healthz   # {"ok":true,"rows":4605,...}
```

完整流程、回滚与排障见 `docs/`（含密钥的运维手册与速查表只存本地，不入库）。

## 接口一览

**起始页**（`/studio` 前缀，`LIBRARY_TOKEN` 为空时不鉴权）

```
GET  /healthz                 GET  /api/config      POST /api/config
GET  /api/library             GET  /api/backup
GET|POST /api/categories      PUT|DELETE /api/categories/{id}
POST /api/categories/reorder
GET|POST /api/bookmarks       PUT|DELETE /api/bookmarks/{id}
POST /api/bookmarks/reorder   POST /api/bookmarks/{id}/move   POST /api/bookmarks/{id}/hit
GET  /api/library/export      POST /api/library/import
```

**行情 market**

```
GET  /healthz      GET  /api/status     GET  /api/quotes
GET|POST /api/watch                    GET  /api/datasets   GET /api/history
WS   /ws/quotes
```

**量化 quant**

```
GET  /healthz      GET  /api/overview   GET  /api/list      GET  /api/sectors
GET  /api/quote/{code}                 GET  /api/kline/{code}
GET  /api/account  GET  /api/orders     GET  /api/strategies   POST /api/strategy
POST /api/engine   GET  /api/status     POST /api/trade        POST /api/reset
```

## 已知限制（不是 bug，别重复排查）

- 行情只覆盖沪深主板 + 创业板（约 4605 只），**不含科创板、北交所**
- 东财 `push2` 有 IP 级限流，不能当唯一数据源；腾讯 `fqkline` 日线偶发 HTTP 501，需新浪源兜底
- 盘中日线当日缓存不刷新，收盘后才有准确数据
- 模拟盘不做除权除息、不模拟盘口深度与排队；非交易时段默认允许撮合（`QUANT_OFFHOURS=0` 可关）
- 天气、热榜未接入——`verify-static.py` 拦外链，这是设计约束
- 便笺存 localStorage，换浏览器或清缓存就丢
- 数据湖当前不可用（容器内 `CNE_LAKE_ROOT` 不存在），`/api/datasets`、`/api/history` 会返回空
- 服务器容器非 root（uid 5797）、无 cron，证书续期靠启动脚本里的 `while sleep 86400` 守护

## 文档

| 文件 | 内容 |
|---|---|
| `docs/PRD.md` | 需求与边界 |
| `docs/新版起始页与量化台.md` | 界面与交互设计 |
| `docs/交付与运维手册.md` | 部署、证书续期、故障排查（**含密钥，仅本地**） |
| `docs/站点与密钥速查.md` | 站点地址与凭据速查（**含密钥，仅本地**） |

两份含密钥的文档在 `.gitignore` 里，永远不会进本仓库。
