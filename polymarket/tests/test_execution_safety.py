from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest


POLYMARKET_ROOT = Path(__file__).resolve().parents[1]

LIVE_MODULE_PATHS = {
    "polymarket-bot": POLYMARKET_ROOT / "bot" / "scripts" / "polymarket_live.py",
    "polymarket-maker-rebate-bot": POLYMARKET_ROOT / "maker-rebate-bot" / "scripts" / "polymarket_live.py",
    "liquidity-paired-basis-maker": POLYMARKET_ROOT / "liquidity-paired-basis-maker" / "scripts" / "polymarket_live.py",
    "high-throughput-paired-basis-maker": POLYMARKET_ROOT / "high-throughput-paired-basis-maker" / "scripts" / "polymarket_live.py",
    "paired-market-basis-maker": POLYMARKET_ROOT / "paired-market-basis-maker" / "scripts" / "polymarket_live.py",
}

UNWIND_AGENT_PATHS = {
    "polymarket-maker-rebate-bot": POLYMARKET_ROOT / "maker-rebate-bot" / "scripts" / "agent.py",
    "liquidity-paired-basis-maker": POLYMARKET_ROOT / "liquidity-paired-basis-maker" / "scripts" / "agent.py",
    "high-throughput-paired-basis-maker": POLYMARKET_ROOT / "high-throughput-paired-basis-maker" / "scripts" / "agent.py",
    "paired-market-basis-maker": POLYMARKET_ROOT / "paired-market-basis-maker" / "scripts" / "agent.py",
}
BOT_AGENT_PATH = POLYMARKET_ROOT / "bot" / "scripts" / "agent.py"


class _JsonResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _load_module(name: str, path: Path, *, clear_modules: tuple[str, ...] = ()) -> object:
    for module_name in clear_modules:
        sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_bot_agent_module() -> object:
    for module_name in (
        "dotenv",
        "seren_client",
        "polymarket_client",
        "position_tracker",
        "logger",
        "serendb_storage",
        "kelly",
    ):
        sys.modules.pop(module_name, None)

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv

    seren_client = types.ModuleType("seren_client")
    seren_client.SerenClient = object
    sys.modules["seren_client"] = seren_client

    polymarket_client = types.ModuleType("polymarket_client")
    polymarket_client.PolymarketClient = object
    sys.modules["polymarket_client"] = polymarket_client

    position_tracker = types.ModuleType("position_tracker")
    position_tracker.PositionTracker = object
    sys.modules["position_tracker"] = position_tracker

    logger = types.ModuleType("logger")
    logger.TradingLogger = object
    sys.modules["logger"] = logger

    serendb_storage = types.ModuleType("serendb_storage")
    serendb_storage.SerenDBStorage = object
    sys.modules["serendb_storage"] = serendb_storage

    sys.modules["kelly"] = types.ModuleType("kelly")

    return _load_module(
        "polymarket_bot_agent_test",
        BOT_AGENT_PATH,
        clear_modules=("agent",),
    )


@pytest.mark.parametrize("skill_slug", sorted(LIVE_MODULE_PATHS))
def test_marketable_sell_plan_uses_min_tick_and_full_bid_sweep(
    skill_slug: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        f"{skill_slug.replace('-', '_')}_live_test",
        LIVE_MODULE_PATHS[skill_slug],
        clear_modules=("polymarket_live",),
    )

    monkeypatch.setattr(
        module,
        "fetch_book",
        lambda token_id, timeout_seconds=30.0: {
            "best_bid": 0.35,
            "best_ask": 0.36,
            "tick_size": "0.01",
            "neg_risk": False,
            "raw": {
                "bids": [
                    {"price": "0.35", "size": "10"},
                    {"price": "0.30", "size": "5"},
                ],
                "asks": [],
            },
        },
    )
    monkeypatch.setattr(module, "fetch_fee_rate_bps", lambda token_id, timeout_seconds=30.0: 7)

    plan = module.build_marketable_sell_order("TOKEN-1", 12.0, timeout_seconds=1.0)

    assert plan["price"] == pytest.approx(0.01)
    assert plan["tick_size"] == "0.01"
    assert plan["best_bid"] == pytest.approx(0.35)
    assert plan["estimated_exit_value_usd"] == pytest.approx(4.1)
    assert plan["estimated_fill_size"] == pytest.approx(12.0)
    assert plan["estimated_unfilled_size"] == pytest.approx(0.0)
    assert plan["execution_style"] == "marketable-limit-min-tick"


