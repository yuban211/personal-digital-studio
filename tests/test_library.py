"""起始页收藏夹 API 测试：分类 / 收藏 / 排序 / 导入导出。

重点守三件事：
  1. 首次启动灌入示例数据，但用户删空后重启不能又塞回来
  2. javascript: 这类伪协议必须被拒（收藏可导入，别开 XSS 口子）
  3. 删分类不能连坐删掉收藏（ON DELETE SET NULL）
"""

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
    cfg.prefix = ""
    cfg.ensure_dirs()
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


class TestHomePage:
    def test_index_is_new_home(self, client):
        r = client.get("/")
        assert r.status_code == 200
        body = r.text
        assert "home/home.css" in body and "home/bookmarks.js" in body
        assert "收藏" in body and "搜索" in body

    def test_home_assets_all_200(self, client):
        for name in ("home.css", "store.js", "bookmarks.js", "home.js", "favicon.svg"):
            r = client.get(f"/home/{name}")
            assert r.status_code == 200, name

    def test_both_prefix_shapes_mount(self, client):
        # 反代剥前缀与不剥前缀两种形态都要能取到资源
        assert client.get("/home/home.css").status_code == 200
        assert client.get("/web/style.css").status_code == 200


class TestLibrary:
    def test_seeded(self, client):
        d = client.get("/api/library").json()
        assert len(d["categories"]) == 4
        assert len(d["bookmarks"]) == 6

    def test_create_update_delete(self, client):
        r = client.post("/api/bookmarks", json={"title": "示例站", "url": "example.com"})
        assert r.status_code == 200
        bid = r.json()["id"]

        assert client.put(f"/api/bookmarks/{bid}", json={"title": "改名", "note": "备注"}).status_code == 200
        row = [b for b in client.get("/api/bookmarks").json()["bookmarks"] if b["id"] == bid][0]
        assert row["title"] == "改名"
        assert row["url"] == "https://example.com"      # 自动补协议
        assert row["note"] == "备注"

        assert client.delete(f"/api/bookmarks/{bid}").status_code == 200
        assert client.delete(f"/api/bookmarks/{bid}").status_code == 404

    def test_reject_pseudo_protocol(self, client):
        r = client.post("/api/bookmarks", json={"title": "x", "url": "javascript:alert(1)"})
        assert r.status_code == 400

    def test_move_between_categories(self, client):
        cats = client.get("/api/categories").json()["categories"]
        target = cats[1]["id"]
        bid = client.get("/api/bookmarks").json()["bookmarks"][0]["id"]
        r = client.post(f"/api/bookmarks/{bid}/move",
                        json={"category_id": target, "position": 0})
        assert r.status_code == 200
        row = [b for b in client.get("/api/bookmarks").json()["bookmarks"] if b["id"] == bid][0]
        assert row["category_id"] == target
        assert row["sort_order"] == 0

    def test_reorder(self, client):
        ids = [b["id"] for b in client.get("/api/bookmarks").json()["bookmarks"]]
        rev = list(reversed(ids))
        assert client.post("/api/bookmarks/reorder", json={"ids": rev}).json()["count"] == len(rev)
        after = [b["id"] for b in client.get("/api/bookmarks").json()["bookmarks"]]
        assert after == rev

    def test_category_delete_keeps_bookmarks(self, client):
        cats = client.get("/api/categories").json()["categories"]
        victim = cats[0]
        owned = [b for b in client.get("/api/bookmarks").json()["bookmarks"]
                 if b["category_id"] == victim["id"]]
        assert owned
        assert client.delete(f"/api/categories/{victim['id']}").status_code == 200
        left = client.get("/api/bookmarks").json()["bookmarks"]
        assert {b["id"] for b in owned} <= {b["id"] for b in left}
        assert all(b["category_id"] is None for b in left if b["id"] in {x["id"] for x in owned})

    def test_category_delete_cascade(self, client):
        cats = client.get("/api/categories").json()["categories"]
        victim = cats[0]
        owned = [b["id"] for b in client.get("/api/bookmarks").json()["bookmarks"]
                 if b["category_id"] == victim["id"]]
        assert client.delete(f"/api/categories/{victim['id']}?cascade=true").status_code == 200
        left = {b["id"] for b in client.get("/api/bookmarks").json()["bookmarks"]}
        assert not (set(owned) & left)

    def test_export_import_roundtrip(self, client):
        data = client.get("/api/library/export").json()
        assert data["bookmarks"] and data["bookmarks"][0]["category"]
        # 导出的东西再导回去：分类按名字对齐，不该产生重复分类
        before = len(client.get("/api/categories").json()["categories"])
        r = client.post("/api/library/import", json=data)
        assert r.status_code == 200
        assert r.json()["categories"] == 0
        assert len(client.get("/api/categories").json()["categories"]) == before

    def test_import_rejects_bad_items_but_keeps_good(self, client):
        payload = {"categories": [{"name": "新分组"}],
                   "bookmarks": [{"title": "坏", "url": "javascript:alert(1)"},
                                 {"title": "好", "url": "ok.com", "category": "新分组"}]}
        stats = client.post("/api/library/import", json=payload).json()
        assert stats["bookmarks"] == 1
        assert stats["categories"] == 1
