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

    spec = importlib.util.spec_from_file_location("alpaca_saas_short_strategy_engine_test", SCRIPT_DIR / "strategy_engine.py")
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


module = _load_strategy_engine_module()


class _FakeStorage:
    def __init__(self, latest_orders=None):
        self.latest_orders = latest_orders or []

    def get_latest_selected_orders(self, mode="live"):
        return list(self.latest_orders)


class _FakeSeren:
    def __init__(self, *, account, open_orders=None):
        self.account = account
        self.open_orders = open_orders or []
        self.cancelled = []

    def call_publisher(self, publisher, method="GET", path="/", timeout=30, **kwargs):
        del publisher, timeout, kwargs
        if method == "GET" and path == "/v2/account":
            return self.account
        if method == "GET" and path.startswith("/v2/orders?"):
            return list(self.open_orders)
        if method == "DELETE" and path.startswith("/v2/orders/"):
            order_id = path.rsplit("/", 1)[-1]
            self.cancelled.append(order_id)
            return {"id": order_id, "status": "cancelled"}
        raise AssertionError((method, path))


def _build_engine(tmp_path: Path):
    engine = module.StrategyEngine.__new__(module.StrategyEngine)
    engine.storage = _FakeStorage()
    engine.strict_required_feeds = True
    engine.api_key = "sb_test"
    engine.seren = _FakeSeren(account={})
    engine.live_controls = module.StrategyEngine._normalize_live_controls({})
    engine.live_safety_state = {}
    module.LIVE_SAFETY_STATE_PATH = tmp_path / "live_safety_state.json"
    return engine


def test_live_preflight_blocks_when_buying_power_reserve_breaches(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    engine.seren = _FakeSeren(account={"equity": "100000", "buying_power": "5000"})
    engine.live_controls = module.StrategyEngine._normalize_live_controls({"min_buying_power_usd": 1000})

    orders = [
        {
            "order_ref": "crm-live-1",
            "ticker": "CRM",
            "side": "SELL",
            "qty": 10,
            "limit_price": 500,
            "details": {"entry_price": 500},
        }
    ]

    with pytest.raises(module.LiveRiskError):
        engine._compute_live_risk(orders)


def test_live_cleanup_cancels_matching_strategy_refs(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    engine.seren = _FakeSeren(
        account={"equity": "100000", "buying_power": "50000"},
        open_orders=[
            {"id": "alpaca-1", "client_order_id": "crm-live-1"},
            {"id": "alpaca-2", "client_order_id": "ignore-me"},
        ],
    )

    cancelled = engine._cancel_live_orders(["crm-live-1"])

    assert engine.seren.cancelled == ["alpaca-1"]
    assert cancelled == [
        {
            "order_id": "alpaca-1",
            "client_order_id": "crm-live-1",
            "result": {"id": "alpaca-1", "status": "cancelled"},
        }
    ]


def test_live_mode_requires_allow_live_flag() -> None:
    with pytest.raises(SystemExit, match="--allow-live"):
        module._require_live_confirmation("live", False)


def test_cancel_all_live_orders_stops_trading(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    engine.storage = _FakeStorage(latest_orders=[{"order_ref": "crm-live-1"}])
    engine.seren = _FakeSeren(
        account={"equity": "100000", "buying_power": "50000"},
        open_orders=[{"id": "alpaca-1", "client_order_id": "crm-live-1"}],
    )

    result = engine.cancel_all_live_orders()

    assert "stop trading" in result["message"]
    assert result["cancelled_live_orders"][0]["order_id"] == "alpaca-1"
