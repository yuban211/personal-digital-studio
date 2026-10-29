"""安全：密码哈希、会话签名、CSRF、Markdown 安全渲染。

密码用 scrypt（Python 标准库自带，不需要 bcrypt 这种带 C 扩展的依赖）——
这正好符合"减少依赖"的目标，且抗 GPU 暴力破解。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

#: scrypt 参数。n=2**14 在普通机器上约 50ms，够慢且不影响体验。
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

_SAFE_SCHEMES = ("http", "https", "mailto")


# ────────────────────────── 密码 ──────────────────────────

def hash_password(password: str) -> dict:
    salt = os.urandom(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
    )
    return {
        "scheme": "scrypt",
        "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(dk).decode("ascii"),
    }


def verify_password(password: str, record: dict | None) -> bool:
    if not record or record.get("scheme") != "scrypt":
        return False
    try:
        salt = base64.b64decode(record["salt"])
        expected = base64.b64decode(record["hash"])
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=int(record.get("n", SCRYPT_N)), r=int(record.get("r", SCRYPT_R)),
            p=int(record.get("p", SCRYPT_P)), dklen=len(expected),
        )
    except (KeyError, ValueError, TypeError):
        return False
    return secrets.compare_digest(dk, expected)


# ────────────────────────── 管理员凭据 ──────────────────────────

def read_admin(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) and data.get("username") else None


def write_admin(path: Path, username: str, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "username": username,
        "password": hash_password(password),
        "created_at": int(time.time()),
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows 无 chmod


# ────────────────────────── 会话签名 ──────────────────────────

def sign(secret: bytes, value: str) -> str:
    """给一个值附加 HMAC 签名，防止客户端篡改。"""
    digest = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).digest()
    return value + "." + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def unsign(secret: bytes, token: str) -> str | None:
    """校验签名并还原原值。校验失败返回 None。"""
    if not token or "." not in token:
        return None
    value, _, sig = token.rpartition(".")
    expected = sign(secret, value)
    if not secrets.compare_digest(expected, token):
        return None
    return value


# ────────────────────────── 限速 ──────────────────────────

class RateLimiter:
    """登录失败限速。内存态即可——单机部署，重启后清空是可以接受的。"""

    def __init__(self, window: int = 300, max_attempts: int = 8) -> None:
        self.window = window
        self.max_attempts = max_attempts
        self._hits: dict[str, list[float]] = {}

    def allowed(self, key: str) -> bool:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        self._hits[key] = hits
        return len(hits) < self.max_attempts

    def record(self, key: str) -> None:
        self._hits.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


# ────────────────────────── URL 校验 ──────────────────────────

def valid_external_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in _SAFE_SCHEMES and bool(p.netloc or p.scheme == "mailto")


# ────────────────────────── Markdown 子集 ──────────────────────────

#: 只支持这些语法。先转义 HTML 再解析，杜绝 XSS。
#: URL 里允许的字符集 —— 故意不含引号/空格/尖括号。
#: 含非法字符的 URL 一律不匹配（整段降级为纯文本），
#: 这样畸形输入不会残留原始内容，避免给将来放宽规则留注入点。
_URL_CHARS = r"[^\s<>\"'`()]+"

_MD_RULES = [
    # 图片 ![alt](url)
    (re.compile(rf'!\[([^\]]*)\]\(({_URL_CHARS})\)'),
     lambda m: f'<img src="{_safe_attr(m.group(2))}" alt="{_safe_attr(m.group(1))}" loading="lazy">'),
    # 链接 [text](url)
    (re.compile(rf'\[([^\]]+)\]\(({_URL_CHARS})\)'),
     lambda m: _link_replacement(m.group(1), m.group(2))),
    (re.compile(r'^### (.+)$', re.M), r'<h3>\1</h3>'),
    (re.compile(r'^## (.+)$', re.M), r'<h2>\1</h2>'),
    (re.compile(r'^# (.+)$', re.M), r'<h1>\1</h1>'),
    (re.compile(r'\*\*([^*]+)\*\*'), r'<strong>\1</strong>'),
    (re.compile(r'(?<!\*)\*([^*]+)\*(?!\*)'), r'<em>\1</em>'),
    (re.compile(r'`([^`]+)`'), r'<code>\1</code>'),
]


def _safe_attr(v: str) -> str:
    """属性值转义。

    ★ 只转义引号和尖括号，不碰 & ——
      正文在进这里之前已经整体 escape 过一次，
      再 escape 会把 &amp; 变成 &amp;amp;（双重转义）。
    """
    return (str(v or "")
            .replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _link_replacement(text: str, url: str) -> str:
    """生成链接。URL 不安全时只保留文字，绝不回显危险 URL。"""
    if valid_external_url(url):
        return (f'<a href="{_safe_attr(url)}" target="_blank" '
                f'rel="noopener noreferrer">{text}</a>')
    return text


def safe_markdown(text: str) -> str:
    """渲染 Markdown 的安全子集。原始 HTML 一律转义，不信任任何输入。

    顺序很关键，不能颠倒：
      1. 整体转义 HTML —— 任何标签都变成文本，这是 XSS 的第一道防线
      2. 逐条套用 Markdown 规则 —— 只把"结构合法且 URL 安全"的片段换成标签
      3. 丢弃所有没被消费掉的 "](" 片段 —— 那些是畸形的、含引号/空格的
         危险输入。不原样回显，不留裸引号。
    第 3 步必须在第 2 步之后，否则会把合法链接一起吃掉。
    """
    out = html.escape(str(text or ""), quote=False)

    for pattern, repl in _MD_RULES:
        out = pattern.sub(repl, out)

    # 未被任何规则消费的链接片段 → 畸形输入，整体丢弃。
    # 末尾可选的 \) 一并吃掉，避免留下孤立的右括号噪音。
    out = re.sub(r'!?\[[^\]]*\]\([^)]*\)?\)?', '', out)

    # 段落：连续两个换行分段
    blocks = re.split(r"\n\s*\n", out)
    return "\n".join(f"<p>{b.strip()}</p>" if b.strip() and not b.strip().startswith("<h")
                     else b.strip() for b in blocks if b.strip())
