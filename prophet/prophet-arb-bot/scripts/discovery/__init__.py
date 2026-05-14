"""Auto-discover front-end for the arb-bot (#538).

Wraps the Polymarket campaign filter, Prophet pair lookup, and
candidate-sheet refresh so `cmd_run` can opt into the full discovery
flow with a single call. The Prophet creation step still happens
through the agent-driven Playwright `/create` runbook — the Python
runtime stops at emitting `pending_ui_submission` entries.
"""

from .auto_discover import (
    AutoDiscoverConfig,
    AutoDiscoverResult,
    run_auto_discover,
)
from .prophet_pair_lookup import find_matching_prophet_markets

__all__ = [
    "AutoDiscoverConfig",
    "AutoDiscoverResult",
    "run_auto_discover",
    "find_matching_prophet_markets",
]
