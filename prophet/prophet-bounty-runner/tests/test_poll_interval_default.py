"""Issue #469: lock the local-pull poll cadence at 12 hours.

`seren-cron`'s `POST /api/runners/{id}/poll` endpoint is priced at $0.005
per call. The bounty cron only fires every 6h, so the runner has nothing
to claim 99.97% of the time. The legacy 30s default cost ~$432/mo per
idle user; the 12h default costs ~$0.30/mo. The server can still override
on the fly via `next_poll_seconds` in the poll response, so this only
governs idle cadence.

If anyone reverts this constant the regression is silent in production
(no test fails, no exception throws — just the bill spikes). One test
locks the value so the cost contract cannot drift unnoticed.
"""

from __future__ import annotations


def test_default_poll_interval_is_12_hours() -> None:
    from seren_cron_client import DEFAULT_POLL_INTERVAL_SECONDS

    assert DEFAULT_POLL_INTERVAL_SECONDS == 12 * 60 * 60, (
        "DEFAULT_POLL_INTERVAL_SECONDS must be 12 hours (43200 seconds) per "
        "issue #469. Lowering this default re-introduces the ~$432/mo idle "
        "poll cost on seren-cron. Use the per-tick `next_poll_seconds` "
        "override or the `--poll-interval-seconds` CLI flag for tighter "
        "cadence; do not change this default."
    )
