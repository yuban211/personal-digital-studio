#!/bin/sh
# ── check-acme-ready.sh · 证书续期链路自检（只读，不改任何东西）─────────────
#
# 目的：在没有 DuckDNS token 的前提下，先把「token 到位后能否签发成功」
#       的所有前置条件实测一遍，避免 60 天后才发现容器装不上 acme.sh。
#
# 用法（面板命令循环里执行）：
#     sh /workspace/check-acme-ready.sh
#
# 本脚本不写文件、不装任何东西、不碰证书，纯探测。

CERT="${CERT:-/workspace/certs/cert.pem}"
KEY="${KEY:-/workspace/certs/key.pem}"
CRED="${CRED:-/workspace/acme-cred.env}"
ACME_HOME="/workspace/.acme.sh"

p() { printf '%s\n' "$*"; }
hdr() { p ""; p "── $* ────────────────────────────────────────"; }

mark() {
  case "$1" in
    ok)   p "  [ OK ] $2" ;;
    bad)  p "  [FAIL] $2" ;;
    warn) p "  [WARN] $2" ;;
    *)    p "  [ .. ] $2" ;;
  esac
}

p "══════ 证书续期链路自检  $(date '+%Y-%m-%d %H:%M:%S %Z') ══════"

# ── 1) 运行时：acme.sh 用 bash，没有 bash 就签不了 ──────────────────────────
hdr "1. 运行时"
if command -v bash >/dev/null 2>&1; then
  mark ok "bash 可用：$(bash --version 2>/dev/null | head -1)"
else
  mark bad "没有 bash —— acme.sh 无法安装（这是硬阻塞）"
fi
for t in openssl curl date; do
  if command -v "$t" >/dev/null 2>&1; then
    mark ok "$t 可用"
  else
    mark bad "$t 缺失"
  fi
done
mark info "容器内 crontab：$(command -v crontab 2>/dev/null || echo '无（不影响：start.tls.sh 用 while-sleep 守护续期）')"

# ── 2) 出网：acme.sh 下载 + Let's Encrypt API + DuckDNS API ────────────────
hdr "2. 出网连通性（签发必需的三条）"
probe() {
  _code=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' "$2" 2>/dev/null) || _code="ERR"
  if [ "$_code" = "ERR" ]; then
    mark bad "$1 → 连不上"
  else
    mark ok "$1 → HTTP $_code"
  fi
}
probe "get.acme.sh（装 acme.sh）"        "https://get.acme.sh"
probe "raw.githubusercontent.com（备用源）" "https://raw.githubusercontent.com/acmesh-official/acme.sh/master/acme.sh"
probe "acme-v02.api.letsencrypt.org（签发）" "https://acme-v02.api.letsencrypt.org/directory"
probe "www.duckdns.org（写 TXT 记录）"     "https://www.duckdns.org/"

# ── 3) acme.sh 安装状态 ──────────────────────────────────────────────────
hdr "3. acme.sh"
if [ -x "$ACME_HOME/acme.sh" ]; then
  mark ok "已安装：$ACME_HOME/acme.sh（$(( $(wc -c <"$ACME_HOME/acme.sh") / 1024 )) KB）"
  mark info "版本：$(bash "$ACME_HOME/acme.sh" --version 2>/dev/null | head -1)"
else
  mark warn "$ACME_HOME/acme.sh 不存在 —— 首次签发时会自动下载安装"
  if [ -s /workspace/acme.sh.bak ]; then
    mark ok "存在离线兜底 /workspace/acme.sh.bak（下载失败时脚本会自动用它）"
  else
    mark info "无离线兜底，完全依赖上面的 get.acme.sh / GitHub 两条出网"
  fi
fi

# ── 4) 凭据：唯一的真实缺口 ──────────────────────────────────────────────
hdr "4. 凭据文件 $CRED"
if [ -s "$CRED" ]; then
  mark ok "文件存在（$(( $(wc -c <"$CRED") )) 字节）"
  if grep -q '^[[:space:]]*DuckDNS_Token=[^[:space:]]' "$CRED" 2>/dev/null; then
    mark ok "DuckDNS_Token 已填写"
  elif grep -q '^[[:space:]]*Ali_Key=' "$CRED" 2>/dev/null; then
    mark ok "阿里云 Ali_Key/Ali_Secret 已填写"
  else
    mark bad "文件在，但没识别到 DuckDNS_Token 或 Ali_Key"
  fi
  mark info "DuckDNS_Domain：$(grep -E '^[[:space:]]*DuckDNS_Domain=' "$CRED" 2>/dev/null | tail -1 | cut -d= -f2- || echo '未设（会回落到服务器名）')"
