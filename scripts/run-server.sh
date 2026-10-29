#!/usr/bin/env bash
# ============================================================
#  起始页 V2 —— 服务器启动
# ============================================================
set -euo pipefail

APP_DIR="/workspace/startpage"
PY="/workspace/runtime/python/bin/python3"

cd "$APP_DIR"

if [ -f "$APP_DIR/scripts/startpage.env" ]; then
    set -a
    . "$APP_DIR/scripts/startpage.env"
    set +a
fi

mkdir -p "$DATA_DIR" "$STUDIO_BACKUP_DIR" 2>/dev/null || true

# 依赖自检：缺就装
for m in fastapi uvicorn multipart; do
    if ! "$PY" -c "import $m" >/dev/null 2>&1; then
        echo "[startpage] 安装依赖..."
        "$PY" -m pip install -q -r "$APP_DIR/requirements.txt" \
            -i https://pypi.tuna.tsinghua.edu.cn/simple || {
            echo "[startpage][错误] 依赖安装失败"; exit 1; }
        break
    fi
done

echo "[startpage] 启动 http://${HOST}:${PORT}/"
exec "$PY" -m uvicorn app:app --host "$HOST" --port "$PORT" --workers 1
