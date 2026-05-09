"""Optional seren-polymarket-intelligence enrichment.

The intelligence publisher exposes computed signals (correlation,
suggested pairs, basis spread distributions) that help the arb-bot
distinguish between (a) a real prophet drift worth trading and (b)
short-lived polymarket noise that will mean-revert before our order
fills.

This module is gated by `intelligence.enabled` in config. When disabled
the arb-bot trades on raw spread alone — which is fine, but generally
emits more low-edge signals because it cannot tell whether polymarket
itself is volatile right now.

Endpoints (live probe 2026-05-09):

  GET /api/polymarket/correlations
      $0.10 per call. Returns pair-level basis spread distributions and
      correlation. We use the standard deviation field as a volatility
      proxy: high σ → polymarket reference is jittery → demote the
      opportunity to watchlist.

  GET /api/polymarket/pairs/suggested
      $0.10 per call. Returns suggested basis pairs. We do NOT use this
      in Mode A because every prophet market is already tied to a
      specific polymarket conditionId via the bounty-runner's
      `markets_created` table.

If the publisher returns 402 / pricing the call as low-funds, the agent
treats that as an `intelligence_unavailable` health warning and trades
without enrichment rather than blocking the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PUBLISHER = "seren-polymarket-intelligence"


@dataclass
class IntelligenceConfig:
    enabled: bool = False
    max_basis_volatility: float = 0.05
    fetch_correlations: bool = True


@dataclass
class IntelligenceVerdict:
    health_warnings: list[str]
    enriched: bool


def assess_pair_health(
    *,
    gateway: Any,
    polymarket_condition_id: str,
    config: IntelligenceConfig,
) -> IntelligenceVerdict:
    """Return health warnings for a polymarket reference price.

    No-op (returns enriched=False) when intelligence is disabled. On
    publisher failure or 402 the call is downgraded to a warning rather
    than a hard fail — the arb-bot keeps cycling on raw spread.
    """
    if not config.enabled:
        return IntelligenceVerdict(health_warnings=[], enriched=False)

    if not config.fetch_correlations:
        return IntelligenceVerdict(health_warnings=[], enriched=False)

    try:
        response = gateway.call(
            PUBLISHER,
            "GET",
            f"/api/polymarket/correlations?condition_id={polymarket_condition_id}",
            body=None,
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "402" in msg or "low" in msg and "balance" in msg:
            return IntelligenceVerdict(
                health_warnings=["intelligence_unavailable_low_balance"],
                enriched=False,
            )
        return IntelligenceVerdict(
            health_warnings=[f"intelligence_call_failed:{type(exc).__name__}"],
            enriched=False,
        )

    if not isinstance(response, dict):
        return IntelligenceVerdict(
            health_warnings=["intelligence_unexpected_shape"],
            enriched=False,
        )

    sigma = _extract_sigma(response)
    warnings: list[str] = []
    if sigma is not None and sigma > config.max_basis_volatility:
        warnings.append(
            f"polymarket_volatility_high:sigma={sigma:.4f}"
            f">{config.max_basis_volatility:.4f}"
        )
    return IntelligenceVerdict(health_warnings=warnings, enriched=True)


def _extract_sigma(response: dict[str, Any]) -> float | None:
    """Tolerant pull. The publisher has returned one of:
      {"basis_sigma": 0.02}
      {"sigma": 0.02}
      {"data": {"basis_sigma": 0.02}}
      {"correlations": [{"sigma": 0.02}]}
    """
    for key in ("basis_sigma", "sigma", "stddev", "std_dev"):
        if key in response and isinstance(response[key], (int, float)):
            return float(response[key])
    inner = response.get("data")
    if isinstance(inner, dict):
        for key in ("basis_sigma", "sigma", "stddev", "std_dev"):
            if key in inner and isinstance(inner[key], (int, float)):
                return float(inner[key])
    rows = response.get("correlations") or response.get("pairs")
    if isinstance(rows, list) and rows:
        first = rows[0]
        if isinstance(first, dict):
            for key in ("basis_sigma", "sigma", "stddev"):
                if key in first and isinstance(first[key], (int, float)):
                    return float(first[key])
    return None
