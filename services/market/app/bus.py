"""事件总线：行情采集 → API 广播的解耦层。

为什么做成可插拔而不是"直接上 Redis"：
  Redis Pub/Sub 是额外故障点（进程挂、网络断、内存爆），而"稳定优先"的目标下，
  单实例部署完全不需要它。因此：
    - 默认 memory：进程内广播，零外部依赖，采集与 API 同进程或同容器
    - 可选 redis：多进程 / 多机部署时切换，采集器与 API 分开跑

两种实现遵守同一套接口，切换只改 MARKET_BUS / REDIS_URL。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Protocol

log = logging.getLogger("market.bus")

TOPIC_QUOTES = "quotes"


class EventBus(Protocol):
    async def publish(self, topic: str, payload: dict) -> None: ...

    def subscribe(self, topic: str) -> AsyncIterator[dict]: ...

    async def close(self) -> None: ...


class MemoryBus:
    """进程内广播：每个订阅者一个有界队列，满了丢最旧的。

    丢消息而不是阻塞采集器 —— 实时行情允许丢帧，不允许采集线程被慢消费者卡住。
    """

    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, topic: str, payload: dict) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(topic, ()))
        for q in queues:
            if q.full():
                try:
                    q.get_nowait()          # 丢最旧
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, topic: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.setdefault(topic, set()).add(q)
        try:
            while True:
                yield await q.get()
        finally:
            async with self._lock:
                subs = self._subscribers.get(topic)
                if subs:
                    subs.discard(q)
                    if not subs:
                        self._subscribers.pop(topic, None)

    async def close(self) -> None:
        async with self._lock:
            self._subscribers.clear()


class RedisBus:
    """Redis Pub/Sub 广播：采集器与 API 分进程/分机部署时用。

    只做发布订阅，不存行情 —— 行情快照另有缓存，避免 Redis 内存被高频写爆。
    """

    def __init__(self, url: str, queue_size: int = 256) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, decode_responses=True)
        self._queue_size = queue_size
        self._lock = asyncio.Lock()
        self._subscribers: set[str] = set()

    async def publish(self, topic: str, payload: dict) -> None:
        await self._redis.publish(topic, json.dumps(payload, ensure_ascii=False, default=str))

    async def subscribe(self, topic: str) -> AsyncIterator[dict]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(topic)
        async with self._lock:
            self._subscribers.add(topic)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if not data:
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    log.warning("Redis 消息解析失败，丢弃")
        finally:
            async with self._lock:
                self._subscribers.discard(topic)
            try:
                await pubsub.unsubscribe(topic)
                await pubsub.aclose()
            except Exception:
                pass

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception:
            pass


def create_bus(mode: str, redis_url: str = "", queue_size: int = 256) -> EventBus:
    if mode == "redis":
        if not redis_url:
            raise RuntimeError("MARKET_BUS=redis 但 REDIS_URL 为空")
        return RedisBus(redis_url, queue_size)
    return MemoryBus(queue_size)
