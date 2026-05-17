"""Issue #636: under `--yes-live`, `cmd_run` must chain into
`cmd_create_market_via_ui` for every `pending_ui_submission` entry and
attach per-entry outcomes as `ui_submission_results` in the run envelope.

This is the seam the cron-driven autonomous flow uses; before #636 it
required a human-driven Playwright runbook.
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


def test_run_chains_into_create_market_per_pending_ui_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = [
        _pending("cond_a", question="Question A?"),
        _pending("cond_b", question="Question B?"),
    ]

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
            candidates_found=2,
            already_paired=0,
            raw_markets_fetched=2,
            markets_passing_gates=2,
            candidates_evaluated_for_pairing=2,
            max_candidates=250,
            auto_paired=[],
            pending_ui_submission=list(pending),
        ),
    )
    # Skip the funds preflight (it would otherwise need a hedger). The
    # critical-path test only cares about the UI submission chain.
    monkeypatch.setattr(
        agent, "_apply_seed_preflight_and_trim", lambda **kwargs: None
    )

    calls: list[dict[str, Any]] = []

    def stub_create_market_via_ui(**kwargs: Any) -> CycleResult:
        calls.append(dict(kwargs))
        cid = kwargs["polymarket_condition_id"]
        return CycleResult(
            status="ok",
            reason="pair_created",
            payload={"prophet_market_id": f"pm_{cid}"},
        )

    result = agent.cmd_run(
        config=_config(),
        gateway=object(),
        yes_live=True,
        transport=object(),
        hedger=object(),
        create_market_via_ui=stub_create_market_via_ui,
    )

    # Both pending entries got dispatched to create-market-via-ui exactly once.
    assert len(calls) == 2
    dispatched_cids = [c["polymarket_condition_id"] for c in calls]
    assert dispatched_cids == ["cond_a", "cond_b"]
    # The pending entry's `question` was passed through (not its market_id).
    questions = [c["question"] for c in calls]
    assert questions == ["Question A?", "Question B?"]

    # The envelope carries ui_submission_results with one entry per pending row.
    ui_results = result.payload.get("ui_submission_results")
    assert isinstance(ui_results, list)
    assert len(ui_results) == 2
    for entry in ui_results:
        assert entry["status"] == "ok"
        assert entry["reason"] == "pair_created"
    assert {e["polymarket_condition_id"] for e in ui_results} == {"cond_a", "cond_b"}
    # Overall cycle status is ok (no_pairs_seeded_pending_ui_submission is
    # the early-return path since `list_arb_pairs` returns [] both before
    # and after the UI chain in this stub).
    assert result.status == "ok"


class _FreshCacheEntry:
    jwt = "eyJ.fresh.jwt"
    refresh_token = "rt_fresh"
    prophet_viewer_id = "vid_x"
    state = "fresh"


class _FakeWarmGateway:
    instances: list["_FakeWarmGateway"] = []
    enter_count = 0

    def __init__(self) -> None:
        self.resets = 0
        self.health_checks = 0
        self.exited = False
        _FakeWarmGateway.instances.append(self)

    def __enter__(self) -> "_FakeWarmGateway":
        _FakeWarmGateway.enter_count += 1
        return self

    def __exit__(self, *args: Any) -> None:
        self.exited = True

    def reset_for_next_entry(self) -> None:
        self.resets += 1

    def is_session_healthy(self) -> bool:
        self.health_checks += 1
        return True


class _FakeBrowserSession:
    enter_count = 0
    exit_count = 0

    def __init__(self, *, gateway: Any, headless: bool = True) -> None:
        self.gateway = gateway
        self.headless = headless

    def __enter__(self) -> "_FakeBrowserSession":
        _FakeBrowserSession.enter_count += 1
        return self

    def __exit__(self, *args: Any) -> None:
        _FakeBrowserSession.exit_count += 1


def _patch_common_pending_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pending: list[dict[str, Any]],
) -> None:
    monkeypatch.setattr(agent, "RunRecorder", _Recorder)
    monkeypatch.setattr(agent, "_resolve_target", lambda config: object())
    monkeypatch.setattr(agent, "_acquire_jwt", lambda **kwargs: ("eyJ.jwt", "vid_1", "cache"))
    monkeypatch.setattr(agent, "list_arb_pairs", lambda target: [])
    monkeypatch.setattr(
        agent,
        "run_auto_discover",
        lambda **kwargs: AutoDiscoverResult(
            candidates_found=len(pending),
            already_paired=0,
            raw_markets_fetched=len(pending),
            markets_passing_gates=len(pending),
            candidates_evaluated_for_pairing=len(pending),
            max_candidates=250,
            auto_paired=[],
            pending_ui_submission=list(pending),
        ),
    )
    monkeypatch.setattr(agent, "_apply_seed_preflight_and_trim", lambda **kwargs: None)
    monkeypatch.setattr(
        agent._playwright_mcp_gateway.PlaywrightStealthGateway,
        "_resolve_default_command",
        classmethod(lambda cls: ["stub-playwright-stealth"]),
    )
    monkeypatch.setattr(agent, "PlaywrightStealthGateway", _FakeWarmGateway)
    monkeypatch.setattr(agent, "RealBrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(
        agent,
        "establish_browser_session_for_create",
        lambda **kwargs: _FreshCacheEntry(),
    )


def test_run_reuses_one_warm_playwright_context_for_pending_ui_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #654: an N-entry cycle pays the MCP/Chromium cold start once."""

    _FakeWarmGateway.instances = []
    _FakeWarmGateway.enter_count = 0
    _FakeBrowserSession.enter_count = 0
    _FakeBrowserSession.exit_count = 0
    pending = [
        _pending("cond_a", question="Question A?"),
        _pending("cond_b", question="Question B?"),
        _pending("cond_c", question="Question C?"),
    ]
    _patch_common_pending_run(monkeypatch, pending=pending)

    inner_calls: list[dict[str, Any]] = []

    def fake_inner(**kwargs: Any) -> CycleResult:
        inner_calls.append(dict(kwargs))
        cid = kwargs["polymarket_condition_id"]
        return CycleResult(
            status="ok",
            reason="pair_created",
            payload={"prophet_market_id": f"pm_{cid}"},
        )

    monkeypatch.setattr(agent, "_run_create_market_via_ui_inner", fake_inner)

    result = agent.cmd_run(
        config=_config(),
        gateway=object(),
        yes_live=True,
        transport=object(),
        hedger=object(),
    )

    assert result.status == "ok"
    assert _FakeWarmGateway.enter_count == 1
    assert _FakeBrowserSession.enter_count == 1
    assert _FakeBrowserSession.exit_count == 1
    assert len(inner_calls) == 3
    assert {id(call["session"]) for call in inner_calls} == {id(inner_calls[0]["session"])}
    assert {call["cache_entry"].jwt for call in inner_calls} == {"eyJ.fresh.jwt"}
    assert _FakeWarmGateway.instances[0].resets == 2


