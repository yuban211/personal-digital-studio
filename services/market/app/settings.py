"""集中配置：全部环境变量在这里读取，带默认值与类型收敛。

设计原则（与起始页 V2 的 config.py 同一套规矩）：
  - 敏感值不写死默认值，缺失即拒绝启动（API_TOKEN 除外，留空＝不鉴权）
  - 所有路径用 Path，不做字符串拼接
  - 一个模块只负责"读配置"，不掺杂业务
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ── 服务监听 ──
    host: str = "127.0.0.1"
    port: int = 9110

    # ── 事件总线 ──
    # bus=auto：REDIS_URL 非空用 Redis，否则用进程内广播
    # bus=redis：强制 Redis（REDIS_URL 为空则启动失败）
    # bus=memory：强制进程内广播
    bus: str = "auto"
    redis_url: str = ""
    bus_queue_size: int = 256

    # ── 行情采集 ──
    # 逗号分隔的标的代码，如 600519.SH,000858.SZ
    symbols: tuple[str, ...] = ()
    #: 采集 worker 数；每个 worker 独占一个 easy_tdx 连接
    workers: int = 2
    #: 轮询间隔（秒）。免费节点建议 ≥3，避免被限流踢下线。
    poll_interval: float = 3.0
    #: 协议：mac（MAC 二进制，实测能取到实时报价，默认）/ std（标准 7709，节点多但 1.10.0 的批量报价返回空）
    protocol: str = "mac"
    #: 自定义节点池（逗号分隔）。留空＝用 easy_tdx 内置池 + 标准池里实测兼容的节点
    hosts: tuple[str, ...] = ()
    #: 单次请求的最大标的数。通达信批量报价上限 80。
    batch_size: int = 80
    #: 采集失败后的退避基数（秒），实际间隔为 base * 2^连续失败次数，封顶 60
    backoff_base: float = 1.0
    backoff_max: float = 60.0

    # ── 历史数据湖（CNEquity）──
    #: 数据湖根目录，对应 cne config create --data-root <root>
    lake_root: Path = field(default_factory=lambda: Path(os.getenv("CNE_LAKE_ROOT", "D:/cnequity")))
    #: CNEquity 配置文件（绝对路径），用于 cnequity.query
    lake_config: str = ""

    # ── 本地运行数据 ──
    #: 节点排名缓存、easy_tdx 配置目录等本地状态
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("MARKET_DATA_DIR", "./data")))
    #: 节点排名缓存有效期（秒）。免费节点可达性会变，过期重测
    host_ttl: float = 3600.0
    #: 参与测速的候选节点上限（按延迟取前 N）
    host_top: int = 12
    #: easy_tdx 单次请求超时（秒）
    tdx_timeout: float = 5.0

    # ── 鉴权与限流 ──
    #: 非空时 REST/WS 需要 ?token= 或 X-API-Token
    api_token: str = ""
    #: 快照超过该秒数视为陈旧，REST 返回 503 而不是给过期价
    stale_after: float = 15.0

    @property
    def bus_mode(self) -> str:
        mode = self.bus.strip().lower()
        if mode not in ("auto", "redis", "memory"):
            mode = "auto"
        if mode == "auto":
            return "redis" if self.redis_url else "memory"
        return mode

    @property
    def lake_available(self) -> bool:
        return self.lake_root.exists()


def load_settings() -> Settings:
    raw_symbols = os.getenv("TDX_SYMBOLS", "")
    symbols = tuple(s.strip().upper() for s in raw_symbols.split(",") if s.strip())

    settings = Settings(
        host=os.getenv("MARKET_HOST", "127.0.0.1"),
        port=_env_int("MARKET_PORT", 9110),
        bus=os.getenv("MARKET_BUS", "auto"),
        redis_url=os.getenv("REDIS_URL", ""),
        bus_queue_size=_env_int("MARKET_BUS_QUEUE", 256),
        symbols=symbols,
        workers=max(1, _env_int("TDX_WORKERS", 2)),
        poll_interval=_env_float("TDX_POLL_INTERVAL", 3.0),
        protocol=os.getenv("TDX_PROTOCOL", "mac").strip().lower(),
        hosts=tuple(h.strip() for h in os.getenv("TDX_HOSTS", "").split(",") if h.strip()),
        batch_size=max(1, min(_env_int("TDX_BATCH_SIZE", 80), 80)),
        backoff_base=_env_float("TDX_BACKOFF_BASE", 1.0),
        backoff_max=_env_float("TDX_BACKOFF_MAX", 60.0),
        lake_root=Path(os.getenv("CNE_LAKE_ROOT", "D:/cnequity")),
        lake_config=os.getenv("CNE_CONFIG", ""),
        data_dir=Path(os.getenv("MARKET_DATA_DIR", "./data")),
        host_ttl=_env_float("TDX_HOST_TTL", 3600.0),
        host_top=max(1, _env_int("TDX_HOST_TOP", 12)),
        tdx_timeout=_env_float("TDX_TIMEOUT", 5.0),
        api_token=os.getenv("API_TOKEN", ""),
        stale_after=_env_float("MARKET_STALE_AFTER", 15.0),
    )

    if settings.protocol not in ("mac", "std"):
        settings = Settings(**{**settings.__dict__, "protocol": "mac"})
    return settings
