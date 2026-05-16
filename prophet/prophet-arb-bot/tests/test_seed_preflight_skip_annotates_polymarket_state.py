"""Issue #598 — close the #589 / #596 interaction gap.

When the seed preflight reports `max_fundable=0` because Polymarket
collateral is short, `seed_preflight_orchestration.resolve_seed_preflight_action`
splits two ways:

  * `existing_pairs_count == 0` — block the cycle (the old behavior).
  * `existing_pairs_count > 0` — drop the pending list, keep scoring
    the existing pairs (the #589 fix).

The block branch annotates polymarket state and runs the #596 auto-
approve broadcast. The skip branch did not — so wallets with USDC.e
on-chain but no Polymarket approvals stayed stuck in `no_approvals`
across every cycle, even though the bot is capable of fixing the
problem on the operator's behalf.

This is the critical regression test. One assertion path, covering
the exact bug: when we take the skip branch and `polymarket_deficit
> 0`, `_annotate_polymarket_state` MUST be called. The annotation's
own behavior (no_approvals → broadcast, no_balance → no-op) is
already covered by `test_polymarket_approvals_auto_submit.py` from
#597 and `test_polymarket_state_diagnostic.py` from #592 — not
re-tested here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import agent  # noqa: E402


class _StubRecorder:
    """Minimal recorder satisfying the surface `_apply_seed_preflight_and_trim` touches."""

    def __init__(self) -> None:
        self.summary: dict[str, Any] = {}
        self.blockers: list[str] = []

    def record_blocker(self, code: str) -> None:
        self.blockers.append(code)


class _StubTrader:
    """Stand-in for `live_hedger._trader` — only the attributes the
    seed preflight reads."""

    def __init__(self, *, address: str, polymarket_avail: float) -> None:
        self.address = address
        self._polymarket_avail = polymarket_avail

    def get_cash_balance(self) -> float:
        return self._polymarket_avail


class _StubLiveHedger:
    def __init__(self, trader: _StubTrader) -> None:
        self._trader = trader


def test_skip_branch_annotates_polymarket_state_when_polymarket_deficit_positive(
    stub_transport,
    monkeypatch,
) -> None:
    """The skip branch (#589 keep-trading-existing-pairs) must invoke
    `_annotate_polymarket_state` whenever Polymarket has a deficit —
    otherwise the #596 auto-approve broadcast never gets a chance to
    fire and wallets in `no_approvals` stay broken indefinitely.
    """

    # Prophet has enough cash to fund all seeds; Polymarket has $0.
    # `resolve_seed_preflight_action` will pick the keep-trading branch
    # because `existing_pairs_count == 1`.
    stub_transport.register(
        "ViewerWalletBalance",
        {
            "data": {
                "viewer": {
                    "walletBalance": {
                        "availableCents": 5_000,  # $50 — covers 2 seeds at $1
                        "totalCents": 5_000,
                    }
                }
            }
        },
    )

    # Test-only example address (NOT the real operator wallet).
    trader = _StubTrader(
        address="0x0000000000000000000000000000000000000001",
        polymarket_avail=0.0,
    )
    live_hedger = _StubLiveHedger(trader)

    # Capture every invocation of the annotation helper. The helper's
    # own behavior is exercised by #597's auto-submit test — we only
    # need to prove the skip branch reaches it.
    annotate_calls: list[dict[str, Any]] = []

    def _spy_annotate(**kwargs: Any) -> None:
        annotate_calls.append(kwargs)

    monkeypatch.setattr(agent, "_annotate_polymarket_state", _spy_annotate)

    recorder = _StubRecorder()
    pending = [
        {"polymarket_market_id": f"M{i}", "initial_bet_usdc": 1.0}
        for i in range(2)
    ]

    result = agent._apply_seed_preflight_and_trim(
        pending=pending,
        initial_bet_usdc=1.0,
        delta_neutral=True,
        live_hedger=live_hedger,
        max_hedge_slippage_bps=200.0,
        transport=stub_transport,
        jwt="stub-jwt",
        gateway=None,
        recorder=recorder,
        existing_pairs_count=1,
    )

    # Skip branch must return None (continue scoring).
    assert result is None
    # And it must have called the annotation — this is the bug fix.
    assert len(annotate_calls) == 1, (
        "_annotate_polymarket_state was never called from the skip branch; "
        "#596 auto-approve cannot fire for operators with existing pairs"
    )
    # Sanity: the annotation was called with the polymarket balance the
    # preflight measured (0.0), so it can correctly classify state.
    assert annotate_calls[0]["polymarket_avail_usdc"] == 0.0
    assert annotate_calls[0]["live_hedger"] is live_hedger
    assert annotate_calls[0]["recorder"] is recorder