else
  mark bad "文件不存在 —— 这就是 boot.log 里 13 次「跳过证书签发」的直接原因"
  mark info "需要两行：DuckDNS_Token=<token> 和 DuckDNS_Domain=yuban211"
fi

# ── 5) 现有证书还能撑多久 ────────────────────────────────────────────────
hdr "5. 现有证书"
if [ -s "$CERT" ]; then
  _subj=$(openssl x509 -in "$CERT" -noout -subject 2>/dev/null | sed 's/^subject=//')
  _iss=$(openssl x509 -in "$CERT" -noout -issuer 2>/dev/null | sed 's/^issuer=//')
  _end=$(openssl x509 -in "$CERT" -noout -enddate 2>/dev/null | cut -d= -f2)
  mark ok "subject：$_subj"
  mark info "issuer ：$_iss"
  if [ -n "$_end" ]; then
    _end_s=$(date -d "$_end" +%s 2>/dev/null)
    if [ -n "$_end_s" ]; then
      _days=$(( (_end_s - $(date +%s)) / 86400 ))
      if [ "$_days" -lt 0 ]; then
        mark bad "已过期 $_days 天（$_end）"
      elif [ "$_days" -lt 15 ]; then
        mark bad "仅剩 $_days 天（$_end）—— 必须立刻处理"
      elif [ "$_days" -lt 30 ]; then
        mark warn "仅剩 $_days 天（$_end）—— 建议本周内接通续期"
      else
        mark ok "剩 $_days 天（$_end）"
      fi
    else
      mark info "notAfter：$_end（无法计算剩余天数）"
    fi
  fi
  if [ -s "$KEY" ]; then mark ok "私钥存在且非空"; else mark bad "私钥 $KEY 缺失"; fi
  _mod=$(openssl x509 -in "$CERT" -noout -modulus 2>/dev/null | openssl dgst -sha256 2>/dev/null)
  _kmod=$(openssl rsa -in "$KEY" -noout -modulus 2>/dev/null | openssl dgst -sha256 2>/dev/null)
  if [ -n "$_mod" ] && [ "$_mod" = "$_kmod" ]; then
    mark ok "证书与私钥匹配"
  elif [ -n "$_kmod" ]; then
    mark bad "证书与私钥不匹配（Caddy 会拒绝加载）"
  fi
else
  mark bad "证书不存在：$CERT"
fi

# ── 6) 端口：决定能否用 HTTP-01 做备选方案 ──────────────────────────────
hdr "6. 端口占用（关系备选挑战方式）"
if command -v ss >/dev/null 2>&1; then
  for port in 80 443 59684; do
    if ss -ltn 2>/dev/null | grep -q ":$port "; then
      mark info "容器内 :$port 已被监听"
    else
      mark info "容器内 :$port 空闲"
    fi
  done
elif command -v netstat >/dev/null 2>&1; then
  for port in 80 443 59684; do
    if netstat -ltn 2>/dev/null | grep -q ":$port "; then
      mark info "容器内 :$port 已被监听"
    else
      mark info "容器内 :$port 空闲"
    fi
  done
else
  mark info "无 ss / netstat，跳过"
fi
mark info "注：容器内空闲 ≠ 外网可达。本实例外部仅映射 59684，80/443 在边缘被拦，故 80/443 的结论不影响 DNS-01"

# ── 7) 结论 ─────────────────────────────────────────────────────────────
hdr "7. 结论"
p "  签发方式只能是 DNS-01（外部只有 59684 一条映射，HTTP-01/TLS-ALPN 都用不了）。"
p "  除 DuckDNS token 外的其余前置条件，请对照上面 1~3 项的 [FAIL]。"
p "  全部为 OK 时，填好 $CRED 后执行以下命令即可立即签发（不必等守护）："
p "      cd /workspace && bash ./setup-acme.sh"
p "  签发成功后 Caddy 需要重新加载证书，重启方式见 start.tls.sh 第 8 节。"
p ""
p "══════ 自检结束 ══════"