@pytest.mark.parametrize("skill_slug", sorted(LIVE_MODULE_PATHS))
def test_neg_risk_approval_check_prefers_seren_polygon_publisher_when_funded(
    skill_slug: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        f"{skill_slug.replace('-', '_')}_seren_polygon_test",
        LIVE_MODULE_PATHS[skill_slug],
        clear_modules=("polymarket_live",),
    )
    publisher_calls: list[tuple[str, str, str, str]] = []

    monkeypatch.setattr(module, "get_seren_prepaid_balance", lambda **kwargs: 5.0)
    monkeypatch.setattr(
        module,
        "discover_seren_polygon_publisher",
        lambda **kwargs: "seren-polygon",
    )

    def fake_call_publisher_json(
        publisher: str,
        method: str,
        path: str,
        headers=None,
        body=None,
        timeout_seconds: float = 30.0,
    ):
        rpc_method = body["method"]
        publisher_calls.append((publisher, method, path, rpc_method))
        if rpc_method == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x89"}
        if body["params"][0]["to"] == module.POLYGON_USDC_E:
            return {"jsonrpc": "2.0", "id": 1, "result": hex(2 * (10 ** module.USDC_DECIMALS))}
        return {"jsonrpc": "2.0", "id": 1, "result": "0x1"}

    monkeypatch.setattr(module, "call_publisher_json", fake_call_publisher_json)
    monkeypatch.setattr(
        module,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("public fallback should not be used")),
    )

    result = module.check_neg_risk_approvals("0x" + ("1" * 40))

    assert result["checks_passed"] is True
    assert result["rpc_transport"] == "seren-publisher"
    assert result["rpc_publisher"] == "seren-polygon"
    assert all(call[0] == "seren-polygon" for call in publisher_calls)
    assert [call[3] for call in publisher_calls] == ["eth_chainId", "eth_call", "eth_call"]


@pytest.mark.parametrize("skill_slug", sorted(LIVE_MODULE_PATHS))
def test_neg_risk_approval_check_uses_seren_polygon_publisher_even_without_seren_funding(
    skill_slug: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        f"{skill_slug.replace('-', '_')}_seren_polygon_zero_balance_test",
        LIVE_MODULE_PATHS[skill_slug],
        clear_modules=("polymarket_live",),
    )
    publisher_calls: list[tuple[str, str, str, str]] = []

    monkeypatch.setattr(module, "get_seren_prepaid_balance", lambda **kwargs: 0.0)
    monkeypatch.setattr(module, "discover_seren_polygon_publisher", lambda **kwargs: "seren-polygon")

    def fake_call_publisher_json(
        publisher: str,
        method: str,
        path: str,
        headers=None,
        body=None,
        timeout_seconds: float = 30.0,
    ):
        rpc_method = body["method"]
        publisher_calls.append((publisher, method, path, rpc_method))
        if rpc_method == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x89"}
        if body["params"][0]["to"] == module.POLYGON_USDC_E:
            return {"jsonrpc": "2.0", "id": 1, "result": hex(2 * (10 ** module.USDC_DECIMALS))}
        return {"jsonrpc": "2.0", "id": 1, "result": "0x1"}

    monkeypatch.setattr(module, "call_publisher_json", fake_call_publisher_json)
    monkeypatch.setattr(
        module,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("public fallback should not be used")),
    )

    result = module.check_neg_risk_approvals("0x" + ("2" * 40))

    assert result["checks_passed"] is True
    assert result["rpc_transport"] == "seren-publisher"
    assert result["rpc_publisher"] == "seren-polygon"
    assert all(call[0] == "seren-polygon" for call in publisher_calls)
    assert [call[3] for call in publisher_calls] == ["eth_chainId", "eth_call", "eth_call"]


