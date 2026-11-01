"""本地联调代理：模拟线上 Caddy 的挂载方式。

线上结构：
    /studio/*        → startpage 应用（静态前端）
    /studio/market/* → 行情服务（剥掉前缀后转给 127.0.0.1:9111）

本地没有 Caddy，直接开两个进程 + 这个代理，就能在浏览器里按线上路径验证。
只用标准库，不引依赖。

用法：
    python scripts/dev-proxy.py --port 9090 --market http://127.0.0.1:9110
    然后访问 http://127.0.0.1:9090/studio/
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "src" / "static" / "web"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}

MARKET = "http://127.0.0.1:9110"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):        # 静默，避免刷屏
        pass

    # ── 静态 ──
    def _serve_file(self, fs_path: Path) -> None:
        if not fs_path.is_file():
            self.send_error(404, "not found")
            return
        data = fs_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(fs_path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        # 与线上一致的 CSP，提前暴露被拦的请求
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self' https://api.open-meteo.com "
            "https://geocoding-api.open-meteo.com; object-src 'none'; base-uri 'self'")
        self.end_headers()
        self.wfile.write(data)

    # ── 代理 ──
    def _proxy(self, rest: str, body: bytes | None = None) -> None:
        target = MARKET.rstrip("/") + "/" + rest.lstrip("/")
        req = urllib.request.Request(target, data=body, method=self.command)
        for key in ("Content-Type", "Accept"):
            if self.headers.get(key):
                req.add_header(key, self.headers[key])
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as exc:      # 503/401 这类要原样透出
            payload = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:                   # noqa: BLE001
            msg = f'{{"error":"upstream unreachable: {exc}"}}'.encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def do_GET(self):                              # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/studio/market/"):
            return self._proxy(self.path[len("/studio/market/"):])
        if path in ("/studio", "/studio/"):
            return self._serve_file(WEB / "index.html")
        if path.startswith("/studio/web/"):
            return self._serve_file(WEB / path[len("/studio/web/"):])
        if path == "/studio/healthz":
            body = b'{"ok":true,"dev":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        self.send_error(404, "not found")

    def do_POST(self):                             # noqa: N802
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        if path.startswith("/studio/market/"):
            return self._proxy(self.path[len("/studio/market/"):], body)
        self.send_error(404, "not found")


def main() -> None:
    global MARKET
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9090)
    ap.add_argument("--market", default=MARKET)
    args = ap.parse_args()
    MARKET = args.market

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"dev proxy: http://127.0.0.1:{args.port}/studio/  →  market {MARKET}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
