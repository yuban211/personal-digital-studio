"""数据库连接与 schema 迁移。

设计要点：
  1. 只用标准库 sqlite3，不引 ORM —— 依赖越少越好（这是重写的核心目标）
  2. WAL 模式 + foreign_keys=ON，每次连接都设置（SQLite 不记全局）
  3. 带版本号迁移：改表结构只加不删，旧库自动升级，不需要手工导数据
  4. 备份/恢复是一等公民：数据比代码重要

为什么不用 ORM：
  这个项目只有 3 张表、不到 20 个查询，ORM 带来的抽象成本
  大于收益，而且会引入十几个传递依赖。
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

#: 当前 schema 版本。改动表结构时必须 +1 并在 MIGRATIONS 里追加。
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  title      TEXT NOT NULL,
  slug       TEXT NOT NULL UNIQUE,
  summary    TEXT NOT NULL DEFAULT '',
  body       TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT '规划中',
  url        TEXT NOT NULL DEFAULT '',
  repo_url   TEXT NOT NULL DEFAULT '',
  featured   INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  title        TEXT NOT NULL,
  slug         TEXT NOT NULL UNIQUE,
  summary      TEXT NOT NULL DEFAULT '',
  body         TEXT NOT NULL DEFAULT '',
  tags         TEXT NOT NULL DEFAULT '',
  published    INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  published_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_posts_published
  ON posts(published, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_projects_sort
  ON projects(featured DESC, sort_order ASC, updated_at DESC);
"""

#: 版本 1 → 2 的升级脚本。目前为空，仅示范结构。
MIGRATIONS: dict[int, str] = {}

DEFAULT_SETTINGS = {
    "site_title": "高冷的网友 208 · 个人数字工作室",
    "signature": "高冷的网友 208",
    "tagline": "个人数字工作室 · 独立项目与技术探索",
    "about": "这里记录独立项目、技术实验与过程笔记。",
    "contact": "",
    "motion_intensity": "immersive",
    "now": "## 正在做\n\n把个人数字工作室完善起来。",
}


def connect(path: Path) -> sqlite3.Connection:
    """建立连接并应用必要的 PRAGMA。

    ★ check_same_thread=False 是必需的：
      uvicorn 会在不同线程处理请求，而我们的连接是"每请求一个"，
      实测 POST 与 GET 落在不同线程时报
      "SQLite objects created in a thread can only be used in that same thread"。
      连接本身不跨线程共享（每请求新建），所以关闭这个检查是安全的。
    """
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # SQLite 的这些设置是连接级的，每次都要设
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def open_db(path: Path):
    """上下文管理器：自动提交/回滚并关闭连接。"""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def migrate(conn: sqlite3.Connection) -> int:
    """把数据库升级到 SCHEMA_VERSION，返回应用的迁移数。"""
    conn.executescript(SCHEMA)
    version = current_version(conn)
    applied = 0
    while version < SCHEMA_VERSION:
        nxt = version + 1
        script = MIGRATIONS.get(nxt, "")
        if script:
            conn.executescript(script)
        conn.execute(f"PRAGMA user_version={nxt}")
        version = nxt
        applied += 1
    return applied


def init_db(path: Path) -> None:
    """建表 + 迁移 + 写入默认设置。启动时调用一次。"""
    with open_db(path) as conn:
        migrate(conn)
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v)
            )


def backup(path: Path, backup_dir: Path) -> Path | None:
    """把数据库复制一份到备份目录，文件名带时间戳。

    用 SQLite 官方的 backup API 而不是直接 copy 文件：
    WAL 模式下 db 文件可能处于中间状态，直接复制会拿到坏库。
    """
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"studio-{datetime.now():%Y%m%d-%H%M%S}.db"
    with open_db(path) as src:
        dst = connect(dest)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    return dest


def restore(backup_file: Path, target: Path) -> bool:
    """用一份备份覆盖当前数据库。调用前请先自行备份当前数据。"""
    src = Path(backup_file)
    if not src.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    # 先拷贝到临时文件再原子替换，避免中途失败把现有库弄坏
    tmp = target.with_suffix(".db.restore-tmp")
    shutil.copy2(str(src), str(tmp))
    tmp.replace(target)
    # WAL 附属文件要清掉，否则会和新主库不匹配
    for suffix in ("-wal", "-shm"):
        side = Path(str(target) + suffix)
        if side.exists():
            try:
                side.unlink()
            except OSError:
                pass
    return True
