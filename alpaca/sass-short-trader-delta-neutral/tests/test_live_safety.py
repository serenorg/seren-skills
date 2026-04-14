from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_strategy_engine_module():
    for module_name in (
        "self_learning",
        "backtest_optimizer",
        "alpaca_local_broker",
        "seren_client",
        "serendb_bootstrap",
        "serendb_storage",
    ):
        sys.modules.pop(module_name, None)

    self_learning = types.ModuleType("self_learning")
    self_learning.ensure_champion = lambda conn: None
    self_learning.run_label_update = lambda conn, mode="paper-sim": {}
    sys.modules["self_learning"] = self_learning

    backtest_optimizer = types.ModuleType("backtest_optimizer")
    backtest_optimizer.optimize_scan_config = lambda *args, **kwargs: {}
    sys.modules["backtest_optimizer"] = backtest_optimizer

    alpaca_local_broker = types.ModuleType("alpaca_local_broker")

    class _BrokerClient:
        @classmethod
        def from_env(cls):
            return None

    alpaca_local_broker.AlpacaLocalBrokerClient = _BrokerClient
    sys.modules["alpaca_local_broker"] = alpaca_local_broker

    seren_client = types.ModuleType("seren_client")

    class _SerenClient:
        @staticmethod
        def unwrap_body(resp):
            return resp.get("body", resp) if isinstance(resp, dict) else resp

    seren_client.SerenClient = _SerenClient
    sys.modules["seren_client"] = seren_client

    serendb_bootstrap = types.ModuleType("serendb_bootstrap")
    serendb_bootstrap.resolve_dsn = lambda **kwargs: "postgres://example"
    sys.modules["serendb_bootstrap"] = serendb_bootstrap

    serendb_storage = types.ModuleType("serendb_storage")

    class _Storage:
        def __init__(self, dsn):
            self.dsn = dsn

        def get_latest_selected_orders(self, mode="live"):
            return []

    serendb_storage.SerenDBStorage = _Storage
    sys.modules["serendb_storage"] = serendb_storage

    spec = importlib.util.spec_from_file_location(
        "alpaca_sass_short_dn_strategy_engine_test",
        SCRIPT_DIR / "strategy_engine.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


module = _load_strategy_engine_module()


class _FakeStorage:
    def __init__(self, latest_orders=None):
        self.latest_orders = latest_orders or []
        self.inserted_order_events = []
        self.position_mark_calls = []
        self.pnl_calls = []
        self.status_updates = []

    def get_latest_selected_orders(self, mode="live"):
        return list(self.latest_orders)

    def check_overlap(self, mode="paper-sim", run_type="scan"):
        del mode, run_type
        return None

    def insert_run(self, **kwargs):
        del kwargs
        return "run-live-test"

    def insert_candidate_scores(self, run_id, rows):
        del run_id, rows

    def insert_order_events(self, run_id, mode, events):
        self.inserted_order_events.append({"run_id": run_id, "mode": mode, "events": list(events)})

    def upsert_position_marks(self, *args, **kwargs):
        self.position_mark_calls.append({"args": args, "kwargs": kwargs})

    def upsert_pnl_daily(self, *args, **kwargs):
        self.pnl_calls.append({"args": args, "kwargs": kwargs})

    def update_run_status(self, run_id, status, metadata):
        self.status_updates.append({"run_id": run_id, "status": status, "metadata": metadata})


class _FakeSeren:
    def call_publisher(self, publisher, method="GET", path="/", timeout=30, **kwargs):
        del publisher, timeout, kwargs
        raise AssertionError((method, path))

    def extract_rows(self, response):
        del response
        return []


class _FakeBroker:
    def __init__(self, *, account=None, open_orders=None):
        self.account = account or {}
        self.open_orders = open_orders or []
        self.cancelled = []
        self.submitted = []

    def get_account(self, timeout=30):
        del timeout
        return dict(self.account)

    def list_orders(self, *, status="open", limit=500, nested=False, timeout=30):
        del status, limit, nested, timeout
        return list(self.open_orders)

    def cancel_order(self, order_id, timeout=30):
        del timeout
        self.cancelled.append(order_id)
        return {"id": order_id, "status": "cancelled"}

    def submit_order(self, order, timeout=30):
        del timeout
        self.submitted.append(dict(order))
        return {
            "id": f"alpaca-{len(self.submitted)}",
            "asset_id": f"asset-{order['symbol']}",
            "symbol": order["symbol"],
            "side": order["side"],
            "type": order["type"],
            "qty": order["qty"],
            "limit_price": order.get("limit_price"),
            "client_order_id": order["client_order_id"],
            "status": "new",
            "submitted_at": "2026-04-13T12:00:00Z",
        }


def _build_engine(tmp_path: Path):
    engine = module.StrategyEngine.__new__(module.StrategyEngine)
    engine.storage = _FakeStorage(latest_orders=[{"qty": 4, "limit_price": 500, "details": {"entry_price": 500}}])
    engine.strict_required_feeds = True
    engine.api_key = "sb_test"
    engine.broker = _FakeBroker()
    engine.seren = _FakeSeren()
    engine.live_controls = module.StrategyEngine._normalize_live_controls({})
    engine.live_safety_state = {}
    module.LIVE_SAFETY_STATE_PATH = tmp_path / "live_safety_state.json"
    return engine


def test_live_preflight_blocks_when_projected_gross_exceeds_cap(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    engine.broker = _FakeBroker(account={"equity": "100000", "buying_power": "50000"})
    engine.live_controls = module.StrategyEngine._normalize_live_controls({"max_live_gross_exposure_usd": 15000})

    orders = [
        {
            "order_ref": "short-1",
            "ticker": "CRM",
            "side": "SELL",
            "qty": 20,
            "limit_price": 500,
            "details": {"entry_price": 500},
        },
        {
            "order_ref": "hedge-1",
            "ticker": "QQQ",
            "side": "BUY",
            "qty": 10,
            "limit_price": 600,
            "details": {"entry_price": 600},
        },
    ]

    with pytest.raises(module.LiveRiskError):
        engine._compute_live_risk(orders)


def test_live_cleanup_cancels_matching_strategy_refs(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    engine.broker = _FakeBroker(
        open_orders=[
            {"id": "alpaca-1", "client_order_id": "short-1"},
            {"id": "alpaca-2", "client_order_id": "hedge-1"},
            {"id": "alpaca-3", "client_order_id": "ignore-me"},
        ],
    )

    cancelled = engine._cancel_live_orders(["short-1", "hedge-1"])

    assert engine.broker.cancelled == ["alpaca-1", "alpaca-2"]
    assert cancelled == [
        {
            "order_id": "alpaca-1",
            "client_order_id": "short-1",
            "result": {"id": "alpaca-1", "status": "cancelled"},
        },
        {
            "order_id": "alpaca-2",
            "client_order_id": "hedge-1",
            "result": {"id": "alpaca-2", "status": "cancelled"},
        },
    ]


def test_live_mode_requires_allow_live_flag() -> None:
    with pytest.raises(SystemExit, match="--allow-live"):
        module._require_live_confirmation("live", False)


def test_cancel_all_live_orders_stops_trading(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    engine.storage = _FakeStorage(latest_orders=[{"order_ref": "short-1"}, {"order_ref": "hedge-1"}])
    engine.broker = _FakeBroker(
        open_orders=[
            {"id": "alpaca-1", "client_order_id": "short-1"},
            {"id": "alpaca-2", "client_order_id": "hedge-1"},
        ]
    )

    result = engine.cancel_all_live_orders()

    assert "stop trading" in result["message"]
    assert [row["order_id"] for row in result["cancelled_live_orders"]] == ["alpaca-1", "alpaca-2"]


def test_market_feed_missing_seren_api_key_fails_closed(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    engine.seren = None

    result = engine.fetch_market_features(["CRM"])

    assert result.ok is False
    assert result.error == "SEREN_API_KEY missing"


def test_live_scan_submits_short_and_hedge_orders_locally(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    engine.broker = _FakeBroker(account={"equity": "100000", "buying_power": "50000"})
    engine.fetch_sec_features = lambda universe: module.FeedResult(ok=True, data={ticker: {} for ticker in universe})
    engine.fetch_trends_features = lambda universe: module.FeedResult(ok=True, data={ticker: {} for ticker in universe})
    engine.fetch_news_features = lambda universe: module.FeedResult(ok=True, data={"_source": "exa", **{ticker: {} for ticker in universe}})
    engine.fetch_market_features = lambda universe: module.FeedResult(ok=True, data={ticker: {"price": 500.0} for ticker in universe})
    engine.score_universe = lambda **kwargs: [{"ticker": "CRM", "selected": True}]
    engine.build_orders = lambda **kwargs: [
        {
            "order_ref": "short-1",
            "ticker": "CRM",
            "side": "SELL",
            "order_type": "limit",
            "status": "planned",
            "qty": 10.0,
            "limit_price": 500.0,
            "stop_price": 540.0,
            "filled_qty": None,
            "filled_avg_price": None,
            "is_simulated": False,
            "details": {"entry_price": 500.0, "planned_notional_usd": 5000.0, "leg_type": "short"},
        },
        {
            "order_ref": "hedge-1",
            "ticker": "QQQ",
            "side": "BUY",
            "order_type": "limit",
            "status": "planned",
            "qty": 5.0,
            "limit_price": 400.0,
            "stop_price": 380.0,
            "filled_qty": None,
            "filled_avg_price": None,
            "is_simulated": False,
            "details": {"entry_price": 400.0, "planned_notional_usd": 2000.0, "leg_type": "hedge"},
        },
    ]

    result = engine.run_scan(
        mode="live",
        universe=["CRM"],
        max_names_scored=1,
        max_names_orders=1,
        hedge_ticker="QQQ",
        hedge_ratio=0.4,
    )

    assert result["status"] == "completed"
    assert [row["client_order_id"] for row in engine.broker.submitted] == ["short-1", "hedge-1"]
    assert [row["side"] for row in engine.broker.submitted] == ["sell", "buy"]
    inserted = engine.storage.inserted_order_events[0]["events"]
    assert [row["order_id"] for row in inserted] == ["alpaca-1", "alpaca-2"]
    assert {row["side"] for row in inserted} == {"SELL", "BUY"}
    assert engine.storage.position_mark_calls == []
    assert engine.storage.pnl_calls == []