@pytest.mark.parametrize("skill_slug", sorted(LIVE_MODULE_PATHS))
def test_neg_risk_approval_check_blocks_unfunded_public_rpc_without_explicit_opt_in(
    skill_slug: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        f"{skill_slug.replace('-', '_')}_public_polygon_opt_in_required_test",
        LIVE_MODULE_PATHS[skill_slug],
        clear_modules=("polymarket_live",),
    )
    monkeypatch.setattr(module, "get_seren_prepaid_balance", lambda **kwargs: 0.0)
    monkeypatch.setattr(module, "discover_seren_polygon_publisher", lambda **kwargs: "")
    monkeypatch.delenv(module.POLYMARKET_ALLOW_PUBLIC_RPC_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(
        module,
        "call_publisher_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Seren publisher should not be used")),
    )
    monkeypatch.setattr(
        module,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("public fallback should not be used without opt-in")),
    )

    result = module.check_neg_risk_approvals("0x" + ("4" * 40))

    assert result["checks_passed"] is False
    assert result["rpc_transport"] == "public-disabled"
    assert result["rpc_public_opt_in_required"] is True
    assert any("https://serendb.com/serenbucks" in error for error in result["errors"])
    assert any("https://console.serendb.com" in error for error in result["errors"])
    assert any("$5.00" in error for error in result["errors"])
    assert any("verified email" in error for error in result["errors"])
    assert any("POST /wallet/deposit" in error for error in result["errors"])
    assert any(module.POLYMARKET_ALLOW_PUBLIC_RPC_FALLBACK_ENV in error for error in result["errors"])


@pytest.mark.parametrize("skill_slug", sorted(LIVE_MODULE_PATHS))
def test_neg_risk_approval_check_flags_public_rpc_zero_state_as_non_authoritative(
    skill_slug: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        f"{skill_slug.replace('-', '_')}_public_polygon_warning_test",
        LIVE_MODULE_PATHS[skill_slug],
        clear_modules=("polymarket_live",),
    )
    public_rpc_methods: list[str] = []

    monkeypatch.setattr(module, "get_seren_prepaid_balance", lambda **kwargs: 0.0)
    monkeypatch.setattr(module, "discover_seren_polygon_publisher", lambda **kwargs: "")
    monkeypatch.setenv(module.POLYMARKET_ALLOW_PUBLIC_RPC_FALLBACK_ENV, "1")
    monkeypatch.setattr(
        module,
        "call_publisher_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Seren publisher should not be used")),
    )

    def fake_urlopen(request, timeout: float = 10.0):
        payload = json.loads(request.data.decode("utf-8"))
        public_rpc_methods.append(payload["method"])
        if payload["method"] == "eth_getTransactionCount":
            return _JsonResponse({"jsonrpc": "2.0", "id": 1, "result": "0x3"})
        return _JsonResponse({"jsonrpc": "2.0", "id": 1, "result": "0x0"})

    monkeypatch.setattr(module, "urlopen", fake_urlopen)

    result = module.check_neg_risk_approvals("0x" + ("2" * 40))

    assert result["checks_passed"] is False
    assert result["rpc_transport"] == "public"
    assert result["wallet_nonce"] == 3
    assert any("not being treated as authoritative" in error for error in result["errors"])
    assert public_rpc_methods == ["eth_call", "eth_call", "eth_getTransactionCount"]
    assert not any("not approved" in error.lower() for error in result["errors"])


