"""Small, dependency-light authentication and content-safety helpers."""
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

INSTANCE = Path(__file__).resolve().parent.parent / "instance"
#: 管理员凭据文件。可用 STUDIO_ADMIN_FILE 覆盖 —— 部署时 instance/ 目录
#: 可能只读（容器里归 root），需要一个可写位置来更新凭据。
ADMIN_FILE = Path(os.getenv("STUDIO_ADMIN_FILE") or (INSTANCE / "admin.json"))


def secret_key() -> bytes:
    value = os.getenv("APP_SECRET")
    if value:
        return value.encode("utf-8")
    # Development fallback is generated and persisted locally, never committed.
    INSTANCE.mkdir(parents=True, exist_ok=True)
    p = INSTANCE / ".secret"
    if not p.exists():
        p.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        try:
            p.chmod(0o600)
        except OSError:
            pass
    return p.read_text(encoding="utf-8").strip().encode("utf-8")


def hash_password(password: str, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return {"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode(), "scheme": "scrypt"}


def verify_password(password: str, record: dict) -> bool:
    try:
        salt = base64.b64decode(record["salt"])
        expected = base64.b64decode(record["hash"])
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (KeyError, ValueError, TypeError):
        return False


def read_admin() -> dict | None:
    if not ADMIN_FILE.exists():
        return None
    try:
        return json.loads(ADMIN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_admin(username: str, password: str) -> None:
    INSTANCE.mkdir(parents=True, exist_ok=True)
    data = {"username": username, "password": hash_password(password), "created_at": int(time.time())}
    ADMIN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        ADMIN_FILE.chmod(0o600)
    except OSError:
        pass


def sign(value: str) -> str:
    sig = hmac.new(secret_key(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def unsign(token: str) -> str | None:
    try:
        value, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret_key(), value.encode(), hashlib.sha256).hexdigest()
    return value if hmac.compare_digest(sig, expected) else None


def safe_markdown(source: str) -> str:
    """Tiny safe Markdown subset. Raw HTML is escaped before formatting."""
    import re
    source = (source or "")[:100_000]
    lines = source.splitlines()
    out, paragraph, code = [], [], []
    in_code = in_list = False

    def flush_paragraph():
        if paragraph:
            out.append("<p>" + " ".join(paragraph) + "</p>")
            paragraph.clear()

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def inline(text):
        safe = html.escape(text)
        safe = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', safe)
        safe = re.sub(r'\*(.+?)\*', r'<em>\1</em>', safe)
        safe = re.sub(r'`([^`]+)`', r'<code>\1</code>', safe)

        def image_replace(match):
            """![alt](url) — only safe image sources are rendered."""
            alt, url = match.group(1), match.group(2)
            if not safe_image_url(url):
                return alt
            return (
                f'<img src="{url}" alt="{alt}" loading="lazy" decoding="async">'
            )

        def link_replace(match):
            label, url = match.group(1), match.group(2)
            if valid_external_url(url):
                return f'<a href="{url}" rel="nofollow noopener noreferrer" target="_blank">{label}</a>'
            return label

        # Images first: their syntax contains a bracket pair that the link
        # pattern would otherwise consume.
        safe = re.sub(r'!\[([^\]]{0,200}?)\]\(([^\s)]+)\)', image_replace, safe)
        safe = re.sub(r'\[(.{1,200}?)\]\(([^\s)]+)\)', link_replace, safe)
        return safe

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            flush_paragraph(); close_list()
            if in_code:
                out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
                code, in_code = [], False
            else:
                in_code = True
            continue
        if in_code:
            code.append(html.escape(line)); continue
        if not line.strip():
            flush_paragraph(); close_list(); continue
        if line.startswith("### "):
            flush_paragraph(); close_list(); out.append("<h3>" + html.escape(line[4:]) + "</h3>"); continue
        if line.startswith("## "):
            flush_paragraph(); close_list(); out.append("<h2>" + html.escape(line[3:]) + "</h2>"); continue
        if line.startswith("# "):
            flush_paragraph(); close_list(); out.append("<h1>" + html.escape(line[2:]) + "</h1>"); continue
        if line.startswith("- "):
            flush_paragraph()
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append("<li>" + inline(line[2:]) + "</li>")
            continue
        close_list()
        paragraph.append(inline(line))
    flush_paragraph(); close_list()
    if in_code:
        out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
    return "\n".join(out)


def safe_image_url(url: str) -> bool:
    """Only allow images from the site's own media path or over http(s).

    Rejects `javascript:`, `data:` and protocol-relative URLs so a crafted
    Markdown body cannot execute script or exfiltrate through an image tag.
    """
    if not url or len(url) > 2048:
        return False
    u = url.strip()
    if not u or u.startswith("//") or u.startswith("\\") or "\n" in u or "\r" in u:
        return False
    # Site-relative media path (the only local shape the server serves).
    if u.startswith("/") and not u.startswith("//"):
        return re_safe_path(u)
    try:
        parsed = urlparse(u)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    return bool(parsed.netloc)


def re_safe_path(path: str) -> bool:
    """A relative URL must stay on this site: no traversal, no scheme."""
    import re
    if not path.startswith("/"):
        return False
    if path.startswith("//"):
        return False
    if re.match(r"^/+\.\./", path) or "/../" in path or path.endswith("/.."):
        return False
    return ":" not in path.split("/")[1] if len(path.split("/")) > 1 else True


def valid_external_url(url: str) -> bool:
    if not url:
        return True
    try:
        return urlparse(url).scheme in {"http", "https"} and bool(urlparse(url).netloc)
    except ValueError:
        return False
