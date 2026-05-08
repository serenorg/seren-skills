"""Phase 7: Seren-Bounty integration.

Public surface:
  - BountyClient — thin wrapper around the seren-bounty publisher
  - SubmissionReconciler — folds persisted prior markets into each new
    submission so the operator's reconciler sees the cumulative ledger
  - exceptions: BountyClientError, BountyUnauthorized, IdentityDrift

Plan §13.
"""

from __future__ import annotations


class BountyClientError(Exception):
    """Base for all bounty-client exceptions."""


class BountyUnauthorized(BountyClientError):
    """seren-bounty returned 401 — SEREN_API_KEY missing or invalid."""


class IdentityDrift(BountyClientError):
    """The participant's prophet_viewer_id has changed between runs.

    Per plan §13.2 this is a P0 attribution defect: a different Prophet
    account is now creating markets under the same bounty participant.
    The operator's reconciler will not credit those markets, and silently
    proceeding would surface as zero earnings without explanation. Abort
    with blocked_identity_drift instead.
    """


__all__ = [
    "BountyClientError",
    "BountyUnauthorized",
    "IdentityDrift",
]
