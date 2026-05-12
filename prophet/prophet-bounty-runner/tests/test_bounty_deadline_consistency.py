"""Issue #498 — cross-module deadline-constant drift guard.

The bounty deadline lives as a constant in two source modules:
  - `agent.BOUNTY_DEADLINE_ISO` (drives the polymarket-discovery query
    and post-create eligibility gate)
  - `prophet.client.BOUNTY_RESOLUTION_DEADLINE_ISO` (defense-in-depth
    constant referenced by the prophet GraphQL client doc paths)

Plus a parallel `datetime` form `agent.BOUNTY_DEADLINE` whose ISO
projection must agree with `agent.BOUNTY_DEADLINE_ISO` (otherwise the
date comparison the discovery filter performs would silently disagree
with the string sent on the wire).

The previous deadline extension (2026-05-11 -> 2026-05-26, issue #498)
required edits in two files; future extensions will too. A drift here
silently breaks the bounty pipeline. This single fail-fast check is
cheaper than chasing the next stale reference through production logs.
"""

from __future__ import annotations

from agent import BOUNTY_DEADLINE, BOUNTY_DEADLINE_ISO
from prophet.client import BOUNTY_RESOLUTION_DEADLINE_ISO


def test_bounty_deadline_iso_matches_across_modules() -> None:
    assert BOUNTY_DEADLINE_ISO == BOUNTY_RESOLUTION_DEADLINE_ISO


def test_bounty_deadline_datetime_matches_iso() -> None:
    expected = BOUNTY_DEADLINE.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert expected == BOUNTY_DEADLINE_ISO
