"""Critical-only tests for setup_cron.py and seren_cron_client.

Coverage:
  - test_skill_slug_is_arb_bot: hard-coded skill identity protects
    against accidental re-pointing of the cron at the bounty-runner.
  - test_default_poll_interval_is_12h: locks the cost-control invariant
    inherited from #469. Anything below 12h burns hundreds of dollars
    a month per idle user on the seren-cron poll endpoint.
  - test_default_cron_is_hourly: arb-bot cadence is hourly, not 6h.
  - test_build_local_payload_yes_live_only_when_not_dry_run: --dry-run
    must dominate --yes-live so the operator can force a dry-run schedule
    without un-setting --yes-live first.
"""

from __future__ import annotations

import seren_cron_client
from setup_cron import build_local_payload


def test_skill_slug_is_arb_bot() -> None:
    assert seren_cron_client.SKILL_SLUG == "prophet-arb-bot"


def test_default_poll_interval_is_12h() -> None:
    assert seren_cron_client.DEFAULT_POLL_INTERVAL_SECONDS == 12 * 60 * 60


def test_default_cron_is_hourly() -> None:
    assert seren_cron_client.DEFAULT_CRON_EXPRESSION == "0 * * * *"


def test_build_local_payload_yes_live_only_when_not_dry_run() -> None:
    live = build_local_payload(
        config_path="config.json",
        prophet_email="x@example.com",
        email_provider="gmail",
        yes_live=True,
        dry_run=False,
    )
    assert live["yes_live"] is True
    assert live["dry_run"] is False

    forced_dry = build_local_payload(
        config_path="config.json",
        prophet_email="x@example.com",
        email_provider="gmail",
        yes_live=True,
        dry_run=True,
    )
    assert forced_dry["yes_live"] is False  # dry_run wins
    assert forced_dry["dry_run"] is True
