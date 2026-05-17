"""Issue #545 runtime contracts.

These tests pin the user-facing fixes that are easiest to regress in
the agent orchestration layer: no silent single-leg fallback when
Polymarket credentials are absent, and seed preflight/trimming running
even when the CLI live flag is not set.
"""

from __future__ import annotations

from typing import Any

import pytest

import agent
from agent import (
    AgentConfig,
    EXECUTION_MODE_DELTA_NEUTRAL,
    PolymarketCredentialsMissing,
)
from arbitrage.intelligence import IntelligenceConfig
from arbitrage.scoring import ScoringConfig
from discovery import AutoDiscoverConfig, AutoDiscoverResult


class _Recorder:
    """DB-free recorder stub for cmd_run orchestration tests."""

    def __init__(self, *, run_id: str, target: Any) -> None:
        self.run_id = run_id
        self.target = target
        self.summary: dict[str, Any] = {}
        self.blockers: list[str] = []
        self.pairs: list[dict[str, Any]] = []
        self.opportunities: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.started_at = "2026-05-14T00:00:00Z"
        self.finished_at = "2026-05-14T00:00:01Z"
        self.status = ""

    def record_pair(self, prophet_market_id: str, polymarket_condition_id: str) -> None:
        self.pairs.append(
            {
                "prophet_market_id": prophet_market_id,
                "polymarket_condition_id": polymarket_condition_id,
            }
        )

    def record_blocker(self, code: str) -> None:
        self.blockers.append(code)

    def finish(self, status: str, reason: str) -> dict[str, Any]:
        self.status = status
        return {
            "run_id": self.run_id,
            "status": status,
            "reason": reason,
            "summary": self.summary,
            "pairs": self.pairs,
            "opportunities": self.opportunities,
            "orders": self.orders,
            "blockers": self.blockers,
        }


def _config() -> AgentConfig:
    return AgentConfig(
        inputs={"prophet_email": "jill@volume.finance", "email_provider": "gmail"},
        project_name="prophet",
        database_name="prophet",
        scoring=ScoringConfig(),
        intelligence=IntelligenceConfig(),
        auto_discover=AutoDiscoverConfig(enabled=True, initial_bet_usdc=1.0),
        live_mode=False,
        max_orders_per_run=5,
        execution_mode=EXECUTION_MODE_DELTA_NEUTRAL,
        max_hedge_slippage_bps=200.0,
    )


def _pending_entry(market_id: str) -> dict[str, Any]:
    return {
        "polymarket_market_id": market_id,
        "question": "Will the Yankees win?",
        "category": "Sports",
        "category_slug": "sports",
        "resolution_date_iso": "2026-05-20T22:00:00Z",
        "initial_bet_usdc": 1.0,
        "bounty_id": "",
        "prophet_viewer_id": "vid_1",
        "source_skill": "prophet-arb-bot",
        "volume_24h_usd": 50_000.0,
    }


