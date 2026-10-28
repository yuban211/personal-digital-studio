"""带分层的缓存层（借鉴参考站 api/_lib/cache.ts 的思路，重写成 Python）。

参考站做对的地方：
  1. 缓存键集中在 CACHE_KEYS，不写散在各处
  2. TTL 按数据性质分档（浏览量 120s / 时间线 600s / 贡献图 1800s）
  3. cached() 统一封装「查缓存 → 未命中则加载 → 写缓存」
  4. shouldCache 允许拒绝缓存坏值

本实现补了参考站没有的两点（它没有的）：
  ★ stale-while-error：加载失败时返回上一次的成功值（标记 stale），
    而不是直接降级成演示数据。这样接口临时抽风时页面数据不会跳变。
  ★ 单飞（single-flight）：并发请求只打一次上游，其余等同一个结果。

不引入 Redis：studio 是单进程 uvicorn --workers 1，
进程内缓存就够，加 Redis 是过度设计。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")

#: 缓存键集中管理（照参考站的做法）
CACHE_KEYS = {
    "weather": lambda city: f"weather:now:{city}:v1",
    "shots": "media:shots:v1",
    "projects": "home:projects:v1",
}

#: TTL 分档（秒）
CACHE_TTL = {
    "weather": 600,        # 10 分钟：心知免费额度有限
    "shots": 60,           # 1 分钟：上传图片后要尽快出现
    "projects": 120,       # 2 分钟
    "demo": 60,            # 演示数据 1 分钟：配好密钥后快速生效
}


@dataclass
class Entry(Generic[T]):
    value: T
    stored_at: float
    ttl: int
    #: 是否来自上游失败后的旧值
    stale: bool = False

    def fresh(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return (now - self.stored_at) < self.ttl


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    stale_hits: int = 0
    loads: int = 0
    failures: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def bump(self, name: str) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)

    def as_dict(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "stale_hits": self.stale_hits,
            "loads": self.loads,
            "failures": self.failures,
        }


class LayeredCache:
    """进程内分层缓存，带 stale 兜底与单飞。"""

    def __init__(self) -> None:
        self._data: dict[str, Entry[Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.stats = CacheStats()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lk = self._locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._locks[key] = lk
            return lk

    def get(self, key: str) -> Entry[Any] | None:
        return self._data.get(key)

    def put(self, key: str, value: Any, ttl: int) -> Entry[Any]:
        entry = Entry(value=value, stored_at=time.time(), ttl=ttl, stale=False)
        self._data[key] = entry
        return entry

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def load(
        self,
        key: str,
        ttl: int,
        loader: Callable[[], T | None],
        should_cache: Callable[[T], bool] = lambda _v: True,
        *,
        keep_on_error: bool = True,
    ) -> tuple[T | None, str]:
        """返回 (值, 来源标记)。

        来源标记：
          'fresh'  —— 命中的是新鲜缓存
          'loaded' —— 本次从上游加载成功
          'stale'  —— 上游失败，返回上次的成功值
          'miss'   —— 上游失败且无旧值可用，返回 None
        """
        now = time.time()
        entry = self._data.get(key)

        # 1) 新鲜命中
        if entry is not None and entry.fresh(now):
            self.stats.bump("hits")
            return entry.value, "fresh"

        # 2) 单飞：同一 key 并发时只打一次上游
        lock = self._lock_for(key)
        with lock:
            # 拿到锁后复查（可能别的线程刚填好）
            entry = self._data.get(key)
            if entry is not None and entry.fresh():
                self.stats.bump("hits")
                return entry.value, "fresh"

            self.stats.bump("misses")
            self.stats.bump("loads")
            try:
                value = loader()
            except Exception:
                value = None

            if value is not None and should_cache(value):
                self.put(key, value, ttl)
                return value, "loaded"

            # 3) 上游失败：有旧值就返回旧值（标记 stale）
            self.stats.bump("failures")
            if keep_on_error and entry is not None:
                self.stats.bump("stale_hits")
                entry.stale = True
                return entry.value, "stale"

            return None, "miss"
