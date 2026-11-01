"""模拟盘账户存储：SQLite 单文件。

表少、查询少，不引 ORM。所有写操作用一把锁串行化 ——
同一进程内既有引擎线程又有 HTTP 线程，锁比连接池更简单可靠。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime
from typing import Any

from .settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS acct (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  cash       REAL NOT NULL,
  initial    REAL NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
  code       TEXT PRIMARY KEY,
  name       TEXT NOT NULL DEFAULT '',
  qty        INTEGER NOT NULL DEFAULT 0,
  available  INTEGER NOT NULL DEFAULT 0,
  cost       REAL NOT NULL DEFAULT 0,
  opened_at  TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS orders (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT NOT NULL,
  code   TEXT NOT NULL,
  name   TEXT NOT NULL DEFAULT '',
  side   TEXT NOT NULL,
  qty    INTEGER NOT NULL,
  price  REAL NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  src    TEXT NOT NULL DEFAULT 'strategy'
);

CREATE TABLE IF NOT EXISTS trades (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT NOT NULL,
  code   TEXT NOT NULL,
  name   TEXT NOT NULL DEFAULT '',
  side   TEXT NOT NULL,
  qty    INTEGER NOT NULL,
  price  REAL NOT NULL,
  fee    REAL NOT NULL DEFAULT 0,
  amount REAL NOT NULL DEFAULT 0,
  src    TEXT NOT NULL DEFAULT 'strategy'
);

CREATE TABLE IF NOT EXISTS equity (
  day          TEXT PRIMARY KEY,
  ts           TEXT NOT NULL,
  total        REAL NOT NULL,
  cash         REAL NOT NULL,
  market_value REAL NOT NULL,
  pnl          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS klines (
  code TEXT NOT NULL,
  day  TEXT NOT NULL,
  bars TEXT NOT NULL,
  PRIMARY KEY (code, day)
);

CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
"""


