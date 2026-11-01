#!/usr/bin/env python3
"""通达信节点延迟实测 —— 部署第一步必跑。

回答一个问题：你的服务器到哪些免费行情节点延迟最低、哪些连不上。
免费节点波动大，先测出可用集合，再决定 worker 数量与轮询间隔。

用法：
    python scripts/probe_tdx.py                 # MAC 协议（默认）
    python scripts/probe_tdx.py --protocol std  # 标准 7709
    python scripts/probe_tdx.py --top 10

输出：延迟排序表 + 可用节点数。退出码 0 表示至少有一个节点可用。
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor


def tcp_ping(host: str, port: int, timeout: float = 3.0) -> tuple[str, float | None, str]:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return host, (time.perf_counter() - start) * 1000, ""
    except Exception as exc:
        return host, None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", choices=["mac", "std"], default="mac")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    try:
        from easy_tdx.transport.sync import MAC_HOSTS, KNOWN_HOSTS
    except ImportError:
        print("easy-tdx 未安装，无法取节点列表", file=sys.stderr)
        return 2

    hosts = list(MAC_HOSTS if args.protocol == "mac" else KNOWN_HOSTS)
    port = args.port or (7709)

    print(f"协议={args.protocol} 端口={port} 节点数={len(hosts)} 并发探测中…")
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda h: tcp_ping(h, port, args.timeout), hosts))

    ok = sorted([(ms, h) for h, ms, _ in results if ms is not None])
    bad = [(h, err) for h, ms, err in results if ms is None]

    print(f"\n可用 {len(ok)} / {len(results)} 个节点，按延迟排序（前 {args.top}）：")
    for ms, h in ok[:args.top]:
        print(f"  {ms:8.1f} ms  {h}")
    if bad:
        print(f"\n连不上 {len(bad)} 个：")
        for h, err in bad[:10]:
            print(f"  {h:>16}  {err}")

    if not ok:
        print("\n结论：当前网络到所有通达信节点均不可达 —— 先解决出网/端口问题，"
              "否则实时行情层无法工作。", file=sys.stderr)
        return 1
    best = ok[0]
    print(f"\n结论：最快节点 {best[1]}（{best[0]:.0f} ms）。"
          f"若最快仍 >200ms，建议把采集器放到离行情节点更近的机器上，API 层另行部署。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
