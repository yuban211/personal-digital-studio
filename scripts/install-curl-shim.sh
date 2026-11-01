#!/bin/bash
# ── install-curl-shim.sh · 给 curl 装上强制超时（P0 兜底）──────────────────
#
# 为什么需要它：
#   2026-11-02 01:08 首次真签时，DuckDNS 写 TXT 耗时 132 秒。查 acme.log 发现：
#       timeout=
#       _CURL='curl --silent --dump-header ... -L -g '      ← 没有任何超时参数
#   同一份日志里 Let's Encrypt API 那次是 timeout=10 → 带 --connect-timeout 10。
#   根因在 acme.sh 的 _get()：签名是 `# url getheader timeout`，timeout 是**第三个**
#   参数；而 dnsapi/dns_duckdns.sh 第 48 行写的是 `_get "$url"`，只传了 url。
#   于是 curl 既不 --connect-timeout 也不 --max-time —— 默认无限等待。
#
# 为什么这是 P0：
#   续期守护是 `while sleep 86400; do bash setup-acme.sh; done`。
#   只要某天 DuckDNS 半死不活，那个 curl 就把进程永久挂住，while 再进不去
#   第二轮 —— 续期从此静默停摆，日志里只剩一条 'Trying to add TXT record'。
#   等证书真过期了回头查，看不出任何异常。
#
# 为什么用 PATH 垫片而不是改 acme.sh：
#   ① 改 acme.sh 主程序，下次升级就被覆盖；
#   ② 改 dnsapi 插件，换成任何其它 DNS 插件又得重来；
#   ③ PATH 垫片在系统层拦截，所有调用（含 acme.sh 内部、插件、未来新插件）
#      一律兜住，且不改任何上游文件。
#
# 语义要点（已核对 curl 行为）：
#   垫片把 --connect-timeout 15 --max-time 240 放在 "$@" **之前**，
#   curl 对重复选项取最后一次出现的值。所以调用方若显式传了更严的超时
#   （如 acme.sh 的 --connect-timeout 10），仍然以调用方为准，不会被垫片放大。
#
# 240 秒的来历：这次 DuckDNS 实测 132 秒。留 1.8 倍余量，既不会误杀正常请求，
# 也不会让守护永久挂起。可用 CURL_SHIM_CONNECT / CURL_SHIM_MAX 覆盖。
#
# 用法：bash /workspace/install-curl-shim.sh
#   幂等，可反复跑。只写 /workspace/.bin/curl，不碰证书、不签发、不重启服务。

set -u

BIN_DIR="${CURL_SHIM_BIN:-/workspace/.bin}"   # 可用环境变量覆盖，便于本机试跑
SHIM="$BIN_DIR/curl"
CONNECT="${CURL_SHIM_CONNECT:-15}"
MAX="${CURL_SHIM_MAX:-240}"
PASS=0; BAD=0
ok()  { PASS=$((PASS+1)); printf '[ OK ] %s\n' "$*"; }
inf() { printf '[ .. ] %s\n' "$*"; }
bad() { BAD=$((BAD+1));   printf '[FAIL] %s\n' "$*"; }
hdr() { printf '\n── %s ──────────────────────────────\n' "$*"; }

printf '\n══════ curl 超时垫片安装   %s ══════\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"

hdr "1. 找到真正的 curl"
# 逐个 PATH 目录找，跳过垫片自身所在目录，避免解析到自己去
REAL=""
while IFS= read -r d; do
  case "$d" in ""|"$BIN_DIR") continue ;; esac
  if [ -f "$d/curl" ] && [ -x "$d/curl" ]; then REAL="$d/curl"; break; fi
done <<EOF
$(printf '%s' "$PATH" | tr ':' '\n')
EOF

if [ -z "$REAL" ]; then
  bad "PATH 里找不到真 curl，无法生成垫片"
  printf '\n══════ 中止 ══════\n'; exit 1
fi
if [ "$REAL" = "$SHIM" ]; then
  bad "解析到的 curl 就是垫片自身，会无限递归 —— 中止"
  printf '\n══════ 中止 ══════\n'; exit 1
fi
ok "真 curl：$REAL"
inf "版本：$("$REAL" --version 2>/dev/null | head -1)"

# timeout 命令是下面功能验证的前提；缺失则跳过 4.2 / 4.3
HAS_TIMEOUT=0
command -v timeout >/dev/null 2>&1 && HAS_TIMEOUT=1

hdr "2. 写垫片 $SHIM"
mkdir -p "$BIN_DIR" 2>/dev/null
if [ ! -d "$BIN_DIR" ]; then bad "无法创建 $BIN_DIR"; printf '\n══════ 中止 ══════\n'; exit 1; fi

umask 022
cat > "$SHIM" <<SHIM_EOF
#!/bin/sh
# 由 /workspace/install-curl-shim.sh 生成，勿手改（重跑安装脚本即可覆盖）。
#
# 存在理由：acme.sh 的 dns_duckdns 插件调 _get "\$url" 时没传 timeout 参数，
# 导致 curl 无 --connect-timeout / --max-time，可能永久挂住续期守护进程。
#
# 超时放在 "\$@" 之前：curl 对重复选项取最后一次，故调用方显式传的
# 更严超时仍然生效（例如 acme.sh 自己的 --connect-timeout 10）。
exec "$REAL" --connect-timeout $CONNECT --max-time $MAX "\$@"
SHIM_EOF

