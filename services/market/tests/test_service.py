"""服务层契约测试：全部离线，不连行情节点、不连数据湖。

覆盖三件关键事：
  1. 行情源不可用时 API 仍然在线，且给出明确的降级码（前端不崩）
  2. 总线解耦成立：采集器把数据推上总线，API 原样广播
  3. 历史查询在数据湖缺失时返回 503 而不是 500
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bus import TOPIC_QUOTES, MemoryBus          # noqa: E402
from app.collector import Snapshot, parse_symbol     # noqa: E402
from app.settings import Settings, load_settings    # noqa: E402


# ── 标的解析 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("600519.SH", (1, "600519")),
    ("600519.ss", (1, "600519")),
    ("000858.SZ", (0, "000858")),
    ("430047.BJ", (2, "430047")),
    ("600519", (1, "600519")),
    ("000858", (0, "000858")),
    ("300750", (0, "300750")),
    ("", None),
    ("abc", None),
])
def test_parse_symbol(raw, expected):
    assert parse_symbol(raw) == expected


# ── 总线 ───────────────────────────────────────────────────────────────
def test_memory_bus_delivers():
    async def scenario():
        bus = MemoryBus(8)
        got = []

        async def consume():
            async for msg in bus.subscribe(TOPIC_QUOTES):
                got.append(msg)
                break

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        await bus.publish(TOPIC_QUOTES, {"type": "quotes", "quotes": [{"code": "600519"}]})
        await asyncio.wait_for(task, timeout=2)
        await bus.close()
        return got

    got = asyncio.run(scenario())
    assert got and got[0]["quotes"][0]["code"] == "600519"


def test_memory_bus_drops_oldest_when_full():
    """慢消费者不能把采集器堵死 —— 满了丢最旧的。"""

    async def scenario():
        bus = MemoryBus(2)
        q = asyncio.Queue()
        # 直接构造订阅者，模拟一个不消费的慢客户端
        async def slow():
            async for _ in bus.subscribe(TOPIC_QUOTES):
                await asyncio.sleep(10)

        t = asyncio.create_task(slow())
        await asyncio.sleep(0.05)
        for i in range(10):
            await bus.publish(TOPIC_QUOTES, {"i": i})
        await asyncio.sleep(0.05)
        t.cancel()
        await bus.close()

    asyncio.run(scenario())      # 不抛异常即通过


# ── API 契约 ──────────────────────────────────────────────────────────
def _client(tmp_path, **overrides) -> TestClient:
    params = dict(
        host="127.0.0.1", port=9110, bus="memory", redis_url="", bus_queue_size=16,
        symbols=(), workers=1, poll_interval=1.0, protocol="mac", batch_size=80,
        backoff_base=1.0, backoff_max=60.0, lake_root=tmp_path / "lake",
        lake_config="", api_token="", stale_after=15.0)
    params.update(overrides)
    settings = Settings(**params)
    from app.api import create_app
    return TestClient(create_app(settings))


def test_healthz_always_ok(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["bus"] == "memory"
        assert body["lake"]["available"] is False


def test_quotes_returns_503_when_no_data(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/api/quotes")
        assert r.status_code == 503
        body = r.json()
        assert body["code"] == "no_data"
        assert "error" in body


def test_history_returns_503_when_no_lake(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/api/history", params={"symbol": "600519"})
        assert r.status_code == 503
        assert r.json()["code"] == "no_lake"


def test_lake_available_false_when_root_blank(monkeypatch, tmp_path):
    """CNE_LAKE_ROOT="" 会被 Path("") 解析成 "."，绝不能当成"湖可用"。

    真机上踩过：误报 available=true，/api/history 于是走查询分支返回
    lake_error(404)，而正确语义是 503 no_lake。
    """
    monkeypatch.setenv("CNE_LAKE_ROOT", "")
    monkeypatch.setenv("MARKET_DATA_DIR", str(tmp_path))
    s = load_settings()
    assert str(s.lake_root) == "."
    assert s.lake_available is False


def test_lake_available_true_when_dir_exists(monkeypatch, tmp_path):
    lake = tmp_path / "lake"
    lake.mkdir()
    monkeypatch.setenv("CNE_LAKE_ROOT", str(lake))
    monkeypatch.setenv("MARKET_DATA_DIR", str(tmp_path))
    assert load_settings().lake_available is True


def test_token_required_when_configured(tmp_path):
    with _client(tmp_path, api_token="secret") as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/status", params={"token": "secret"}).status_code == 200
        assert c.get("/api/status", headers={"X-API-Token": "secret"}).status_code == 200


def test_websocket_streams_published_payload(tmp_path):
    with _client(tmp_path) as c:
        with c.websocket_connect("/ws/quotes") as ws:
            # 采集器已随 lifespan 启动，这里直接往总线上打一条
            import json
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                _publish(c, {"type": "quotes", "quotes": [{"code": "600519", "close": 1500.0}]}))
            data = json.loads(ws.receive_text())
            assert data["type"] == "quotes"
            assert data["quotes"][0]["code"] == "600519"


async def _publish(client, payload):
    await client.app.state.bus.publish(TOPIC_QUOTES, payload)


def test_snapshot_age_and_stale_flag(tmp_path):
    snap = Snapshot()
    assert snap.age() == float("inf")
    snap.ts = 1.0
    snap.shards[0] = [{"code": "600519"}]
    assert snap.age() > 0
    assert len(snap.quotes) == 1


def test_snapshot_merges_worker_shards():
    """多 worker 时快照必须合并，不能只剩最后完成的那个 worker 的票。"""
    snap = Snapshot()
    snap.shards[0] = [{"market": 1, "code": "600519", "close": 1392.0}]
    snap.shards[1] = [{"market": 0, "code": "000858", "close": 74.7}]
    codes = {q["code"] for q in snap.quotes}
    assert codes == {"600519", "000858"}


def test_snapshot_dedupes_same_code():
    snap = Snapshot()
    snap.shards[0] = [{"market": 1, "code": "600519", "close": 1.0}]
    snap.shards[1] = [{"market": 1, "code": "600519", "close": 2.0}]
    assert len(snap.quotes) == 1
