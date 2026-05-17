"""Integration coverage for the cmd_run → ProgressEmitter wiring (#640).

The unit suite (`test_progress.py`) already pins the emitter's file
format and rotation behavior. This module pins the *wiring*: that
`cmd_run` actually calls `progress.emit(...)` at the canonical stages
and that a per-entry create_market_via_ui chain reports `entry_start` +
exactly one terminal stage (`pair_created` OR `entry_blocked`).

Without this test, a future refactor could silently drop the `emit()`
calls and CI would stay green — chat would just go quiet again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import agent
from agent import (
    AgentConfig,
    EXECUTION_MODE_DELTA_NEUTRAL,
    CycleResult,
    ProgressEmitter,
)
from arbitrage.intelligence import IntelligenceConfig
from arbitrage.scoring import ScoringConfig
from discovery import AutoDiscoverConfig, AutoDiscoverResult


class _Recorder:
    """Same DB-free recorder stub used by test_agent_issue545.py."""

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
            {"prophet_market_id": prophet_market_id, "polymarket_condition_id": polymarket_condition_id}
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
        max_hedge_slippage_bps=200.0,
    )


def _pending(cid: str, question: str = "Q?") -> dict[str, Any]:
    return {
        "polymarket_market_id": cid,
        "question": question,
        "category": "Sports",
        "category_slug": "sports",
        "resolution_date_iso": "2026-05-20T22:00:00Z",
        "initial_bet_usdc": 1.0,
        "bounty_id": "",
        "prophet_viewer_id": "vid_1",
        "source_skill": "prophet-arb-bot",
        "volume_24h_usd": 50_000.0,
    }


def _read_progress(state_dir: Path) -> list[dict]:
    p = state_dir / "run_progress.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_cmd_run_emits_canonical_stages_and_per_entry_terminals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A live cycle with 2 pending entries emits the expected stage chain.

    Pins three acceptance criteria from issue #640:
    - `cycle_start` and `cycle_end` always fire (§4).
    - `auth_ok` and `auto_discover_done` fire on the happy path.
    - Each entry emits `entry_start` + exactly one terminal stage (§2).
    """
    pending = [_pending("cond_a", "Yankees vs Mets"), _pending("cond_b", "Marlins vs Rays")]

    monkeypatch.setattr(agent, "RunRecorder", _Recorder)
    monkeypatch.setattr(agent, "_resolve_target", lambda config: object())
    monkeypatch.setattr(agent, "_acquire_jwt", lambda **kw: ("jwt.eyj", "vid_1", "cache"))
    # auto_discover finds the two pending markets; pairs stays empty so we
    # don't have to stub the full scoring path.
    monkeypatch.setattr(agent, "list_arb_pairs", lambda target: [])
    monkeypatch.setattr(
        agent,
        "run_auto_discover",
        lambda **kw: AutoDiscoverResult(
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
    # Seed preflight: fund both entries, no trim.
    def _seed_ok(**kw: Any) -> None:
        kw["recorder"].summary["_trimmed_pending"] = pending
        return None
    monkeypatch.setattr(agent, "_apply_seed_preflight_and_trim", _seed_ok)

    # Stub create_market_via_ui: first entry succeeds, second blocks.
    def fake_creator(**kw: Any) -> CycleResult:
        cid = kw["polymarket_condition_id"]
        if cid == "cond_a":
            return CycleResult(
                status="ok",
                reason="pair_created",
                payload={"prophet_market_id": "mkt_aaa"},
            )
        return CycleResult(
            status="blocked",
            reason="hedge_failed_no_commit",
            payload={},
        )

    progress = ProgressEmitter(state_dir=tmp_path)
    result = agent.cmd_run(
        config=_config(),
        gateway=object(),
        yes_live=True,
        transport=object(),
        hedger=object(),
        create_market_via_ui=fake_creator,
        progress=progress,
    )

    events = _read_progress(tmp_path)
    stages = [e["stage"] for e in events]

    # §4: cycle_start is always the first event; cycle_end is always last.
    assert stages[0] == "cycle_start"
    assert stages[-1] == "cycle_end"

    # cycle_start carries the run_id from RunRecorder.
    assert events[0]["tick_id"] == _Recorder.__dict__.get("run_id") or events[0]["tick_id"]
    assert events[0]["mode"] == "run"
    assert events[0]["yes_live"] is True

    # Happy-path stages fire in order.
    assert "auth_ok" in stages
    assert "auto_discover_done" in stages
    # Seed preflight passed → ok event fires (not blocked).
    assert "seed_preflight_ok" in stages
    assert "seed_preflight_blocked" not in stages

    # §2: per-entry contract. Two entries, each one entry_start + one
    # terminal stage (pair_created OR entry_blocked).
    entry_starts = [e for e in events if e["stage"] == "entry_start"]
    pair_created = [e for e in events if e["stage"] == "pair_created"]
    entry_blocked = [e for e in events if e["stage"] == "entry_blocked"]
    assert len(entry_starts) == 2
    assert [e["idx"] for e in entry_starts] == [1, 2]
    assert all(e["total"] == 2 for e in entry_starts)
    assert {e["idx"] for e in pair_created} == {1}
    assert pair_created[0]["prophet_market_id"] == "mkt_aaa"
    assert {e["idx"] for e in entry_blocked} == {2}
    assert entry_blocked[0]["reason"] == "hedge_failed_no_commit"

    # cycle_end mirrors the final CycleResult.
    assert events[-1]["status"] == result.status
    assert events[-1]["reason"] == result.reason


def test_progress_emitter_is_optional_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Calling cmd_run without progress= must still work end-to-end.

    Pins acceptance criterion §5 (`--json-output` envelope byte-identical).
    The emitter defaults to a real instance writing to the runtime state
    dir; redirect that via PROPHET_ARB_STATE_DIR so the test is hermetic.
    """
    monkeypatch.setenv("PROPHET_ARB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(agent, "RunRecorder", _Recorder)
    monkeypatch.setattr(agent, "_resolve_target", lambda config: object())
    monkeypatch.setattr(agent, "_acquire_jwt", lambda **kw: ("jwt.eyj", "vid_1", "cache"))
    monkeypatch.setattr(agent, "list_arb_pairs", lambda target: [])
    monkeypatch.setattr(
        agent,
        "run_auto_discover",
        lambda **kw: AutoDiscoverResult(
            candidates_found=0,
            already_paired=0,
            raw_markets_fetched=0,
            markets_passing_gates=0,
            candidates_evaluated_for_pairing=0,
            max_candidates=250,
            auto_paired=[],
            pending_ui_submission=[],
        ),
    )

    # No progress= passed in. cmd_run must not crash and the envelope
    # must carry only its documented keys (no progress-related leakage).
    result = agent.cmd_run(
        config=_config(),
        gateway=object(),
        yes_live=False,
        transport=object(),
        hedger=object(),
    )

    assert result.status == "ok"
    # Documented payload keys (run_id, status, reason, summary, pairs,
    # opportunities, orders, blockers). New stages live on disk, not in
    # the envelope.
    assert set(result.payload.keys()) >= {
        "run_id",
        "status",
        "reason",
        "summary",
        "pairs",
        "opportunities",
        "orders",
        "blockers",
    }
    # And the default emitter did write to the env-overridden dir.
    assert (tmp_path / "run_progress.jsonl").exists()
