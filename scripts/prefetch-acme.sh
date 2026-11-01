#!/bin/sh
# ── prefetch-acme.sh · 预装 acme.sh 与 DNS 插件（不签发证书）────────────────
#
# 为什么需要它：
#   setup-acme.sh 在缺凭据时会在第 1b 段直接 exit，走不到安装 acme.sh 那一步。
#   于是「容器能否真正装上 acme.sh」「dnsapi 插件能否就位」从未被验证过 ——
#   自检脚本只证明了 raw.githubusercontent.com 返回 200，没证明 --install 成功。
#   本脚本把这步提前做掉，并留下离线副本，让 token 到位时只需填两行。
#
# 幂等：已装则跳过下载；可反复执行。
# 不做的事：不签发、不修改证书、不碰 Caddyfile、不写 crontab。
#
# 用法：bash /workspace/prefetch-acme.sh

ACME_HOME="/workspace/.acme.sh"
PLUGIN_NAME="dns_duckdns"
RAW="https://raw.githubusercontent.com/acmesh-official/acme.sh/master"
RAW2="https://raw.githubusercontent.com/acmesh-official/acme.sh/main"

p() { printf '%s\n' "$*"; }
hdr() { p ""; p "── $* ──────────────────────────────"; }
ok()  { p "  [ OK ] $*"; }
bad() { p "  [FAIL] $*"; }
inf() { p "  [ .. ] $*"; }

# 下载：成功返回 0。两源依次尝试（get.acme.sh 在本环境不通，故只用 GitHub）
dl() {
  _url="$1"; _out="$2"
  if curl -fsSL --connect-timeout 15 --max-time 120 -o "$_out" "$_url" 2>/dev/null && [ -s "$_out" ]; then
    return 0
  fi
  rm -f "$_out" 2>/dev/null
  return 1
}

p "══════ acme.sh 预装  $(date '+%Y-%m-%d %H:%M:%S %Z') ══════"

hdr "0. 前置"
if ! command -v bash >/dev/null 2>&1; then
  bad "没有 bash，acme.sh 无法运行/自举 —— 中止"
  exit 1
fi
ok "bash $(bash --version 2>/dev/null | head -1 | sed 's/.*version //;s/(.*//')"
command -v curl >/dev/null 2>&1 && ok "curl 可用" || { bad "curl 缺失 —— 中止"; exit 1; }
command -v openssl >/dev/null 2>&1 && ok "openssl 可用" || { bad "openssl 缺失 —— 中止"; exit 1; }

hdr "1. 安装目录可写性"
mkdir -p "$ACME_HOME" 2>/dev/null
if [ -w "$ACME_HOME" ]; then
  ok "$ACME_HOME 可写"
else
  bad "$ACME_HOME 不可写 —— 这是个硬阻塞"
  exit 1
fi
# noexec 会让 chmod +x 白做，acme.sh 只能靠 bash 显式解释
_probe="$ACME_HOME/.execprobe"
printf '#!/bin/sh\nexit 0\n' > "$_probe" 2>/dev/null && chmod +x "$_probe" 2>/dev/null
if [ -x "$_probe" ] && "$_probe" 2>/dev/null; then
  ok "挂载未设 noexec（可直接执行）"
else
  inf "疑似 noexec 或无法直接执行 —— 不影响：setup-acme.sh 用 bash 显式调起"
fi
rm -f "$_probe" 2>/dev/null

hdr "2. acme.sh 主程序"
if [ -s "$ACME_HOME/acme.sh" ]; then
  ok "已存在（$(( $(wc -c <"$ACME_HOME/acme.sh") / 1024 )) KB），跳过下载"
else
  _installed=0
  # 2a) 先用已有离线副本
  if [ -s "/workspace/acme.sh.bak" ]; then
    cp -f "/workspace/acme.sh.bak" "$ACME_HOME/acme.sh" 2>/dev/null \
      && [ -s "$ACME_HOME/acme.sh" ] && { ok "用离线副本 /workspace/acme.sh.bak"; _installed=1; }
  fi
  # 2b) 否则从 GitHub 取（get.acme.sh 在本环境不通，已知）
  if [ "$_installed" != "1" ]; then
    inf "从 raw.githubusercontent.com 下载 ..."
    if dl "$RAW/acme.sh" "$ACME_HOME/acme.sh" || dl "$RAW2/acme.sh" "$ACME_HOME/acme.sh"; then
      ok "下载完成（$(( $(wc -c <"$ACME_HOME/acme.sh") / 1024 )) KB）"
      _installed=1
    fi
  fi
  if [ "$_installed" != "1" ]; then
    bad "主程序获取失败 —— 两个源都不通，签发无法进行"
    exit 1
  fi
  chmod +x "$ACME_HOME/acme.sh" 2>/dev/null
  # 落一份离线副本，供将来断网时复用
  cp -f "$ACME_HOME/acme.sh" "/workspace/acme.sh.bak" 2>/dev/null \
    && ok "已存离线副本 /workspace/acme.sh.bak"
fi