def test_missing_polymarket_credentials_raise_exact_list(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in agent.POLYMARKET_REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POLY_API_KEY", "present")

    with pytest.raises(PolymarketCredentialsMissing) as exc:
        agent._build_hedger(_config())

    assert exc.value.missing_env_vars == [
        "POLY_PRIVATE_KEY",
        "POLY_PASSPHRASE",
        "POLY_SECRET",
    ]


def test_missing_polymarket_credentials_blocked_envelope() -> None:
    result = agent._polymarket_creds_missing_result(
        ["POLY_PRIVATE_KEY", "POLY_API_KEY"]
    )

    assert result.status == "blocked"
    assert result.reason == "polymarket_creds_missing"
    assert result.to_dict() == {
        "status": "blocked",
        "reason": "polymarket_creds_missing",
        "payload": {
            "missing_env_vars": ["POLY_PRIVATE_KEY", "POLY_API_KEY"],
            "action": "set_polymarket_credentials",
        },
    }


def test_cmd_run_applies_seed_preflight_even_without_yes_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    pending = [_pending_entry("cond_a"), _pending_entry("cond_b")]

    monkeypatch.setattr(agent, "RunRecorder", _Recorder)
    monkeypatch.setattr(agent, "_resolve_target", lambda config: object())
    monkeypatch.setattr(agent, "_acquire_jwt", lambda **kwargs: ("eyJ.jwt", "vid_1", "cache"))
    monkeypatch.setattr(agent, "list_arb_pairs", lambda target: [])
    monkeypatch.setattr(
        agent,
        "run_auto_discover",
        lambda **kwargs: AutoDiscoverResult(
            candidates_found=2,
            already_paired=0,
            raw_markets_fetched=9,
            markets_passing_gates=4,
            candidates_evaluated_for_pairing=2,
            max_candidates=250,
            auto_paired=[],
            pending_ui_submission=pending,
        ),
    )

    def fake_seed_preflight(**kwargs: Any) -> None:
        calls.append(kwargs)
        kwargs["recorder"].summary["_trimmed_pending"] = [pending[0]]
        return None

    monkeypatch.setattr(agent, "_apply_seed_preflight_and_trim", fake_seed_preflight)

    result = agent.cmd_run(
        config=_config(),
        gateway=object(),
        yes_live=False,
        transport=object(),
        hedger=object(),
    )

    assert result.status == "ok"
    assert result.reason == "no_pairs_seeded_pending_ui_submission"
    assert len(calls) == 1
    assert calls[0]["pending"] == pending
    assert result.payload["summary"]["auto_discover_raw_markets_fetched"] == 9
    assert result.payload["summary"]["auto_discover_markets_passing_gates"] == 4
    assert result.payload["summary"]["auto_discover_candidates_evaluated_for_pairing"] == 2
    assert result.payload["summary"]["auto_discover_max_candidates"] == 250
    assert result.payload["pending_ui_submission"] == [pending[0]]


class _SeedHedger:
    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []

    def submit_hedge(
        self,
        *,
        condition_id: str,
        hedge_side: str,
        size_usdc: float,
        marketable_price: float,
    ) -> dict[str, Any]:
        self.submitted.append(
            {
                "condition_id": condition_id,
                "hedge_side": hedge_side,
                "size_usdc": size_usdc,
                "marketable_price": marketable_price,
            }
        )
        return {
            "polymarket_order_id": "POLY-seed",
            "filled_qty": 1.0,
            "fill_price": marketable_price,
        }

    def unwind_prophet(self, *, order_id: str) -> None:
        raise AssertionError("seed path must not call Prophet unwind")


def test_record_created_market_preconfirm_submits_polymarket_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hedger = _SeedHedger()
    monkeypatch.setattr(agent, "_build_hedger", lambda config: hedger)
    monkeypatch.setattr(
        agent,
        "upsert_arb_pair",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("must not persist before Prophet id")
        ),
    )

    result = agent.cmd_record_created_market(
        config=_config(),
        polymarket_condition_id="0xCOND",
        prophet_market_id="",
        prophet_seed_side="buy",
        polymarket_marketable_price=0.001,
    )

    assert result.status == "ok"
    assert result.reason == "seed_hedge_ready_for_prophet_confirm"
    assert result.payload["hedge_status"] == "hedged"
    assert result.payload["next_action"] == "click_prophet_confirm"
    assert hedger.submitted == [
        {
            "condition_id": "0xCOND",
            "hedge_side": "sell",
            "size_usdc": 1.0,
            "marketable_price": 0.001,
        }
    ]


def test_record_created_market_unwinds_after_prophet_confirm_decline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hedger = _SeedHedger()
    monkeypatch.setattr(agent, "_build_hedger", lambda config: hedger)

    result = agent.cmd_record_created_market(
        config=_config(),
        polymarket_condition_id="0xCOND",
        prophet_market_id="",
        prophet_seed_side="buy",
        polymarket_marketable_price=0.62,
        prophet_confirm_declined=True,
    )

    assert result.status == "ok"
    assert result.reason == "prophet_confirm_declined"
    assert result.payload["hedge_status"] == "unwound_after_prophet_decline"
    assert hedger.submitted == [
        {
            "condition_id": "0xCOND",
            "hedge_side": "buy",
            "size_usdc": 1.0,
            "marketable_price": 0.62,
        }
    ]
