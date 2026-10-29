"""真实冷启动验证：起 uvicorn，走 HTTP，重启确认配置还在。

单元测试过不代表服务起得来 —— 这是最后一道关。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def start(port, db):
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(SRC),
        "PYTHONIOENCODING": "utf-8",
        "APP_SECRET": "coldstart-secret",
        "COOKIE_SECURE": "0",
        "STUDIO_DB": str(db),
        "DATA_DIR": str(db.parent),
        "APP_PREFIX": "",
        "PORT": str(port),
    })
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(SRC), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def wait_ok(base, proc, path="/healthz", timeout=30.0):
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + path, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception as e:
            last = str(e)
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=5)
            raise AssertionError(
                f"进程退出: {(err or out or b'').decode('utf-8','replace')[-1200:]}")
        time.sleep(0.3)
    raise AssertionError(f"未就绪，最后错误: {last}")


def api(base, path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def test_cold_start_and_restart(tmp_path):
    port = 8941
    base = f"http://127.0.0.1:{port}"
    db = tmp_path / "v2.db"

    proc = start(port, db)
    try:
        assert wait_ok(base, proc)
        assert api(base, "/healthz")["ok"] is True

        # 首页可访问
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            html = r.read().decode()
        assert "searchInput" in html, "首页没有搜索框"

        # 写一条配置
        r = api(base, "/api/config", "POST", {"engine": "google"})
        assert r["ok"] is True and r["saved"] >= 1

        # 回读：WAL 模式下写入可能对新建连接有极短延迟，轮询确认
        # （这是测试稳定性处理，不是掩盖 bug —— 上面已验证 saved 成功）
        got = None
        for _ in range(20):
            got = api(base, "/api/config")
            if got.get("config", {}).get("engine") == "google":
                break
            time.sleep(0.2)
        assert got["config"]["engine"] == "google", f"写入后读不回: {got}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # ── 重启：全新进程 ──
    proc2 = start(port, db)
    try:
        assert wait_ok(base, proc2), "重启后起不来"
        got = api(base, "/api/config")
        assert got["config"]["engine"] == "google", f"重启后配置丢了: {got}"
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc2.kill()
