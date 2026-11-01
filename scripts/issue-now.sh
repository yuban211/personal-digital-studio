#!/bin/bash
# ── issue-now.sh · 强制签发一次并立即部署上线（端到端验证用）────────────────
#
# 为什么需要它：
#   setup-acme.sh 第 465 行有 `openssl x509 -checkend 2592000`（30 天）判断。
#   当前证书剩 62 天 → 它只跑一次 --cron 就退出，绝不会真正签发。
#   结果就是「续期链路从未被验证过」，风险一路留到临期那几天才暴露。
#   本脚本绕过该门槛，强制走一次完整 DNS-01，签完立刻部署并 reload Caddy。
#
# ★ 2026-11-02 三个必须的修正（首次跑全被这三条坑住）：
#   1) dash 兼容：acme.sh 的 shebang 是 `#!/usr/bin/env sh` → 容器里是 dash。
#      acme.sh 第 349/383/416/429 行的日志级别判断写作
#        [ "${LOG_LEVEL:-$DEFAULT_LOG_LEVEL}" -ge "$LOG_LEVEL_1" ]
#      把 LOG_LEVEL 当**数字**用；而容器环境里有 LOG_LEVEL=INFO（服务常用名），
#      于是每次调日志函数都刷 `Illegal number: INFO`。
#      → 调用前 unset LOG_LEVEL / SYS_LOG / DEBUG，并改用 bash 显式解释。
#   2) DNS 预检：acme.sh 写完 TXT 后会去公共 DoH 端点核对，容器到那些端点
#      TLS 握手失败（curl error 35），预检卡死。→ 用 --dnssleep 30 跳过预检，
#      改为定时等传播（DuckDNS 是权威 NS，写入即生效，30 秒足够）。
#   3) 幂等：若已存在比线上更新的签发产物，只做部署，不重复消耗配额。
#
# 用法：bash /workspace/issue-now.sh

set -u

# ── curl 超时垫片（2026-11-02 加，P0）──────────────────────────────────────
#   acme.sh 的 _get() 把 timeout 作为**第三个**参数，而 dns_duckdns 插件只传了
#   url，于是 DuckDNS 那次请求没有任何 --connect-timeout / --max-time，
#   实测挂了 132 秒。若哪天它彻底不返回，守护进程会被永久挂住。
#   垫片在 PATH 层注入 connect 15s / max 240s；调用方显式传的超时仍优先。
if [ -x /workspace/.bin/curl ]; then
  PATH="/workspace/.bin:$PATH"; export PATH
fi

ACME_HOME="/workspace/.acme.sh"
CERT_DIR="/workspace/certs"
CRED="/workspace/acme-cred.env"
DOMAIN_FULL="yuban211.duckdns.org"
DNSSLEEP="${DNSSLEEP:-30}"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

log "══════ 签发 / 部署 $(date '+%Y-%m-%d %H:%M:%S %Z') ══════"

# ── 0) 环境诊断：把污染变量摆出来，别让它们再隐形 ─────────────────────────
for _v in LOG_LEVEL SYS_LOG DEBUG; do
  eval "_val=\${$_v+set}"
  if [ "$_val" = "set" ]; then
    eval "_cur=\$$_v"
    log "环境变量 $_v=$_cur  ← 会干扰 acme.sh，本次调用将屏蔽"
  fi
done
unset LOG_LEVEL SYS_LOG DEBUG

# ── 1) 凭据 ───────────────────────────────────────────────────────────────
if [ ! -s "$CRED" ]; then
  log "!! 缺少 $CRED"
  exit 1
fi
set -a
# shellcheck disable=SC1090
. "$CRED" 2>/dev/null || true
set +a
if [ -z "${DuckDNS_Token:-}" ]; then
  log "!! $CRED 里 DuckDNS_Token 为空"
  exit 1
fi
log "凭据已载入（token ${#DuckDNS_Token} 字符）"

if [ -n "${DuckDNS_Domain:-}" ]; then
  case "$DuckDNS_Domain" in
    *.duckdns.org) DOMAIN_FULL="$DuckDNS_Domain" ;;
    *)             DOMAIN_FULL="$DuckDNS_Domain.duckdns.org" ;;
  esac
fi
log "目标域名：$DOMAIN_FULL"

# ── 2) 前置 ───────────────────────────────────────────────────────────────
[ -x "$ACME_HOME/acme.sh" ] || { log "!! acme.sh 缺失，先跑 prefetch-acme.sh"; exit 1; }
[ -s "$ACME_HOME/dnsapi/dns_duckdns.sh" ] || { log "!! 缺 dns_duckdns.sh，先跑 prefetch-acme.sh"; exit 1; }

