"""Live Polymarket price fetcher — different concern from discovery.

`discovery.py` enumerates settling markets that match the bounty deadline
filter. The arb-bot already has the `polymarket_condition_id` from the
bounty-runner's `markets_created` table; we just need a thin wrapper to
fetch the current YES/NO price for a known conditionId.

The polymarket-data publisher exposes Gamma API at the publisher root.
A single market can be looked up at `/markets/{conditionId}` (or via the
filter `?id=<conditionId>` depending on Gamma vintage). The Polymarket
order book has a "midpoint" we read as our reference price; if midpoint
is missing we fall back to the most recent trade price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PUBLISHER = "polymarket-data"


@dataclass
class PolymarketPrice:
    polymarket_condition_id: str
    yes_price: float
    no_price: float
    last_trade_at: str | None = None
    liquidity_usdc: float = 0.0
    is_stale: bool = False


def fetch_market_price(
    *, gateway: Any, condition_id: str
) -> PolymarketPrice | None:
    """Single-market price lookup. Returns None if the publisher 404s or
    the response shape is unrecognized — callers should treat None as
    'no fair-value reference, skip this pair this cycle'.
    """
    if not condition_id:
        return None
    try:
        # Gamma serves `/markets/<conditionId>` for direct lookup. Some
        # publisher vintages prefer the filter form `/markets?id=...`;
        # try direct first, fall back on 404.
        response = gateway.call(
            PUBLISHER, "GET", f"/markets/{condition_id}", body=None
        )
    except Exception:
        try:
            response = gateway.call(
                PUBLISHER, "GET", f"/markets?id={condition_id}&limit=1", body=None
            )
        except Exception:
            return None

    market = _extract_market(response, condition_id)
    if market is None:
        return None
    yes, no = _extract_prices(market)
    if yes <= 0 and no <= 0:
        return None
    if yes > 0 and no <= 0:
        no = max(0.0, 1.0 - yes)
    if no > 0 and yes <= 0:
        yes = max(0.0, 1.0 - no)
    return PolymarketPrice(
        polymarket_condition_id=condition_id,
        yes_price=yes,
        no_price=no,
        last_trade_at=_safe_str(market.get("lastTradeAt") or market.get("updatedAt")),
        liquidity_usdc=_safe_float(market.get("liquidityNum") or market.get("liquidity")),
        is_stale=bool(market.get("closed")) or bool(market.get("resolved")),
    )


def fetch_market_prices(
    *, gateway: Any, condition_ids: list[str]
) -> dict[str, PolymarketPrice]:
    """Bulk lookup. Falls back to per-id fetches if the publisher does
    not support filter-by-list (Gamma's behavior changes between
    vintages, so we treat single-fetch as the contract and bulk as a
    nice-to-have)."""
    out: dict[str, PolymarketPrice] = {}
    for condition_id in condition_ids:
        price = fetch_market_price(gateway=gateway, condition_id=condition_id)
        if price is not None:
            out[condition_id] = price
    return out


def _extract_market(response: Any, condition_id: str) -> dict[str, Any] | None:
    """Tolerant unwrap. Polymarket-data has returned several shapes:
    a flat list, `{markets: [...]}`, `{data: [...]}`, or a single object
    when looked up by id."""
    if isinstance(response, dict):
        if "markets" in response and isinstance(response["markets"], list):
            for m in response["markets"]:
                if isinstance(m, dict) and m.get("conditionId") == condition_id:
                    return m
            if response["markets"]:
                first = response["markets"][0]
                return first if isinstance(first, dict) else None
        if "data" in response and isinstance(response["data"], list):
            for m in response["data"]:
                if isinstance(m, dict) and m.get("conditionId") == condition_id:
                    return m
        if response.get("conditionId") == condition_id:
            return response
        if response.get("id") == condition_id:
            return response
        # Single-market lookup that returned without conditionId match —
        # trust the publisher and return it.
        if "outcomes" in response or "outcomePrices" in response:
            return response
    if isinstance(response, list):
        for m in response:
            if isinstance(m, dict) and m.get("conditionId") == condition_id:
                return m
        if response and isinstance(response[0], dict):
            return response[0]
    return None


def _extract_prices(market: dict[str, Any]) -> tuple[float, float]:
    """Polymarket exposes prices in two shapes depending on the vintage:
      - `outcomePrices`: ["0.62", "0.38"]  (YES, NO as strings)
      - `outcomes`: [{"name":"Yes","price":0.62}, {"name":"No","price":0.38}]
    We accept both and fall back to `bestBid`/`bestAsk` when neither is
    present (newer vintages hide outcome prices behind the order book).
    """
    op = market.get("outcomePrices")
    if isinstance(op, list) and len(op) >= 2:
        return _safe_float(op[0]), _safe_float(op[1])
    if isinstance(op, str):
        # Some publisher responses serialize outcomePrices as a JSON string.
        try:
            import json

            parsed = json.loads(op)
            if isinstance(parsed, list) and len(parsed) >= 2:
                return _safe_float(parsed[0]), _safe_float(parsed[1])
        except (ValueError, TypeError):
            pass
    outcomes = market.get("outcomes")
    if isinstance(outcomes, list):
        yes = no = 0.0
        for item in outcomes:
            if isinstance(item, dict):
                name = (item.get("name") or "").strip().lower()
                price = _safe_float(item.get("price"))
                if name == "yes":
                    yes = price
                elif name == "no":
                    no = price
        if yes > 0 or no > 0:
            return yes, no
    # Last-resort: use best bid/ask as the YES price reference.
    best_bid = _safe_float(market.get("bestBid"))
    best_ask = _safe_float(market.get("bestAsk"))
    if best_bid > 0 and best_ask > 0:
        midpoint = (best_bid + best_ask) / 2.0
        return midpoint, max(0.0, 1.0 - midpoint)
    return 0.0, 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _safe_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
