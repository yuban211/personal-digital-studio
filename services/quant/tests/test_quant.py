"""量化服务单测：撮合规则、代码映射、策略加载。

不联网：行情由假数据注入，测试只验证"我们自己的逻辑"。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parent.parent.parent   # services/
sys.path.insert(0, str(SERVICES))

# 必须在 import app.* 之前指定独立数据目录，避免污染真实账户
os.environ["QUANT_DATA_DIR"] = tempfile.mkdtemp(prefix="quant-test-")
os.environ["QUANT_ENGINE"] = "0"

from quant.app.broker import fees, is_session, limit_pct  # noqa: E402
from quant.app.feed import join_code, split_code  # noqa: E402
from quant.app.settings import settings  # noqa: E402
from quant.app.store import store  # noqa: E402
from quant.app.strategy import Context, registry  # noqa: E402
from quant.app.broker import broker  # noqa: E402
from datetime import datetime  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_account():
    store.reset()
    yield
    store.reset()


def quote(price=10.0, chg=1.0, name="测试股"):
    return {"code": "600000", "market": 1, "name": name, "price": price,
            "chg": chg, "chg_amt": 0.1, "amount": 1e8}


class TestRules:
    def test_limit_pct_by_board(self):
        assert limit_pct("600519", "贵州茅台") == 10.0
        assert limit_pct("300750", "宁德时代") == 20.0
        assert limit_pct("688981", "中芯国际") == 20.0
        assert limit_pct("830799", "艾融软件") == 30.0
        assert limit_pct("600519", "ST某某") == 5.0

    def test_fees(self):
        # 买入：万2.5 佣金 + 过户费，无印花税
        assert fees("buy", 100000) == pytest.approx(25 + 1, abs=0.01)
        # 卖出多 0.05% 印花税
        assert fees("sell", 100000) == pytest.approx(25 + 1 + 50, abs=0.01)
        # 小额按最低 5 元佣金
        assert fees("buy", 1000) == pytest.approx(5 + 0.01, abs=0.01)

    def test_session_window(self):
        assert is_session(datetime(2026, 11, 2, 10, 0))       # 周一上午
        assert not is_session(datetime(2026, 11, 1, 10, 0))   # 周日上午
        assert not is_session(datetime(2026, 11, 2, 12, 0))   # 午休
        assert is_session(datetime(2026, 11, 2, 14, 30))

    def test_code_mapping(self):
        assert split_code("sz300030") == ("300030", 0)
        assert split_code("sh600519") == ("600519", 1)
        assert join_code("600519", 1) == "sh600519"
        assert join_code("300030", 0) == "sz300030"


class TestBroker:
    def test_buy_lot_rounding(self):
        store.set_cash(1_000_000)
        res = broker.order("600000", "buy", 150, quote(), src="test")
        assert res["ok"] and res["qty"] == 100

    def test_buy_too_small_rejected(self):
        store.set_cash(1_000_000)
        res = broker.order("600000", "buy", 50, quote(), src="test")
        assert not res["ok"] and "100 股" in res["reason"]

    def test_limit_up_blocks_buy(self):
        store.set_cash(1_000_000)
        res = broker.order("600000", "buy", 100, quote(chg=10.0), src="test")
        assert not res["ok"] and "涨停" in res["reason"]

    def test_limit_down_blocks_sell(self):
        store.set_cash(1_000_000)
        broker.order("600000", "buy", 100, quote(), src="test")
        store.unlock_all()
        res = broker.order("600000", "sell", 100, quote(price=9.0, chg=-10.0), src="test")
        assert not res["ok"] and "跌停" in res["reason"]

    def test_t_plus_one(self):
        store.set_cash(1_000_000)
        broker.order("600000", "buy", 100, quote(), src="test")
        res = broker.order("600000", "sell", 100, quote(), src="test")
        assert not res["ok"] and "T+1" in res["reason"]
        store.unlock_all()
        ok = broker.order("600000", "sell", 100, quote(), src="test")
        assert ok["ok"] and ok["qty"] == 100
        assert store.position("600000") is None

    def test_cash_not_enough(self):
        store.set_cash(500)
        res = broker.order("600000", "buy", 100, quote(price=10.0), src="test")
        assert not res["ok"] and "资金" in res["reason"]

    def test_account_mark_to_market(self):
        store.set_cash(1_000_000)
        broker.order("600000", "buy", 1000, quote(price=10.0), src="test")
        acct = broker.account({"600000": 11.0})
        assert acct["position_count"] == 1
        assert acct["market_value"] == pytest.approx(11000, abs=1)
        assert acct["pnl"] > 0


class TestStrategy:
    def test_builtin_and_external_loaded(self):
        keys = set(registry.all())
        assert {"momentum", "ma_cross", "breakout"} <= keys
        assert "example_rsi" in keys          # strategies/ 目录里的示例被自动加载

    def test_momentum_shape(self):
        strat = registry.get("momentum")

        bars = [{"date": f"d{i}", "open": 10, "close": 10 + i * 0.2,
                 "high": 10 + i * 0.2, "low": 9.9, "vol": 1000,
                 "amount": 1e7, "chg": 1.0} for i in range(30)]
        ctx = Context(
            rows=[{"code": "600000", "name": "测试股", "amount": 1e9, "price": 15.8,
                   "chg": 3.0, "vol_ratio": 2.0, "market": 1}],
            positions={}, equity=100000, params={"top_n": 3, "min_amount": 1e8},
            bars_fn=lambda code, n: bars,
        )
        out = strat.select(ctx)
        assert out and out[0]["side"] == "buy" and out[0]["code"] == "600000"

    def test_external_strategy_runs(self):
        strat = registry.get("example_rsi")
        bars = [{"date": f"d{i}", "open": 10, "close": 10 - i * 0.05, "high": 10,
                 "low": 9, "vol": 1, "amount": 1, "chg": 0} for i in range(40)]
        ctx = Context(rows=[{"code": "600000", "name": "测试", "amount": 1e9,
                             "price": 8, "chg": 1.0, "market": 1}],
                      positions={}, equity=100000, params={"top_n": 1},
                      bars_fn=lambda c, n: bars)
        assert isinstance(strat.select(ctx), list)
