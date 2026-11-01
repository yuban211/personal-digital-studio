#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# 行情服务一键安装（在服务器 console 里执行）
#   bash /workspace/market/scripts/install-market.sh
#
# 设计原则：
#   1. 幂等 —— 重复跑不会坏事，已装好的跳过
#   2. 可诊断 —— 每一步都打日志，失败时明确告诉你卡在哪
#   3. 不假设网络 —— pip 源依次探测（清华/阿里/腾讯/官方），全挂就用本地 wheel
#
# 依赖说明：
#   fastapi/uvicorn/pandas/duckdb 都是 manylinux 预编译 wheel，不需要编译工具链。
#   easy-tdx 没有发布到 PyPI，必须从源码装；脚本优先用随包自带的 wheel，
#   避免服务器连不上 GitHub。
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

MARKET_DIR="${MARKET_DIR:-/workspace/market}"
VENV="$MARKET_DIR/venv"
LOG="$MARKET_DIR/install.log"
WHEELS="$MARKET_DIR/wheels"

mkdir -p "$MARKET_DIR"
: > "$LOG"

log()  { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
fail() { log "✗ 失败：$*"; log "完整日志：$LOG"; exit 1; }

log "════ 行情服务安装开始 ════"
log "目标目录：$MARKET_DIR"

# ── 1. 环境体检 ──────────────────────────────────────────────
# 优先用 Pterodactyl 自带的独立运行时：start.tls.sh 就是用它拉起 startpage 的，
# 装到别处会导致"装好了但启动脚本用的是另一个 python"这种最难查的问题。
RUNTIME_PY="/workspace/runtime/python/bin/python3"
PY=""
if [ -x "$RUNTIME_PY" ]; then
    PY="$RUNTIME_PY"
    log "使用容器内置运行时：$RUNTIME_PY"
else
    for c in python3 python3.12 python3.11 python3.10 python; do
        if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
    done
fi
[ -n "$PY" ] || fail "找不到 python3，这台机器不满足运行条件"

PYVER=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
log "python：$($PY -V 2>&1)  路径 $(command -v "$PY")"

if ! "$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)'; then
    fail "需要 Python ≥ 3.10（easy_tdx 要求），当前 $PYVER。"
fi

log "磁盘：$(df -h "$MARKET_DIR" | tail -1 | awk '{print $4" 可用"}')"
log "内存：$(free -m 2>/dev/null | awk '/Mem:/{print $7" MB 可用"}' || echo '未知')"

# ── 2. 决定装到哪 ────────────────────────────────────────────
# 用内置运行时就直装它的 site-packages（与 start.tls.sh 完全一致）；
# 只有系统 python 时才建 venv，避免污染系统环境。
VPY="$PY"
VPIP="$PY -m pip"
if [ "$PY" = "$RUNTIME_PY" ]; then
    log "直接安装到内置运行时的 site-packages（与启动脚本一致）"
elif [ -x "$VENV/bin/python" ]; then
    log "venv 已存在，复用"
    VPY="$VENV/bin/python"; VPIP="$VPY -m pip"
else
    log "创建 venv…"
    if "$PY" -m venv "$VENV" >>"$LOG" 2>&1; then
        VPY="$VENV/bin/python"; VPIP="$VPY -m pip"
    else
        log "venv 创建失败，退回 --user 安装"
        VPIP="$PY -m pip --user"
    fi
fi

# ── 3. 探测可用 pip 源 ──────────────────────────────────────
log "探测 pip 源…"
# 用 HTTP 探活而不是 pip install 探活：升级 pip 有把现有环境搞坏的风险，
# 而这台机器上还有 startpage 在跑，不能冒险。
INDEX=""
for src in \
    "https://pypi.tuna.tsinghua.edu.cn/simple" \
    "https://mirrors.aliyun.com/pypi/simple" \
    "https://mirrors.cloud.tencent.com/pypi/simple" \
    "https://pypi.org/simple"
do
    if "$VPY" -c "import sys,urllib.request;urllib.request.urlopen(sys.argv[1],timeout=8).read(200)" \
        "$src/pip/" >>"$LOG" 2>&1; then
        INDEX="$src"; log "  可用源：$src"; break
    fi
    log "  不可用：$src"
done
[ -n "$INDEX" ] || fail "所有 pip 源都不可达。这台机器无法在线安装依赖，需要改用离线 wheel 包部署。"

# ── 4. 装依赖 ───────────────────────────────────────────────
log "安装运行时依赖（fastapi/uvicorn/pandas/duckdb）…"
if ! $VPIP install -q --disable-pip-version-check --index-url "$INDEX" \
    --retries 2 --timeout 20 \
    "fastapi>=0.110" "uvicorn[standard]>=0.29" "duckdb>=1.0" "pandas>=2.0" \
    "tzdata>=2024.1" "click>=8.0" >>"$LOG" 2>&1; then
    log "  依赖安装失败，细节见 $LOG"
    tail -15 "$LOG"
    fail "依赖安装失败"
fi
log "  依赖 OK"

# ── 5. easy-tdx（PyPI 上没有，必须单独装）──────────────────
log "安装 easy-tdx…"
ETDX_WHEEL=$(ls "$WHEELS"/easy_tdx-*.whl 2>/dev/null | head -1)
if [ -n "$ETDX_WHEEL" ]; then
    log "  使用随包 wheel：$(basename "$ETDX_WHEEL")"
    $VPIP install -q --disable-pip-version-check --no-deps "$ETDX_WHEEL" >>"$LOG" 2>&1 \
        && log "  easy-tdx OK（本地 wheel）" \
        || fail "本地 wheel 安装失败"
else
    log "  未找到本地 wheel，尝试 GitHub 源码"
    if $VPIP install -q --disable-pip-version-check --index-url "$INDEX" \
        "easy-tdx @ git+https://github.com/Justin-ZS/easy_tdx.git" >>"$LOG" 2>&1; then
        log "  easy-tdx OK（GitHub）"
    else
        fail "easy-tdx 装不上：既没有本地 wheel，也连不上 GitHub。请把 easy_tdx-*.whl 放到 $WHEELS/"
    fi
fi

# ── 6. 验证导入 ─────────────────────────────────────────────
log "验证依赖可导入…"
$VPY - <<'PY' >>"$LOG" 2>&1
import sys
import fastapi, uvicorn, duckdb, pandas
import easy_tdx
from easy_tdx import MacClient
print("fastapi", fastapi.__version__)
print("pandas", pandas.__version__)
print("duckdb", duckdb.__version__)
print("easy_tdx", getattr(easy_tdx, "__version__", "?"))
PY
if [ $? -ne 0 ]; then
    log "导入失败细节："
    tail -20 "$LOG"
    fail "依赖装上了但导入失败"
fi
log "  导入 OK"

# ── 7. 写运行配置（已存在则不覆盖）──────────────────────────
CONF="$MARKET_DIR/config.env"
if [ -f "$CONF" ]; then
    log "配置已存在，保留：$CONF"
else
    log "生成 config.env"
    cat > "$CONF" <<'EOF'
# 行情数据服务运行配置（由 install-market.sh 生成）
MARKET_HOST="127.0.0.1"
MARKET_PORT="9111"
MARKET_BUS="auto"

# 初始自选池；前端启动后会用自己的列表覆盖（merge 语义）
TDX_SYMBOLS="600519.SH,000858.SZ,300750.SZ"
TDX_WORKERS="2"
TDX_POLL_INTERVAL="3"
TDX_PROTOCOL="mac"
TDX_BATCH_SIZE="80"

# 数据湖：暂时留空（没有 cne init 就禁用历史接口，实时行情不受影响）
CNE_LAKE_ROOT=""

# 不鉴权：只监听 127.0.0.1，外部经 Caddy 同源反代进来
API_TOKEN=""
MARKET_STALE_AFTER="20"
CORS_ORIGINS="*"
LOG_LEVEL="INFO"
EOF
fi

# ── 8. 预跑节点测速（生成缓存，让首次请求就不用等）────────
log "预跑节点测速（约 5-15 秒）…"
( cd "$MARKET_DIR" && set -a && . "$CONF" && set +a && \
  MARKET_DATA_DIR="$MARKET_DIR/data" "$VPY" - <<'PY'
import os, sys, time
sys.path.insert(0, os.getcwd())
from pathlib import Path
from app.settings import load_settings
from app.collector import HostRanker, _candidate_hosts
s = load_settings()
r = HostRanker(_candidate_hosts(s), Path(s.data_dir), ttl=s.host_ttl, top=s.host_top)
def validate(host):
    from easy_tdx import MacClient, Market
    try:
        c = MacClient(host, timeout=4.0)
        try:
            df = c.get_stock_quotes([(Market.SH, "600519")])
            return df is not None and len(df) > 0
        finally:
            c.close()
    except Exception:
        return False
t0 = time.time()
ranked = r.probe(1.5, validate)
print(f"可用节点 {len(ranked)} 个，耗时 {time.time()-t0:.1f}s")
print("排名：", ranked[:5])
PY
) >>"$LOG" 2>&1
log "$(tail -2 "$LOG" | head -1)"

# ── 9. 启动自检 ─────────────────────────────────────────────
log "启动自检（前台跑 12 秒，看能否出数据）…"
( cd "$MARKET_DIR" && set -a && . "$CONF" && set +a && \
  MARKET_PORT=9112 MARKET_DATA_DIR="$MARKET_DIR/data" \
  timeout 12 "$VPY" -m uvicorn app.api:app --host 127.0.0.1 --port 9112 \
  --log-level warning >>"$LOG" 2>&1 ) &
BOOTPID=$!
sleep 8
SELFTEST=$(curl -s -m 5 --noproxy '*' http://127.0.0.1:9112/api/quotes 2>/dev/null | head -c 300)
wait $BOOTPID 2>/dev/null

if echo "$SELFTEST" | grep -q '"ok":true'; then
    log "✓ 自检通过，已取到实时行情"
    log "  样本：$(echo "$SELFTEST" | head -c 200)"
else
    log "⚠ 自检未取到行情（可能节点握手慢，或本机禁止访问通达信节点）"
    log "  返回：${SELFTEST:-空}"
    log "  这不一定是致命问题，服务起得来后仍会持续重试"
fi

log "════ 安装完成 ════"
log "启动：bash $MARKET_DIR/scripts/run-api.sh"
log "配置：$CONF"
log "日志：$LOG"
