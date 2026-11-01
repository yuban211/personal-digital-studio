#!/usr/bin/env bash
# 启动行情 API 服务（同时内嵌采集器）。
# 用法：bash scripts/run-api.sh [config.env]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${1:-$ROOT/config.env}"
if [ -f "$ENV_FILE" ]; then
    set -a
    . "$ENV_FILE"
    set +a
fi

PY="${PYTHON:-$(command -v python3 || command -v python)}"
if [ -z "$PY" ]; then
    echo "[market][错误] 找不到 python3/python" >&2
    exit 1
fi

export MARKET_DATA_DIR="${MARKET_DATA_DIR:-$ROOT/data}"
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
export PYTHONUNBUFFERED=1

mkdir -p "$MARKET_DATA_DIR"

echo "[market] 启动 API：http://${MARKET_HOST:-127.0.0.1}:${MARKET_PORT:-9110}/  (docs: /docs)"
exec "$PY" -m uvicorn app.api:app \
    --host "${MARKET_HOST:-127.0.0.1}" \
    --port "${MARKET_PORT:-9110}" \
    --log-level "${UVICORN_LOG_LEVEL:-info}"
