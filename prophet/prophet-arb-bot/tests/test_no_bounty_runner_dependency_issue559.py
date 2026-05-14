"""Issue #559: arb-bot must not read from prophet-bounty-runner.

Two critical contracts, one assertion each:

1. The Privy session cache default path does not live under prophet-bounty-runner.
2. The persistence module does not expose the cross-skill SELECT helper.
"""

from __future__ import annotations

import importlib


def test_session_cache_default_path_not_under_bounty_runner() -> None:
    session_cache = importlib.import_module("otp_worker.session_cache")
    default_path = session_cache.default_cache_path()
    assert "prophet-bounty-runner" not in str(default_path), (
        f"SessionCache default path leaks into prophet-bounty-runner: {default_path}"
    )


def test_persistence_does_not_expose_cross_skill_read() -> None:
    persistence = importlib.import_module("persistence")
    assert not hasattr(persistence, "discover_pairs_from_bounty_runner"), (
        "persistence.discover_pairs_from_bounty_runner must not exist — "
        "cross-skill reads between arb-bot and bounty-runner are removed."
    )
