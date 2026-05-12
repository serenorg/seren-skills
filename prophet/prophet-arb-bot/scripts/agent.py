"""prophet-arb-bot agent — Mode A operator-arb runner.

Run a single arb cycle. Best-guess Prophet placeOrder mutation is in
`scripts/prophet/orders.py` (§3 ADR — fixture-validated in a follow-on PR).

Commands:
  --command setup           Apply SerenDB schema, seed arb_pairs from
                            inputs.manual_pairs and (optionally) the
                            bounty-runner's markets_created. Idempotent.
  --command run             One arb cycle: refresh JWT, fetch prices, score,
                            place orders, persist. Default.
  --command status          Read-only summary of open Prophet orders +
                            recent runs from SerenDB.
  --command probe-schema    Run schema_probe.py against prophet-ai and
                            write tests/fixtures/prophet_schema.json.

Live execution requires both:
  - `live_mode: true` in config.json
  - `--yes-live` on the CLI
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from arbitrage.intelligence import IntelligenceConfig, assess_pair_health
from arbitrage.scoring import (
    Opportunity,
    PairPrices,
    ScoringConfig,
    score_batch,
)
from db import ResolvedTarget, get_target
from otp_worker import (
    EmailPublisherUnavailable,
    IdentityMismatch,
    OtpEmailTimeout,
    PrivyAuthFailed,
)
from otp_worker.auth_facade import AuthFacade
from otp_worker.playwright_client import RealBrowserSession
from otp_worker.session_cache import SessionCache
from persistence import (
    RunRecorder,
    apply_schema,
    discover_pairs_from_bounty_runner,
    list_arb_pairs,
    list_open_orders,
    list_recent_runs,
    upsert_arb_pair,
)
from polymarket.prices import fetch_market_prices
from prophet import (
    ProphetClientError,
    ProphetGraphQLError,
    ProphetSchemaError,
    ProphetUnauthorized,
)
from prophet.orders import ProphetOrder, ProphetOrderClient
from seren_cron_client import HttpGateway

DEFAULT_CONFIG_PATH = "config.json"
SKILL_SLUG = "prophet-arb-bot"
SCHEMA_PATH = SCRIPT_DIR.parent / "serendb_schema.sql"


# ---------------------------------------------------------------------------
# Config


@dataclass
class AgentConfig:
    inputs: dict[str, Any]
    project_name: str
    database_name: str
    scoring: ScoringConfig
    intelligence: IntelligenceConfig
    live_mode: bool
    max_orders_per_run: int

    @classmethod
    def load(cls, path: str) -> "AgentConfig":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        inputs = raw.get("inputs") or {}
        storage_raw = raw.get("storage") or {}
        scoring_raw = raw.get("scoring") or {}
        intel_raw = raw.get("intelligence") or {}
        return cls(
            inputs=inputs,
            project_name=storage_raw.get("project_name") or "prophet",
            database_name=storage_raw.get("database_name") or "prophet",
            scoring=ScoringConfig(
                min_spread=float(scoring_raw.get("min_spread", 0.03)),
                max_spread=float(scoring_raw.get("max_spread", 0.30)),
                kelly_fraction=float(scoring_raw.get("kelly_fraction", 0.25)),
                max_trade_size_usdc=float(scoring_raw.get("max_trade_size_usdc", 50.0)),
                min_trade_size_usdc=float(scoring_raw.get("min_trade_size_usdc", 5.0)),
                bankroll_usdc=float(scoring_raw.get("bankroll_usdc", 200.0)),
            ),
            intelligence=IntelligenceConfig(
                enabled=bool(intel_raw.get("enabled", False)),
                max_basis_volatility=float(intel_raw.get("max_basis_volatility", 0.05)),
                fetch_correlations=bool(intel_raw.get("fetch_correlations", True)),
            ),
            live_mode=bool(raw.get("live_mode", False)),
            max_orders_per_run=int(raw.get("max_orders_per_run", 5)),
        )


@dataclass
class CycleResult:
    status: str
    reason: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, **self.payload}


def _resolve_target(config: AgentConfig) -> ResolvedTarget:
    return get_target(
        project_name=config.project_name,
        database_name=config.database_name,
    )


# ---------------------------------------------------------------------------
# Setup


def cmd_setup(*, config: AgentConfig) -> CycleResult:
    payload: dict[str, Any] = {
        "auth": "unchecked",
        "schema": "unchecked",
        "pairs_seeded_manual": 0,
        "pairs_seeded_from_bounty_runner": 0,
        "warnings": [],
    }

    try:
        target = _resolve_target(config)
        payload["auth"] = "ok"
        payload["project_id"] = target.project_id
        payload["database_name"] = target.database_name
    except Exception as exc:
        payload["auth"] = f"failed:{type(exc).__name__}"
        payload["error"] = str(exc)
        return CycleResult(
            status="blocked", reason="auth_or_target_unavailable", payload=payload
        )

    try:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        payload["schema"] = f"missing:{exc}"
        return CycleResult(
            status="blocked", reason="schema_file_missing", payload=payload
        )

    try:
        apply_schema(target, sql)
        payload["schema"] = "applied"
    except Exception as exc:
        payload["schema"] = f"failed:{type(exc).__name__}"
        payload["error"] = str(exc)[:300]
        return CycleResult(
            status="blocked", reason="schema_apply_failed", payload=payload
        )

    # Seed arb_pairs from inputs.manual_pairs.
    manual_pairs = config.inputs.get("manual_pairs") or []
    for pair in manual_pairs:
        if not isinstance(pair, dict):
            continue
        prophet_id = pair.get("prophet_market_id")
        condition_id = pair.get("polymarket_condition_id")
        if not prophet_id or not condition_id:
            continue
        try:
            upsert_arb_pair(
                target=target,
                prophet_market_id=prophet_id,
                polymarket_condition_id=condition_id,
                source_skill="manual",
            )
            payload["pairs_seeded_manual"] = int(payload["pairs_seeded_manual"]) + 1
        except Exception as exc:
            payload["warnings"].append(
                f"pair_upsert_failed:{prophet_id[:8]}:{type(exc).__name__}"
            )

    # Optional: seed from bounty-runner. Empty until bounty-runner migrates.
    inherited = discover_pairs_from_bounty_runner(target=target)
    for row in inherited:
        try:
            upsert_arb_pair(
                target=target,
                prophet_market_id=row["prophet_market_id"],
                polymarket_condition_id=row["polymarket_condition_id"],
                source_skill="prophet-bounty-runner",
            )
            payload["pairs_seeded_from_bounty_runner"] = (
                int(payload["pairs_seeded_from_bounty_runner"]) + 1
            )
        except Exception:
            continue

    if (
        payload["pairs_seeded_manual"] == 0
        and payload["pairs_seeded_from_bounty_runner"] == 0
    ):
        payload["warnings"].append(
            "no pairs seeded — populate inputs.manual_pairs in config.json"
        )

    if not config.inputs.get("prophet_email"):
        payload["warnings"].append("inputs.prophet_email is empty")
    if config.inputs.get("email_provider") not in ("gmail", "outlook"):
        payload["warnings"].append("inputs.email_provider must be 'gmail' or 'outlook'")

    return CycleResult(status="ok", reason="setup_complete", payload=payload)


# ---------------------------------------------------------------------------
# Auth


def _acquire_jwt(
    *,
    config: AgentConfig,
    gateway: HttpGateway,
    transport: Any,
) -> tuple[str | None, str | None, str]:
    env_jwt = os.environ.get("PROPHET_SESSION_TOKEN", "").strip()
    if env_jwt:
        return env_jwt, None, "env"

    cache = SessionCache()  # default = bounty-runner cache
    entry = cache.read()
    if entry.is_fresh():
        return entry.jwt, entry.prophet_viewer_id, "cache"

    email = (config.inputs.get("prophet_email") or "").strip()
    provider = (config.inputs.get("email_provider") or "").strip().lower()
    if not email or provider not in ("gmail", "outlook"):
        return None, None, "blocked_otp_email_missing"

    facade = AuthFacade(cache=cache)
    try:
        with RealBrowserSession() as session:
            fresh = facade.get_fresh_jwt(
                email=email,
                provider=provider,
                seren_user_id=config.inputs.get("seren_user_id") or "",
                bounty_id=config.inputs.get("bounty_id") or "",
                browser_session=session,
                gateway=gateway,
                transport=transport,
            )
        return fresh.jwt, fresh.prophet_viewer_id, fresh.source
    except (
        OtpEmailTimeout,
        EmailPublisherUnavailable,
        PrivyAuthFailed,
        IdentityMismatch,
    ) as exc:
        return None, None, f"blocked_otp:{type(exc).__name__}"
    except Exception as exc:
        return None, None, f"blocked_auth_unexpected:{type(exc).__name__}"


# ---------------------------------------------------------------------------
# Run


def cmd_run(
    *,
    config: AgentConfig,
    gateway: HttpGateway,
    yes_live: bool,
    transport: Any = None,
) -> CycleResult:
    if transport is None:
        from prophet.transport import ProphetDirectTransport

        transport = ProphetDirectTransport()
    try:
        target = _resolve_target(config)
    except Exception as exc:
        return CycleResult(
            status="blocked",
            reason="target_resolution_failed",
            payload={"error": str(exc)[:300]},
        )

    recorder = RunRecorder(run_id=uuid.uuid4().hex, target=target)
    recorder.summary["live_mode"] = config.live_mode and yes_live

    pairs = list_arb_pairs(target=target)
    for p in pairs:
        recorder.record_pair(p["prophet_market_id"], p["polymarket_condition_id"])
    recorder.summary["pairs_evaluated"] = len(pairs)
    if not pairs:
        return CycleResult(
            status="ok",
            reason="no_pairs_seeded",
            payload=recorder.finish("ok", "no_pairs_seeded"),
        )

    condition_ids = [p["polymarket_condition_id"] for p in pairs]
    polymarket_prices = fetch_market_prices(
        gateway=gateway, condition_ids=condition_ids
    )
    recorder.summary["polymarket_prices_fetched"] = len(polymarket_prices)

    jwt, viewer_id, jwt_source = _acquire_jwt(
        config=config, gateway=gateway, transport=transport
    )
    if jwt is None:
        return CycleResult(
            status="blocked",
            reason=jwt_source,
            payload=recorder.finish("blocked", jwt_source),
        )
    recorder.summary["jwt_source"] = jwt_source
    if viewer_id:
        recorder.summary["prophet_viewer_id"] = viewer_id

    order_client = ProphetOrderClient(transport=transport)

    open_orders_by_pair: dict[tuple[str, str, str], ProphetOrder] = {}
    try:
        existing = order_client.list_user_orders(jwt=jwt, status="OPEN")
        for o in existing:
            open_orders_by_pair[(o.market_id, o.outcome, o.side)] = o
    except (ProphetSchemaError, ProphetGraphQLError) as exc:
        recorder.record_blocker(
            f"list_user_orders_failed:{type(exc).__name__}:{str(exc)[:120]}"
        )
    except ProphetUnauthorized:
        return CycleResult(
            status="blocked",
            reason="prophet_unauthorized",
            payload=recorder.finish("blocked", "prophet_unauthorized"),
        )

    aligned: list[PairPrices] = []
    health_by_pair: dict[str, list[str]] = {}
    for pair in pairs:
        prophet_id = pair["prophet_market_id"]
        condition_id = pair["polymarket_condition_id"]
        polymarket_price = polymarket_prices.get(condition_id)
        if polymarket_price is None:
            health_by_pair.setdefault(prophet_id, []).append("polymarket_price_missing")
            continue
        try:
            prophet_price = order_client.market_prices(jwt=jwt, market_id=prophet_id)
        except ProphetUnauthorized:
            return CycleResult(
                status="blocked",
                reason="prophet_unauthorized",
                payload=recorder.finish("blocked", "prophet_unauthorized"),
            )
        except (ProphetSchemaError, ProphetGraphQLError) as exc:
            health_by_pair.setdefault(prophet_id, []).append(
                f"prophet_price_failed:{type(exc).__name__}"
            )
            continue
        aligned.append(
            PairPrices(
                prophet_market_id=prophet_id,
                polymarket_condition_id=condition_id,
                prophet_yes=prophet_price.yes_price,
                prophet_no=prophet_price.no_price,
                polymarket_yes=polymarket_price.yes_price,
                polymarket_no=polymarket_price.no_price,
            )
        )
        verdict = assess_pair_health(
            gateway=gateway,
            polymarket_condition_id=condition_id,
            config=config.intelligence,
        )
        if verdict.health_warnings:
            health_by_pair.setdefault(prophet_id, []).extend(verdict.health_warnings)

    opportunities = score_batch(
        aligned,
        config=config.scoring,
        health_warnings_by_pair=health_by_pair,
    )
    recorder.summary["opportunities_scored"] = len(opportunities)

    actionable: list[Opportunity] = []
    for opp in opportunities:
        recorder.record_opportunity(opp)
        if not opp.is_actionable():
            recorder.record_blocker(f"opp_not_actionable:{opp.reason}")
            continue
        if (opp.prophet_market_id, opp.outcome, opp.side) in open_orders_by_pair:
            recorder.record_blocker(
                f"duplicate_open_order:{opp.prophet_market_id}:{opp.outcome}:{opp.side}"
            )
            continue
        actionable.append(opp)

    submitted = 0
    for opp in actionable[: config.max_orders_per_run]:
        if not (config.live_mode and yes_live):
            recorder.record_blocker("dry_run_mode")
            continue
        try:
            order = order_client.place_order(
                jwt=jwt,
                market_id=opp.prophet_market_id,
                outcome=opp.outcome,
                side=opp.side,
                shares=opp.size_usdc,
                limit_price=opp.limit_price,
            )
        except (
            ProphetSchemaError,
            ProphetGraphQLError,
            ProphetClientError,
        ) as exc:
            recorder.record_blocker(
                f"place_order_failed:{type(exc).__name__}:{str(exc)[:120]}"
            )
            continue
        recorder.record_order(order)
        submitted += 1

    recorder.summary["orders_submitted"] = submitted
    recorder.summary["actionable_opportunities"] = len(actionable)

    if not (config.live_mode and yes_live) and len(actionable) > 0:
        return CycleResult(
            status="ok",
            reason="cycle_complete_dry_run",
            payload=recorder.finish("ok", "cycle_complete_dry_run"),
        )
    if submitted == 0 and len(actionable) > 0:
        return CycleResult(
            status="ok_no_fills",
            reason="all_orders_blocked",
            payload=recorder.finish("ok_no_fills", "all_orders_blocked"),
        )
    return CycleResult(
        status="ok",
        reason="cycle_complete",
        payload=recorder.finish("ok", "cycle_complete"),
    )


# ---------------------------------------------------------------------------
# Status


def cmd_status(
    *,
    config: AgentConfig,
    gateway: HttpGateway,
    transport: Any = None,
) -> CycleResult:
    if transport is None:
        from prophet.transport import ProphetDirectTransport

        transport = ProphetDirectTransport()
    try:
        target = _resolve_target(config)
    except Exception as exc:
        return CycleResult(
            status="blocked",
            reason="target_resolution_failed",
            payload={"error": str(exc)[:300]},
        )

    payload: dict[str, Any] = {
        "open_orders_db": list_open_orders(target=target),
        "recent_runs": list_recent_runs(target=target, limit=10),
    }

    jwt, viewer_id, jwt_source = _acquire_jwt(
        config=config, gateway=gateway, transport=transport
    )
    payload["jwt_source"] = jwt_source
    if jwt is None:
        return CycleResult(status="ok", reason="status_db_only", payload=payload)

    order_client = ProphetOrderClient(transport=transport)
    try:
        live_orders = order_client.list_user_orders(jwt=jwt, status="OPEN")
        payload["open_orders_live"] = [
            {
                "order_id": o.order_id,
                "market_id": o.market_id,
                "side": o.side,
                "outcome": o.outcome,
                "shares": o.shares,
                "limit_price": o.limit_price,
                "filled_shares": o.filled_shares,
                "status": o.status,
            }
            for o in live_orders
        ]
        if viewer_id:
            payload["prophet_viewer_id"] = viewer_id
    except (ProphetSchemaError, ProphetGraphQLError) as exc:
        payload["live_query_error"] = f"{type(exc).__name__}:{str(exc)[:120]}"
    except ProphetUnauthorized:
        payload["live_query_error"] = "prophet_unauthorized"

    return CycleResult(status="ok", reason="status_ok", payload=payload)


# ---------------------------------------------------------------------------
# CLI


def _emit(result: CycleResult, *, json_output: bool) -> int:
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str))
    else:
        print(f"status={result.status} reason={result.reason}")
        for key, value in result.payload.items():
            print(f"  {key}: {value}")
    return 0 if result.status in ("ok", "ok_no_fills") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"path to config.json (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--command",
        choices=["setup", "run", "status", "probe-schema"],
        default="run",
    )
    parser.add_argument(
        "--yes-live",
        action="store_true",
        help="Confirm live execution. Required in addition to live_mode=true.",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Emit a single JSON envelope (suitable for cron consumption).",
    )
    args = parser.parse_args(argv)

    if args.command == "probe-schema":
        from prophet.schema_probe import main as probe_main  # type: ignore

        # Pass an explicit empty argv so the probe's argparse does not
        # re-parse the parent agent's --config / --command flags.
        return probe_main([])

    config = AgentConfig.load(args.config)
    gateway = HttpGateway()
    # Issue #493: Prophet GraphQL goes direct to app.prophetmarket.ai.
    from prophet.transport import ProphetDirectTransport

    transport = ProphetDirectTransport()

    if args.command == "setup":
        result = cmd_setup(config=config)
    elif args.command == "run":
        result = cmd_run(
            config=config,
            gateway=gateway,
            yes_live=args.yes_live,
            transport=transport,
        )
    elif args.command == "status":
        result = cmd_status(config=config, gateway=gateway, transport=transport)
    else:
        parser.error(f"unknown command: {args.command}")
        return 2

    return _emit(result, json_output=args.json_output)


if __name__ == "__main__":
    raise SystemExit(main())
