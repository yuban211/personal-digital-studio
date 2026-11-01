#!/bin/bash
# ── issue-now.sh · 强制签发一次并立即部署上线（端到端验证用）────────────────
#
# 为什么需要它：
#   setup-acme.sh 第 465 行有 `openssl x509 -checkend 2592000`（30 天）判断。
#   当前证书剩 62 天 → 它只跑一次 --cron 就退出，绝不会真正签发。
#   结果就是「续期链路从未被验证过」，风险一路留到临期那几天才暴露。
#   本脚本绕过该门槛，强制走一次完整 DNS-01，签完立刻部署并 reload Caddy。
#
# 代价：消耗 1 次 Let's Encrypt 签发配额（上限 5 次/周）。偶尔验证一次无妨。
#
# 幂等：可反复执行，每次都会重新签（因为带 --force）。
# 用法：bash /workspace/issue-now.sh

set -u

ACME_HOME="/workspace/.acme.sh"
CERT_DIR="/workspace/certs"
CRED="/workspace/acme-cred.env"
DOMAIN_FULL="yuban211.duckdns.org"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

log "══════ 强制签发 $(date '+%Y-%m-%d %H:%M:%S %Z') ══════"

# ── 1) 凭据 ───────────────────────────────────────────────────────────────
if [ ! -s "$CRED" ]; then
  log "!! 缺少 $CRED —— 先建好凭据文件再跑"
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

# 域名：允许写注册名或全名
if [ -n "${DuckDNS_Domain:-}" ]; then
  case "$DuckDNS_Domain" in
    *.duckdns.org) DOMAIN_FULL="$DuckDNS_Domain" ;;
    *)             DOMAIN_FULL="$DuckDNS_Domain.duckdns.org" ;;
  esac
fi
log "目标域名：$DOMAIN_FULL"

# ── 2) 前置 ───────────────────────────────────────────────────────────────
if [ ! -x "$ACME_HOME/acme.sh" ]; then
  log "!! acme.sh 不可执行：$ACME_HOME/acme.sh（先跑 prefetch-acme.sh）"
  exit 1
fi
if [ ! -s "$ACME_HOME/dnsapi/dns_duckdns.sh" ]; then
  log "!! 缺少 dns_duckdns.sh 插件（先跑 prefetch-acme.sh）"
  exit 1
fi
_before_fp=$(openssl x509 -in "$CERT_DIR/cert.pem" -noout -fingerprint -sha256 2>/dev/null)
log "当前证书指纹：${_before_fp:-无}"

# ── 3) 强制签发（DNS-01）──────────────────────────────────────────────────
#   注意：--force 在这个语境下是必要的 —— 我们要的正是「不等到期，立刻签一次」。
#   它和「每日守护里不该出现 --force」并不矛盾：守护要幂等，这里是有意为之。
log "开始签发（DNS-01，通常 20~60 秒）..."
if ! "$ACME_HOME/acme.sh" --issue \
      --home "$ACME_HOME" \
      --server letsencrypt \
      --dns dns_duckdns \
      -d "$DOMAIN_FULL" \
      --keylength 2048 \
      --force \
      --log "$ACME_HOME/acme.log"; then
  log "!! 签发失败。下面是 acme.log 末尾（关键行常含 TXT 校验或速率限制提示）："
  tail -25 "$ACME_HOME/acme.log" 2>/dev/null | sed 's/^/    /'
  log "!! 已签发失败但**没有动线上证书** —— 站点继续用旧证书，不受影响"
  exit 1
fi
log "签发成功"

# ── 4) 部署到 certs/ ─────────────────────────────────────────────────────
SRC="$ACME_HOME/$DOMAIN_FULL"
if [ ! -s "$SRC/fullchain.cer" ] || [ ! -s "$SRC/$DOMAIN_FULL.key" ]; then
  log "!! 产物不全，检查 $SRC"
  ls -l "$SRC" 2>/dev/null | sed 's/^/    /'
  exit 1
fi
mkdir -p "$CERT_DIR"
cp -f "$SRC/fullchain.cer" "$CERT_DIR/cert.pem"
cp -f "$SRC/$DOMAIN_FULL.key" "$CERT_DIR/key.pem"
chmod 644 "$CERT_DIR/cert.pem" 2>/dev/null
chmod 600 "$CERT_DIR/key.pem" 2>/dev/null

_after_fp=$(openssl x509 -in "$CERT_DIR/cert.pem" -noout -fingerprint -sha256 2>/dev/null)
log "已部署。新证书："
openssl x509 -in "$CERT_DIR/cert.pem" -noout -subject -issuer -dates 2>/dev/null | sed 's/^/    /'
log "新指纹：${_after_fp:-读取失败}"

# ── 5) 让 Caddy 真正用上 ──────────────────────────────────────────────────
if [ "$_before_fp" = "$_after_fp" ]; then
  log "指纹未变（不该发生），跳过重载"
elif curl -s -m 3 -o /dev/null "http://127.0.0.1:2019/config/" 2>/dev/null; then
  if ( cd /workspace && ./caddy reload --config Caddyfile ) >>/workspace/caddy.log 2>&1; then
    log "Caddy 已重载 —— 新证书即时生效，无需重启容器"
  else
    log "!! Caddy reload 失败，见 /workspace/caddy.log；重启容器后同样会生效"
  fi
else
  log "Caddy admin API 未就绪，跳过重载（下次启动会读到新证书）"
fi

# ── 6) 收尾提示 ───────────────────────────────────────────────────────────
log ""
log "验证线上实际使用的证书（从容器内自测）："
log "  echo | openssl s_client -connect 127.0.0.1:59684 -servername $DOMAIN_FULL 2>/dev/null | openssl x509 -noout -dates"
log "完成。"
