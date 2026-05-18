"""Issue #697: cmd_run drops the OCS capture diagnostic when assembling
`ui_submission_results`, masking the diagnostic surface #696 wired into
`cmd_create_market_via_ui.payload`.

#696 verified that the inner driver populates `payload.capture_observations`
and `payload.capture_error` when `poll_for_ocs_id` times out — but the
envelope assembler in cmd_run only copies four keys (status, reason,
polymarket_condition_id, prophet_market_id) per entry, so the diagnostic
exists only in memory and never reaches the operator's `--json-output`.

SKILL.md promises:

> Since #695, the blocked envelope carries `payload.capture_observations`
> (the diagnostic ring buffer) and `payload.capture_error` directly, so
> the operator can tell the failure mode without re-driving.

This test pins that promise at the cmd_run boundary so the next emit-layer
regression has a CI floor.
"""

from __future__ import annotations

from typing import Any

import pytest

import agent
from agent import AgentConfig, CycleResult, EXECUTION_MODE_DELTA_NEUTRAL
from arbitrage.intelligence import IntelligenceConfig
from arbitrage.scoring import ScoringConfig
from discovery import AutoDiscoverConfig, AutoDiscoverResult


class _Recorder:
    def __init__(self, *, run_id: str, target: Any) -> None:
        self.run_id = run_id
        self.target = target
        self.summary: dict[str, Any] = {}
        self.blockers: list[str] = []
        self.pairs: list[dict[str, Any]] = []
        self.opportunities: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []

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
        live_mode=True,
        max_orders_per_run=5,
        execution_mode=EXECUTION_MODE_DELTA_NEUTRAL,
        max_hedge_slippage_bps=100.0,
    )


def _pending(market_id: str, *, question: str) -> dict[str, Any]:
    return {
        "polymarket_market_id": market_id,
        "polymarket_yes_token_id": f"{market_id}_YES",
        "question": question,
        "category": "Sports",
        "category_slug": "sports",
        "resolution_date_iso": "2026-05-25T22:00:00Z",
        "initial_bet_usdc": 1.0,
        "bounty_id": "",
        "prophet_viewer_id": "vid_1",
        "source_skill": "prophet-arb-bot",
    }


_FAKE_OBSERVATIONS = [
    {
        "url": "https://app.prophetmarket.ai/api/graphql",
        "ok": False,
        "shape": "errors,extensions",
    }
]
_FAKE_CAPTURE_ERROR = "TypeError: Cannot read properties of undefined"


def test_cmd_run_envelope_surfaces_capture_observations_and_capture_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `cmd_create_market_via_ui` returns blocked with the #696
    diagnostic in payload, cmd_run's `ui_submission_results[i]` MUST
    surface `capture_observations` and `capture_error` so they reach the
    operator's `--json-output` envelope.
    """
    pending = [_pending("cond_a", question="Question A?")]

    monkeypatch.setattr(agent, "RunRecorder", _Recorder)
    monkeypatch.setattr(agent, "_resolve_target", lambda config: object())
    monkeypatch.setattr(
        agent, "_acquire_jwt", lambda **kwargs: ("eyJ.jwt", "vid_1", "cache")
    )
    monkeypatch.setattr(agent, "list_arb_pairs", lambda target: [])
    monkeypatch.setattr(
        agent,
        "run_auto_discover",
        lambda **kwargs: AutoDiscoverResult(
            candidates_found=1,
            already_paired=0,
            raw_markets_fetched=1,
            markets_passing_gates=1,
            candidates_evaluated_for_pairing=1,
            max_candidates=250,
            auto_paired=[],
            pending_ui_submission=list(pending),
        ),
    )
    monkeypatch.setattr(
        agent, "_apply_seed_preflight_and_trim", lambda **kwargs: None
    )

    def stub_create_market_via_ui(**_: Any) -> CycleResult:
        return CycleResult(
            status="blocked",
            reason="ocs_session_id_not_captured",
            payload={
                "polymarket_condition_id": "cond_a",
                "question": "Question A?",
                "capture_observations": list(_FAKE_OBSERVATIONS),
                "capture_error": _FAKE_CAPTURE_ERROR,
            },
        )

    result = agent.cmd_run(
        config=_config(),
        gateway=object(),
        yes_live=True,
        transport=object(),
        hedger=object(),
        create_market_via_ui=stub_create_market_via_ui,
    )

    ui_results = result.payload.get("ui_submission_results")
    assert isinstance(ui_results, list) and len(ui_results) == 1, ui_results

    entry = ui_results[0]
    assert entry["status"] == "blocked"
    assert entry["reason"] == "ocs_session_id_not_captured"

    # The diagnostic ring buffer MUST survive into the envelope — without
    # this, the operator can't tell empty-buffer (new transport) from
    # non-empty-buffer-with-ok-false (schema drift). SKILL.md promised it.
    assert "capture_observations" in entry, (
        "ui_submission_results[i] must surface capture_observations on an "
        "ocs_session_id_not_captured block; saw "
        f"{sorted(entry.keys())}"
    )
    assert entry["capture_observations"] == _FAKE_OBSERVATIONS

    # The JS error captured by the shim MUST also survive.
    assert "capture_error" in entry, (
        "ui_submission_results[i] must surface capture_error on an "
        "ocs_session_id_not_captured block; saw "
        f"{sorted(entry.keys())}"
    )
    assert entry["capture_error"] == _FAKE_CAPTURE_ERROR


def test_cmd_run_envelope_omits_capture_fields_when_entry_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On the success path, ui_submission_results[i] must NOT carry the
    diagnostic fields — they're only meaningful when the OCS capture
    missed. This pin prevents a refactor from leaking the keys onto every
    success envelope and bloating the payload.
    """
    pending = [_pending("cond_a", question="Question A?")]

    monkeypatch.setattr(agent, "RunRecorder", _Recorder)
    monkeypatch.setattr(agent, "_resolve_target", lambda config: object())
    monkeypatch.setattr(
        agent, "_acquire_jwt", lambda **kwargs: ("eyJ.jwt", "vid_1", "cache")
    )
    monkeypatch.setattr(agent, "list_arb_pairs", lambda target: [])
    monkeypatch.setattr(
        agent,
        "run_auto_discover",
        lambda **kwargs: AutoDiscoverResult(
            candidates_found=1,
            already_paired=0,
            raw_markets_fetched=1,
            markets_passing_gates=1,
            candidates_evaluated_for_pairing=1,
            max_candidates=250,
            auto_paired=[],
            pending_ui_submission=list(pending),
        ),
    )
    monkeypatch.setattr(
        agent, "_apply_seed_preflight_and_trim", lambda **kwargs: None
    )

    def stub_create_market_via_ui(**_: Any) -> CycleResult:
        return CycleResult(
            status="ok",
            reason="pair_created",
            payload={"prophet_market_id": "pm_cond_a"},
        )

    result = agent.cmd_run(
        config=_config(),
        gateway=object(),
        yes_live=True,
        transport=object(),
        hedger=object(),
        create_market_via_ui=stub_create_market_via_ui,
    )

    ui_results = result.payload.get("ui_submission_results")
    assert isinstance(ui_results, list) and len(ui_results) == 1
    entry = ui_results[0]
    assert entry["status"] == "ok"
    assert "capture_observations" not in entry
    assert "capture_error" not in entry
