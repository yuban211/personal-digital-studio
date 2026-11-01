"""配置：环境变量 → 强类型对象。全部有默认值，不配也能跑。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    # ── 运行 ──
    host: str = os.getenv("QUANT_HOST", "127.0.0.1")
    port: int = _i("QUANT_PORT", 9113)
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("QUANT_DATA_DIR", str(ROOT / "data"))))
    web_dir: Path = field(default_factory=lambda: ROOT / "web")
    strategy_dir: Path = field(default_factory=lambda: Path(os.getenv("QUANT_STRATEGY_DIR", str(ROOT / "strategies"))))

    # ── 账户 ──
    initial_cash: float = _f("QUANT_INIT_CASH", 1_000_000.0)
    commission_rate: float = _f("QUANT_COMMISSION", 0.00025)   # 万 2.5
    commission_min: float = _f("QUANT_COMMISSION_MIN", 5.0)    # 单笔最低 5 元
    stamp_rate: float = _f("QUANT_STAMP", 0.0005)               # 卖出印花税 0.05%
    transfer_rate: float = _f("QUANT_TRANSFER", 0.00001)        # 过户费 0.001%
    slippage_bps: float = _f("QUANT_SLIPPAGE_BPS", 5.0)         # 滑点 5bp

    # ── 行情 ──
    snapshot_ttl: float = _f("QUANT_SNAPSHOT_TTL", 20.0)        # 全A快照缓存秒数
    quote_ttl: float = _f("QUANT_QUOTE_TTL", 3.0)               # 指数/板块缓存秒数
    kline_ttl: float = _f("QUANT_KLINE_TTL", 900.0)             # 日线缓存秒数
    page_size: int = _i("QUANT_PAGE_SIZE", 100)                 # 东财单页上限就是 100
    fetch_workers: int = _i("QUANT_FETCH_WORKERS", 8)
    http_timeout: float = _f("QUANT_HTTP_TIMEOUT", 8.0)

    # ── 模拟盘 ──
    tick_seconds: float = _f("QUANT_TICK", 10.0)                # 引擎轮询间隔
    prefilter_top: int = _i("QUANT_PREFILTER_TOP", 300)          # 策略只看成交额前 N
    max_positions: int = _i("QUANT_MAX_POSITIONS", 8)
    position_pct: float = _f("QUANT_POSITION_PCT", 0.12)        # 单票目标仓位占净值比
    engine_enabled: bool = os.getenv("QUANT_ENGINE", "1").strip() not in ("0", "false", "no")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.strategy_dir.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "quant.db"


settings = Settings()
