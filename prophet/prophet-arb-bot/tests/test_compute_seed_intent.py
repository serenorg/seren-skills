"""Critical-path tests for the per-market seed-side decision (issue #548).

Two layers:

  1. `derive_seed_intent` (pure function in `arbitrage.hedge`): given
     Prophet's AI `yesFairValueBps` and the live Polymarket book, decide
     `seed_side`, `hedge_side`, and the marketable Polymarket price
     snapped to tick. Returns None on a no-edge or no-liquidity result.

  2. `cmd_compute_seed_intent` (CLI command in `agent.py`): wires the
     odds-session poll + Polymarket book fetch + derive call together,
     and returns the structured envelope the agent runbook reads. Three
     outcomes the agent has to react to: usable intent, non-viable
     Prophet session, completed-but-no-edge.

No tests for the GraphQL transport layer (covered by
`test_prophet_transport.py`) or the depth scan (covered by
`test_hedge.py` / `test_seed_preflight.py`).
"""

from __future__ import annotations

from typing import Any

import pytest

from arbitrage.hedge import derive_seed_intent
from agent import cmd_compute_seed_intent, AgentConfig
from prophet.odds_session import BinaryPricing, OddsSession


def _book(
    *,
    best_bid: float = 0.50,
    best_ask: float = 0.52,
    tick_size: str = "0.01",
) -> dict:
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "tick_size": tick_size,
        "neg_risk": False,
        "raw": {},
    }


# ---------------------------------------------------------------------------
# derive_seed_intent (pure)


def test_prophet_above_polymarket_picks_buy_seed_sell_hedge_at_best_bid() -> None:
    intent = derive_seed_intent(
        prophet_fair_value_bps=5800,
        polymarket_yes_price=0.50,
        book_payload=_book(best_bid=0.49, best_ask=0.51, tick_size="0.01"),
    )
    assert intent is not None
    assert intent.seed_side == "buy"
    assert intent.hedge_side == "sell"
    # SELL on Polymarket hits bids; price snaps UP (ceil) to next tick.
    assert intent.hedge_price == pytest.approx(0.49)
    assert intent.edge_bps == pytest.approx(800.0)
    assert intent.tick_size == "0.01"


def test_prophet_below_polymarket_picks_sell_seed_buy_hedge_at_best_ask() -> None:
    intent = derive_seed_intent(
        prophet_fair_value_bps=4200,
        polymarket_yes_price=0.55,
        book_payload=_book(best_bid=0.54, best_ask=0.56, tick_size="0.01"),
    )
    assert intent is not None
    assert intent.seed_side == "sell"
    assert intent.hedge_side == "buy"
    # BUY on Polymarket hits asks; price snaps DOWN (floor) to current tick.
    assert intent.hedge_price == pytest.approx(0.56)
    assert intent.edge_bps == pytest.approx(1300.0)


def test_zero_edge_returns_none() -> None:
    intent = derive_seed_intent(
        prophet_fair_value_bps=5000,
        polymarket_yes_price=0.50,
        book_payload=_book(),
    )
    assert intent is None


def test_missing_bid_when_sell_hedge_needed_returns_none() -> None:
    intent = derive_seed_intent(
        prophet_fair_value_bps=5800,
        polymarket_yes_price=0.50,
        # No bid → cannot hedge a SELL on Polymarket.
        book_payload=_book(best_bid=0.0, best_ask=0.51),
    )
    assert intent is None


# ---------------------------------------------------------------------------
# cmd_compute_seed_intent (wired)


def _completed_session(*, yes_fair_value_bps: int, is_viable: bool = True) -> OddsSession:
    return OddsSession(
        id="ocs_1",
        status="COMPLETED",
        total_models=6,
        completed_models=6,
        pricing=BinaryPricing(
            yes_price_bps=yes_fair_value_bps,
            no_price_bps=10000 - yes_fair_value_bps,
            yes_fair_value_bps=yes_fair_value_bps,
            no_fair_value_bps=10000 - yes_fair_value_bps,
            is_viable=is_viable,
            confidence_bps=7200,
        ),
        rejection_reason=None,
    )


def _fake_config() -> AgentConfig:
    # cmd_compute_seed_intent does not touch the config beyond reading
    # auto_discover.initial_bet_usdc, so a minimal hand-rolled instance is
    # fine. Constructing the dataclass directly avoids loading config.json.
    from agent import AutoDiscoverConfig, IntelligenceConfig, ScoringConfig

    return AgentConfig(
        inputs={"prophet_email": "x@y.z"},
        project_name="prophet",
        database_name="prophet",
        scoring=ScoringConfig(
            min_spread=0.03,
            max_spread=0.30,
            kelly_fraction=0.25,
            max_trade_size_usdc=50.0,
            min_trade_size_usdc=5.0,
            bankroll_usdc=200.0,
        ),
        intelligence=IntelligenceConfig(
            enabled=False, max_basis_volatility=0.05, fetch_correlations=False
        ),
        auto_discover=AutoDiscoverConfig.from_dict({}),
        live_mode=True,
        max_orders_per_run=5,
        execution_mode="delta_neutral",
        max_hedge_slippage_bps=200.0,
    )