hdr "3. 自举安装（--install --nocron）"
# 2026-11-02 实测：在自己的目录外执行 install 会失败，因为自举脚本用相对路径
# `cp acme.sh`，报 "cannot stat 'acme.sh' / Installation failed"。必须先 cd 过去。
if [ -x "$ACME_HOME/acme.sh" ]; then
  ( cd "$ACME_HOME" && bash ./acme.sh --install --home "$ACME_HOME" --nocron ) \
    >>"$ACME_HOME/install.log" 2>&1
  if [ -s "$ACME_HOME/acme.sh.env" ]; then
    ok "自举安装成功（生成 acme.sh.env）"
  elif grep -qi "install.*success\|already installed" "$ACME_HOME/install.log" 2>/dev/null; then
    ok "自举安装成功"
  else
    inf "自举安装未成功（不影响签发，原因见末行）"
    inf "  $(tail -1 "$ACME_HOME/install.log" 2>/dev/null)"
  fi
  # ★ 真正要紧的一项：setup-acme.sh 用的是 "$ACME"（直接 exec，靠 shebang + x 位），
  #   不是 `bash acme.sh`。所以这里必须按同样方式测，否则签到时才会暴露。
  _ver=$("$ACME_HOME/acme.sh" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  if [ -n "$_ver" ]; then
    ok "直接执行可用（shebang + x 位正常）：acme.sh v$_ver"
  else
    _ver2=$(bash "$ACME_HOME/acme.sh" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [ -n "$_ver2" ]; then
      bad "只能经 bash 解释运行，直接 exec 失败 —— 而 setup-acme.sh 用的正是直接 exec"
      inf "  修法：chmod 755 $ACME_HOME/acme.sh"
    else
      bad "acme.sh 完全无法运行 —— 请检查 $ACME_HOME/install.log"
    fi
  fi
  # --home 载入测试（不签发、不联网）
  if "$ACME_HOME/acme.sh" --home "$ACME_HOME" --list >/dev/null 2>&1; then
    ok "--home 载入正常（配置目录可读）"
  else
    inf "--home --list 返回非零（当前无证书，属正常）"
  fi
  inf "注：--install 只负责 cron / alias / .bashrc，签发与续期不依赖它"
else
  bad "$ACME_HOME/acme.sh 不存在或不可执行"
fi

hdr "4. DNS 插件 $PLUGIN_NAME"
mkdir -p "$ACME_HOME/dnsapi" 2>/dev/null
PLUGIN="$ACME_HOME/dnsapi/$PLUGIN_NAME.sh"
if [ -s "$PLUGIN" ]; then
  ok "已存在（$(wc -c <"$PLUGIN") 字节），跳过"
else
  _got=0
  if [ -s "/workspace/$PLUGIN_NAME.sh.bak" ]; then
    cp -f "/workspace/$PLUGIN_NAME.sh.bak" "$PLUGIN" 2>/dev/null && [ -s "$PLUGIN" ] \
      && { ok "用离线副本 /workspace/$PLUGIN_NAME.sh.bak"; _got=1; }
  fi
  if [ "$_got" != "1" ]; then
    if dl "$RAW/dnsapi/$PLUGIN_NAME.sh" "$PLUGIN" || dl "$RAW2/dnsapi/$PLUGIN_NAME.sh" "$PLUGIN"; then
      ok "下载完成（$(wc -c <"$PLUGIN") 字节）"
      _got=1
    fi
  fi
  if [ "$_got" != "1" ]; then
    bad "插件获取失败 —— DNS-01 无法进行"
  else
    chmod +x "$PLUGIN" 2>/dev/null
    cp -f "$PLUGIN" "/workspace/$PLUGIN_NAME.sh.bak" 2>/dev/null \
      && ok "已存离线副本 /workspace/$PLUGIN_NAME.sh.bak"
  fi
fi

hdr "5. 插件完整性校验"
if [ -s "$PLUGIN" ]; then
  # acme.sh 约定：插件必须实现 dns_<name>_add / _rm 两个函数
  _add=$(grep -c "^${PLUGIN_NAME}_add()" "$PLUGIN" 2>/dev/null)
  _rm=$(grep -c "^${PLUGIN_NAME}_rm()" "$PLUGIN" 2>/dev/null)
  [ "${_add:-0}" -ge 1 ] && ok "实现 ${PLUGIN_NAME}_add()" || bad "缺少 ${PLUGIN_NAME}_add() —— 插件文件可能不完整"
  [ "${_rm:-0}" -ge 1 ]  && ok "实现 ${PLUGIN_NAME}_rm()"  || bad "缺少 ${PLUGIN_NAME}_rm()"
  grep -q "DuckDNS_Token" "$PLUGIN" 2>/dev/null && ok "读取 DuckDNS_Token 环境变量" \
    || inf "未在插件中发现 DuckDNS_Token 字样（不同版本写法有别，可忽略）"
  # 函数命名前缀也顺带确认，防止插件是别的后端
  grep -q "duckdns.org" "$PLUGIN" 2>/dev/null && ok "包含 duckdns.org API 端点" \
    || bad "未包含 duckdns.org 端点 —— 疑似取到了错误的插件"
else
  bad "插件不存在，跳过校验"
fi

hdr "6. 结论"
_acme_ok=0; _plug_ok=0
[ -x "$ACME_HOME/acme.sh" ] && "$ACME_HOME/acme.sh" --version >/dev/null 2>&1 && _acme_ok=1
[ -s "$PLUGIN" ] && _plug_ok=1
if [ "$_acme_ok" = "1" ] && [ "$_plug_ok" = "1" ]; then
  p "  预装完成。现在唯一的缺口就是凭据文件。"
  p "  填好 /workspace/acme-cred.env 两行后直接执行："
  p "      cd /workspace && bash ./setup-acme.sh"
  p "  届时它会跳过下载、直接签发，全程只用本地副本与 LE API。"
else
  p "  预装未完成：acme.sh=$([ "$_acme_ok" = 1 ] && echo OK || echo FAIL)  插件=$([ "$_plug_ok" = 1 ] && echo OK || echo FAIL)"
  p "  上面标 [FAIL] 的项即为阻塞点。"
fi
p ""
p "══════ 预装结束 ══════"
