#!/bin/sh
# ── reload-caddy.sh · 强制让 Caddy 重新读取 certs/ 下的证书 ──────────────────
#
# 为什么需要这个脚本：
#   Caddy 的 `reload` 会先比对「新配置 vs 现有配置」的差异，只在有差异时才重载对应模块。
#   而我们换证书的方式是覆盖 certs/cert.pem（Caddyfile 文本一个字都没改），
#   于是 Caddy 判定「配置未变化」→ 跳过 TLS 模块 → 内存里仍是旧证书。
#   表现是：reload 返回 0、日志显示 adapted config to JSON、certs/ 里确实是新日期，
#          但外部访问拿到的仍是旧证书，直到容器重启。
#   解法是加 --force，强制完整重载，不看差异。
#
# 本脚本只读 + 一次 reload，不签发、不碰证书文件、不消耗 Let's Encrypt 配额。
# 用法：sh /workspace/reload-caddy.sh

set -u
CERT_DIR="/workspace/certs"
LOG="/workspace/caddy.log"
CADDY_BIN="/workspace/caddy"

ok()  { printf '[ OK ] %s\n' "$*"; }
bad() { printf '[FAIL] %s\n' "$*"; }
inf() { printf '[ .. ] %s\n' "$*"; }

printf '══════ Caddy 强制重载  %s ══════\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"

# ── 1. 前置 ────────────────────────────────────────────────────────────────
hdr() { printf '\n── %s ──────────────────────────────\n' "$1"; }

hdr "1. 前置"
if [ ! -f "$CERT_DIR/cert.pem" ]; then
  bad "$CERT_DIR/cert.pem 不存在"
  exit 1
fi
ok "证书文件存在"

if [ ! -x "$CADDY_BIN" ]; then
  bad "$CADDY_BIN 不存在或不可执行"
  exit 1
fi
ok "caddy 可执行"

# ── 2. 重载前 ──────────────────────────────────────────────────────────────
hdr "2. 重载前的证书文件"
_fp_before=$(openssl x509 -in "$CERT_DIR/cert.pem" -noout -fingerprint -sha256 2>/dev/null)
inf "${_fp_before:-读不到指纹}"
openssl x509 -in "$CERT_DIR/cert.pem" -noout -dates 2>/dev/null | sed 's/^/    /'

# 顺手记下容器内实测到的线上证书（这才是 Caddy 内存里的真相）
hdr "3. 重载前 Caddy 实际在用的证书"
_live_before=$(echo | openssl s_client -connect 127.0.0.1:59684 -servername yuban211.duckdns.org 2>/dev/null \
  | openssl x509 -noout -fingerprint -sha256 2>/dev/null)
inf "${_live_before:-探测失败（可能端口无监听）}"

# ── 4. 强制重载 ────────────────────────────────────────────────────────────
hdr "4. 强制重载（--force）"
if ( cd /workspace && ./caddy reload --config Caddyfile --force ) >>"$LOG" 2>&1; then
  ok "reload 返回成功"
else
  bad "reload 失败，见 $LOG 末尾"
  tail -10 "$LOG" 2>/dev/null | sed 's/^/    /'
  inf "兜底：重启容器同样能让新证书生效"
  exit 1
fi

# ── 5. 重载后 ──────────────────────────────────────────────────────────────
hdr "5. 重载后 Caddy 实际在用的证书"
sleep 2
_live_after=$(echo | openssl s_client -connect 127.0.0.1:59684 -servername yuban211.duckdns.org 2>/dev/null \
  | openssl x509 -noout -fingerprint -sha256 2>/dev/null)
inf "${_live_after:-探测失败}"
echo | openssl s_client -connect 127.0.0.1:59684 -servername yuban211.duckdns.org 2>/dev/null \
  | openssl x509 -noout -dates 2>/dev/null | sed 's/^/    /'

hdr "6. 结论"
if [ -n "$_live_after" ] && [ "$_live_after" = "$_fp_before" ]; then
  ok "线上证书已与 certs/cert.pem 一致 —— 重载生效"
elif [ -n "$_live_before" ] && [ "$_live_after" = "$_live_before" ]; then
  bad "线上文件质仍与重载前相同 —— --force 也没能让 Caddy 换证"
  inf "改用重启容器：面板「停止」→「启动」，新证书会随启动一起加载"
else
  inf "无法自动判定。请把上面第 2 段与第 5 段的指纹手工比对一次"
fi

printf '══════ 结束 ══════\n'
