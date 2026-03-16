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
        "seren_client",
        "serendb_bootstrap",
        "serendb_storage",
    ):
        sys.modules.pop(module_name, None)

    self_learning = types.ModuleType("self_learning")
    self_learning.ensure_champion = lambda conn: None
    self_learning.run_label_update = lambda conn, mode="paper-sim": {}
    sys.modules["self_learning"] = self_learning

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
    engine.storage = _FakeStorage(latest_orders=[{"qty": 4, "limit_price": 500, "details": {"entry_price": 500}}])
    engine.strict_required_feeds = True
    engine.api_key = "sb_test"
    engine.seren = _FakeSeren(account={})
    engine.live_controls = module.StrategyEngine._normalize_live_controls({})
    engine.live_safety_state = {}
    module.LIVE_SAFETY_STATE_PATH = tmp_path / "live_safety_state.json"
    return engine


def test_live_preflight_blocks_when_projected_gross_exceeds_cap(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    engine.seren = _FakeSeren(account={"equity": "100000", "buying_power": "50000"})
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
    engine.seren = _FakeSeren(
        account={"equity": "100000", "buying_power": "50000"},
        open_orders=[
            {"id": "alpaca-1", "client_order_id": "short-1"},
            {"id": "alpaca-2", "client_order_id": "hedge-1"},
            {"id": "alpaca-3", "client_order_id": "ignore-me"},
        ],
    )

    cancelled = engine._cancel_live_orders(["short-1", "hedge-1"])

    assert engine.seren.cancelled == ["alpaca-1", "alpaca-2"]
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
