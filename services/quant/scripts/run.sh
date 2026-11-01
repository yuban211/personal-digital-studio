#!/usr/bin/env bash
# 量化服务一键启动（Linux / 容器）
# 用法：bash services/quant/scripts/run.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"     # services/quant
SERVICES="$(cd "$HERE/.." && pwd)"                          # services
PORT="${QUANT_PORT:-9113}"
HOST="${QUANT_HOST:-0.0.0.0}"
PY="${PYTHON:-python3}"

cd "$SERVICES"
export PYTHONPATH="$SERVICES"
export QUANT_PORT="$PORT"
export QUANT_HOST="$HOST"

"$PY" - <<'PYCHECK'
import importlib, sys
missing = [m for m in ("fastapi", "uvicorn") if importlib.util.find_spec(m) is None]
if missing:
    sys.exit("缺少依赖: " + ", ".join(missing) + "  → pip install -r quant/requirements.txt")
print("依赖检查通过")
PYCHECK

mkdir -p "${QUANT_DATA_DIR:-$HERE/data}"

echo "启动量服务 http://$HOST:$PORT"
exec "$PY" -m uvicorn quant.app.api:app --host "$HOST" --port "$PORT" --log-level info
