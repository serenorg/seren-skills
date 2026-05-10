"""SubmissionReconciler — fold persisted prior markets into each submission.

Plan §13.2 spells out the "REPLACE" contract: every submission overwrites
the prior one server-side, but the *content* must include all markets
the participant has ever created under this bounty — not just markets
from the current run. The operator's reconciler reads the latest
submission and uses it as the cumulative breadcrumb ledger.

P0 fail-closed: if the saved prophet_viewer_id from any prior market
disagrees with the current run's viewer_id, that's `blocked_identity_drift`
(§13.2). A different Prophet account is now creating markets under the
same bounty participant; silently submitting them would mean the
operator's reconciler refuses to credit any of the new markets and the
participant sees zero earnings without explanation. Better to abort the
run loudly.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import IdentityDrift


@dataclass(frozen=True)
class MarketRecord:
    prophet_market_id: str
    prophet_market_url: str
    polymarket_source_url: str
    resolution_date_iso: str
    prophet_viewer_id: str
    created_at_iso: str = ""


def render_submission_text(
    *,
    prophet_viewer_id: str,
    markets: list[MarketRecord],
) -> str:
    """Compose the submission body per plan §13.2 schema.

    Format (one breadcrumb line per market, prophet_market_id first as
    the operator reconciler's idempotency key):

        Participant: prophet_viewer_id=<id>

        Markets created in this run:
        - <prophet_market_id> | <url> (mirrors <source>, resolves <iso>, creator=<viewer_id>)
        - ...
    """
    lines = [
        f"Participant: prophet_viewer_id={prophet_viewer_id}",
        "",
        "Markets created in this run:",
    ]
    for m in markets:
        lines.append(
            f"- {m.prophet_market_id} | {m.prophet_market_url} "
            f"(mirrors {m.polymarket_source_url}, "
            f"resolves {m.resolution_date_iso}, "
            f"creator={m.prophet_viewer_id})"
        )
    return "\n".join(lines)


class SubmissionReconciler:
    """Folds prior persisted markets + the current run into one submission body.

    The reconciler is intentionally stateless — `prior_markets` comes
    from the skill's own `markets_created` table (Phase 11 / §17), and
    `current_run_markets` comes from this run's successful Prophet
    creates. The render step concatenates them in chronological order
    so the operator's reconciler sees a stable cumulative ledger.
    """

    def fold(
        self,
        *,
        current_viewer_id: str,
        prior_markets: list[MarketRecord],
        current_run_markets: list[MarketRecord],
    ) -> str:
        """Return the submission body to POST to seren-bounty.

        Raises IdentityDrift if any prior market's prophet_viewer_id
        disagrees with current_viewer_id — see plan §13.2 P0 row.
        """
        if not current_viewer_id:
            raise IdentityDrift("current_viewer_id is empty; cannot bind submission")

        # All prior markets must have been created under the same viewer.
        # Empty viewer_id on a prior record is a data-integrity fault —
        # treat it as drift rather than silently passing.
        drifted = [
            m for m in prior_markets if m.prophet_viewer_id != current_viewer_id
        ]
        if drifted:
            raise IdentityDrift(
                f"prophet_viewer_id drift: current={current_viewer_id!r} "
                f"prior had {len(drifted)} record(s) with different viewer ids"
            )

        # Merge + dedupe by prophet_market_id (canonical key per §13.2).
        # Preserve chronological order: prior first, then this run.
        seen: set[str] = set()
        merged: list[MarketRecord] = []
        for m in [*prior_markets, *current_run_markets]:
            if m.prophet_market_id in seen:
                continue
            seen.add(m.prophet_market_id)
            merged.append(m)

        return render_submission_text(
            prophet_viewer_id=current_viewer_id, markets=merged
        )
