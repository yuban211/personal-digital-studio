#!/usr/bin/env bash
# CNEquity 数据湖运维封装：建湖 / 每日增量 / 体检。
#
# 用法：
#   bash scripts/lake.sh init          # 首次建湖（默认 profile，约 3 年全市场，GB 级，耗时以小时计）
#   bash scripts/lake.sh init demo     # 演示湖（5 只票 30 个交易日，约 1 分钟）
#   bash scripts/lake.sh daily         # 每日增量（收盘后跑）
#   bash scripts/lake.sh check         # 体检：新鲜度、覆盖、质量；有 error 时退出码非 0
#   bash scripts/lake.sh doctor        # 离线体检：连通性、数据源可达性
#
# 环境变量：
#   CNE_LAKE_ROOT  数据湖根目录（默认 D:/cnequity，Linux 建议 /data/cnequity）
#   PIP_INDEX      pip 镜像（默认 pypi.org；国内机器可换清华源）
set -euo pipefail

PY="${PYTHON:-$(command -v python3 || command -v python)}"
ROOT_DIR="${CNE_LAKE_ROOT:-/data/cnequity}"
ACTION="${1:-check}"

command -v cne >/dev/null 2>&1 || {
    echo "[market][错误] 没找到 cne 命令，先装：pip install cnequity" >&2
    exit 1
}

case "$ACTION" in
    init)
        mkdir -p "$ROOT_DIR"
        PROFILE="${2:-}"
        if [ -n "$PROFILE" ]; then
            cne init --profile "$PROFILE" --data-root "$ROOT_DIR"
        else
            cne init --data-root "$ROOT_DIR"
        fi
        ;;
    daily)
        cne run daily
        ;;
    check)
        cne check
        ;;
    doctor)
        cne doctor
        ;;
    *)
        echo "用法：$0 {init|daily|check|doctor}" >&2
        exit 2
        ;;
esac

echo "[market] lake $ACTION 完成（root=$ROOT_DIR）"
