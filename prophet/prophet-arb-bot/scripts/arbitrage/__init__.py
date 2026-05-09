"""Arbitrage scoring + opportunity emission for the Prophet ↔ Polymarket pair.

The arb-bot watches prophet markets that the bounty-runner created from
polymarket sources. Because every prophet market is seeded from a
specific polymarket conditionId, the polymarket price is the
operator-believable fair value reference; any drift on prophet that
exceeds the configured threshold is a tradable opportunity.

Modules:
  - scoring       — spread math + threshold gates + sizing
  - intelligence  — optional seren-polymarket-intelligence enrichment
"""

from __future__ import annotations


class ArbError(Exception):
    """Base for arb-bot scoring errors."""


class InsufficientPriceDataError(ArbError):
    """Either prophet or polymarket price was missing for the pair."""


class HealthGateFailureError(ArbError):
    """A pre-trade health check failed (volatility, freshness, liquidity)."""


__all__ = [
    "ArbError",
    "InsufficientPriceDataError",
    "HealthGateFailureError",
]