class Store:
    def __init__(self) -> None:
        settings.ensure_dirs()
        self._lock = threading.RLock()
        self.c = sqlite3.connect(str(settings.db_path), timeout=15, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA synchronous=NORMAL")
        self.c.executescript(SCHEMA)
        self._init_acct()
        self.c.commit()

    # ── 账户 ──
    def _init_acct(self) -> None:
        row = self.c.execute("SELECT * FROM acct WHERE id=1").fetchone()
        if row is None:
            now = datetime.now().isoformat(timespec="seconds")
            self.c.execute(
                "INSERT INTO acct(id, cash, initial, created_at, updated_at) VALUES(1,?,?,?,?)",
                (settings.initial_cash, settings.initial_cash, now, now),
            )

    def acct(self) -> dict[str, Any]:
        with self._lock:
            r = dict(self.c.execute("SELECT * FROM acct WHERE id=1").fetchone())
        return r

    def set_cash(self, cash: float) -> None:
        with self._lock:
            self.c.execute(
                "UPDATE acct SET cash=?, updated_at=? WHERE id=1",
                (round(cash, 2), datetime.now().isoformat(timespec="seconds")),
            )
            self.c.commit()

    # ── 持仓 ──
    def positions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.c.execute(
                "SELECT * FROM positions WHERE qty > 0 ORDER BY code")]

    def position(self, code: str) -> dict[str, Any] | None:
        with self._lock:
            r = self.c.execute("SELECT * FROM positions WHERE code=?", (code,)).fetchone()
            return dict(r) if r else None

    def upsert_position(self, code: str, name: str, qty: int,
                        available: int, cost: float) -> None:
        with self._lock:
            now = datetime.now().isoformat(timespec="seconds")
            if qty <= 0:
                self.c.execute("DELETE FROM positions WHERE code=?", (code,))
            else:
                self.c.execute(
                    "INSERT INTO positions(code,name,qty,available,cost,opened_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
                    "name=excluded.name, qty=excluded.qty, available=excluded.available, "
                    "cost=excluded.cost, updated_at=excluded.updated_at",
                    (code, name, qty, available, round(cost, 4), now, now),
                )
            self.c.commit()

    def unlock_all(self) -> int:
        """新交易日：把昨日买入的持仓转为可卖（T+1 解禁）。"""
        with self._lock:
            cur = self.c.execute(
                "UPDATE positions SET available=qty, updated_at=? WHERE available < qty",
                (datetime.now().isoformat(timespec="seconds"),),
            )
            self.c.commit()
            return cur.rowcount

    # ── 委托与成交 ──
    def add_order(self, code: str, name: str, side: str, qty: int,
                  price: float, status: str, reason: str = "", src: str = "strategy") -> int:
        with self._lock:
            cur = self.c.execute(
                "INSERT INTO orders(ts,code,name,side,qty,price,status,reason,src) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), code, name, side,
                 int(qty), round(price, 3), status, reason, src),
            )
            self.c.commit()
            return int(cur.lastrowid)

    def add_trade(self, code: str, name: str, side: str, qty: int,
                  price: float, fee: float, amount: float, src: str = "strategy") -> None:
        with self._lock:
            self.c.execute(
                "INSERT INTO trades(ts,code,name,side,qty,price,fee,amount,src) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), code, name, side,
                 int(qty), round(price, 3), round(fee, 2), round(amount, 2), src),
            )
            self.c.commit()

    def orders(self, limit: int = 60) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.c.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))]

    def trades(self, limit: int = 60) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.c.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))]

    # ── 净值 ──
    def put_equity(self, total: float, cash: float, mv: float, pnl: float) -> None:
        with self._lock:
            now = datetime.now()
            self.c.execute(
                "INSERT INTO equity(day,ts,total,cash,market_value,pnl) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET ts=excluded.ts, total=excluded.total, "
                "cash=excluded.cash, market_value=excluded.market_value, pnl=excluded.pnl",
                (now.strftime("%Y-%m-%d"), now.isoformat(timespec="seconds"),
                 round(total, 2), round(cash, 2), round(mv, 2), round(pnl, 2)),
            )
            self.c.commit()

    def equity_curve(self, limit: int = 250) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(r) for r in self.c.execute(
                "SELECT * FROM equity ORDER BY day DESC LIMIT ?", (limit,))]
        return list(reversed(rows))

    # ── 日线磁盘缓存 ──
    def kline_get(self, code: str, day: str) -> str:
        with self._lock:
            r = self.c.execute(
                "SELECT bars FROM klines WHERE code=? AND day=?", (code, day)).fetchone()
            return r["bars"] if r else ""

    def kline_put(self, code: str, day: str, bars: str) -> None:
        with self._lock:
            self.c.execute(
                "INSERT OR REPLACE INTO klines(code,day,bars) VALUES(?,?,?)",
                (code, day, bars))
            self.c.commit()

    # ── 键值 ──
    def meta_get(self, k: str, default: str = "") -> str:
        with self._lock:
            r = self.c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return r["v"] if r else default

    def meta_set(self, k: str, v: str) -> None:
        with self._lock:
            self.c.execute(
                "INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, str(v)),
            )
            self.c.commit()

    # ── 重置 ──
    def reset(self) -> None:
        with self._lock:
            for t in ("positions", "orders", "trades", "equity"):
                self.c.execute(f"DELETE FROM {t}")
            now = datetime.now().isoformat(timespec="seconds")
            self.c.execute(
                "UPDATE acct SET cash=?, initial=?, created_at=?, updated_at=? WHERE id=1",
                (settings.initial_cash, settings.initial_cash, now, now),
            )
            self.c.commit()

    # ── 交易日 ──
    def maybe_new_day(self) -> bool:
        today = date.today().isoformat()
        last = self.meta_get("last_day", "")
        if last != today:
            self.unlock_all()
            self.meta_set("last_day", today)
            return True
        return False


store = Store()
