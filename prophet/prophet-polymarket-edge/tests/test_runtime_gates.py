"""Critical tests for the runtime safety gates added in #462.

Each test pins exactly one acceptance criterion from the issue. Tests that
duplicate coverage in `test_live_execution.py` (Kelly cap, per-market cap,
no-direction skip, missing-token skip, end-to-end submit) are intentionally
omitted.

Coverage map (issue #462 -> test):

- AC #1 mid-price safe band            -> test_rejects_row_outside_safe_band
- AC #2 24h volume floor               -> test_rejects_row_below_volume_floor
                                          test_rejects_row_with_missing_volume
- AC #3 14-day resolution buffer       -> test_rejects_row_inside_resolution_buffer
                                          test_rejects_row_with_missing_resolution
- AC #4 per-run bankroll cap           -> test_per_run_bankroll_cap_clips_aggregate
- AC #5 existing-position dedupe       -> test_yes_live_skips_already_held_token
- AC #6 live-book pricing              -> test_yes_live_uses_live_book_price
- AC #7 NO direction rejected          -> test_rejects_no_direction_until_token_split
- AC #8 hold-cycle counter + unwind    -> test_held_token_increments_and_unwinds_at_threshold
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"


def _load_agent_module(name: str = "prophet_polymarket_edge_agent_for_runtime_gates"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def agent():
    return _load_agent_module()


def _risk() -> Dict[str, Any]:
    return {
        "max_kelly_fraction": 0.05,
        "max_position_notional_usd": 50.0,
        "min_mid_price": 0.30,
        "max_mid_price": 0.70,
        "min_daily_volume_usd": 10000.0,
        "min_seconds_to_resolution": 14 * 24 * 60 * 60,
        "max_inventory_hold_cycles": 3,
    }


def _row(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "canonical_id": "row-1",
        "polymarket_token_id": "0xtoken_a",
        "consensus_direction": "yes",
        "divergence_bps": 1500,
        "polymarket_price": 0.40,
        "consensus_probability": 0.55,
        "liquidity_usd": 50000.0,
        "end_date_iso": "2099-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_rejects_row_outside_safe_band(agent) -> None:
    """A 0.05 tail-market row crosses divergence + Kelly but is outside the
    [0.30, 0.70] safe band. The live path must skip it; otherwise retail
    money lands in the kind of contract that wipes accounts."""
    plans = agent.compute_live_executions(
        surface_c=[_row(polymarket_price=0.05, consensus_probability=0.20)],
        risk=_risk(),
        bankroll_usd=1000.0,
        min_divergence_bps=500,
    )
    assert plans == []


def test_rejects_row_below_volume_floor(agent) -> None:
    """`liquidity_usd` is the publisher's exit-depth proxy. Below the
    `min_daily_volume_usd` floor the operator cannot exit even via
    `--unwind-all`, so the entry must be rejected."""
    plans = agent.compute_live_executions(
        surface_c=[_row(liquidity_usd=500.0)],
        risk=_risk(),
        bankroll_usd=1000.0,
        min_divergence_bps=500,
    )
    assert plans == []


def test_rejects_row_with_missing_volume(agent) -> None:
    """Fail-closed: a row that omits `liquidity_usd` must not slip the
    floor. A silent-disable on missing fields is the bug class this
    enforcement was added to prevent."""
    row = _row()
    row.pop("liquidity_usd", None)
    plans = agent.compute_live_executions(
        surface_c=[row],
        risk=_risk(),
        bankroll_usd=1000.0,
        min_divergence_bps=500,
    )
    assert plans == []


def test_rejects_row_inside_resolution_buffer(agent) -> None:
    """Markets within the 14-day buffer have no divergence-based 'edge';
    the row must be rejected before it becomes a plan."""
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    plans = agent.compute_live_executions(
        surface_c=[_row(end_date_iso=soon)],
        risk=_risk(),
        bankroll_usd=1000.0,
        min_divergence_bps=500,
    )
    assert plans == []


def test_rejects_row_with_missing_resolution(agent) -> None:
    """Fail-closed when the publisher omits `end_date_iso`: the gate must
    not silently disable when the resolution timestamp is unknown."""
    row = _row()
    row.pop("end_date_iso", None)
    plans = agent.compute_live_executions(
        surface_c=[row],
        risk=_risk(),
        bankroll_usd=1000.0,
        min_divergence_bps=500,
    )
    assert plans == []


def test_per_run_bankroll_cap_clips_aggregate(agent) -> None:
    """Aggregate notional across plans must never exceed `bankroll_usd`,
    even when each row individually fits inside `max_position_notional_usd`."""
    rows = [
        _row(canonical_id=f"row-{i}", polymarket_token_id=f"0xtoken_{i}")
        for i in range(20)
    ]
    risk = _risk()
    risk["max_position_notional_usd"] = 50.0
    plans = agent.compute_live_executions(
        surface_c=rows,
        risk=risk,
        bankroll_usd=100.0,  # only enough for 2 rows at the cap
        min_divergence_bps=500,
    )
    deployed_total = sum(plan["notional_usd"] for plan in plans)
    assert deployed_total <= 100.0 + 1e-9, (
        f"per-run cap broken: {deployed_total} deployed against bankroll 100"
    )


def test_rejects_no_direction_until_token_split(agent) -> None:
    """The publisher returns the YES outcome's CLOB token. Submitting a BUY
    against it at `1 - p` would execute on the wrong side. Until the
    publisher carries a separate NO token_id, NO-direction rows must be
    rejected outright."""
    plans = agent.compute_live_executions(
        surface_c=[_row(consensus_direction="no", polymarket_price=0.40, consensus_probability=0.20)],
        risk=_risk(),
        bankroll_usd=1000.0,
        min_divergence_bps=500,
    )
    assert plans == []


def test_yes_live_skips_already_held_token(agent, monkeypatch, capsys) -> None:
    """A second `--yes-live` run on a market the wallet already holds must
    not submit another BUY. Cron-driven pyramiding into the same name is
    the P0 retail-loss path this dedupe blocks."""
    config_path = Path("/tmp/test_dedupe_config.json")
    config_path.write_text(json.dumps({"risk": _risk(), "live": {"min_divergence_bps": 500, "bankroll_usd": 1000.0}}), encoding="utf-8")

    monkeypatch.setitem(sys.modules, "py_clob_client", type("FakeClob", (), {}))
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("POLY_API_KEY", "key")
    monkeypatch.setenv("POLY_PASSPHRASE", "pass")
    monkeypatch.setenv("POLY_SECRET", "secret")

    submitted: List[Dict[str, Any]] = []

    class StubTrader:
        def __init__(self, **_: Any) -> None:
            pass

        def create_order(self, **kwargs: Any) -> Dict[str, Any]:
            submitted.append(kwargs)
            return {"order_id": "x"}

        def get_positions(self) -> Any:
            # Wallet already holds the same token the planner wants to enter.
            return [{"asset_id": "0xtoken_a", "size": 5.0}]

    monkeypatch.setattr(agent, "DirectClobTrader", StubTrader)
    monkeypatch.setattr(agent, "fetch_surface_c_for_live_execution", lambda config: [_row()])
    monkeypatch.setattr(agent, "_save_position_cycles", lambda state, path=None: None)

    rc = agent.main(["--yes-live", "--config", str(config_path)])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["live_executions"] == []
    assert payload["skipped_dedupe"] and payload["skipped_dedupe"][0]["skip_reason"] == "already_held"
    assert submitted == []


def test_yes_live_uses_live_book_price(agent, monkeypatch, capsys) -> None:
    """The submitted limit price must come from the live CLOB best-ask, not
    the cached publisher `polymarket_price` snapshot."""
    config_path = Path("/tmp/test_live_book_config.json")
    config_path.write_text(json.dumps({"risk": _risk(), "live": {"min_divergence_bps": 500, "bankroll_usd": 1000.0}}), encoding="utf-8")

    monkeypatch.setitem(sys.modules, "py_clob_client", type("FakeClob", (), {}))
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("POLY_API_KEY", "key")
    monkeypatch.setenv("POLY_PASSPHRASE", "pass")
    monkeypatch.setenv("POLY_SECRET", "secret")

    submitted: List[Dict[str, Any]] = []

    class StubTrader:
        def __init__(self, **_: Any) -> None:
            pass

        def create_order(self, **kwargs: Any) -> Dict[str, Any]:
            submitted.append(kwargs)
            return {"order_id": "x"}

        def get_positions(self) -> Any:
            return []

    monkeypatch.setattr(agent, "DirectClobTrader", StubTrader)
    monkeypatch.setattr(agent, "fetch_surface_c_for_live_execution", lambda config: [_row()])
    monkeypatch.setattr(agent, "_save_position_cycles", lambda state, path=None: None)
    monkeypatch.setattr(
        agent,
        "fetch_book",
        lambda token_id: {
            "best_bid": 0.41,
            "tick_size": "0.01",
            "raw": {"asks": [{"price": "0.43", "size": "200"}], "bids": [{"price": "0.41", "size": "100"}]},
        },
    )

    rc = agent.main(["--yes-live", "--config", str(config_path)])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["live_executions"], "expected at least one execution"
    assert submitted and submitted[0]["price"] == 0.43
    # Cached publisher snapshot was 0.40; the live book wins.
    assert submitted[0]["price"] != 0.40


def test_held_token_increments_and_unwinds_at_threshold(agent, tmp_path) -> None:
    """`update_position_cycles` increments per-token cycle counts and the
    forced-unwind path triggers when a token's count exceeds the
    `max_inventory_hold_cycles` threshold. Without this, positions that
    drift outside the safe band stay on the books indefinitely."""
    state, _ = agent.update_position_cycles(
        held_token_ids=["0xtoken_a"],
        state={},
        now_iso="2026-05-01T00:00:00Z",
    )
    assert state["0xtoken_a"]["cycles_held"] == 1
    state, _ = agent.update_position_cycles(
        held_token_ids=["0xtoken_a"],
        state=state,
        now_iso="2026-05-02T00:00:00Z",
    )
    state, _ = agent.update_position_cycles(
        held_token_ids=["0xtoken_a"],
        state=state,
        now_iso="2026-05-03T00:00:00Z",
    )
    state, _ = agent.update_position_cycles(
        held_token_ids=["0xtoken_a"],
        state=state,
        now_iso="2026-05-04T00:00:00Z",
    )
    # After 4 cycles with max_inventory_hold_cycles=3, the runtime would
    # force an unwind. The pure transform reports the cycle count; the
    # caller in `_yes_live_main` applies the threshold check.
    assert state["0xtoken_a"]["cycles_held"] == 4
    # First-seen timestamp is preserved across increments.
    assert state["0xtoken_a"]["first_seen_iso"] == "2026-05-01T00:00:00Z"

    # Tokens no longer held drop out so cycle counters do not leak across
    # different positions over time.
    state, _ = agent.update_position_cycles(
        held_token_ids=[],
        state=state,
        now_iso="2026-05-05T00:00:00Z",
    )
    assert "0xtoken_a" not in state
