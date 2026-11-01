"""自选池：服务端动态维护的标的清单。

为什么需要它：
  采集器最初把标的写死在 TDX_SYMBOLS 环境变量里，改一只票就得重启服务。
  前端要支持用户自己加/删自选，就必须让采集池变成运行时可变的。

语义约定（避免多设备互相清空）：
  - merge  ：并集，前端启动上报自己的池时用，不会删别人的
  - replace：全量替换，仅在明确知道自己在干什么时用（运维脚本）
  - remove ：显式删除，前端删掉某只票时用

持久化：data_dir/watchlist.json。进程重启后沿用上次的池，
避免开机瞬间"池子空了"导致前端一片空白。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from .symbols import canonical, parse_symbol

log = logging.getLogger("market.watchlist")

#: 上限：免费通达信节点批量报价单批 80 只，200 只已需要 3 批；
#: 再多就该考虑全市场快照而不是逐个拉了。
MAX_SYMBOLS = 200


def normalize(symbols) -> list[str]:
    """统一成 'CODE.MKT' 形式，去重保序。无法识别的直接丢弃。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        sym = canonical(str(raw))
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


class WatchList:
    """线程安全的自选池。

    采集 worker 跑在线程池里（to_thread 调用 _fetch），API 在主线程改池子，
    所以这里必须加锁 —— 否则 worker 可能读到被并发修改的列表。
    """

    def __init__(self, path: Path, defaults: list[str] | None = None) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._items: list[str] = []
        self._load(defaults or [])

    # ── 持久化 ──
    def _load(self, defaults: list[str]) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = normalize(data.get("symbols", []))
            if items:
                self._items = items
                log.info("自选池从磁盘恢复：%d 只", len(items))
                return
        except FileNotFoundError:
            pass
        except Exception as exc:                       # noqa: BLE001
            log.warning("自选池文件损坏，改用默认值：%s", exc)
        self._items = normalize(defaults)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"symbols": self._items}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.path)                     # 原子替换，避免读到半截文件
        except Exception as exc:                      # noqa: BLE001
            log.warning("自选池写入失败（不影响运行）：%s", exc)

    # ── 读 ──
    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._items)

    def pairs(self) -> list[tuple[int, str]]:
        """给采集器用的 (market, code) 列表。"""
        out: list[tuple[int, str]] = []
        for sym in self.snapshot():
            parsed = parse_symbol(sym)
            if parsed:
                out.append(parsed)
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    # ── 写 ──
    def merge(self, symbols) -> list[str]:
        incoming = normalize(symbols)
        with self._lock:
            merged = list(self._items)
            seen = set(merged)
            for sym in incoming:
                if sym in seen:
                    continue
                if len(merged) >= MAX_SYMBOLS:
                    log.warning("自选池已达上限 %d，忽略 %s", MAX_SYMBOLS, sym)
                    break
                merged.append(sym)
                seen.add(sym)
            changed = merged != self._items
            self._items = merged
            if changed:
                self._save()
            return list(self._items)

    def replace(self, symbols) -> list[str]:
        incoming = normalize(symbols)[:MAX_SYMBOLS]
        with self._lock:
            changed = incoming != self._items
            self._items = incoming
            if changed:
                self._save()
            return list(self._items)

    def remove(self, symbols) -> list[str]:
        drop = set(normalize(symbols))
        with self._lock:
            if not drop:
                return list(self._items)
            left = [s for s in self._items if s not in drop]
            changed = left != self._items
            self._items = left
            if changed:
                self._save()
            return list(self._items)
