"""Polymarket source discovery — plan §14.

Fetches the settling-markets feed from the polymarket-data publisher and
applies three fail-closed filters before any row reaches candidate
generation:

  - deadline gate: resolution_date < deadline (strict <, not <=).
    Rows resolving exactly at the deadline are dropped because the
    bounty's verifier window closes on the same instant — a market
    resolving at deadline cannot be paid before the window shuts.
  - settled gate: settled=True rows are skipped (no value mirroring an
    already-finalized market).
  - parseable date gate: rows missing or with malformed resolution_date
    are dropped. Without a verifiable date we cannot prove the deadline
    gate, and creating an out-of-window Prophet market costs gas and
    earns nothing.

The Polymarket source row is normalized into PolymarketSource so the
downstream candidate generator does not have to handle raw publisher
JSON shape variations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PUBLISHER = "polymarket-data"
# Phase-14 live probe (2026-05-08): the seren `polymarket-data` publisher
# exposes Polymarket's Gamma API directly. Default `/markets` returns 20
# arbitrary rows; we narrow to deadline-eligible open markets server-side
# so the response we filter is already pre-trimmed.
_BASE_PATH = "/markets"
_DEFAULT_LIMIT = 100
# #538 — auto-discover sweeps a larger universe so the volume filter has
# enough qualifiers to produce ~24 actionable candidates. Mirrors the
# bounty-runner's Phase-15 bump (`scripts/polymarket/discovery.py`).
_AUTO_DISCOVER_LIMIT = 500
DEFAULT_AUTO_DISCOVER_MAX_CANDIDATES = 250


@dataclass
class PolymarketSource:
    polymarket_market_id: str
    question: str
    resolution_date: datetime
    category: str | None
    settled: bool
    # #538 — added so auto-discover can rank/filter by 24h volume.
    # Defaults to 0.0 so existing callers that don't set it keep working.
    volume_24h_usd: float = 0.0
    slug: str | None = None


@dataclass
class ArbCandidateDiscoveryStats:
    """Audit counters for the capped auto-discover scan."""

    raw_markets_fetched: int
    markets_passing_gates: int
    candidates_returned: int
    max_candidates: int

    @property
    def truncated_by_max_candidates(self) -> bool:
        return self.markets_passing_gates > self.candidates_returned


def discover_polymarket_sources(
    *, gateway: Any, deadline: datetime
) -> list[PolymarketSource]:
    """Fetch settling-markets feed and return the deadline-eligible subset."""
    deadline_utc = _ensure_utc(deadline)
    deadline_iso = deadline_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = (
        f"{_BASE_PATH}?end_date_max={deadline_iso}"
        f"&closed=false&active=true&limit={_DEFAULT_LIMIT}"
    )
    response = gateway.call(PUBLISHER, "GET", path, body=None)
    raw_sources = _extract_sources(response)

    keepers: list[PolymarketSource] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        # Polymarket Gamma exposes per-market lifecycle as `closed` (true
        # once resolution is finalized). Fall back to `archived` /
        # `resolved` if `closed` is absent — the publisher mirrors the
        # source verbatim and shapes drift between vintages.
        # `closed` is the live publisher field; `settled` is the legacy
        # name preserved by older fixtures and downstream consumers.
        if any(raw.get(k) is True for k in ("closed", "archived", "resolved", "settled")):
            continue
        # Field-name mapping (live probe 2026-05-08): the publisher
        # returns Polymarket's native shape. `conditionId` is the
        # canonical on-chain identifier; `endDate` is ISO-8601 UTC.
        resolution_date = _parse_resolution_date(
            raw.get("endDate") or raw.get("endDateIso") or raw.get("resolution_date")
        )
        if resolution_date is None:
            continue
        if resolution_date >= deadline_utc:
            continue
        market_id = raw.get("conditionId") or raw.get("id") or raw.get("polymarket_market_id")
        question = raw.get("question")
        if not isinstance(market_id, str) or not market_id:
            continue
        if not isinstance(question, str) or not question:
            continue
        category = _extract_category(raw)
        keepers.append(
            PolymarketSource(
                polymarket_market_id=market_id,
                question=question,
                resolution_date=resolution_date,
                category=category,
                settled=False,
            )
        )
    return keepers


def _extract_sources(response: Any) -> list[Any]:
    # Live probe 2026-05-08: the seren `polymarket-data` publisher returns
    # a flat list of market objects. Tolerate the legacy `{sources: [...]}`
    # envelope too so test fixtures keep working.
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        sources = response.get("sources") or response.get("markets") or response.get("data")
        if isinstance(sources, list):
            return sources
    return []


def _extract_category(raw: dict[str, Any]) -> str | None:
    direct = raw.get("category")
    if isinstance(direct, str) and direct:
        return direct
    # Polymarket nests category under `events[0].category` for some shapes.
    events = raw.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict):
            cat = first.get("category")
            if isinstance(cat, str) and cat:
                return cat
    return None


def _parse_resolution_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Auto-discover (#538)
#
# Layered on top of the dedup-mode discover above. The arb-bot's
# auto-discover path needs three additional gates the bounty-runner
# discovery doesn't enforce:
#
#   - 24h volume floor — only mirror markets with real Polymarket flow,
#     otherwise Prophet's odds-vs-Polymarket spread is meaningless.
#   - Execution headroom — drop markets resolving inside the agent's
#     UI-submission window (Validate ~10s + odds calc 60-120s + bet form
#     + Prophet signing prompt = ~90-180s minimum). Same default the
#     bounty-runner applies (10 min).
#   - Candidate cap — keep the per-tick batch bounded while still
#     sweeping past the top-50 volume sample. Default 250.
#
# Returns the same `PolymarketSource` shape with `volume_24h_usd` /
# `slug` populated, plus a per-row signal that lets the caller route
# matched pairs vs. pending-creation entries downstream.


def discover_arb_candidates(
    *,
    gateway: Any,
    deadline: datetime,
    min_24h_volume_usd: float = 10_000.0,
    minimum_headroom_seconds: int = 24 * 3600,
    max_candidates: int = DEFAULT_AUTO_DISCOVER_MAX_CANDIDATES,
    now: datetime | None = None,
) -> list[PolymarketSource]:
    """Auto-discover qualifying Polymarket candidates for arb.

    Pure-data: the publisher call is the only side effect. Caller is
    responsible for the Prophet pair lookup and any persistence.
    """
    candidates, _stats = discover_arb_candidates_with_stats(
        gateway=gateway,
        deadline=deadline,
        min_24h_volume_usd=min_24h_volume_usd,
        minimum_headroom_seconds=minimum_headroom_seconds,
        max_candidates=max_candidates,
        now=now,
    )
    return candidates


def discover_arb_candidates_with_stats(
    *,
    gateway: Any,
    deadline: datetime,
    min_24h_volume_usd: float = 10_000.0,
    minimum_headroom_seconds: int = 24 * 3600,
    max_candidates: int = DEFAULT_AUTO_DISCOVER_MAX_CANDIDATES,
    now: datetime | None = None,
) -> tuple[list[PolymarketSource], ArbCandidateDiscoveryStats]:
    """Auto-discover candidates and return scan counters.

    The scan continues through the fetched publisher page after the
    return cap is full so the run summary can distinguish "250 were
    evaluated" from "only 250 existed in the filtered universe."
    """
    from datetime import timedelta

    deadline_utc = _ensure_utc(deadline)
    now_utc = _ensure_utc(now) if now is not None else datetime.now(tz=timezone.utc)
    earliest_resolution = now_utc + timedelta(seconds=int(minimum_headroom_seconds))
    max_count = max(0, int(max_candidates))

    deadline_iso = deadline_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = (
        f"{_BASE_PATH}?end_date_min={now_iso}&end_date_max={deadline_iso}"
        f"&closed=false&active=true"
        f"&order=volume24hr&ascending=false&limit={_AUTO_DISCOVER_LIMIT}"
    )
    response = gateway.call(PUBLISHER, "GET", path, body=None)
    raw_sources = _extract_sources(response)

    keepers: list[PolymarketSource] = []
    markets_passing_gates = 0
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        if any(raw.get(k) is True for k in ("closed", "archived", "resolved", "settled")):
            continue
        # Volume gate — strict >= so the operator can set the cutoff at
        # exactly $10k and get the full 10-k tier.
        volume_24h = _safe_volume(raw)
        if volume_24h < float(min_24h_volume_usd):
            continue
        resolution_date = _parse_resolution_date(
            raw.get("endDate") or raw.get("endDateIso") or raw.get("resolution_date")
        )
        if resolution_date is None:
            continue
        if resolution_date >= deadline_utc:
            continue
        if resolution_date < earliest_resolution:
            # Markets too close to resolution waste a seed bet — the UI
            # submission can't complete before the market closes.
            continue
        market_id = raw.get("conditionId") or raw.get("id") or raw.get("polymarket_market_id")
        question = raw.get("question") or raw.get("title")
        if not isinstance(market_id, str) or not market_id:
            continue
        if not isinstance(question, str) or not question:
            continue
        markets_passing_gates += 1
        if len(keepers) >= max_count:
            continue
        category = _extract_category(raw)
        slug = raw.get("slug")
        slug_str = slug if isinstance(slug, str) and slug else None
        keepers.append(
            PolymarketSource(
                polymarket_market_id=market_id,
                question=question,
                resolution_date=resolution_date,
                category=category,
                settled=False,
                volume_24h_usd=volume_24h,
                slug=slug_str,
            )
        )
    stats = ArbCandidateDiscoveryStats(
        raw_markets_fetched=len(raw_sources),
        markets_passing_gates=markets_passing_gates,
        candidates_returned=len(keepers),
        max_candidates=max_count,
    )
    return keepers, stats


def _safe_volume(raw: dict[str, Any]) -> float:
    """Extract 24h volume from Gamma's response. Field name varies by
    response shape — try the live name first, then the legacy fallbacks."""
    for key in ("volume24hr", "volumeNum24hr", "volume_24h", "volume24h"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0
