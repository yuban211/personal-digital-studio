#!/usr/bin/env bash
# 安装 easy-tdx。
#
# 关键事实：easy-tdx **没有发布到 PyPI**（pypi.org/pypi/easy-tdx/json 返回 404），
# 所以 `pip install easy_tdx` 装不上，必须从 GitHub 源码装。
#
# 用法：
#   bash scripts/install-easy-tdx.sh              # 从 GitHub 装
#   bash scripts/install-easy-tdx.sh /path/easy_tdx.zip   # 从本地 zip 装（GitHub 受限时）
set -euo pipefail

PY="${PYTHON:-$(command -v python3 || command -v python)}"
PIP_INDEX="${PIP_INDEX:---index-url https://pypi.org/simple}"
SRC="${1:-}"

if [ -n "$SRC" ]; then
    echo "[market] 从本地包安装：$SRC"
    "$PY" -m pip install -q $PIP_INDEX "$SRC"
else
    echo "[market] 从 GitHub 安装 easy-tdx（git+https）"
    "$PY" -m pip install -q $PIP_INDEX "easy-tdx @ git+https://github.com/Justin-ZS/easy_tdx.git"
fi

"$PY" - <<'EOF'
import easy_tdx
print("[market] easy-tdx 安装完成:", easy_tdx.__file__)
EOF