def test_run_reopens_warm_playwright_context_after_corruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #654: a corrupted warm context blocks one entry, then recovers."""

    class FlakyWarmGateway(_FakeWarmGateway):
        def is_session_healthy(self) -> bool:
            self.health_checks += 1
            # The first context corrupts after its first entry. The reopened
            # context stays healthy for the next pending entry.
            return _FakeWarmGateway.enter_count > 1

    _FakeWarmGateway.instances = []
    _FakeWarmGateway.enter_count = 0
    _FakeBrowserSession.enter_count = 0
    _FakeBrowserSession.exit_count = 0
    pending = [
        _pending("cond_bad", question="Question bad?"),
        _pending("cond_ok", question="Question ok?"),
    ]
    _patch_common_pending_run(monkeypatch, pending=pending)
    monkeypatch.setattr(agent, "PlaywrightStealthGateway", FlakyWarmGateway)

    def fake_inner(**kwargs: Any) -> CycleResult:
        cid = kwargs["polymarket_condition_id"]
        return CycleResult(
            status="ok",
            reason="pair_created",
            payload={"prophet_market_id": f"pm_{cid}"},
        )

    monkeypatch.setattr(agent, "_run_create_market_via_ui_inner", fake_inner)

    result = agent.cmd_run(
        config=_config(),
        gateway=object(),
        yes_live=True,
        transport=object(),
        hedger=object(),
    )

    ui_results = result.payload.get("ui_submission_results")
    assert [r["polymarket_condition_id"] for r in ui_results] == ["cond_bad", "cond_ok"]
    assert ui_results[0]["status"] == "blocked"
    assert ui_results[0]["reason"] == "warm_context_corrupted"
    assert ui_results[1]["status"] == "ok"
    assert ui_results[1]["reason"] == "pair_created"
    assert _FakeWarmGateway.enter_count == 2
    assert _FakeBrowserSession.enter_count == 2
    assert _FakeBrowserSession.exit_count == 2