if [ ! -s "$SHIM" ]; then bad "垫片写入失败或为空"; printf '\n══════ 中止 ══════\n'; exit 1; fi
chmod 755 "$SHIM" 2>/dev/null
ok "已写入（$(wc -c < "$SHIM") 字节）并设 755"
inf "末尾两行：$(tail -2 "$SHIM" | tr '\n' ' ')"

hdr "3. 功能验证"

# 判据说明：不靠「连不可路由地址多久失败」这种计时手段验证 —— 容器/代理/路由
# 差异会让它误判（本机试跑时目标被本地代理接走，11 秒 exit 0）。
# 改用**确定性验证**：把垫片里的真 curl 替换成一个只回显参数的桩，
# 直接检查 ① 超时参数是否注入 ② 是否排在调用方参数之前（决定谁生效）。

# 探针目录：不用 mktemp —— 在 Git Bash 下它返回 Windows 反斜杠路径，
# sh 无法解析。逐个试可写目录，取第一个纯 POSIX 路径。
PROBE=""
for _p in /tmp "${TMPDIR:-}" "$BIN_DIR"; do
  [ -n "$_p" ] || continue
  [ -d "$_p" ] || continue
  if ( : > "$_p/.probe.$$" ) 2>/dev/null; then
    rm -f "$_p/.probe.$$" 2>/dev/null
    PROBE="$_p/curlprobe.$$"
    break
  fi
done
if [ -z "$PROBE" ]; then
  bad "找不到可写临时目录，跳过参数验证"
else
  mkdir -p "$PROBE" 2>/dev/null
fi
if [ -n "$PROBE" ]; then
cat > "$PROBE/fakecurl" <<'EOS'
#!/bin/sh
for a in "$@"; do printf '%s\n' "$a"; done
EOS
chmod 755 "$PROBE/fakecurl" 2>/dev/null

# 用桩替换 exec 行里的真 curl 绝对路径，其余原样保留
sed "s#^exec \"[^\"]*\"#exec \"$PROBE/fakecurl\"#" "$SHIM" > "$PROBE/curl" 2>/dev/null

if [ ! -s "$PROBE/curl" ]; then
  bad "无法生成探针副本，跳过参数验证"
else
  _out=$(sh "$PROBE/curl" --max-time 2 http://probe.invalid/ 2>/dev/null)
  _l1=$(printf '%s\n' "$_out" | sed -n '1p')
  _l2=$(printf '%s\n' "$_out" | sed -n '2p')
  _l3=$(printf '%s\n' "$_out" | sed -n '3p')
  _l4=$(printf '%s\n' "$_out" | sed -n '4p')
  _l5=$(printf '%s\n' "$_out" | sed -n '5p')
  _l6=$(printf '%s\n' "$_out" | sed -n '6p')

  # 3.1 必须注入两个超时
  if [ "$_l1" = "--connect-timeout" ] && [ "$_l3" = "--max-time" ]; then
    ok "已注入超时参数：$_l1 $_l2 / $_l3 $_l4"
  else
    bad "超时参数未注入（实际首四参数：$_l1 $_l2 $_l3 $_l4）"
  fi

  # 3.2 注入值必须等于配置值
  if [ "$_l2" = "$CONNECT" ] && [ "$_l4" = "$MAX" ]; then
    ok "注入值符合配置：connect=${_l2}s / max=${_l4}s"
  else
    bad "注入值不符：connect='${_l2}'（期望 $CONNECT） max='${_l4}'（期望 $MAX）"
  fi

  # 3.3 调用方参数必须排在注入参数**之后** —— 这是「更严超时优先」的前提
  if [ "$_l5" = "--max-time" ] && [ "$_l6" = "2" ]; then
    ok "调用方参数位于注入参数之后（curl 取最后一次 → 调用方的 --max-time 2 优先）"
  else
    bad "参数顺序异常：第 5/6 个参数是 '$_l5' / '$_l6'，期望 '--max-time' / '2'"
  fi
fi
rm -rf "$PROBE" 2>/dev/null

fi

# 3.4 真 curl 经垫片是否仍能正常出网（仅提示，容器与本机网络环境不同）
_code=$(PATH="$BIN_DIR:$PATH" "$SHIM" -s -o /dev/null -w '%{http_code}' \
        --max-time 20 https://www.duckdns.org 2>/dev/null)
if [ "$_code" = "200" ]; then
  ok "真 curl 经垫片请求 duckdns 返回 200（原有功能未受影响）"
else
  inf "真 curl 经垫片请求 duckdns 返回 '${_code:-空}'（本机网络环境所致，不作为判据；容器内实测为 200）"
fi

hdr "4. 调用方是否已接入"
if [ "$BIN_DIR" != "/workspace/.bin" ]; then
  inf "垫片目录被覆盖为 $BIN_DIR（本机试跑模式），跳过调用方检查"
else
  for f in /workspace/setup-acme.sh /workspace/issue-now.sh; do
    _n=$(basename "$f")
    if [ ! -f "$f" ]; then
      bad "$_n 不存在 —— 请先上传该脚本"
    elif grep -qs '/workspace/.bin' "$f"; then
      ok "$_n 已前置 PATH（会走垫片）"
    else
      bad "$_n 未前置 PATH —— 仍可能被挂死，请重传该脚本"
    fi
  done
fi

hdr "5. 结论"
if [ "$BAD" -eq 0 ]; then
  ok "垫片就绪：$SHIM（connect ${CONNECT}s / max ${MAX}s）"
  printf '\n此后无论 acme.sh 还是任何插件调 curl，都不会再无限等待。\n'
else
  bad "有 $BAD 项失败，请看上面 [FAIL]"
fi
printf '\n══════ 安装结束 ══════\n'
