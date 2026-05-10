"""Phase 8: Polymarket source discovery.

Public surface:
  - PolymarketSource — normalized row carried forward into candidate generation
  - discover_polymarket_sources(gateway, *, deadline) — fetch + fail-closed filter
  - exceptions: PolymarketDiscoveryError

Plan §14.
"""

from __future__ import annotations


class PolymarketDiscoveryError(Exception):
    """Base for polymarket discovery exceptions."""


__all__ = [
    "PolymarketDiscoveryError",
]
