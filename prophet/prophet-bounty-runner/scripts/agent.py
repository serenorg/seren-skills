#!/usr/bin/env python3
"""Phase 10 + Phase 14a + Phase 14b — end-to-end orchestration (plan §16).

Wires the modules from phases 5–9 (OTP worker, Prophet client, bounty
client, polymarket discovery, candidate generator) into the three
top-level commands the spec exposes: `setup`, `run`, `status`.

Issue #505 (Phase 14a) — the obsolete single-shot
`createMarket(source: PolymarketSourceInput!)` mutation that Prophet
retired on mainnet is gone. The submission loop now drives
`MinimalProphetClient.create_market_chain(...)`:

    initiateMarket
      → startOddsCalculation
      → oddsCalculationSession (poll)
      → marketCreationOrderParams
      → createMarketWithBet

The chain's wiring and fail-closed UX landed in Phase 14a: any chain
failure surfaces into the run envelope's `blockers[]` array and
downgrades `status` to `blocked` when zero markets were created, so
the cron's auto-pause path sees the break instead of silently burning
ticks.

Issue #505 (Phase 14b) — captured the live Prophet GraphQL schema to
`tests/fixtures/prophet_schema.json` (2026-05-13, 126 types). Two
concrete changes landed against the fixture:

  - **Plan §14.3 dedup pre-filter.** Before the chain submission loop
    fires, `_cmd_run` calls `markets_for_dedup` and drops every
    candidate whose `question` is already listed on Prophet (case-
    insensitive exact match). If Prophet's markets query is
    unreachable the run fails closed with
    `reason=prophet_dedup_unavailable` — fail-open here would risk
    duplicate creations that reconciliation refuses to credit.
  - **`markets_for_dedup` query shape pinned** to the captured
    `MarketsInput.filter` shape (`MarketFilter.status`,
    `.resolvingBefore`). The legacy `{limit, status}` flat shape was
    schema-drift from a pre-Phase-14 placeholder.

Phase 15 (#505): the live audit (2026-05-13) showed Prophet uses two
asymmetric signing models. `placeOrder` is **server-signed** — Prophet
signs CTF orders on behalf of the user via Privy session signers; the
arb-bot calls it with the JWT alone. `createMarketWithBet` is
**client-signed** — Prophet's UI invokes the in-browser Privy SDK to
sign the `OrderParams` typed-data the user can see and confirm. There
is no agent-accessible API to drive that signing prompt headlessly.

The bounty-runner therefore stops at `marketCreationOrderParams` and
emits a `pending_ui_submission` envelope. The agent (running in Seren
Desktop with `mcp__playwright__*` tools) drives the Prophet web UI to
finalize each market: navigate to `/create`, fill the question, await
odds calc, fill the bet amount, accept the in-browser Privy signing
prompt. See SKILL.md → Agent-driven UI submission runbook.

The arb-bot's `placeOrder` / `cancelOrder` / `userOrders` shapes are
live-validated against the captured fixture (#505 Phase 15 live test)
and need no client-side signing; they live in
`prophet-arb-bot/scripts/prophet/orders.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bounty.client import BountyClient
from bounty.reconciler import MarketRecord  # SubmissionReconciler.fold moves to record_created_market in a follow-on PR
from candidates import filter_candidates, generate_candidates, score_candidates
from expected_bounty_spec import CUSTOMER_SLUG as EXPECTED_CUSTOMER_SLUG
from expected_bounty_spec import validate_bounty as _validate_bounty_spec
from polymarket.discovery import discover_polymarket_sources
from prophet import (
    ProphetGraphQLError,
    ProphetSchemaError,
    ProphetUnauthorized,
)
from prophet.client import MinimalProphetClient

DEFAULT_DRY_RUN = False
AVAILABLE_CONNECTORS = ["bounty", "email_otp", "prophet", "storage"]

ALLOWED_COMMANDS = {"setup", "run", "status"}
ALLOWED_PROVIDERS = {"gmail", "outlook"}
DEFAULT_BOUNTY_ID = "bounty_fixture_001"

# Plan §3 ADR: every Prophet market must resolve before this instant.
# Phase 15 (#505): tightened from 2026-05-26 to 2026-05-24 because
# Prophet's `/create` UI enforces `[2026-05-11, 2026-05-24]` as the
# early-access resolution window. Submitting a market that resolves
# 2026-05-24 .. 2026-05-25 will be rejected by `initiateMarket` with a
# user-visible "Please try a different question" error.
BOUNTY_DEADLINE = datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
BOUNTY_DEADLINE_ISO = "2026-05-24T00:00:00Z"


def _get_now() -> datetime:
    """Return the UTC instant Polymarket discovery should treat as "now".

    Indirected through a module-level function so tests can pin it via
    monkeypatch. Without the pin, the `end_date_min` URL parameter the
    discovery module sends would drift every minute and break the
    StubGateway exact-path match.
    """
    return datetime.now(timezone.utc)


# Phase-14a chain defaults (#505). The chain input shape is still
# best-guess until the live schema probe lands; these constants are
# the values the agent passes when the candidate doesn't carry an
# explicit override. `INITIAL_BET_USDC` is the seed bet attached to
# createMarketWithBet — Prophet requires a non-zero opening bet to
# create a market.
DEFAULT_TOPIC_SLUG = "general"
DEFAULT_INITIAL_BET_USDC = 1


def _category_to_slug(category: str) -> str:
    """Best-effort categorySlug derived from polymarket category.

    The exact slug vocabulary Prophet accepts is not yet introspected
    (#505 follow-up). For now we normalize to a kebab-case lowercase
    token and let the chain surface a `ProphetSchemaError` /
    `ProphetGraphQLError` if Prophet rejects it. That error rides the
    fail-closed UX path documented in `_cmd_run`.
    """
    slug = (category or "").strip().lower().replace(" ", "-")
    return slug or "general"


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

    # Phase 15 (#505): candidate_limit bumped to 500 so the discovery
    # pass samples enough Polymarket markets to find qualifiers in the
    # tight [2026-05-11, 2026-05-24] resolution window. submit_limit
    # stays small — it caps how many of those candidates we hand off to
    # the agent for UI submission per run.
    out["candidate_limit"] = int(out.get("candidate_limit", 500))
    out["submit_limit"] = int(out.get("submit_limit", 3))
    if not 1 <= out["candidate_limit"] <= 500:
        raise ValueError("candidate_limit out of range [1,500]")
    if not 1 <= out["submit_limit"] <= 8:
        raise ValueError("submit_limit out of range [1,8]")

    out["dry_run"] = bool(out.get("dry_run", DEFAULT_DRY_RUN))
    out["json_output"] = bool(out.get("json_output", False))

    return out


class MissingSessionToken(Exception):
    """`PROPHET_SESSION_TOKEN` env var is unset.

    Issue #487: browser automation is the agent's responsibility, not
    the subprocess's. The agent must drive Privy via Seren Desktop's
    Playwright MCP, capture the JWT from `localStorage["privy:token"]`,
    and inject it as `PROPHET_SESSION_TOKEN` before shelling out to
    `agent.py`. See SKILL.md for the runbook.
    """


def acquire_prophet_token_via_otp(
    email: str,
    *,
    provider: str,
    gateway: Any,
    bounty_id: str = "",
    seren_user_id: str = "",
    headless: bool = True,
    require_viewer_binding: bool = True,
    transport: Any = None,
) -> dict:
    """Read the JWT supplied by the agent and bind it to a viewer_id.

    Issue #487: the Python subprocess no longer drives a browser. The
    agent (running in Seren Desktop with `mcp__playwright__*` tools)
    walks Privy email-OTP, captures the JWT from
    `localStorage["privy:token"]`, and exports it as
    `PROPHET_SESSION_TOKEN` before invoking this script.

    Issue #493: viewer-bind now uses `ProphetDirectTransport` (direct to
    `app.prophetmarket.ai`) instead of the broken `prophet-ai` Seren
    publisher hop. The `transport` kwarg is injected by `_cmd_run`;
    tests pass a `StubProphetTransport`.

    Missing env var → `MissingSessionToken`. Viewer-bind failure →
    `PrivyAuthFailed`. Both surface as `status=blocked` with distinct
    reasons in `_cmd_run`.

    `provider`, `bounty_id`, `seren_user_id`, and `headless` are kept
    in the signature for backward-compat with existing test
    monkeypatches; they are otherwise unused. `gateway` is kept for
    the same reason but Prophet calls now route through `transport`.
    """
    from otp_worker import PrivyAuthFailed
    from otp_worker.token_acquirer import _query_viewer
    from prophet.transport import ProphetDirectTransport

    del provider, bounty_id, seren_user_id, headless, gateway  # see docstring

    jwt = (os.environ.get("PROPHET_SESSION_TOKEN") or "").strip()
    if not jwt:
        raise MissingSessionToken(
            "PROPHET_SESSION_TOKEN is required. The agent must drive Privy "
            "OTP via Seren Desktop's Playwright MCP, capture the JWT from "
            "localStorage[\"privy:token\"], and export it before calling "
            "agent.py. See SKILL.md."
        )

    if transport is None:
        transport = ProphetDirectTransport()
    try:
        viewer_id, viewer_email = _query_viewer(transport=transport, jwt=jwt)
    except Exception as exc:
        if require_viewer_binding:
            raise PrivyAuthFailed(
                f"PROPHET_SESSION_TOKEN viewer bind failed: {exc}"
            ) from exc
        return {
            "token": jwt,
            "prophet_viewer_id": "",
            "viewer_email": email,
            "source": "env_token_unbound",
            "binding_error": str(exc),
        }

    if (
        require_viewer_binding
        and viewer_email
        and email
        and viewer_email.casefold() != email.casefold()
    ):
        raise PrivyAuthFailed(
            f"PROPHET_SESSION_TOKEN viewer.email {viewer_email!r} does "
            f"not match prophet_email {email!r}"
        )
    return {
        "token": jwt,
        "prophet_viewer_id": viewer_id,
        "viewer_email": viewer_email or email,
        "source": "env_token",
    }


def run_command(
    request: dict, *, gateway: Any, storage: Any, transport: Any = None
) -> dict:
    """Top-level entrypoint that the CLI and scheduled runs both call.

    `gateway` is the Seren publisher gateway used for non-Prophet
    publishers (gmail/outlook for OTP, seren-bounty for earnings,
    polymarket-data for source discovery). `transport` is the
    direct-to-Prophet transport introduced in #493; when None the
    `_cmd_run` path constructs a default `ProphetDirectTransport`.
    """
    req = normalize_request(request)
    cmd = req["command"]

    if cmd == "status":
        return _cmd_status(req, gateway=gateway, storage=storage)
    if cmd == "setup":
        return _cmd_setup(req, gateway=gateway, storage=storage)
    return _cmd_run(req, gateway=gateway, storage=storage, transport=transport)


# ---------------------------------------------------------------------------
# Command handlers


def _cmd_status(req: dict, *, gateway: Any, storage: Any) -> dict:
    """Read-only earnings + local-ledger query.

    Plan §17.3: the status command shows both server-side earnings and
    the skill-owned `markets_created` count. Must NOT touch
    polymarket-data or prophet-ai — earnings come from seren-bounty,
    market count comes from local SerenDB.
    """
    bounty_client = BountyClient(gateway=gateway)
    bounty_id = req.get("bounty_id")
    earnings = bounty_client.earnings(bounty_id=bounty_id)
    local_count = _count_local_markets(storage, bounty_id=bounty_id)
    return {
        "status": "ok",
        "command": "status",
        "bounty_id": bounty_id,
        "earnings_count": len(earnings),
        "local_markets_created": local_count,
    }


def _count_local_markets(storage: Any, *, bounty_id: str | None) -> int:
    """Count rows in the skill-owned `markets_created` ledger.

    When bounty_id is None, counts across all bounties (the user's
    cumulative market output for whoever they are). When bounty_id is
    set, scopes to that bounty only — the operator's reconciler shape.
    """
    rows = getattr(storage, "markets_created", None) or []
    if bounty_id is None:
        return sum(1 for r in rows if isinstance(r, dict))
    return sum(
        1 for r in rows if isinstance(r, dict) and r.get("bounty_id") == bounty_id
    )


def _cmd_setup(req: dict, *, gateway: Any, storage: Any) -> dict:
    """Auth + reachability checks plus bounty auto-resolve.

    Plan §22 acceptance criterion #4: `--command setup` against a real
    SEREN_API_KEY returns ok=true and reports the auto-resolved
    bounty_id. Operator-supplied `bounty_id` overrides auto-resolve
    (per plan §3 ADR "Bounty auto-resolution hardening").
    """
    bounty_client = BountyClient(gateway=gateway)
    pinned = (req.get("bounty_id") or "").strip()
    if pinned:
        return {
            "status": "ok",
            "command": "setup",
            "connectors": AVAILABLE_CONNECTORS,
            "bounty_id": pinned,
            "bounty_resolution": "operator_pinned",
        }
    bounty_id, reason = _auto_resolve_bounty_id(bounty_client)
    if not bounty_id:
        return {
            "status": "blocked",
            "command": "setup",
            "connectors": AVAILABLE_CONNECTORS,
            "bounty_id": None,
            "bounty_resolution": "blocked_no_bounty",
            "reason": reason,
        }
    return {
        "status": "ok",
        "command": "setup",
        "connectors": AVAILABLE_CONNECTORS,
        "bounty_id": bounty_id,
        "bounty_resolution": "auto_resolved",
    }


def _auto_resolve_bounty_id(bounty_client: BountyClient) -> tuple[str, str]:
    """Return (bounty_id, reason). bounty_id is empty when blocked.

    Plan §3 ADR "Bounty auto-resolution hardening": filter open bounties
    by `customer_slug=prophet`, validate each candidate against
    `expected_bounty_spec.py`, pick the newest match. No fallback to
    newest-by-created_at without spec validation.
    """
    try:
        candidates = bounty_client.list_my_bounties(
            customer_slug=EXPECTED_CUSTOMER_SLUG, status="open"
        )
    except Exception as exc:
        return "", f"list_my_bounties failed: {exc}"
    if not candidates:
        return "", f"no open bounties with customer_slug={EXPECTED_CUSTOMER_SLUG!r}"
    matched: list[dict] = []
    failures: list[str] = []
    for bounty in candidates:
        ok, reason = _validate_bounty_spec(bounty)
        if ok:
            matched.append(bounty)
        else:
            failures.append(f"{bounty.get('id')}: {reason}")
    if not matched:
        return "", "no candidate matched expected_bounty_spec; rejected: " + "; ".join(failures)
    matched.sort(key=lambda b: b.get("created_at") or "", reverse=True)
    return matched[0].get("id") or "", "auto_resolved"


def _cmd_run(
    req: dict, *, gateway: Any, storage: Any, transport: Any = None
) -> dict:
    from prophet.transport import ProphetDirectTransport

    if transport is None:
        transport = ProphetDirectTransport()
    bounty_client = BountyClient(gateway=gateway)
    pinned = (req.get("bounty_id") or "").strip()
    if pinned:
        bounty_id = pinned
    else:
        resolved, reason = _auto_resolve_bounty_id(bounty_client)
        if not resolved:
            storage.insert(
                "runs",
                {
                    "bounty_id": None,
                    "command": "run",
                    "status": "blocked_no_bounty",
                    "error": reason,
                    "dry_run": req["dry_run"],
                },
            )
            return {
                "status": "blocked",
                "command": "run",
                "bounty_id": None,
                "reason": "blocked_no_bounty",
                "error": reason,
            }
        bounty_id = resolved

    # Step 1 — bounty join is always idempotent and must happen before
    # OTP so we have the participant record persisted even if OTP fails.
    join = bounty_client.join(bounty_id)

    # Step 2 — OTP. Issue #487: missing PROPHET_SESSION_TOKEN is a
    # distinct, agent-actionable failure (the agent must drive Privy
    # via Playwright MCP and inject the JWT first); viewer-bind
    # failures keep the existing `blocked_otp` reason. No Prophet or
    # Polymarket calls happen in the blocked branch (fail-closed
    # evidence per plan §3 ADR).
    try:
        otp = acquire_prophet_token_via_otp(
            req["prophet_email"],
            provider=req["email_provider"],
            gateway=gateway,
            bounty_id=bounty_id,
            require_viewer_binding=not bool(req["dry_run"]),
            transport=transport,
        )
    except MissingSessionToken as exc:
        storage.insert(
            "runs",
            {
                "bounty_id": bounty_id,
                "command": "run",
                "status": "blocked_missing_session_token",
                "error": str(exc),
                "dry_run": req["dry_run"],
            },
        )
        return {
            "status": "blocked",
            "command": "run",
            "bounty_id": bounty_id,
            "reason": "missing_session_token",
            "error": str(exc),
            "prophet_auth": {"method": "env_token", "source": "missing"},
        }
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
            "prophet_auth": {"method": "env_token", "source": "failed"},
        }

    jwt = (otp or {}).get("token") or ""
    viewer_id = (otp or {}).get("prophet_viewer_id") or ""
    viewer_email = (otp or {}).get("viewer_email") or ""
    auth_source = (otp or {}).get("source") or "otp"

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

    # Step 3 — Polymarket source discovery (plan §14). Both gates here
    # gate out-of-window markets: the deadline gate drops far-future
    # rows (e.g. `0xpoly-003` in the smoke fixture), and the
    # past-cutoff gate (anchored on `_get_now()`) drops UMA-stuck
    # rows whose `endDate` is months in the past — see the discovery
    # module docstring for the live-probe evidence.
    sources = discover_polymarket_sources(
        gateway=gateway, deadline=BOUNTY_DEADLINE, now=_get_now()
    )

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
            "polymarket_sources_considered": len(sources),
            "candidates_generated": len(filtered),
            "prophet_markets_created": [],
            "bounty_submission": {"status": "not_attempted", "submission_id": ""},
            "prophet_auth": {
                "method": "otp",
                "source": auth_source,
                "viewer_id": viewer_id,
            },
        }

    # Step 6 — Phase 15 (#505): the four-step write chain
    # (`createMarketWithBet`) is no longer driven from Python. Live
    # audit (2026-05-13) confirmed `CreateMarketWithBetInput.signedOrder`
    # requires an in-browser Privy signing prompt that no
    # agent-accessible API can produce. Each surviving candidate is
    # therefore emitted under `pending_ui_submission` and the agent
    # (Seren Desktop with `mcp__playwright__*`) drives the Prophet
    # `/create` UI per the SKILL.md runbook.
    #
    # The Prophet client is still needed for the dedup pre-filter
    # below; the chain methods on it (`create_market_chain`, `market`)
    # remain implemented but are no longer called from `_cmd_run`.
    # Issue #493: dedup reads still go directly to app.prophetmarket.ai
    # via the transport (Authorization: Bearer <JWT>).
    prophet_client = MinimalProphetClient(transport=transport)

    # Step 5.5 — Plan §14.3 dedup pre-filter (#505 Phase 14b). Prophet's
    # `markets` query is the source of truth for currently-listed markets.
    # We fetch the active set, normalize each row's `question` for case-
    # insensitive comparison, and drop any candidate whose question is an
    # exact match. Reconciliation refuses to credit duplicate creations,
    # so submitting them blind wastes both the chain's `amountCents` seed
    # bet and the bounty pool slot — fail closed if dedup is unreachable.
    try:
        existing_markets = prophet_client.markets_for_dedup(
            jwt=jwt,
            resolving_before_iso=BOUNTY_DEADLINE_ISO,
        )
    except (ProphetSchemaError, ProphetGraphQLError) as exc:
        storage.insert(
            "runs",
            {
                "bounty_id": bounty_id,
                "command": "run",
                "status": "blocked_dedup_unavailable",
                "market_count": 0,
                "dry_run": False,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return {
            "status": "blocked",
            "command": "run",
            "bounty_id": bounty_id,
            "referral_code": join.referral_code,
            "reason": "prophet_dedup_unavailable",
            "blockers": [
                f"markets_for_dedup_failed:{type(exc).__name__}:{exc}"
            ],
            "polymarket_sources_considered": len(sources),
            "candidates_generated": len(filtered),
            "prophet_markets_created": [],
            "bounty_submission": {"status": "not_attempted", "submission_id": ""},
            "prophet_auth": {
                "method": "otp",
                "source": auth_source,
                "viewer_id": viewer_id,
            },
        }
    except ProphetUnauthorized as exc:
        storage.insert(
            "runs",
            {
                "bounty_id": bounty_id,
                "command": "run",
                "status": "blocked_auth",
                "market_count": 0,
                "dry_run": False,
                "error": f"dedup_unauthorized: {exc}",
            },
        )
        return {
            "status": "blocked",
            "command": "run",
            "bounty_id": bounty_id,
            "referral_code": join.referral_code,
            "reason": "prophet_unauthorized",
            "blockers": [f"markets_for_dedup_unauthorized:{exc}"],
            "prophet_markets_created": [],
            "bounty_submission": {"status": "not_attempted", "submission_id": ""},
            "prophet_auth": {
                "method": "otp",
                "source": auth_source,
                "viewer_id": viewer_id,
            },
        }
    existing_questions = {
        (m.get("question") or "").strip().casefold()
        for m in existing_markets
        if isinstance(m, dict)
    }
    duplicate_candidates = [
        c for c in filtered if c.question.strip().casefold() in existing_questions
    ]
    for cand in duplicate_candidates:
        storage.insert(
            "events",
            {
                "event_type": "prophet.market_dedup_skipped",
                "polymarket_market_id": cand.polymarket_market_id,
                "question": cand.question,
            },
        )
    filtered = [
        c for c in filtered if c.question.strip().casefold() not in existing_questions
    ]
    # Phase 15 (#505): handoff to the agent for UI-driven submission.
    # See the module-level docstring for why createMarketWithBet has to
    # go through the browser. Each candidate becomes a row in
    # `pending_ui_submission`; the agent reads it, drives the Prophet
    # web UI per the SKILL.md runbook, and reports back via
    # `record_created_market(...)`.
    pending_ui_submission: list[dict] = []
    for cand in filtered[: req["submit_limit"]]:
        resolution_date_iso = cand.payload.get("source_resolution_date", "")
        entry = {
            "polymarket_market_id": cand.polymarket_market_id,
            "question": cand.question,
            "category": cand.category,
            "category_slug": _category_to_slug(cand.category),
            "resolution_date_iso": resolution_date_iso,
            "initial_bet_usdc": DEFAULT_INITIAL_BET_USDC,
            "bounty_id": bounty_id,
            "prophet_viewer_id": viewer_id,
        }
        pending_ui_submission.append(entry)
        storage.insert(
            "events",
            {
                "event_type": "prophet.market_pending_ui_submission",
                **entry,
            },
        )

    # Step 8 — persist the run row. Phase 15 (#505): zero markets are
    # ever created inside the Python subprocess now (the chain stops at
    # marketCreationOrderParams). `market_count=0` reflects that; the
    # agent reports back via `record_created_market` after each UI
    # submission, which increments `markets_created` separately.
    storage.insert(
        "runs",
        {
            "bounty_id": bounty_id,
            "command": "run",
            "status": "succeeded",
            "market_count": 0,
            "dry_run": False,
        },
    )

    return {
        "status": "ok",
        "command": "run",
        "bounty_id": bounty_id,
        "referral_code": join.referral_code,
        "polymarket_sources_considered": len(sources),
        "candidates_generated": len(filtered),
        "prophet_auth": {
            "method": "otp",
            "source": auth_source,
            "viewer_id": viewer_id,
        },
        "prophet_markets_created": [],
        "pending_ui_submission": pending_ui_submission,
        "blockers": [],
        "bounty_submission": {
            "status": "deferred_to_ui_submission",
            "submission_id": "",
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


class _InMemoryStorage:
    """Phase-14 in-memory persistence stand-in for the live smoke.

    Mirrors the StubStorage shape the unit tests use so `_cmd_run` and
    `_cmd_status` don't notice the difference. Plan §17 SerenDB-backed
    persistence (Phase 14.5) will swap this for a real run_sql writer;
    the smoke test verifies pipeline behavior, not the persistence layer.
    """

    def __init__(self) -> None:
        self.runs: list[dict] = []
        self.submissions: list[dict] = []
        self.events: list[dict] = []
        self.markets_created: list[dict] = []
        self.participant_identity: list[dict] = []

    def insert(self, table: str, row: dict) -> None:
        if not hasattr(self, table):
            raise AssertionError(f"_InMemoryStorage: unknown table {table!r}")
        getattr(self, table).append(row)


def main() -> int:
    args = parse_args()
    _config = load_config(args.config)

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
    try:
        normalize_request(request)
    except ValueError as exc:
        print(json.dumps({"status": "invalid_request", "error": str(exc)}))
        return 2

    # Lazy import so unit tests that import agent.py do not pay the
    # urllib cost. The HttpGateway is the same one the cron runner
    # uses (plan §12); it unwraps the seren publisher `data.body`
    # envelope so BountyClient sees flat dicts.
    from seren_cron_client import HttpGateway

    try:
        gateway = HttpGateway()
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    # Issue #474: production runs must persist to SerenDB so the
    # operator's reconciler, dedup, and cumulative submission folding
    # actually see prior ticks. Fall back to the in-memory stand-in
    # only when SEREN_API_KEY is unset (standalone CLI dry-runs on a
    # box without auth).
    if os.environ.get("SEREN_API_KEY") or os.environ.get("API_KEY"):
        from serendb_storage import SerenDBStorage

        storage: Any = SerenDBStorage(gateway=gateway)
    else:
        storage = _InMemoryStorage()
    # Issue #493: Prophet GraphQL calls now bypass the gateway and go
    # directly to app.prophetmarket.ai. Construct the transport here so
    # tests can override via the run_command kwarg.
    from prophet.transport import ProphetDirectTransport

    transport = ProphetDirectTransport()
    try:
        result = run_command(
            request, gateway=gateway, storage=storage, transport=transport
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc), "command": request["command"]}))
        return 1

    print(json.dumps(result, sort_keys=True, default=str))
    return 0 if result.get("status") in ("ok", "blocked") else 1


if __name__ == "__main__":
    sys.exit(main())