@pytest.mark.parametrize("skill_slug", sorted(LIVE_MODULE_PATHS))
def test_neg_risk_approval_check_fails_closed_when_seren_and_public_rpc_reads_fail(
    skill_slug: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        f"{skill_slug.replace('-', '_')}_polygon_rpc_failure_test",
        LIVE_MODULE_PATHS[skill_slug],
        clear_modules=("polymarket_live",),
    )

    public_rpc_methods: list[str] = []

    monkeypatch.setattr(module, "get_seren_prepaid_balance", lambda **kwargs: 5.0)
    monkeypatch.setattr(
        module,
        "discover_seren_polygon_publisher",
        lambda **kwargs: "seren-polygon",
    )
    monkeypatch.setattr(
        module,
        "call_publisher_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("seren rpc unavailable")),
    )
    monkeypatch.setattr(
        module,
        "urlopen",
        lambda request, *args, **kwargs: (
            public_rpc_methods.append(json.loads(request.data.decode("utf-8"))["method"]),
            (_ for _ in ()).throw(OSError("public rpc unavailable")),
        )[1],
    )

    result = module.check_neg_risk_approvals("0x" + ("3" * 40))

    assert result["checks_passed"] is False
    assert result["rpc_transport"] == "public"
    assert result["errors"]
    assert any("Polygon RPC read failed" in error for error in result["errors"])
    assert "probe failed" in str(result.get("rpc_fallback_reason", ""))
    assert public_rpc_methods == ["eth_call"]
    assert not any("not approved" in error.lower() for error in result["errors"])


@pytest.mark.parametrize("skill_slug", sorted(UNWIND_AGENT_PATHS))
def test_unwind_all_requires_yes_live_confirmation(
    skill_slug: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        f"{skill_slug.replace('-', '_')}_agent_test",
        UNWIND_AGENT_PATHS[skill_slug],
        clear_modules=("polymarket_live", "pair_stateful_replay"),
    )

    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: argparse.Namespace(
            config="config.json",
            run_type="trade",
            yes_live=False,
            unwind_all=True,
            markets_file=None,
            backtest_file=None,
            backtest_days=None,
            allow_negative_backtest=False,
        ),
    )
    monkeypatch.setattr(module, "load_config", lambda path: {})

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = module.main()
    payload = json.loads(stdout.getvalue())

    assert exit_code == 1
    assert payload["status"] == "error"
    assert payload["error_code"] == "unwind_confirmation_required"


@pytest.mark.parametrize("skill_slug", sorted(UNWIND_AGENT_PATHS))
def test_unwind_all_uses_marketable_sell_plan(
    skill_slug: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        f"{skill_slug.replace('-', '_')}_agent_unwind_test",
        UNWIND_AGENT_PATHS[skill_slug],
        clear_modules=("polymarket_live", "pair_stateful_replay"),
    )
    captured_order: dict[str, object] = {}

    class FakeTrader:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def cancel_all(self) -> dict[str, object]:
            return {"cancelled": True}

        def get_positions(self) -> list[dict[str, object]]:
            return [{"asset_id": "TOKEN-1", "size": 3.0}]

        def create_order(self, **kwargs) -> dict[str, object]:
            captured_order.update(kwargs)
            return {"orderID": "ORDER-1"}

    monkeypatch.setattr(module, "DirectClobTrader", FakeTrader)
    monkeypatch.setattr(module, "positions_by_key", lambda raw_positions: {"TOKEN-1": 3.0})
    monkeypatch.setattr(
        module,
        "build_marketable_sell_order",
        lambda token_id, shares: {
            "price": 0.01,
            "tick_size": "0.01",
            "neg_risk": False,
            "fee_rate_bps": 7,
            "best_bid": 0.35,
            "best_ask": 0.36,
            "estimated_exit_value_usd": 1.02,
            "estimated_fill_size": 3.0,
            "estimated_unfilled_size": 0.0,
            "estimated_average_price": 0.34,
            "execution_style": "marketable-limit-min-tick",
        },
    )

    result = module.run_unwind_all(config={})

    assert result["status"] == "ok"
    assert captured_order["price"] == pytest.approx(0.01)
    assert captured_order["tick_size"] == "0.01"
    assert captured_order["fee_rate_bps"] == 7
    assert result["sell_results"][0]["estimated_exit_value_usd"] == pytest.approx(1.02)
    assert result["sell_results"][0]["execution_style"] == "marketable-limit-min-tick"


def test_polymarket_bot_requires_yes_live_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_bot_agent_module()
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["agent.py", "--config", str(config_path)])

    stdout = io.StringIO()
    with pytest.raises(SystemExit) as exc, redirect_stdout(stdout):
        module.main()

    assert exc.value.code == 1
    assert "--yes-live" in stdout.getvalue()
