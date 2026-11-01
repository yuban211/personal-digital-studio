#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# 行情服务一键拉起（在面板控制台执行，输入这一行）：
#
#     sh bash /workspace/market/scripts/bringup.sh
#
# 为什么需要这个脚本：
#   MCSM 面板的「终端」不是真 shell，而是 start.tls.sh 自己 read stdin 实现的
#   命令循环，只认 status / stop / help / sh <命令> 四类。且面板会把多行粘贴
#   合并成一行，所以 heredoc、多行 python 全部无效（实测：整段变成一个
#   「未知命令」）。因此把「探测网络 → 装依赖 → 起服务 → 自检」全部封进
#   这个脚本，外部只需一条单行命令。
#
# 幂等：重复执行安全，已装好的依赖会跳过下载，旧进程会被替换。
# 日志：/workspace/market-bringup.log（本脚本）、/workspace/market/install.log（安装）
#       /workspace/market.log（服务运行）
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=/workspace/market
PY=/workspace/runtime/python/bin/python3
say() { echo "[bringup] $*"; }

[ -d "$ROOT" ] || { say "✗ 目录不存在：$ROOT"; exit 1; }
[ -x "$PY" ]   || { say "✗ 找不到运行时：$PY"; exit 1; }

# ── ① 出网探测：容器能否连通达信节点的 7709 ────────────────
# 这一条是整套方案的生死线：不通的话装了也取不到数据。
say "① 探测通达信节点 7709…"
ok=0
for h in 180.153.18.170 124.71.187.122 115.238.56.198 218.75.126.9 111.229.247.189; do
    if timeout 5 bash -c "exec 3<>/dev/tcp/$h/7709" 2>/dev/null; then
        say "   OK   $h"; ok=$((ok + 1))
    else
        say "   FAIL $h"
    fi
done
if [ "$ok" -eq 0 ]; then
    say "✗ 全部不可达 —— 容器出网被拦 7709。先解决网络，装依赖没有意义。"
    exit 2
fi
say "   可达 $ok/5"

# ── ② 安装依赖（幂等，首次 1-3 分钟）──────────────────────
# 已装好就跳过：这样同一行命令既能首次安装，也能当"快速重启"用。
if [ -f "$ROOT/config.env" ] && "$PY" -c "import fastapi,uvicorn,pandas,duckdb,easy_tdx" >/dev/null 2>&1; then
    say "② 依赖已就绪，跳过安装（强制重装：删掉 $ROOT/config.env 再跑）"
else
    say "② 安装依赖…"
    if ! bash "$ROOT/scripts/install-market.sh" > /workspace/market-bringup.log 2>&1; then
        say "✗ 安装失败，日志末尾："
        tail -25 /workspace/market-bringup.log
        exit 3
    fi
    # 把安装脚本的关键结论回显出来（自检结果就在里面）
    grep -E "可用节点|排名|✓ 自检|⚠ 自检|依赖 OK|easy-tdx OK" /workspace/market-bringup.log | tail -5
fi

# ── ③ 收掉旧进程 ──────────────────────────────────────────
if [ -f /workspace/market.pid ]; then
    old=$(cat /workspace/market.pid 2>/dev/null || echo "")
    if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
        kill -TERM "$old" 2>/dev/null || true
        sleep 1
        kill -9 "$old" 2>/dev/null || true
    fi
    rm -f /workspace/market.pid
fi
# 兜底：按模块名清理（startpage 跑的是 app:app，与 app.api:app 不冲突）
pkill -f "uvicorn app.api:app" 2>/dev/null || true
sleep 1

# ── ④ 启动服务 ────────────────────────────────────────────
[ -f "$ROOT/config.env" ] || { say "✗ 安装脚本没生成 config.env"; exit 4; }
MPORT=$(grep -E '^MARKET_PORT=' "$ROOT/config.env" | head -1 | tr -dc '0-9')
[ -n "$MPORT" ] || MPORT=9111

export PYTHON="$PY"
export MARKET_DATA_DIR="${MARKET_DATA_DIR:-/workspace/market-data}"
mkdir -p "$MARKET_DATA_DIR"
cd "$ROOT" || exit 5
nohup bash scripts/run-api.sh >> /workspace/market.log 2>&1 &
NEWPID=$!
echo "$NEWPID" > /workspace/market.pid
say "③ 服务已拉起 pid=$NEWPID，端口 $MPORT，等待就绪…"

ready=0
for _ in $(seq 1 40); do
    sleep 1
    if curl -fsS --max-time 2 "http://127.0.0.1:$MPORT/healthz" >/dev/null 2>&1; then ready=1; break; fi
    kill -0 "$NEWPID" 2>/dev/null || break
done

if [ "$ready" != "1" ]; then
    say "✗ 服务没起来，日志末尾："
    tail -25 /workspace/market.log
    exit 6
fi
say "   健康检查通过"

# ── ⑤ 取一次真实行情做终检 ────────────────────────────────
say "④ 等待首轮行情（节点测速约 2-5 秒）…"
got=0
for _ in $(seq 1 30); do
    body=$(curl -fsS --max-time 5 "http://127.0.0.1:$MPORT/api/quotes" 2>/dev/null || true)
    case "$body" in
        *'"quotes":['*'"code"'*) got=1; break ;;
    esac
    sleep 2
done
if [ "$got" = "1" ]; then
    say "✓ 取到实时行情："
    echo "$body" | "$PY" -c 'import sys,json;d=json.load(sys.stdin);print("   age=%.2fs" % d.get("age",0));[print("   %s %s %s" % (q.get("code"),q.get("name"),q.get("close"))) for q in d.get("quotes",[])]' 2>/dev/null || echo "   $body" | head -c 300
    say "════ 完成：外网 https://yuban211.duckdns.org:59684/studio/ 应能看到自选行情 ════"
else
    say "⚠ 服务在跑，但 60 秒内没取到行情（节点握手慢或全部被拒）"
    say "   服务会持续重试，稍后刷新页面即可；明细看 /workspace/market.log"
fi
