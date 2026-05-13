"""Polymarket source discovery — plan §14.

Fetches the settling-markets feed from the polymarket-data publisher and
applies five fail-closed filters before any row reaches candidate
generation:

  - deadline gate: resolution_date < deadline (strict <, not <=).
    Rows resolving exactly at the deadline are dropped because the
    bounty's verifier window closes on the same instant — a market
    resolving at deadline cannot be paid before the window shuts.
  - past-cutoff gate: resolution_date > now (strict >, not >=).
    Polymarket Gamma keeps UMA-stuck markets in the "active" pool
    long after their `endDate` has passed (live probe 2026-05-13:
    Harvey Weinstein sentencing markets dated 2025-12-31 still
    reported `closed=false active=true`). Mirroring one of those onto
    Prophet creates a market for an event that already happened —
    the bounty's reconciler refuses to credit it and the agent burns
    the seed bet for nothing. Both a server-side `end_date_min` query
    parameter and a client-side guard catch this: server side
    narrows the response, client side fails closed if Gamma drops
    the filter at the source.
  - execution-headroom gate (issue #522): rows whose `resolution_date
    - now < minimum_headroom_seconds` are dropped. Phase-15 UI
    submission needs Validate Question + odds calc (60-120s,
    occasionally slower) + bet form + Privy signing prompt -
    realistically 90-180s minimum. Candidates resolving inside that
    window guarantee the seed bet is burned for nothing. Default
    headroom is 0 (no filter) so existing callers stay backward
    compatible; `_cmd_run` opts in via `minimum_ui_headroom_seconds`.
    Dropped rows are appended to the caller-supplied
    `filtered_for_headroom` sink so the operator can see what was
    filtered without blocking the run.
  - settled gate: settled=True rows are skipped (no value mirroring an
    already-finalized market).
  - parseable date gate: rows missing or with malformed resolution_date
    are dropped. Without a verifiable date we cannot prove the deadline
    or past-cutoff gates, and creating an out-of-window Prophet market
    costs gas and earns nothing.

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
# Phase 15 (#505): bumped from 100 to 500 so the discovery pass has
# enough headroom to find qualifiers inside Prophet's tight
# [2026-05-11, 2026-05-24] resolution window.
_DEFAULT_LIMIT = 500


@dataclass
class PolymarketSource:
    """Normalized Polymarket source row for the candidate generator.

    Issue #520: a Polymarket child market like
    ``"Map 3: Odd/Even Total Rounds?"`` carries zero context — teams,
    tournament, and date all live under ``events[0].title`` /
    ``groupItemTitle`` / ``slug``. Prophet's ``Validate Question`` step
    rejects ambiguous strings, so discovery must enrich the question
    here rather than ship the bare child predicate downstream.

    Round-trip contract: ``polymarket_market_id`` is the on-chain CTF
    ``conditionId`` — the durable settlement key the bounty reconciler
    matches against. It is **not** Gamma's lookup key. Gamma's
    ``/markets/<id>`` and ``?id=<id>`` endpoints 422 on conditionId; the
    only round-trippable filter is ``?condition_ids=<conditionId>``
    (plural) — see ``prophet-arb-bot/scripts/polymarket/prices.py`` for
    the live-probed shape. ``polymarket_gamma_id`` is Gamma's integer
    market id, exposed here for any future code that does a by-id
    Gamma lookup.
    """

    polymarket_market_id: str
    question: str
    resolution_date: datetime
    category: str | None
    settled: bool
    polymarket_gamma_id: str | None = None
    slug: str | None = None
    event_title: str | None = None
    event_slug: str | None = None
    group_item_title: str | None = None

    @property
    def display_question(self) -> str:
        """Prophet-ready question with parent-series context.

        Prefers ``events[0].title + ": " + (groupItemTitle or question)``
        — the shape Polymarket itself uses to render the child market in
        its UI. Falls back to the bare ``question`` when no event context
        is present (e.g. standalone single-market events).
        """
        leaf = self.group_item_title or self.question
        if self.event_title:
            return f"{self.event_title}: {leaf}"
        return self.question


def discover_polymarket_sources(
    *,
    gateway: Any,
    deadline: datetime,
    now: datetime | None = None,
    minimum_headroom_seconds: int = 0,
    filtered_for_headroom: list[dict] | None = None,
) -> list[PolymarketSource]:
    """Fetch settling-markets feed and return the in-window subset.

    `now` is injected so callers (and tests) control the past-cutoff
    instant deterministically. Production callers pass
    `datetime.now(timezone.utc)`; tests pin a fixed value so the
    generated URL is stable enough to stub.

    `minimum_headroom_seconds` (issue #522): when > 0, drop rows whose
    `resolution_date - now` is less than this threshold. Default 0 is
    a no-op so existing callers stay backward compatible. The
    operator-facing default is set by `_cmd_run` via the
    `minimum_ui_headroom_seconds` input (600s).

    `filtered_for_headroom`: when provided, the function appends one
    dict per dropped row in the shape `{polymarket_market_id,
    headroom_seconds, resolution_date_iso}` so the run envelope can
    surface what was filtered without blocking.
    """
    deadline_utc = _ensure_utc(deadline)
    now_utc = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
    deadline_iso = deadline_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    # `order=endDate&ascending=true` makes the 500-row budget surface
    # the soonest-resolving in-window markets first instead of being
    # burned on far-future rows that we would filter out anyway.
    path = (
        f"{_BASE_PATH}?end_date_min={now_iso}"
        f"&end_date_max={deadline_iso}"
        f"&closed=false&active=true&order=endDate&ascending=true"
        f"&limit={_DEFAULT_LIMIT}"
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
        # Belt-and-suspenders: even though we ask Gamma for
        # end_date_min=now, drop anything that slips through. UMA-stuck
        # markets have surfaced live (see module docstring).
        if resolution_date <= now_utc:
            continue
        # Issue #522: execution-headroom gate. Phase-15 UI submission
        # cannot clear a candidate resolving inside `Validate +
        # odds_calc + bet + Privy signing` (~90-180s realistically), so
        # dropping these here keeps `pending_ui_submission` actionable.
        # `minimum_headroom_seconds=0` is a no-op (backward compat with
        # callers that haven't opted in).
        if minimum_headroom_seconds > 0:
            headroom = (resolution_date - now_utc).total_seconds()
            if headroom < minimum_headroom_seconds:
                if filtered_for_headroom is not None:
                    candidate_id = (
                        raw.get("conditionId") or raw.get("polymarket_market_id") or ""
                    )
                    # Preserve the publisher's raw ISO string so the
                    # envelope echoes back exactly what Gamma reported.
                    raw_iso = (
                        raw.get("endDate")
                        or raw.get("endDateIso")
                        or raw.get("resolution_date")
                        or ""
                    )
                    filtered_for_headroom.append(
                        {
                            "polymarket_market_id": candidate_id,
                            "headroom_seconds": int(headroom),
                            "resolution_date_iso": raw_iso,
                        }
                    )
                continue
        # Issue #520: `polymarket_market_id` is the CTF conditionId — the
        # durable settlement key — NOT Gamma's `id`. The two are distinct
        # and must not be conflated; see the PolymarketSource docstring.
        market_id = raw.get("conditionId") or raw.get("polymarket_market_id")
        question = raw.get("question")
        if not isinstance(market_id, str) or not market_id:
            continue
        if not isinstance(question, str) or not question:
            continue
        category = _extract_category(raw)
        gamma_id_raw = raw.get("id")
        gamma_id = str(gamma_id_raw) if gamma_id_raw not in (None, "") else None
        event_title, event_slug = _extract_event_fields(raw)
        keepers.append(
            PolymarketSource(
                polymarket_market_id=market_id,
                question=question,
                resolution_date=resolution_date,
                category=category,
                settled=False,
                polymarket_gamma_id=gamma_id,
                slug=_safe_str(raw.get("slug")),
                event_title=event_title,
                event_slug=event_slug,
                group_item_title=_safe_str(raw.get("groupItemTitle")),
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


def _extract_event_fields(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(events[0].title, events[0].slug)`` if present.

    Issue #520: the parent series context Prophet's ``Validate Question``
    needs lives here, not on the child market row.
    """
    events = raw.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict):
            return _safe_str(first.get("title")), _safe_str(first.get("slug"))
    return None, None


def _safe_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


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
