"""实时行情采集器：easy_tdx → 事件总线。

连接模型（用户方案里的"必须用连接池"）：
  每个 worker 独占一个 easy_tdx 客户端连接，标的池按 worker 轮转分片。
  互不阻塞、互不排队；某个节点抽风只影响它自己那个 worker。

为什么自己测速选点，而不用 easy_tdx 的 from_best_host()：
  1. from_best_host() 会往 ~/.easy_tdx/config.json 写盘（容器内 / 非 root 用户常写不了，
     实测直接抛 PermissionError，导致整个采集器起不来）；
  2. 它只在内置的 3 个 MAC 节点里挑，池子太小；
  3. 我们需要把"节点排名"落到自己的数据目录里，重启后能直接复用，不重复探测。

实测要点（2026-11-01，easy-tdx 1.10.0）：
  - MAC 协议：能取到实时报价；内置池只有 3 个节点，但标准池里有多个节点同样能应答 MAC 协议，
    合并后池子足够做故障转移。
  - 标准协议：批量报价 get_security_quotes() 在 1.10.0 上对所有节点都返回空 DataFrame，
    所以默认走 MAC 协议。
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bus import TOPIC_QUOTES, EventBus
from .settings import Settings
from .symbols import parse_symbol                 # noqa: F401 - 对外保持原有导入路径

log = logging.getLogger("market.collector")


@dataclass
class Snapshot:
    """最近一次成功抓取的行情快照。

    多 worker 时各 worker 负责一片标的，这里按 worker 分片保存再合并 ——
    直接覆盖会让 REST 端只看到最后完成的那个 worker 的票。
    """

    ts: float = 0.0
    shards: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    errors: int = 0
    last_error: str = ""
    worker_errors: dict[str, int] = field(default_factory=dict)

    @property
    def quotes(self) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for rows in self.shards.values():
            for row in rows:
                key = f"{row.get('market')}-{row.get('code')}"
                merged[key] = row
        return list(merged.values())

    def age(self) -> float:
        return time.time() - self.ts if self.ts else float("inf")


class HostRanker:
    """行情节点测速与排名。

    - 启动时并发 TCP 探测候选节点，按延迟排序
    - 排名缓存到 data_dir/hosts.json（带时间戳），重启免重复探测
    - 超过 ttl 则重新探测（免费节点的可达性会变）
    """

    def __init__(self, candidates: list[str], data_dir: Path,
                 ttl: float = 3600.0, top: int = 12) -> None:
        self.candidates = candidates
        self.cache_file = data_dir / "hosts.json"
        self.ttl = ttl
        self.top = top
        self.ranked: list[str] = []
        self.latency: dict[str, float] = {}

    def load_cache(self) -> list[str]:
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            if time.time() - float(data.get("ts", 0)) < self.ttl:
                self.ranked = [h for h in data.get("ranked", []) if h in self.candidates]
                self.latency = {k: float(v) for k, v in data.get("latency", {}).items()}
                return self.ranked
        except Exception:
            pass
        return []

    def save_cache(self) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps({
                "ts": time.time(),
                "ranked": self.ranked,
                "latency": self.latency,
            }, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:
            log.warning("节点排名缓存写入失败（不影响运行）：%s", exc)

    def probe(self, timeout: float = 1.5, validate=None,
              want: int = 5, budget: float = 15.0) -> list[str]:
        """探测候选节点。

        TCP 能连上 ≠ 协议能用：实测标准池多数节点不响应 MAC 协议，
        所以先 TCP 过滤，再用真实协议握手过滤"能连但不会说话"的节点。

        两个必须知道的实测坑（2026-11-01）：
          1. 坏节点握手要 20 秒才抛错，且 easy_tdx 的 timeout 参数管不住它内部的
             重试 —— 52 个节点按 8 并发排完要 130 秒以上，直接把首屏数据拖没。
             所以这里全并发提交 + 先到先得 + 限时返回，凑够 want 个就收工。
          2. 能用的节点其实集中在少数几个，多测无益；剩下的靠运行期故障转移。
        """
        if not self.candidates:
            return []

        def ping(host: str) -> tuple[str, float | None]:
            start = time.perf_counter()
            try:
                with socket.create_connection((host, 7709), timeout=timeout):
                    return host, (time.perf_counter() - start) * 1000
            except Exception:
                return host, None

        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(ping, self.candidates))
        reachable = sorted([(ms, h) for h, ms in results if ms is not None], key=lambda x: x[0])
        if not reachable:
            self.ranked = []
            return []

        if validate is not None:
            reachable = self._validate_first(reachable, validate, want, budget)

        self.ranked = [h for _, h in reachable[:self.top]]
        self.latency = {h: ms for ms, h in reachable[:self.top]}
        if self.ranked:
            self.save_cache()
        return self.ranked

    @staticmethod
    def _validate_first(reachable, validate, want: int, budget: float):
        """并发握手，凑够 want 个可用节点或超时即返回。"""
        pool = ThreadPoolExecutor(max_workers=min(32, max(4, len(reachable))))
        futures = {pool.submit(validate, host): (ms, host) for ms, host in reachable}
        ok: list[tuple[float, str]] = []
        deadline = time.time() + budget
        try:
            for fut in as_completed(futures, timeout=max(0.1, budget)):
                try:
                    passed = fut.result()
                except Exception:
                    passed = False
                if passed:
                    ok.append(futures[fut])
                    if len(ok) >= want or time.time() > deadline:
                        break
                if time.time() > deadline:
                    break
        except FuturesTimeout:
            pass
        finally:
            for f in futures:
                f.cancel()
            # 不等剩下的慢节点：它们的线程会自己跑完，但不能阻塞我们出首屏数据
            pool.shutdown(wait=False, cancel_futures=True)
        return sorted(ok, key=lambda x: x[0])

    def ensure(self, timeout: float = 1.5, validate=None) -> list[str]:
        cached = self.load_cache()
        if cached:
            return cached
        return self.probe(timeout, validate)


@dataclass
class QuoteCollector:
    settings: Settings
    bus: EventBus
    snap: Snapshot
    #: 自选池（需提供 pairs()）。为空则退回 settings.symbols 的静态池。
    watch: Any = None
    ranker: HostRanker = field(init=False)
    _tasks: list = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.watch is None:
            self.watch = _StaticWatch(self.settings.symbols)
        self.ranker = HostRanker(
            candidates=_candidate_hosts(self.settings),
            data_dir=self.settings.data_dir,
            ttl=self.settings.host_ttl,
            top=self.settings.host_top,
        )
        self._host_idx = 0
        self._probe_lock: asyncio.Lock | None = None

    # ── 对外 ──
    async def start(self) -> None:
        n = max(1, self.settings.workers)
        # 即便当前池子为空也起 worker —— 池子可以运行时加票，不该要求重启
        for i in range(n):
            self._tasks.append(asyncio.create_task(self._worker(i, n), name=f"tdx-worker-{i}"))
        log.info("采集器启动：%d 个 worker，%d 只标的，协议=%s，轮询=%ss",
                 n, len(self.watch.pairs()), self.settings.protocol, self.settings.poll_interval)
        if not self.watch.pairs():
            log.warning("自选池为空，等待前端上报或 TDX_SYMBOLS 配置")

    async def _ensure_hosts(self) -> None:
        """惰性测速：第一次真要连节点时才算排名。

        不在 start() 里预探测，是因为没标的时白扫 50+ 节点纯浪费，
        而且会让测试/无票启动拖上几十秒。
        """
        if self._probe_lock is None:
            self._probe_lock = asyncio.Lock()
        async with self._probe_lock:
            if self.ranker.ranked:
                return
            try:
                ranked = await asyncio.to_thread(self.ranker.ensure, 2.0, self._validate_host)
                if ranked:
                    log.info("节点测速完成，可用 %d 个，最快 %s (%.0f ms)",
                             len(ranked), ranked[0], self.ranker.latency.get(ranked[0], 0))
                else:
                    log.warning("所有候选节点都不可达或不支持当前协议，采集器将持续重试")
            except Exception:
                log.exception("节点测速失败")

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # ── 内部 ──
    async def _worker(self, idx: int, total: int) -> None:
        client = None
        fails = 0
        while True:
            # 每轮重新取池子：前端加/删自选立即生效，无需重启服务
            pairs = self.watch.pairs()
            mine = pairs[idx::total] if pairs else []
            if not mine:
                # 分片被清空时必须同时清掉快照里的旧数据，否则已删除的票会一直残留
                self.snap.shards[idx] = []
                await asyncio.sleep(1.0)
                continue
            try:
                if client is None:
                    await self._ensure_hosts()
                    client = await asyncio.to_thread(self._make_client)
                    fails = 0
                payload = await asyncio.to_thread(self._fetch, client, mine)
                self.snap.ts = time.time()
                self.snap.shards[idx] = payload
                self.snap.errors = 0
                self.snap.last_error = ""
                self.snap.worker_errors.pop(f"w{idx}", None)
                await self.bus.publish(TOPIC_QUOTES, {
                    "type": "quotes",
                    "ts": self.snap.ts,
                    "worker": idx,
                    "quotes": self.snap.quotes,      # 合并所有 worker 的分片
                })
                await asyncio.sleep(self.settings.poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                      # noqa: BLE001 - 采集层必须兜住一切
                fails += 1
                self.snap.errors += 1
                self.snap.last_error = f"{type(exc).__name__}: {exc}"
                self.snap.worker_errors[f"w{idx}"] = fails
                log.warning("worker %d 第 %d 次失败：%s", idx, fails, self.snap.last_error)
                await self.bus.publish(TOPIC_QUOTES, {
                    "type": "status",
                    "state": "degraded",
                    "worker": idx,
                    "error": self.snap.last_error,
                    "ts": time.time(),
                })
                try:
                    if client is not None:
                        await asyncio.to_thread(client.close)
                except Exception:
                    pass
                client = None
                self._rotate_host()
                delay = min(self.settings.backoff_max,
                            self.settings.backoff_base * (2 ** min(fails, 6)))
                await asyncio.sleep(delay)

    def _validate_host(self, host: str) -> bool:
        """真实协议握手校验：能 TCP 连上不代表能应答行情协议。

        MAC：拉一只票的报价，能出数据才算可用。
        标准：1.10.0 的批量报价返回空，改用证券总数做连通性校验。
        """
        try:
            if self.settings.protocol == "std":
                from easy_tdx import TdxClient, Market
                c = TdxClient(host, timeout=self.settings.tdx_timeout)
                try:
                    return bool(c.get_security_count(Market.SH))
                finally:
                    c.close()
            from easy_tdx import MacClient, Market
            c = MacClient(host, timeout=self.settings.tdx_timeout)
            try:
                df = c.get_stock_quotes([(Market.SH, "600519")])
                return df is not None and len(df) > 0
            finally:
                c.close()
        except Exception:
            return False

    def _rotate_host(self) -> None:
        """换节点。池子按测速排名轮转，实测挂掉一个不影响整体。"""
        if self.ranker.ranked:
            self._host_idx = (self._host_idx + 1) % len(self.ranker.ranked)
            log.info("切换到下一个候选节点：%s", self.ranker.ranked[self._host_idx])

    def _current_host(self) -> str | None:
        if self.ranker.ranked:
            return self.ranker.ranked[self._host_idx % len(self.ranker.ranked)]
        return None

    def _make_client(self):
        """建客户端。不用 from_best_host()：它会写 ~/.easy_tdx，容器里常写不了。"""
        host = self._current_host()
        if self.settings.protocol == "std":
            from easy_tdx import TdxClient
            return TdxClient(host, timeout=self.settings.tdx_timeout,
                             auto_reconnect=True, heartbeat_interval=15.0)
        from easy_tdx import MacClient
        return MacClient(host, timeout=self.settings.tdx_timeout,
                         auto_reconnect=True, heartbeat_interval=15.0)

    def _fetch(self, client, shard: list[tuple[int, str]]) -> list[dict[str, Any]]:
        """按协议取报价。两种协议的批量报价方法名不同，但入参一致：
        list[(market, code)]，单批上限 80。"""
        if self.settings.protocol == "std":
            method = getattr(client, "get_security_quotes", None)
        else:
            method = getattr(client, "get_stock_quotes", None)
        if method is None:                      # 兜底
            method = getattr(client, "get_security_quotes", None) or \
                     getattr(client, "get_stock_quotes", None)
        if method is None:
            raise RuntimeError("客户端没有可用的批量报价方法")

        out: list[dict[str, Any]] = []
        for i in range(0, len(shard), self.settings.batch_size):
            batch = shard[i:i + self.settings.batch_size]
            df = method(batch)
            if df is None or len(df) == 0:
                continue
            out.extend(_df_to_records(df))
        return out


def _candidate_hosts(settings: Settings) -> list[str]:
    """候选节点池：自定义优先，其次 MAC 内置池，最后标准池（实测多个兼容 MAC 协议）。"""
    hosts: list[str] = []
    seen: set[str] = set()

    def add(items) -> None:
        for h in items:
            if h and h not in seen:
                seen.add(h)
                hosts.append(h)

    add(settings.hosts)
    try:
        from easy_tdx.transport.sync import KNOWN_HOSTS, MAC_HOSTS
        add(MAC_HOSTS)
        add(KNOWN_HOSTS)
    except Exception:
        pass
    return hosts


class _StaticWatch:
    """只读的静态池，用于没接 WatchList 的场景（测试、单机临时跑）。

    分片用固定取模（pairs[idx::total]），保证 worker i 每轮拿到的还是第 i 片，
    不会因为某片为空就整体错位。
    """

    def __init__(self, symbols) -> None:
        pairs = []
        for sym in symbols:
            parsed = parse_symbol(sym)
            if parsed:
                pairs.append(parsed)
            else:
                log.warning("标的 %r 无法解析，跳过", sym)
        self._pairs = pairs

    def pairs(self) -> list[tuple[int, str]]:
        return list(self._pairs)


def _df_to_records(df) -> list[dict[str, Any]]:
    """DataFrame → list[dict]，数值统一转成原生类型（NaN → None）。"""
    records: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for k, v in row.items():
            key = str(k).strip()
            if isinstance(v, float):
                clean[key] = None if v != v else v      # NaN
            elif hasattr(v, "item"):
                try:
                    clean[key] = v.item()
                except Exception:
                    clean[key] = str(v)
            else:
                clean[key] = v
        records.append(clean)
    return records