def test_cmd_compute_seed_intent_success_emits_intent_envelope() -> None:
    captured: dict[str, Any] = {}

    def fake_poll(transport, *, jwt, session_id, **kwargs):
        captured["session_id"] = session_id
        captured["jwt"] = jwt
        return _completed_session(yes_fair_value_bps=5800)

    def fake_fetch_book(condition_id: str) -> dict:
        captured["condition_id"] = condition_id
        return _book(best_bid=0.49, best_ask=0.51, tick_size="0.01")

    result = cmd_compute_seed_intent(
        config=_fake_config(),
        polymarket_condition_id="0xabc",
        odds_session_id="ocs_1",
        polymarket_yes_price=0.50,
        transport=object(),
        jwt="eyJ...",
        poll=fake_poll,
        fetch_book=fake_fetch_book,
    )

    assert result.status == "ok"
    assert result.reason == "seed_intent_ready"
    assert captured["session_id"] == "ocs_1"
    assert captured["condition_id"] == "0xabc"
    assert result.payload["seed_side"] == "buy"
    assert result.payload["hedge_side"] == "sell"
    assert result.payload["hedge_price"] == pytest.approx(0.49)
    assert result.payload["prophet_fair_value_bps"] == 5800
    assert result.payload["polymarket_yes_price"] == pytest.approx(0.50)
    assert result.payload["edge_bps"] == pytest.approx(800.0)
    assert result.payload["session_status"] == "COMPLETED"
    assert result.payload["is_viable"] is True
    # #551 — side-by-side human-readable summary the agent renders verbatim.
    summary = result.payload["edge_summary"]
    assert "Prophet 58.0%" in summary
    assert "Polymarket 50.0%" in summary
    assert "800 bps" in summary


def test_cmd_compute_seed_intent_blocks_when_session_not_completed() -> None:
    def fake_poll(transport, *, jwt, session_id, **kwargs):
        return OddsSession(
            id="ocs_1",
            status="REJECTED",
            total_models=6,
            completed_models=3,
            pricing=None,
            rejection_reason="model_disagreement",
        )

    def fake_fetch_book(condition_id: str) -> dict:
        raise AssertionError("fetch_book must not run when session rejected")

    result = cmd_compute_seed_intent(
        config=_fake_config(),
        polymarket_condition_id="0xabc",
        odds_session_id="ocs_1",
        polymarket_yes_price=0.50,
        transport=object(),
        jwt="eyJ...",
        poll=fake_poll,
        fetch_book=fake_fetch_book,
    )

    assert result.status == "blocked"
    assert result.reason == "odds_session_not_completed"
    assert result.payload["session_status"] == "REJECTED"
    assert result.payload["rejection_reason"] == "model_disagreement"


def test_cmd_compute_seed_intent_blocks_when_completed_but_not_viable() -> None:
    def fake_poll(transport, *, jwt, session_id, **kwargs):
        return _completed_session(yes_fair_value_bps=5500, is_viable=False)

    def fake_fetch_book(condition_id: str) -> dict:
        raise AssertionError("fetch_book must not run when prophet says not viable")

    result = cmd_compute_seed_intent(
        config=_fake_config(),
        polymarket_condition_id="0xabc",
        odds_session_id="ocs_1",
        polymarket_yes_price=0.50,
        transport=object(),
        jwt="eyJ...",
        poll=fake_poll,
        fetch_book=fake_fetch_book,
    )

    assert result.status == "blocked"
    assert result.reason == "prophet_market_not_viable"
    assert result.payload["is_viable"] is False


def test_cmd_compute_seed_intent_blocks_when_no_edge() -> None:
    def fake_poll(transport, *, jwt, session_id, **kwargs):
        return _completed_session(yes_fair_value_bps=5000)

    def fake_fetch_book(condition_id: str) -> dict:
        return _book()

    result = cmd_compute_seed_intent(
        config=_fake_config(),
        polymarket_condition_id="0xabc",
        odds_session_id="ocs_1",
        polymarket_yes_price=0.50,
        transport=object(),
        jwt="eyJ...",
        poll=fake_poll,
        fetch_book=fake_fetch_book,
    )

    assert result.status == "blocked"
    assert result.reason == "no_edge"
    assert result.payload["prophet_fair_value_bps"] == 5000
    assert result.payload["polymarket_yes_price"] == pytest.approx(0.50)
    # #551 — no_edge envelope must carry a human-readable side-by-side.
    summary = result.payload["edge_summary"]
    assert "Prophet 50.0%" in summary
    assert "Polymarket 50.0%" in summary
    assert "no_edge" in summary


def test_cmd_compute_seed_intent_auto_derives_polymarket_yes_price_from_midpoint() -> None:
    """When --polymarket-yes-price is omitted (passed as 0.0), the command
    derives the price from the book midpoint and uses it for the
    seed-side decision. Asserts the envelope reports the resolved value,
    not the placeholder 0.0 that was passed in."""

    def fake_poll(transport, *, jwt, session_id, **kwargs):
        # Prophet says YES is more likely (60%). Polymarket midpoint
        # from the book below is (0.40 + 0.44)/2 = 0.42, so seed_side
        # must be `buy` and edge_bps == 1800.
        return _completed_session(yes_fair_value_bps=6000)

    def fake_fetch_book(condition_id: str) -> dict:
        return _book(best_bid=0.40, best_ask=0.44, tick_size="0.01")

    result = cmd_compute_seed_intent(
        config=_fake_config(),
        polymarket_condition_id="0xabc",
        odds_session_id="ocs_1",
        polymarket_yes_price=0.0,  # omitted by the caller
        transport=object(),
        jwt="eyJ...",
        poll=fake_poll,
        fetch_book=fake_fetch_book,
    )

    assert result.status == "ok"
    assert result.reason == "seed_intent_ready"
    assert result.payload["polymarket_yes_price"] == pytest.approx(0.42)
    assert result.payload["polymarket_yes_price_source"] == "book_midpoint"
    assert result.payload["seed_side"] == "buy"
    assert result.payload["edge_bps"] == pytest.approx(1800.0)
