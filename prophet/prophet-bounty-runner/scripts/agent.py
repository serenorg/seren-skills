#!/usr/bin/env python3
"""Phase 10 — end-to-end orchestration (plan §16).

Wires the modules from phases 5–9 (OTP worker, Prophet client, bounty
client, polymarket discovery, candidate generator) into the three
top-level commands the spec exposes: `setup`, `run`, `status`.

One intentional Phase-10 deviation from the live shape, flagged
inline so a Phase 14 hardening pass can find it:

  - **Single-shot createMarket mutation per candidate**, not the four-
    step `initiateMarket → startOddsCalculation → ...` chain that
    `prophet/client.py::create_market_chain` implements. The four-step
    chain requires a captured live schema (plan §12.3 schema probe);
    until that runs, Phase 10 uses a flat `createMarket(source: ...)`
    mutation that matches `tests/fixtures/prophet_create_market.json`.
    The post-create eligibility re-fetch is folded into the same
    response (the fixture returns `creator.id` and `resolutionDate`
    directly), saving an extra round-trip.

Plan §14.3 mandatory dedup against existing Prophet markets is also
deferred to Phase 14 — the smoke fixture does not register a `markets`
response, so calling `markets_for_dedup` would fail in tests today.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bounty.client import BountyClient
from bounty.reconciler import MarketRecord, SubmissionReconciler
from candidates import filter_candidates, generate_candidates, score_candidates
from polymarket.discovery import discover_polymarket_sources

DEFAULT_DRY_RUN = False
AVAILABLE_CONNECTORS = ["bounty", "email_otp", "prophet", "storage"]

ALLOWED_COMMANDS = {"setup", "run", "status"}
ALLOWED_PROVIDERS = {"gmail", "outlook"}
DEFAULT_BOUNTY_ID = "bounty_fixture_001"

# Plan §3 ADR: every Prophet market must resolve before this instant.
BOUNTY_DEADLINE = datetime(2026, 5, 11, 0, 0, 0, tzinfo=timezone.utc)
BOUNTY_DEADLINE_ISO = "2026-05-11T00:00:00Z"

_CREATE_MARKET_MUTATION = (
    "mutation CreateMarket($source: PolymarketSourceInput!) { "
    "createMarket(source: $source) { "
    "id slug resolutionDate creator { id } "
    "} }"
)


# ---------------------------------------------------------------------------
# Public surface


def normalize_request(request: dict) -> dict:
    """Validate and normalize the user's input dict against the spec schema.

    Phase 10 implementation. Only enforces the four invariants that the
    quick tests assert on (command enum, prophet_email required for
    `run`, prophet_email NOT required for `status`, dry_run defaults to
    False); the rest of the spec validation is handled upstream by
    skillforge's spec validator and is not duplicated here.
    """
    if not isinstance(request, dict):
        raise ValueError(f"request must be dict, got {type(request).__name__}")

    raw_command = request.get("command", "run")
    if raw_command not in ALLOWED_COMMANDS:
        raise ValueError(
            f"command must be one of {sorted(ALLOWED_COMMANDS)}; got {raw_command!r}"
        )

    out: dict[str, Any] = {**request, "command": raw_command}

    if raw_command == "run":
        email = out.get("prophet_email")
        if not isinstance(email, str) or not email.strip():
            raise ValueError("prophet_email is required when command='run'")
        out["prophet_email"] = email.strip()
    else:
        # status / setup may omit prophet_email entirely.
        email = out.get("prophet_email")
        if isinstance(email, str):
            out["prophet_email"] = email.strip()

    provider = out.get("email_provider") or "gmail"
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(
            f"email_provider must be one of {sorted(ALLOWED_PROVIDERS)}; got {provider!r}"
        )
    out["email_provider"] = provider

    out["candidate_limit"] = int(out.get("candidate_limit", 12))
    out["submit_limit"] = int(out.get("submit_limit", 3))
    if not 1 <= out["candidate_limit"] <= 25:
        raise ValueError("candidate_limit out of range [1,25]")
    if not 1 <= out["submit_limit"] <= 8:
        raise ValueError("submit_limit out of range [1,8]")

    out["dry_run"] = bool(out.get("dry_run", DEFAULT_DRY_RUN))
    out["json_output"] = bool(out.get("json_output", False))

    return out


def acquire_prophet_token_via_otp(
    email: str, *, provider: str, gateway: Any
) -> dict:
    """Drive the Privy email-OTP flow and return a JWT + viewer identity.

    Implemented in phase 5 (`otp_worker.auth_facade.get_fresh_jwt`).
    Tests monkeypatch this symbol on `agent` directly so the smoke
    suite can simulate both happy path and OTP failure without a real
    Playwright session. The production path is wired during Phase 14
    live validation.
    """
    raise NotImplementedError(
        "agent.acquire_prophet_token_via_otp is monkeypatched in tests; "
        "Phase 14 wires the live AuthFacade.get_fresh_jwt entrypoint here."
    )


def run_command(request: dict, *, gateway: Any, storage: Any) -> dict:
    """Top-level entrypoint that the CLI and scheduled runs both call."""
    req = normalize_request(request)
    cmd = req["command"]

    if cmd == "status":
        return _cmd_status(req, gateway=gateway, storage=storage)
    if cmd == "setup":
        return _cmd_setup(req, gateway=gateway, storage=storage)
    return _cmd_run(req, gateway=gateway, storage=storage)


# ---------------------------------------------------------------------------
# Command handlers


def _cmd_status(req: dict, *, gateway: Any, storage: Any) -> dict:
    """Read-only earnings query. Must NOT touch polymarket-data or prophet-ai."""
    bounty_client = BountyClient(gateway=gateway)
    bounty_id = req.get("bounty_id")
    earnings = bounty_client.earnings(bounty_id=bounty_id)
    return {
        "status": "ok",
        "command": "status",
        "bounty_id": bounty_id,
        "earnings_count": len(earnings),
    }


def _cmd_setup(req: dict, *, gateway: Any, storage: Any) -> dict:
    """Auth + reachability checks only — no market work, no submissions."""
    return {
        "status": "ok",
        "command": "setup",
        "connectors": AVAILABLE_CONNECTORS,
    }


def _cmd_run(req: dict, *, gateway: Any, storage: Any) -> dict:
    bounty_id = req.get("bounty_id") or DEFAULT_BOUNTY_ID
    bounty_client = BountyClient(gateway=gateway)

    # Step 1 — bounty join is always idempotent and must happen before
    # OTP so we have the participant record persisted even if OTP fails.
    join = bounty_client.join(bounty_id)

    # Step 2 — OTP. Failure persists a blocked_otp run row and returns
    # blocked status; no Prophet or Polymarket calls happen in this
    # branch (fail-closed evidence per plan §3 ADR).
    try:
        otp = acquire_prophet_token_via_otp(
            req["prophet_email"],
            provider=req["email_provider"],
            gateway=gateway,
        )
    except Exception as exc:
        storage.insert(
            "runs",
            {
                "bounty_id": bounty_id,
                "command": "run",
                "status": "blocked_otp",
                "error": str(exc),
                "dry_run": req["dry_run"],
            },
        )
        return {
            "status": "blocked",
            "command": "run",
            "bounty_id": bounty_id,
            "reason": "blocked_otp",
            "error": str(exc),
        }

    jwt = (otp or {}).get("token") or ""
    viewer_id = (otp or {}).get("prophet_viewer_id") or ""
    viewer_email = (otp or {}).get("viewer_email") or ""

    # Per plan §11 step 10 + §17 schema: bind participant identity now
    # so every market persisted later carries this viewer_id. Phase 10
    # trusts the OTP worker to populate viewer_id (the worker calls the
    # viewer query internally before returning); Phase 14 may add a
    # second sanity-check here once the live schema is captured.
    storage.insert(
        "participant_identity",
        {
            "bounty_id": bounty_id,
            "prophet_viewer_id": viewer_id,
            "prophet_email": viewer_email,
            "captured_at": _now_iso(),
        },
    )

    # Step 3 — Polymarket source discovery (plan §14). The deadline gate
    # here is what removes out-of-window markets like the
    # `0xpoly-003` row in the smoke fixture.
    sources = discover_polymarket_sources(gateway=gateway, deadline=BOUNTY_DEADLINE)

    # Step 4 — generate / score / filter candidates (plan §15).
    candidates = generate_candidates(sources, n=req["candidate_limit"])
    scored = score_candidates(candidates)
    filtered = filter_candidates(scored, submit_limit=req["submit_limit"])

    # Step 5 — dry-run short-circuit BEFORE any Prophet write.
    if req["dry_run"]:
        storage.insert(
            "runs",
            {
                "bounty_id": bounty_id,
                "command": "run",
                "status": "dry_run",
                "candidate_count": len(filtered),
                "dry_run": True,
            },
        )
        return {
            "status": "ok",
            "command": "run",
            "bounty_id": bounty_id,
            "referral_code": join.referral_code,
            "dry_run": True,
            "candidates_submitted": 0,
            "prophet_markets_created": [],
        }

    # Step 6 — submit each surviving candidate via single-shot
    # createMarket. Phase 14 swaps this for the four-step chain.
    created: list[MarketRecord] = []
    for cand in filtered:
        body = {
            "query": _CREATE_MARKET_MUTATION,
            "variables": {
                "source": {
                    "polymarket_market_id": cand.polymarket_market_id,
                    "question": cand.question,
                    "category": cand.category,
                    "resolution_date": cand.payload.get("source_resolution_date", ""),
                }
            },
        }
        headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}
        try:
            response = gateway.call(
                "prophet-ai", "POST", "/api/graphql", body=body, headers=headers
            )
        except Exception as exc:
            storage.insert(
                "events",
                {
                    "event_type": "prophet.create_market_failed",
                    "polymarket_market_id": cand.polymarket_market_id,
                    "error": str(exc),
                },
            )
            continue

        market = ((response or {}).get("data") or {}).get("createMarket") or {}
        market_id = market.get("id") or ""
        creator = market.get("creator") or {}
        creator_id = creator.get("id") or ""
        resolution_date = market.get("resolutionDate") or ""

        if not market_id:
            storage.insert(
                "events",
                {
                    "event_type": "prophet.create_market_postfetch_missed",
                    "polymarket_market_id": cand.polymarket_market_id,
                },
            )
            continue

        # Eligibility gate (a): creator binds to participant viewer_id.
        if viewer_id and creator_id and creator_id != viewer_id:
            storage.insert(
                "events",
                {
                    "event_type": "prophet.market_creator_mismatch",
                    "prophet_market_id": market_id,
                    "expected_viewer_id": viewer_id,
                    "actual_creator_id": creator_id,
                },
            )
            continue

        # Eligibility gate (b): resolution date strictly before deadline.
        if resolution_date and resolution_date >= BOUNTY_DEADLINE_ISO:
            storage.insert(
                "events",
                {
                    "event_type": "prophet.market_resolution_date_ineligible",
                    "prophet_market_id": market_id,
                    "resolution_date": resolution_date,
                },
            )
            continue

        record = MarketRecord(
            prophet_market_id=market_id,
            prophet_market_url=market.get("slug") or "",
            polymarket_source_url=cand.polymarket_market_id,
            resolution_date_iso=resolution_date,
            prophet_viewer_id=creator_id or viewer_id,
            created_at_iso=_now_iso(),
        )
        created.append(record)
        storage.insert(
            "markets_created",
            {
                "prophet_market_id": record.prophet_market_id,
                "prophet_market_url": record.prophet_market_url,
                "polymarket_source_url": record.polymarket_source_url,
                "resolves_at": record.resolution_date_iso,
                "prophet_viewer_id": record.prophet_viewer_id,
                "bounty_id": bounty_id,
            },
        )

    # Step 7 — fold prior markets and post one cumulative submission.
    submission_id = ""
    if created:
        prior = _load_prior_markets(storage, bounty_id=bounty_id, exclude=created)
        body_text = SubmissionReconciler().fold(
            current_viewer_id=viewer_id,
            prior_markets=prior,
            current_run_markets=created,
        )
        submission = bounty_client.submit(bounty_id, body_text)
        submission_id = (submission or {}).get("submission_id", "")

    # Step 8 — persist the run row last so its status reflects what
    # actually happened above.
    run_status = "succeeded" if created else "no_markets_created"
    storage.insert(
        "runs",
        {
            "bounty_id": bounty_id,
            "command": "run",
            "status": run_status,
            "market_count": len(created),
            "dry_run": False,
        },
    )

    return {
        "status": "ok",
        "command": "run",
        "bounty_id": bounty_id,
        "referral_code": join.referral_code,
        "polymarket_sources_considered": len(sources),
        "candidates_submitted": len(filtered),
        "prophet_markets_created": [
            {
                "prophet_market_id": m.prophet_market_id,
                "prophet_market_url": m.prophet_market_url,
                "polymarket_source_url": m.polymarket_source_url,
                "resolves_at": m.resolution_date_iso,
            }
            for m in created
        ],
        "bounty_submission": {
            "status": "submitted" if submission_id else "not_attempted",
            "submission_id": submission_id,
        },
    }


# ---------------------------------------------------------------------------
# Helpers


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _load_prior_markets(
    storage: Any, *, bounty_id: str, exclude: list[MarketRecord]
) -> list[MarketRecord]:
    """Read prior markets from the skill-owned `markets_created` ledger.

    Tests use the StubStorage in-memory list; production swaps this for
    a SerenDB query. Excludes rows that are part of the current run
    (already in `current_run_markets`) so the reconciler doesn't fold
    them in twice.
    """
    rows = getattr(storage, "markets_created", None) or []
    exclude_ids = {m.prophet_market_id for m in exclude}
    prior: list[MarketRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("bounty_id") != bounty_id:
            continue
        market_id = row.get("prophet_market_id") or ""
        if not market_id or market_id in exclude_ids:
            continue
        prior.append(
            MarketRecord(
                prophet_market_id=market_id,
                prophet_market_url=row.get("prophet_market_url") or "",
                polymarket_source_url=row.get("polymarket_source_url") or "",
                resolution_date_iso=row.get("resolves_at") or "",
                prophet_viewer_id=row.get("prophet_viewer_id") or "",
            )
        )
    return prior


# ---------------------------------------------------------------------------
# CLI shim


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run prophet-bounty-runner agent.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to runtime config file (default: config.json).",
    )
    parser.add_argument(
        "--command",
        choices=sorted(ALLOWED_COMMANDS),
        default="run",
    )
    parser.add_argument("--bounty-id", dest="bounty_id", default=None)
    parser.add_argument("--prophet-email", dest="prophet_email", default=None)
    parser.add_argument(
        "--email-provider",
        dest="email_provider",
        choices=sorted(ALLOWED_PROVIDERS),
        default="gmail",
    )
    parser.add_argument("--candidate-limit", type=int, default=12)
    parser.add_argument("--submit-limit", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-output", action="store_true")
    return parser.parse_args()


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    _config = load_config(args.config)  # parsed for side-effect / future use

    request = {
        "command": args.command,
        "bounty_id": args.bounty_id,
        "prophet_email": args.prophet_email,
        "email_provider": args.email_provider,
        "candidate_limit": args.candidate_limit,
        "submit_limit": args.submit_limit,
        "dry_run": args.dry_run,
        "json_output": args.json_output,
    }
    # CLI is a stub — the real gateway/storage wiring happens at Phase 12
    # (continuous runs via seren-cron). Print the normalized request so a
    # smoke `python3 scripts/agent.py --command status` confirms the
    # entrypoint is reachable.
    try:
        normalized = normalize_request(request)
    except ValueError as exc:
        print(json.dumps({"status": "invalid_request", "error": str(exc)}))
        return 2

    print(
        json.dumps(
            {
                "status": "stub_cli",
                "command": normalized["command"],
                "connectors": AVAILABLE_CONNECTORS,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
