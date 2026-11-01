#!/usr/bin/env bash
# 打包上线产物：先自检静态资源，再打 tar.gz，最后给校验和。
# 用法：bash scripts/make-package.sh [输出路径]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/dist/startpage-v2.tar.gz}"

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
    echo "[package][错误] 找不到 python3/python，无法做静态资源自检" >&2
    exit 1
fi

echo "[package] 1/3 静态资源自检"
"$PY" "$ROOT/scripts/verify-static.py"

echo "[package] 2/3 打包"
mkdir -p "$(dirname "$OUT")"
tar czf "$OUT" \
    -C "$ROOT" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    src scripts requirements.txt

echo "[package] 3/3 校验和"
SIZE="$(du -h "$OUT" | cut -f1)"
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$OUT"
else
    "$PY" -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()+'  '+sys.argv[1])" "$OUT"
fi
echo "[package] 完成：$OUT（$SIZE）"
echo
echo "上传后在服务器上执行："
echo "  mkdir -p /workspace/startpage && tar xzf $(basename "$OUT") -C /workspace/startpage"
echo "  chmod +x /workspace/startpage/scripts/run-server.sh"
