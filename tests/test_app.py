"""V2 应用测试：起真实 FastAPI，验证页面与 API。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ["APP_SECRET"] = "test-secret"
os.environ["COOKIE_SECURE"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import create_app  # noqa: E402
from config import Config  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    cfg = Config()
    cfg.db_path = tmp_path / "v2.db"
    cfg.media_dir = tmp_path / "media"
    cfg.admin_file = tmp_path / "admin.json"
    cfg.backup_dir = tmp_path / "backups"
    cfg.sync_token = ""
    cfg.cookie_secure = False
    cfg.ensure_dirs()
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c, cfg


class TestPages:
    def test_index_served(self, client):
        c, cfg = client
        r = c.get("/")
        assert r.status_code == 200
        assert "搜索" in r.text or "searchInput" in r.text

    def test_index_at_prefix(self, client):
        c, cfg = client
        assert c.get(cfg.prefix + "/").status_code == 200

    def test_healthz(self, client):
        c, cfg = client
        d = c.get(cfg.prefix + "/healthz").json()
        assert d["ok"] is True

    def test_static_assets(self, client):
        c, cfg = client
        for f in ("web/style.css", "web/app.js", "web/config.js", "web/search.js"):
            assert c.get("/" + f).status_code == 200, f

    def test_security_headers(self, client):
        c, _ = client
        r = c.get("/")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert "Content-Security-Policy" in r.headers

    def test_no_external_resources_in_page(self, client):
        """★ 本机 CDN 不可达，页面不得引用外部资源。"""
        import re
        c, _ = client
        html = c.get("/").text
        ext = re.findall(r'(?:href|src)="(https?://[^"]+)"', html)
        assert not ext, f"页面引用了外部资源: {ext}"


class TestConfigApi:
    def test_save_and_read(self, client):
        c, cfg = client
        r = c.post(cfg.prefix + "/api/config", json={"engine": "google"})
        assert r.json()["ok"] is True
        got = c.get(cfg.prefix + "/api/config").json()
        assert got["config"]["engine"] == "google"

    def test_rejects_non_object(self, client):
        c, cfg = client
        r = c.post(cfg.prefix + "/api/config", json=["not", "object"])
        assert r.status_code == 400

    def test_backup_endpoint(self, client):
        c, cfg = client
        c.post(cfg.prefix + "/api/config", json={"a": "1"})
        d = c.get(cfg.prefix + "/api/backup").json()
        assert d["ok"] is True and "a" in d["config"]


class TestSyncToken:
    def test_no_token_means_open(self, client):
        """不设 SYNC_TOKEN 时完全开放（单人自用）。"""
        c, cfg = client
        assert cfg.sync_token == ""
        assert c.get(cfg.prefix + "/api/config").status_code == 200

    def test_token_required_when_set(self, tmp_path):
        cfg = Config()
        cfg.db_path = tmp_path / "t.db"
        cfg.sync_token = "my-secret-token"
        cfg.ensure_dirs()
        app = create_app(cfg)
        with TestClient(app) as c:
            # 不带令牌 → 401
            assert c.get(cfg.prefix + "/api/config").status_code == 401
            # 带错令牌 → 401
            r = c.get(cfg.prefix + "/api/config",
                      headers={"X-Sync-Token": "wrong"})
            assert r.status_code == 401
            # 带对令牌 → 200
            r = c.get(cfg.prefix + "/api/config",
                      headers={"X-Sync-Token": "my-secret-token"})
            assert r.status_code == 200
