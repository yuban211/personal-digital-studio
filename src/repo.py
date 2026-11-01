"""数据访问层：所有 SQL 集中在这里。

为什么集中：
  旧版 SQL 散落在路由函数里，改一个字段要翻遍 main.py。
  现在路由只调方法，SQL 只有这一处，改表结构时不用满世界找。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

SLUG_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = SLUG_RE.sub("-", value).strip("-")
    return value[:80] or "item"


def unique_slug(conn: sqlite3.Connection, table: str, slug: str,
                exclude_id: int | None = None) -> str:
    """生成一个不重复的 slug，冲突时自动加序号。"""
    base, candidate, i = slugify(slug), slugify(slug), 2
    while True:
        if exclude_id:
            found = conn.execute(
                f"SELECT id FROM {table} WHERE slug=? AND id<>?", (candidate, exclude_id)
            ).fetchone()
        else:
            found = conn.execute(
                f"SELECT id FROM {table} WHERE slug=?", (candidate,)
            ).fetchone()
        if not found:
            return candidate
        candidate = f"{base}-{i}"
        i += 1


class SettingsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.c = conn

    def all(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.c.execute("SELECT key,value FROM settings")}

    def get(self, key: str, default: str = "") -> str:
        row = self.c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_many(self, pairs: dict[str, str]) -> None:
        self.c.executemany(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            list(pairs.items()),
        )


class ProjectsRepo:
    #: 允许外部更新的字段白名单（防 SQL 注入，也防误改 created_at）
    FIELDS = ("title", "slug", "summary", "body", "status",
              "url", "repo_url", "featured", "sort_order")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.c = conn

    def list_all(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT * FROM projects "
               "ORDER BY featured DESC, sort_order ASC, updated_at DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.c.execute(sql)]

    def get(self, item_id: int) -> dict[str, Any] | None:
        r = self.c.execute("SELECT * FROM projects WHERE id=?", (item_id,)).fetchone()
        return dict(r) if r else None

    def get_by_slug(self, slug: str) -> dict[str, Any] | None:
        r = self.c.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
        return dict(r) if r else None

    def create(self, data: dict[str, Any]) -> int:
        slug = unique_slug(self.c, "projects", data.get("slug") or data["title"])
        cur = self.c.execute(
            "INSERT INTO projects(title,slug,summary,body,status,url,repo_url,"
            "featured,sort_order) VALUES(?,?,?,?,?,?,?,?,?)",
            (data["title"], slug, data.get("summary", ""), data.get("body", ""),
             data.get("status", "规划中"), data.get("url", ""), data.get("repo_url", ""),
             int(data.get("featured", 0)), int(data.get("sort_order", 0))),
        )
        return int(cur.lastrowid)

    def update(self, item_id: int, data: dict[str, Any]) -> bool:
        cols = {k: v for k, v in data.items() if k in self.FIELDS}
        if not cols:
            return False
        if "slug" in cols:
            cols["slug"] = unique_slug(self.c, "projects", str(cols["slug"]), item_id)
        sets = ", ".join(f"{k}=?" for k in cols)  # 键来自白名单，非用户输入
        sets += ", updated_at=CURRENT_TIMESTAMP"
        cur = self.c.execute(
            f"UPDATE projects SET {sets} WHERE id=?", (*cols.values(), item_id)
        )
        return cur.rowcount > 0

    def delete(self, item_id: int) -> bool:
        return self.c.execute("DELETE FROM projects WHERE id=?", (item_id,)).rowcount > 0


class PostsRepo:
    FIELDS = ("title", "slug", "summary", "body", "tags", "published")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.c = conn

    def list_published(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT * FROM posts WHERE published=1 "
               "ORDER BY published_at DESC, id DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.c.execute(sql)]

    def list_all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.c.execute("SELECT * FROM posts ORDER BY id DESC")]

    def get(self, item_id: int) -> dict[str, Any] | None:
        r = self.c.execute("SELECT * FROM posts WHERE id=?", (item_id,)).fetchone()
        return dict(r) if r else None

    def get_by_slug(self, slug: str) -> dict[str, Any] | None:
        r = self.c.execute("SELECT * FROM posts WHERE slug=?", (slug,)).fetchone()
        return dict(r) if r else None

    def create(self, data: dict[str, Any]) -> int:
        slug = unique_slug(self.c, "posts", data.get("slug") or data["title"])
        cur = self.c.execute(
            "INSERT INTO posts(title,slug,summary,body,tags,published,published_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (data["title"], slug, data.get("summary", ""), data.get("body", ""),
             data.get("tags", ""), int(data.get("published", 0)),
             datetime_now() if data.get("published") else None),
        )
        return int(cur.lastrowid)

    def update(self, item_id: int, data: dict[str, Any]) -> bool:
        cols = {k: v for k, v in data.items() if k in self.FIELDS}
        if not cols:
            return False
        if "slug" in cols:
            cols["slug"] = unique_slug(self.c, "posts", str(cols["slug"]), item_id)
        # 从草稿转为发布时补上发布时间
        if cols.get("published"):
            self.c.execute(
                "UPDATE posts SET published_at=? WHERE id=? AND published_at IS NULL",
                (datetime_now(), item_id),
            )
        sets = ", ".join(f"{k}=?" for k in cols)  # 键来自白名单
        sets += ", updated_at=CURRENT_TIMESTAMP"
        cur = self.c.execute(
            f"UPDATE posts SET {sets} WHERE id=?", (*cols.values(), item_id)
        )
        return cur.rowcount > 0

    def delete(self, item_id: int) -> bool:
        return self.c.execute("DELETE FROM posts WHERE id=?", (item_id,)).rowcount > 0


def datetime_now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════════
# 收藏夹（v2 新增）
# ─────────────────────────────────────────────────────────────────
# 设计取舍：
#   1. 分类与站点分表，站点 category_id 可空 —— 删分类不删站点
#   2. 排序用整数 sort_order，重排时整段重写（量级只有几百，成本可忽略）
#   3. 导入导出走同一份 JSON 结构，前后端共用
# ═══════════════════════════════════════════════════════════════

class CategoryRepo:
    FIELDS = ("name", "icon", "sort_order")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.c = conn

    def list_all(self) -> list[dict[str, Any]]:
        rows = self.c.execute(
            "SELECT id, name, icon, sort_order, created_at FROM bm_categories "
            "ORDER BY sort_order ASC, id ASC"
        )
        return [dict(r) for r in rows]

    def get(self, cid: int) -> dict[str, Any] | None:
        r = self.c.execute("SELECT * FROM bm_categories WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None

    def create(self, data: dict[str, Any]) -> int:
        name = str(data.get("name", "")).strip()[:30] or "新分类"
        icon = str(data.get("icon", ""))[:8]
        nxt = self.c.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM bm_categories"
        ).fetchone()[0]
        cur = self.c.execute(
            "INSERT INTO bm_categories(name, icon, sort_order) VALUES(?,?,?)",
            (name, icon, nxt),
        )
        return int(cur.lastrowid)

    def update(self, cid: int, data: dict[str, Any]) -> bool:
        cols = {k: data[k] for k in self.FIELDS if k in data}
        if "name" in cols:
            cols["name"] = str(cols["name"]).strip()[:30] or "未命名"
        if not cols:
            return False
        sets = ", ".join(f"{k}=?" for k in cols)
        cur = self.c.execute(
            f"UPDATE bm_categories SET {sets} WHERE id=?", (*cols.values(), cid)
        )
        return cur.rowcount > 0

    def delete(self, cid: int, cascade: bool = False) -> bool:
        if cascade:
            self.c.execute("DELETE FROM bookmarks WHERE category_id=?", (cid,))
        return self.c.execute("DELETE FROM bm_categories WHERE id=?", (cid,)).rowcount > 0

    def reorder(self, ids: list[int]) -> int:
        for i, cid in enumerate(ids):
            self.c.execute("UPDATE bm_categories SET sort_order=? WHERE id=?", (i, cid))
        return len(ids)


class BookmarkRepo:
    FIELDS = ("title", "url", "icon", "category_id", "note", "sort_order")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.c = conn

    def list_all(self, category_id: int | None = None) -> list[dict[str, Any]]:
        if category_id is None:
            sql = ("SELECT id, title, url, icon, category_id, note, hits, sort_order, "
                   "created_at, updated_at FROM bookmarks "
                   "ORDER BY category_id IS NULL, sort_order ASC, id ASC")
            rows = self.c.execute(sql)
        else:
            rows = self.c.execute(
                "SELECT id, title, url, icon, category_id, note, hits, sort_order, "
                "created_at, updated_at FROM bookmarks WHERE category_id=? "
                "ORDER BY sort_order ASC, id ASC",
                (category_id,),
            )
        return [dict(r) for r in rows]

    def get(self, bid: int) -> dict[str, Any] | None:
        r = self.c.execute("SELECT * FROM bookmarks WHERE id=?", (bid,)).fetchone()
        return dict(r) if r else None

    def create(self, data: dict[str, Any]) -> int:
        url = normalize_url(str(data.get("url", "")))
        if not url:
            raise ValueError("网址不能为空")
        title = str(data.get("title", "")).strip()[:60] or host_of(url)
        cat = data.get("category_id")
        cat = int(cat) if cat not in (None, "", 0) else None
        nxt = self.c.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM bookmarks "
            "WHERE category_id IS ?", (cat,)
        ).fetchone()[0]
        cur = self.c.execute(
            "INSERT INTO bookmarks(title, url, icon, category_id, note, sort_order) "
            "VALUES(?,?,?,?,?,?)",
            (title, url, str(data.get("icon", ""))[:8], cat,
             str(data.get("note", ""))[:200], nxt),
        )
        return int(cur.lastrowid)

    def update(self, bid: int, data: dict[str, Any]) -> bool:
        cols: dict[str, Any] = {}
        for k in self.FIELDS:
            if k in data:
                cols[k] = data[k]
        if "url" in cols:
            cols["url"] = normalize_url(str(cols["url"]))
        if "title" in cols:
            cols["title"] = str(cols["title"]).strip()[:60]
        if "category_id" in cols:
            v = cols["category_id"]
            cols["category_id"] = int(v) if v not in (None, "", 0) else None
        if not cols:
            return False
        sets = ", ".join(f"{k}=?" for k in cols)
        sets += ", updated_at=CURRENT_TIMESTAMP"
        cur = self.c.execute(
            f"UPDATE bookmarks SET {sets} WHERE id=?", (*cols.values(), bid)
        )
        return cur.rowcount > 0

    def delete(self, bid: int) -> bool:
        return self.c.execute("DELETE FROM bookmarks WHERE id=?", (bid,)).rowcount > 0

    def hit(self, bid: int) -> None:
        self.c.execute(
            "UPDATE bookmarks SET hits=hits+1 WHERE id=?", (bid,)
        )

    def reorder(self, ids: list[int]) -> int:
        for i, bid in enumerate(ids):
            self.c.execute("UPDATE bookmarks SET sort_order=? WHERE id=?", (i, bid))
        return len(ids)

    def move(self, bid: int, category_id: int | None, position: int | None) -> bool:
        """把站点挪到某个分类的指定位置。position 为空则放到末尾。"""
        row = self.get(bid)
        if not row:
            return False
        self.c.execute(
            "UPDATE bookmarks SET category_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (category_id, bid),
        )
        siblings = [b["id"] for b in self.list_all(category_id) if b["id"] != bid]
        pos = len(siblings) if position is None else max(0, min(position, len(siblings)))
        siblings.insert(pos, bid)
        return self.reorder(siblings) > 0

    def export_all(self) -> dict[str, Any]:
        return {"categories": CategoryRepo(self.c).list_all(),
                "bookmarks": self.list_all()}

    def import_all(self, payload: dict[str, Any], replace: bool = False) -> dict[str, int]:
        """导入 JSON。replace=True 时先清空（危险操作，前端要二次确认）。

        分类按名字对齐：同名复用，不同名新建，避免重复导入产生一堆同名分类。
        """
        if replace:
            self.c.execute("DELETE FROM bookmarks")
            self.c.execute("DELETE FROM bm_categories")
        cats = CategoryRepo(self.c)
        name2id = {c["name"]: c["id"] for c in cats.list_all()}
        added_c, added_b = 0, 0
        for c in payload.get("categories", []) or []:
            name = str(c.get("name", "")).strip()[:30]
            if not name or name in name2id:
                continue
            name2id[name] = cats.create({"name": name, "icon": c.get("icon", "")})
            added_c += 1
        for b in payload.get("bookmarks", []) or []:
            url = normalize_url(str(b.get("url", "")))
            if not url:
                continue
            cat_name = b.get("category")
            cat_id = name2id.get(cat_name) if cat_name else None
            if cat_id is None and "category_id" in b:
                cid = b.get("category_id")
                cat_id = int(cid) if cid not in (None, "", 0) else None
            try:
                self.create({"title": b.get("title", ""), "url": url,
                             "icon": b.get("icon", ""), "note": b.get("note", ""),
                             "category_id": cat_id})
                added_b += 1
            except ValueError:
                continue
        return {"categories": added_c, "bookmarks": added_b}


def normalize_url(raw: str) -> str:
    """把用户输入收敛成可点击的 URL。

    常见输入：example.com / https://example.com / www.example.com/path
    javascript: 这类伪协议一律拒绝 —— 收藏是可以被导入的，别开 XSS 口子。
    """
    v = raw.strip()
    if not v:
        return ""
    low = v.lower()
    if low.startswith(("javascript:", "data:", "vbscript:", "file:")):
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", v):
        v = "https://" + v.lstrip("/")
    return v[:2000]


def host_of(url: str) -> str:
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", url)
    return m.group(1).replace("www.", "") if m else url[:60]
