"""Auto-discover orchestrator (#538).

Stitches three pure-ish steps into one call the arb-bot's `cmd_run`
invokes when ``auto_discover.enabled = true``:

  1. **Fetch live Polymarket candidates** via
     ``polymarket.discovery.discover_arb_candidates`` (active markets,
     24h vol ≥ floor, resolves within campaign window + headroom).
  2. **Dedup against existing ``arb_pairs``** so we don't double-queue
     markets the operator has already paired or driven through the UI.
  3. **Look up matching Prophet markets** via
     ``find_matching_prophet_markets``. Matched candidates are UPSERTed
     into ``arb_pairs`` (``source_skill="auto_discover"``) so the
     existing scoring loop picks them up immediately. Unmatched
     candidates become ``pending_ui_submission`` entries the agent
     drives through Prophet's `/create` UI — the same envelope shape
     the bounty-runner emits, so the agent's runbook is reusable
     verbatim.

The orchestrator is intentionally tolerant of partial Prophet outages
— if ``markets_for_dedup`` fails, we treat every candidate as
pending-creation rather than blocking the cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db import ResolvedTarget
from persistence import list_arb_pairs, upsert_arb_pair
from polymarket.discovery import PolymarketSource, discover_arb_candidates

from .candidate_sheet import write_candidate_sheet
from .prophet_pair_lookup import find_matching_prophet_markets


SOURCE_SKILL = "prophet-arb-bot"
SKILL_SLUG = "prophet-arb-bot"
DEFAULT_INITIAL_BET_USDC = 1.0


@dataclass
class AutoDiscoverConfig:
    """Runtime knobs for auto-discover. Defaults align with the
    May 2026 Prophet campaign (24-market shortlist)."""

    enabled: bool = False
    min_24h_volume_usd: float = 10_000.0
    min_headroom_hours: float = 24.0
    resolution_deadline_iso: str = "2026-05-24T23:59:59Z"
    max_candidates: int = 50
    initial_bet_usdc: float = DEFAULT_INITIAL_BET_USDC

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "AutoDiscoverConfig":
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            min_24h_volume_usd=float(raw.get("min_24h_volume_usd", 10_000.0)),
            min_headroom_hours=float(raw.get("min_headroom_hours", 24.0)),
            resolution_deadline_iso=str(
                raw.get("resolution_deadline_iso") or "2026-05-24T23:59:59Z"
            ),
            max_candidates=int(raw.get("max_candidates", 50)),
            initial_bet_usdc=float(raw.get("initial_bet_usdc", DEFAULT_INITIAL_BET_USDC)),
        )


@dataclass
class AutoDiscoverResult:
    candidates_found: int
    already_paired: int
    auto_paired: list[dict] = field(default_factory=list)
    pending_ui_submission: list[dict] = field(default_factory=list)
    sheet_path: str | None = None
    prophet_lookup_failed: bool = False
    # #611: when prophet_lookup_failed is True, this carries
    # "<ExceptionClass>: <message>" so the operator can triage instead
    # of staring at a bare boolean.
    prophet_failure_detail: str | None = None


def _parse_iso(value: str) -> datetime:
    s = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _category_to_slug(category: str | None) -> str:
    """Mirror the bounty-runner's category→slug normalization so the
    agent's UI runbook treats both skills' envelopes interchangeably."""
    if not category:
        return "other"
    text = re.sub(r"[^\w]+", "-", str(category).lower()).strip("-")
    return text or "other"


def _build_pending_entry(
    *,
    cand: PolymarketSource,
    initial_bet_usdc: float,
    viewer_id: str = "",
) -> dict:
    """One ``pending_ui_submission`` entry. Field shape matches the
    bounty-runner's exactly (audited 2026-05-14) so the agent's existing
    Playwright runbook handles both skills without branching."""
    return {
        "polymarket_market_id": cand.polymarket_market_id,
        "question": cand.question,
        "category": cand.category or "Other",
        "category_slug": _category_to_slug(cand.category),
        "resolution_date_iso": cand.resolution_date.isoformat(),
        "initial_bet_usdc": float(initial_bet_usdc),
        "bounty_id": "",
        "prophet_viewer_id": viewer_id,
        "source_skill": SKILL_SLUG,
    }


def run_auto_discover(
    *,
    gateway: Any,
    prophet_client: Any,
    jwt: str | None,
    target: ResolvedTarget | None,
    config: AutoDiscoverConfig,
    viewer_id: str = "",
    sheet_output_dir: Path | None = None,
    now: datetime | None = None,
) -> AutoDiscoverResult:
    """Run the full auto-discover cycle. Pure orchestration — no
    placeOrder, no signing, no Playwright. Caller owns the side
    effects after this function returns.
    """
    deadline = _parse_iso(config.resolution_deadline_iso)
    candidates = discover_arb_candidates(
        gateway=gateway,
        deadline=deadline,
        min_24h_volume_usd=config.min_24h_volume_usd,
        minimum_headroom_seconds=int(config.min_headroom_hours * 3600),
        max_candidates=config.max_candidates,
        now=now,
    )

    existing_pairs: set[str] = set()
    if target is not None:
        try:
            for pair in list_arb_pairs(target=target):
                pid = pair.get("polymarket_condition_id")
                if pid:
                    existing_pairs.add(pid)
        except Exception:
            # First-run / schema-not-applied tolerated. Caller has
            # already failed-closed on schema if persistence is broken.
            existing_pairs = set()

    new_candidates = [
        c for c in candidates if c.polymarket_market_id not in existing_pairs
    ]

    prophet_matches: dict[str, str] = {}
    prophet_failed = False
    prophet_failure_detail: str | None = None
    if new_candidates:
        try:
            prophet_matches = find_matching_prophet_markets(
                prophet_client=prophet_client,
                jwt=jwt,
                candidate_questions={
                    c.polymarket_market_id: c.question for c in new_candidates
                },
            )
        except Exception as exc:
            prophet_failed = True
            prophet_failure_detail = f"{type(exc).__name__}: {exc}"
            prophet_matches = {}

    auto_paired: list[dict] = []
    pending_ui: list[dict] = []
    for cand in new_candidates:
        prophet_market_id = prophet_matches.get(cand.polymarket_market_id)
        if prophet_market_id and target is not None:
            try:
                upsert_arb_pair(
                    target=target,
                    prophet_market_id=prophet_market_id,
                    polymarket_condition_id=cand.polymarket_market_id,
                    source_skill="auto_discover",
                )
            except Exception:
                # Persistence flake — surface in pending_ui so the
                # operator sees the unmatched state, but don't bomb.
                pending_ui.append(
                    _build_pending_entry(
                        cand=cand,
                        initial_bet_usdc=config.initial_bet_usdc,
                        viewer_id=viewer_id,
                    )
                )
                continue
            auto_paired.append(
                {
                    "polymarket_condition_id": cand.polymarket_market_id,
                    "prophet_market_id": prophet_market_id,
                    "question": cand.question,
                    "volume_24h_usd": cand.volume_24h_usd,
                }
            )
        else:
            pending_ui.append(
                _build_pending_entry(
                    cand=cand,
                    initial_bet_usdc=config.initial_bet_usdc,
                    viewer_id=viewer_id,
                )
            )

    sheet_path: str | None = None
    try:
        path = write_candidate_sheet(
            candidates=candidates,
            auto_paired_ids={p["polymarket_condition_id"] for p in auto_paired},
            already_paired_ids=existing_pairs,
            pending_ids={p["polymarket_market_id"] for p in pending_ui},
            output_dir=sheet_output_dir,
        )
        sheet_path = str(path) if path else None
    except Exception:
        sheet_path = None

    return AutoDiscoverResult(
        candidates_found=len(candidates),
        already_paired=len(candidates) - len(new_candidates),
        auto_paired=auto_paired,
        pending_ui_submission=pending_ui,
        sheet_path=sheet_path,
        prophet_lookup_failed=prophet_failed,
        prophet_failure_detail=prophet_failure_detail,
    )
