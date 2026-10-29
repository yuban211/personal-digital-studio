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