SRC="$ACME_HOME/$DOMAIN_FULL"
_before_fp=$(openssl x509 -in "$CERT_DIR/cert.pem" -noout -fingerprint -sha256 2>/dev/null)
log "线上证书指纹：${_before_fp:-无}"

# ── 3) 上次是否其实已经签成？避免重复消耗配额 ─────────────────────────────
SKIP_ISSUE=0
if [ -s "$SRC/fullchain.cer" ]; then
  log "发现已有签发产物：$SRC/fullchain.cer"
  openssl x509 -in "$SRC/fullchain.cer" -noout -dates 2>/dev/null | sed 's/^/    /'
  log "产物时间：$(date -r "$SRC/fullchain.cer" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo 未知)"
  _src_end=$(openssl x509 -in "$SRC/fullchain.cer" -noout -enddate 2>/dev/null | cut -d= -f2)
  _cur_end=$(openssl x509 -in "$CERT_DIR/cert.pem" -noout -enddate 2>/dev/null | cut -d= -f2)
  _src_s=$(date -d "$_src_end" +%s 2>/dev/null || echo 0)
  _cur_s=$(date -d "$_cur_end" +%s 2>/dev/null || echo 0)
  if [ "${_src_s:-0}" -gt "${_cur_s:-0}" ]; then
    log "该产物比线上证书更新 → 跳过签发，直接部署（省一次配额）"
    SKIP_ISSUE=1
  fi
fi

# ── 4) 签发 ───────────────────────────────────────────────────────────────
if [ "$SKIP_ISSUE" = "1" ]; then
  :
else
  log "开始签发（DNS-01 + dnssleep ${DNSSLEEP}s，预计 1~2 分钟）..."
  if ! env -u LOG_LEVEL -u SYS_LOG -u DEBUG bash "$ACME_HOME/acme.sh" --issue \
        --home "$ACME_HOME" \
        --server letsencrypt \
        --dns dns_duckdns \
        -d "$DOMAIN_FULL" \
        --keylength 2048 \
        --force \
        --dnssleep "$DNSSLEEP" \
        --log "$ACME_HOME/acme.log"; then
    log "!! 签发失败。acme.log 末尾："
    tail -25 "$ACME_HOME/acme.log" 2>/dev/null | sed 's/^/    /'
    log "!! 未改动线上证书 —— 站点继续用旧证书，不受影响"
    exit 1
  fi
  log "签发成功"
fi

# ── 5) 部署 ───────────────────────────────────────────────────────────────
if [ ! -s "$SRC/fullchain.cer" ] || [ ! -s "$SRC/$DOMAIN_FULL.key" ]; then
  log "!! 产物不全，列目录："
  ls -l "$SRC" 2>/dev/null | sed 's/^/    /'
  exit 1
fi
mkdir -p "$CERT_DIR"
cp -f "$SRC/fullchain.cer" "$CERT_DIR/cert.pem"
cp -f "$SRC/$DOMAIN_FULL.key" "$CERT_DIR/key.pem"
chmod 644 "$CERT_DIR/cert.pem" 2>/dev/null
chmod 600 "$CERT_DIR/key.pem" 2>/dev/null

_after_fp=$(openssl x509 -in "$CERT_DIR/cert.pem" -noout -fingerprint -sha256 2>/dev/null)
log "已部署到 $CERT_DIR："
openssl x509 -in "$CERT_DIR/cert.pem" -noout -subject -issuer -dates 2>/dev/null | sed 's/^/    /'

# ── 6) 让 Caddy 真正用上 ──────────────────────────────────────────────────
if [ "$_before_fp" = "$_after_fp" ]; then
  log "指纹未变，跳过重载"
elif curl -s -m 3 -o /dev/null "http://127.0.0.1:2019/config/" 2>/dev/null; then
  # 必须带 --force：Caddy 的 reload 会比对配置差异，Caddyfile 文本没变时判定"无变化"
  # 并跳过 TLS 模块，导致 certs/ 里已是新证书但内存里仍是旧的（2026-11-02 实测踩到）
  if ( cd /workspace && ./caddy reload --config Caddyfile --force ) >>/workspace/caddy.log 2>&1; then
    log "Caddy 已重载 —— 新证书即时生效，无需重启容器"
  else
    log "!! Caddy reload 失败，见 /workspace/caddy.log；重启容器后同样生效"
  fi
else
  log "Caddy admin API 未就绪，跳过重载（下次启动会读到新证书）"
fi

log ""
log "容器内自测线上实际证书："
log "  echo | openssl s_client -connect 127.0.0.1:59684 -servername $DOMAIN_FULL 2>/dev/null | openssl x509 -noout -dates"
log "完成。"
