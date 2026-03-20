from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
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


def _load_module(name: str, path: Path, *, clear_modules: tuple[str, ...] = ()) -> object:
    for module_name in clear_modules:
        sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
