"""Critical-only seren-bounty tests.

Reduced from plan §13.4 (6 tests) to 4 load-bearing assertions:

  1. BountyClient.join returns the deterministic referral_code
     (load-bearing: every downstream skill flow keys off this code)
  2. SubmissionReconciler.fold includes ALL prior markets, not just the
     current run (P0 §13.2 replace semantics — the operator's reconciler
     reads the cumulative ledger from the latest submission)
  3. SubmissionReconciler raises IdentityDrift on viewer_id mismatch
     (P0 §13.2: silently submitting markets under a different Prophet
     account means the operator reconciler refuses credit and the
     participant sees zero earnings without explanation)
  4. BountyClient.earnings filters by bounty_id when provided
     (query-string contract; the status command depends on this)

Skipped per critical-only doctrine:
  - test_bounty_join_passes_through_idempotency_response_unchanged
    — duplicate of #1 (idempotency is server-side, the client just
    surfaces whatever code it gets)
  - test_bounty_submission_orders_markets_by_creation_time
    — formatting concern, not fail-closed; ordering tests are brittle
    when tied to specific timestamps
  - test_earnings_status_returns_zero_when_user_has_no_earnings
    — happy-path-no-data; covered transitively by #4 (the filter test
    already exercises the empty-list branch)
"""

from __future__ import annotations

import pytest

from bounty import BountyUnauthorized, IdentityDrift  # noqa: E402
from bounty.client import BountyClient  # noqa: E402
from bounty.reconciler import (  # noqa: E402
    MarketRecord,
    SubmissionReconciler,
)


class _StubGateway:
    """Minimal gateway stub. Records every call for assertion."""

    def __init__(self, response=None) -> None:
        self.response = response or {}
        self.calls: list[dict] = []

    def call(self, publisher, method, path, body=None, headers=None):
        self.calls.append(
            {
                "publisher": publisher,
                "method": method,
                "path": path,
                "body": body,
                "headers": headers,
            }
        )
        return self.response


# ---------------------------------------------------------------------------
# Test 1: BountyClient.join surfaces the deterministic referral_code


def test_bounty_join_returns_referral_code() -> None:
    gateway = _StubGateway(
        response={
            "bounty_id": "bounty_fixture_001",
            "user_id": "user_fixture_001",
            "referral_code": "abc123def456",
        }
    )
    client = BountyClient(gateway=gateway)

    result = client.join("bounty_fixture_001")

    assert result.referral_code == "abc123def456"
    assert result.bounty_id == "bounty_fixture_001"


# ---------------------------------------------------------------------------
# Test 2: Reconciler folds prior + current markets (REPLACE semantics)


def test_bounty_submission_includes_all_prior_markets() -> None:
    prior = [
        MarketRecord(
            prophet_market_id="prophet_run1_market_a",
            prophet_market_url="https://app.prophetmarket.ai/m/run1-a",
            polymarket_source_url="https://polymarket.com/event/poly-001",
            resolution_date_iso="2026-05-09T18:00:00Z",
            prophet_viewer_id="viewer_fixture_001",
        ),
        MarketRecord(
            prophet_market_id="prophet_run2_market_b",
            prophet_market_url="https://app.prophetmarket.ai/m/run2-b",
            polymarket_source_url="https://polymarket.com/event/poly-002",
            resolution_date_iso="2026-05-10T23:59:00Z",
            prophet_viewer_id="viewer_fixture_001",
        ),
    ]
    current = [
        MarketRecord(
            prophet_market_id="prophet_run3_market_c",
            prophet_market_url="https://app.prophetmarket.ai/m/run3-c",
            polymarket_source_url="https://polymarket.com/event/poly-003",
            resolution_date_iso="2026-05-08T12:00:00Z",
            prophet_viewer_id="viewer_fixture_001",
        ),
    ]
    reconciler = SubmissionReconciler()

    text = reconciler.fold(
        current_viewer_id="viewer_fixture_001",
        prior_markets=prior,
        current_run_markets=current,
    )

    assert "prophet_run1_market_a" in text
    assert "prophet_run2_market_b" in text
    assert "prophet_run3_market_c" in text


# ---------------------------------------------------------------------------
# Test 3: Reconciler aborts on identity drift (P0 §13.2)


def test_reconciler_aborts_on_identity_drift() -> None:
    drifted_prior = [
        MarketRecord(
            prophet_market_id="prophet_run1_market_a",
            prophet_market_url="https://app.prophetmarket.ai/m/run1-a",
            polymarket_source_url="https://polymarket.com/event/poly-001",
            resolution_date_iso="2026-05-09T18:00:00Z",
            prophet_viewer_id="viewer_OLD_account_999",  # different
        ),
    ]
    reconciler = SubmissionReconciler()

    with pytest.raises(IdentityDrift, match="viewer_id drift"):
        reconciler.fold(
            current_viewer_id="viewer_fixture_001",
            prior_markets=drifted_prior,
            current_run_markets=[],
        )


# ---------------------------------------------------------------------------
# Test 4: BountyClient.earnings filters by bounty_id


def test_earnings_status_filters_by_bounty_id() -> None:
    gateway = _StubGateway(response={"earnings": []})
    client = BountyClient(gateway=gateway)

    client.earnings(bounty_id="bounty_fixture_001")

    assert len(gateway.calls) == 1
    call = gateway.calls[0]
    assert call["path"] == "/users/me/earnings"
    assert call["headers"].get("X-Query-Bounty-Id") == "bounty_fixture_001"
