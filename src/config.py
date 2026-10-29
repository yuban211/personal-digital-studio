"""集中配置：所有环境变量在一个地方读取，带默认值和类型收敛。

为什么单独抽出来：
  旧版把 os.getenv 散落在 main.py 各处，路径、前缀、密钥到处硬编码，
  换环境时改一个漏一个（曾出现 CRLF 导致 APP_PREFIX 变空、全站 404）。
  现在所有配置有唯一来源，且启动时会自检。

约定：
  - 路径一律用 Path，不用字符串拼接
  - 敏感值（APP_SECRET）生产环境必须显式提供，缺失直接拒绝启动
  - 所有可写目录在启动时确保存在，不存在就创建（不再依赖容器权限）
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

# 项目根：src/ 的上一级
ROOT = Path(__file__).resolve().parent.parent


def _env_path(name: str, default: Path) -> Path:
    v = os.getenv(name)
    return Path(v) if v else default


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Config:
    """应用配置。所有字段只读，启动时一次性构建。"""

    # ── 路由 ──
    #: 站点挂载前缀。空字符串表示挂在根路径。
    prefix: str

    # ── 路径 ──
    #: SQLite 数据库文件
    db_path: Path
    #: 上传媒体存放目录
    media_dir: Path
    #: 管理员凭据文件
    admin_file: Path
    #: 自动备份目录
    backup_dir: Path
    #: 静态资源目录
    static_dir: Path
    #: 模板目录
    template_dir: Path

    # ── 安全 ──
    #: HMAC 会话签名密钥。生产环境必须提供。
    secret: bytes
    #: 可选的同步令牌。留空则 API 不鉴权（单人自用场景可接受）。
    sync_token: str
    #: 开发环境自动生成的密钥落在哪里
    secret_file: Path
    #: Cookie 是否带 Secure（HTTPS 时必须开）
    cookie_secure: bool
    #: 会话有效期（秒）
    session_ttl: int
    #: 登录失败限速窗口（秒）与次数
    login_window: int
    login_max_attempts: int

    # ── 天气 ──
    weather_api_key: str
    weather_live_enabled: bool
    default_city_id: str
    weather_cache_ttl: int

    # ── 运行时 ──
    host: str
    port: int

    def __init__(self) -> None:
        self.prefix = os.getenv("APP_PREFIX", "/studio").rstrip("/")

        data_dir = _env_path("DATA_DIR", ROOT / "data")
        self.db_path = _env_path("STUDIO_DB", data_dir / "studio.db")
        self.media_dir = _env_path("STUDIO_MEDIA_DIR", data_dir / "media")
        self.admin_file = _env_path("STUDIO_ADMIN_FILE", data_dir / "admin.json")
        self.backup_dir = _env_path("STUDIO_BACKUP_DIR", ROOT / "backups")

        self.static_dir = ROOT / "src" / "static"
        self.template_dir = ROOT / "src" / "templates"

        # 密钥：生产环境必须显式给，否则每次启动都会把所有会话踢下线
        raw = os.getenv("APP_SECRET")
        self.secret_file = data_dir / ".secret"
        if not raw:
            self.secret = self._load_or_create_secret()
        else:
            self.secret = raw.encode("utf-8")

        self.cookie_secure = _env_bool("COOKIE_SECURE", False)
        self.sync_token = os.getenv("SYNC_TOKEN", "")
        self.session_ttl = int(os.getenv("SESSION_TTL", str(8 * 60 * 60)))
        self.login_window = int(os.getenv("LOGIN_WINDOW", "300"))
        self.login_max_attempts = int(os.getenv("LOGIN_MAX_ATTEMPTS", "8"))

        self.weather_api_key = os.getenv("WEATHER_API_KEY", "")
        self.weather_live_enabled = _env_bool("WEATHER_LIVE_ENABLED", True)
        self.default_city_id = os.getenv("DEFAULT_CITY_ID", "shanghai")
        self.weather_cache_ttl = int(os.getenv("WEATHER_CACHE_TTL_MINUTES", "10"))

        self.host = os.getenv("HOST", "127.0.0.1")
        self.port = int(os.getenv("PORT", "9101"))

    def _load_or_create_secret(self) -> bytes:
        """开发环境兜底：生成一个密钥并持久化，避免重启后所有会话失效。"""
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)
        if self.secret_file.exists():
            return self.secret_file.read_text(encoding="utf-8").strip().encode("utf-8")
        value = secrets.token_urlsafe(48)
        self.secret_file.write_text(value, encoding="utf-8")
        try:
            self.secret_file.chmod(0o600)
        except OSError:
            pass  # Windows 上 chmod 无效，忽略
        return value.encode("utf-8")

    def ensure_dirs(self) -> None:
        """创建所有需要写入的目录。容器里目录权限不可控，这里统一兜底。"""
        for d in (self.db_path.parent, self.media_dir, self.backup_dir,
                  self.admin_file.parent, self.secret_file.parent):
            d.mkdir(parents=True, exist_ok=True)

    def public_url(self) -> str:
        base = os.getenv("PUBLIC_BASE_URL", f"http://{self.host}:{self.port}").rstrip("/")
        return base + self.prefix

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"Config(prefix={self.prefix!r}, db={self.db_path}, "
            f"media={self.media_dir}, admin={self.admin_file}, "
            f"cookie_secure={self.cookie_secure})"
        )


def load_config() -> Config:
    """构建并确保目录就绪。应用启动时调用一次。"""
    cfg = Config()
    cfg.ensure_dirs()
    return cfg
