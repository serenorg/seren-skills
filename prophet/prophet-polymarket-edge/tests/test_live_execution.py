"""Critical tests for the prophet-polymarket-edge live execution path.

Covers only the wiring that issue #458 introduces. Tests that would
re-cover behavior already pinned by `test_trading_safety.py` or
`test_critical.py` are intentionally omitted.

Coverage map (issue #458 -> test):

- AC live exec emits `live_executions`     -> test_yes_live_emits_live_executions_when_gates_pass
- AC sizing respects max_kelly_fraction    -> test_position_sizing_respects_kelly_fraction
- AC sizing respects max_position_notional -> test_position_sizing_respects_per_market_cap
- AC no consensus_direction => no trade    -> test_skips_market_without_consensus_direction
- AC token_id required to submit order     -> test_skips_market_without_token_id
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"


def _load_agent_module(name: str = "prophet_polymarket_edge_agent_for_live_exec"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def agent():
    return _load_agent_module()


def _full_passing_risk() -> Dict[str, Any]:
    return {
        "max_kelly_fraction": 0.05,
        "max_position_notional_usd": 50.0,
        "min_mid_price": 0.30,
        "max_mid_price": 0.70,
        "min_daily_volume_usd": 10000.0,
        "min_seconds_to_resolution": 14 * 24 * 60 * 60,
        "max_inventory_hold_cycles": 3,
    }


def _surface_c_rows() -> List[Dict[str, Any]]:
    return [
        {
            "canonical_id": "row-tradable",
            "market_description": "Will X happen by Q3 2026?",
            "polymarket_token_id": "0xtoken_tradable",
            "polymarket_url": "https://polymarket.com/event/x",
            "polymarket_price": 0.40,
            "consensus_probability": 0.55,
            "consensus_direction": "yes",
            "divergence_bps": 1500,
            "freshness_note": "fresh <30m",
        },
    ]


def test_position_sizing_respects_kelly_fraction(agent) -> None:
    """Per-market notional must not exceed `bankroll * max_kelly_fraction`.

    The skill must never plan a trade larger than the explicit Kelly cap,
    regardless of how attractive the divergence looks.
    """
    plans = agent.compute_live_executions(
        surface_c=_surface_c_rows(),
        risk=_full_passing_risk(),
        bankroll_usd=1000.0,
        min_divergence_bps=500,
    )
    assert len(plans) == 1
    kelly_cap = 1000.0 * 0.05  # bankroll * max_kelly_fraction
    assert plans[0]["notional_usd"] <= kelly_cap + 1e-9


def test_position_sizing_respects_per_market_cap(agent) -> None:
    """Per-market notional must not exceed `max_position_notional_usd`,
    even when Kelly would allow more.
    """
    risk = _full_passing_risk()
    risk["max_position_notional_usd"] = 5.0  # very tight cap
    plans = agent.compute_live_executions(
        surface_c=_surface_c_rows(),
        risk=risk,
        bankroll_usd=10_000.0,  # Kelly would allow 500
        min_divergence_bps=500,
    )
    assert len(plans) == 1
    assert plans[0]["notional_usd"] <= 5.0 + 1e-9


def test_skips_market_without_consensus_direction(agent) -> None:
    """Without a consensus_direction the skill cannot pick a side, so no
    order is planned — even when divergence is large.
    """
    rows = _surface_c_rows()
    rows[0] = {**rows[0], "consensus_direction": None}
    plans = agent.compute_live_executions(
        surface_c=rows,
        risk=_full_passing_risk(),
        bankroll_usd=1000.0,
        min_divergence_bps=500,
    )
    assert plans == []


def test_skips_market_without_token_id(agent) -> None:
    """The CLOB requires a token_id; rows without one are skipped silently
    rather than submitting an unfillable order.
    """
    rows = _surface_c_rows()
    rows[0] = {**rows[0], "polymarket_token_id": None}
    plans = agent.compute_live_executions(
        surface_c=rows,
        risk=_full_passing_risk(),
        bankroll_usd=1000.0,
        min_divergence_bps=500,
    )
    assert plans == []


def test_yes_live_emits_live_executions_when_gates_pass(agent, monkeypatch, capsys) -> None:
    """End-to-end: with all trading-safety gates passing AND a CLOB stub,
    `--yes-live` no longer exits 2; it submits planned orders and prints
    a structured summary including a `live_executions` list.
    """
    # Pass risk-framework gate via config.
    config_path = Path("/tmp/test_yes_live_config.json")
    config_path.write_text(
        '{"risk": {'
        '"max_kelly_fraction": 0.05, '
        '"max_position_notional_usd": 50.0, '
        '"min_mid_price": 0.30, '
        '"max_mid_price": 0.70, '
        '"min_daily_volume_usd": 10000.0, '
        '"min_seconds_to_resolution": 1209600, '
        '"max_inventory_hold_cycles": 3'
        '}, "live": {"min_divergence_bps": 500, "bankroll_usd": 1000.0}}',
        encoding="utf-8",
    )

    # Pass execution-path gate: stub py_clob_client import + POLY_* env.
    fake_module = type("FakeClob", (), {})
    monkeypatch.setitem(sys.modules, "py_clob_client", fake_module)
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("POLY_API_KEY", "key")
    monkeypatch.setenv("POLY_PASSPHRASE", "pass")
    monkeypatch.setenv("POLY_SECRET", "secret")

    submitted: List[Dict[str, Any]] = []

    class StubTrader:
        def __init__(self, **_: Any) -> None:
            pass

        def create_order(self, *, token_id: str, side: str, price: float, size: float) -> Dict[str, Any]:
            submitted.append({"token_id": token_id, "side": side, "price": price, "size": size})
            return {"order_id": f"order-{len(submitted)}", "status": "submitted"}

    monkeypatch.setattr(agent, "DirectClobTrader", StubTrader)

    # Stub the read-only Surface C output that the live path consumes.
    monkeypatch.setattr(
        agent,
        "fetch_surface_c_for_live_execution",
        lambda config: _surface_c_rows(),
    )

    rc = agent.main(["--yes-live", "--config", str(config_path)])
    out = capsys.readouterr().out
    assert rc == 0, f"expected rc=0, got {rc}; stdout={out!r}"
    assert '"live_executions"' in out
    assert len(submitted) == 1
    assert submitted[0]["token_id"] == "0xtoken_tradable"
    assert submitted[0]["side"] == "BUY"
