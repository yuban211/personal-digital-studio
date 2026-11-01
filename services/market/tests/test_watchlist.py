"""自选池测试：动态标的清单是"前端可自选股票"的地基，必须离线可验。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.settings import Settings          # noqa: E402
from app.watchlist import MAX_SYMBOLS, WatchList, normalize   # noqa: E402


# ── 规范化 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    (["600519"], ["600519.SH"]),
    (["600519.SH"], ["600519.SH"]),
    (["sh600519"], ["600519.SH"]),
    (["000858"], ["000858.SZ"]),
    (["430047"], ["430047.BJ"]),
    (["600519", "600519.SH", "SH600519"], ["600519.SH"]),   # 三种写法归一
    (["abc", "12345", ""], []),                              # 无法识别的直接丢
    (["600519", "300750", "600519"], ["600519.SH", "300750.SZ"]),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# ── 池操作 ─────────────────────────────────────────────────────────────
def test_merge_is_idempotent(tmp_path):
    w = WatchList(tmp_path / "w.json", ["600519.SH"])
    w.merge(["000858"])
    w.merge(["000858"])                       # 重复上报不能长出第二条
    assert w.snapshot() == ["600519.SH", "000858.SZ"]


def test_remove_only_touches_named_symbols(tmp_path):
    w = WatchList(tmp_path / "w.json", ["600519.SH", "000858.SZ"])
    w.remove(["300750"])                      # 删不存在的，池子不变
    assert len(w.snapshot()) == 2
    w.remove(["600519.SH"])
    assert w.snapshot() == ["000858.SZ"]


def test_replace_overwrites(tmp_path):
    w = WatchList(tmp_path / "w.json", ["600519.SH", "000858.SZ"])
    assert w.replace(["300750"]) == ["300750.SZ"]


def test_cap_enforced(tmp_path):
    w = WatchList(tmp_path / "w.json", [])
    # 用一个必然超上限的大列表，验证截断而不是无限增长
    many = [f"{600000 + i}" for i in range(MAX_SYMBOLS + 20)]
    w.merge(many)
    assert len(w.snapshot()) == MAX_SYMBOLS


def test_persist_and_reload(tmp_path):
    path = tmp_path / "w.json"
    w = WatchList(path, ["600519.SH"])
    w.merge(["000858"])
    again = WatchList(path, [])               # 模拟进程重启
    assert again.snapshot() == ["600519.SH", "000858.SZ"]


def test_defaults_used_when_file_absent(tmp_path):
    w = WatchList(tmp_path / "missing.json", ["000001"])
    assert w.snapshot() == ["000001.SZ"]


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "w.json"
    path.write_text("{ not json", encoding="utf-8")
    w = WatchList(path, ["600519"])
    assert w.snapshot() == ["600519.SH"]


def test_pairs_for_collector(tmp_path):
    w = WatchList(tmp_path / "w.json", ["600519.SH", "000858"])
    assert w.pairs() == [(1, "600519"), (0, "000858")]


# ── API ────────────────────────────────────────────────────────────────
def _client(tmp_path, **overrides) -> TestClient:
    params = dict(
        host="127.0.0.1", port=9110, bus="memory", redis_url="", bus_queue_size=16,
        symbols=("600519.SH",), workers=1, poll_interval=1.0, protocol="mac", batch_size=80,
        backoff_base=1.0, backoff_max=60.0, lake_root=tmp_path / "lake",
        lake_config="", api_token="", stale_after=15.0, data_dir=tmp_path)
    params.update(overrides)
    from app.api import create_app
    return TestClient(create_app(Settings(**params)))


def test_api_watch_merge_and_remove(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/api/watch")
        assert r.status_code == 200
        assert r.json()["symbols"] == ["600519.SH"]

        r = c.post("/api/watch", json={"symbols": ["000858"], "mode": "merge"})
        assert r.status_code == 200
        assert r.json()["symbols"] == ["600519.SH", "000858.SZ"]

        r = c.post("/api/watch", json={"symbols": ["600519"], "mode": "remove"})
        assert r.json()["symbols"] == ["000858.SZ"]


def test_api_watch_replace(tmp_path):
    with _client(tmp_path) as c:
        r = c.post("/api/watch", json={"symbols": ["300750"], "mode": "replace"})
        assert r.json()["symbols"] == ["300750.SZ"]


def test_api_watch_rejects_garbage_silently(tmp_path):
    """乱填的代码不该 500，也不该污染池子。"""
    with _client(tmp_path) as c:
        r = c.post("/api/watch", json={"symbols": ["???", "12345"], "mode": "merge"})
        assert r.status_code == 200
        assert r.json()["symbols"] == ["600519.SH"]


def test_api_watch_reflected_in_status(tmp_path):
    with _client(tmp_path) as c:
        c.post("/api/watch", json={"symbols": ["000858"], "mode": "merge"})
        body = c.get("/api/status").json()
        assert "000858.SZ" in body["symbols"]
