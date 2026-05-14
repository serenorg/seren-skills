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

from arbitrage.hedge import (
    DepthAssessment,
    HedgeOutcome,
    assess_polymarket_depth,
    hedge_filled_order,
)
from arbitrage.intelligence import IntelligenceConfig, assess_pair_health
from arbitrage.scoring import (
    Opportunity,
    PairPrices,
    ScoringConfig,
    score_batch,
)
from db import ResolvedTarget, get_target
from discovery import AutoDiscoverConfig, AutoDiscoverResult, run_auto_discover
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
from funds_preflight import (
    evaluate_funds_preflight,
    evaluate_two_venue_funds_preflight,
)
from polymarket.prices import fetch_market_prices
from prophet import (
    ProphetClientError,
    ProphetGraphQLError,
    ProphetSchemaError,
    ProphetUnauthorized,
)
from prophet.client import MinimalProphetClient
from prophet.orders import ProphetOrder, ProphetOrderClient
from seren_cron_client import HttpGateway

DEFAULT_CONFIG_PATH = "config.json"
SKILL_SLUG = "prophet-arb-bot"
SCHEMA_PATH = SCRIPT_DIR.parent / "serendb_schema.sql"


# ---------------------------------------------------------------------------
# Config


EXECUTION_MODE_SINGLE_LEG = "single_leg"
EXECUTION_MODE_DELTA_NEUTRAL = "delta_neutral"
_VALID_EXECUTION_MODES = {EXECUTION_MODE_SINGLE_LEG, EXECUTION_MODE_DELTA_NEUTRAL}


@dataclass
class AgentConfig:
    inputs: dict[str, Any]
    project_name: str
    database_name: str
    scoring: ScoringConfig
    intelligence: IntelligenceConfig
    auto_discover: AutoDiscoverConfig
    live_mode: bool
    max_orders_per_run: int
    execution_mode: str
    max_hedge_slippage_bps: float

    @classmethod
    def load(cls, path: str) -> "AgentConfig":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        inputs = raw.get("inputs") or {}
        storage_raw = raw.get("storage") or {}
        scoring_raw = raw.get("scoring") or {}
        intel_raw = raw.get("intelligence") or {}
        auto_raw = raw.get("auto_discover") or {}
        execution_mode = str(
            raw.get("execution_mode") or EXECUTION_MODE_SINGLE_LEG
        ).strip().lower()
        if execution_mode not in _VALID_EXECUTION_MODES:
            raise ValueError(
                f"execution_mode must be one of {sorted(_VALID_EXECUTION_MODES)}; "
                f"got {execution_mode!r}"
            )
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
            auto_discover=AutoDiscoverConfig.from_dict(auto_raw),
            live_mode=bool(raw.get("live_mode", False)),
            max_orders_per_run=int(raw.get("max_orders_per_run", 5)),
            execution_mode=execution_mode,
            max_hedge_slippage_bps=float(raw.get("max_hedge_slippage_bps", 200.0)),
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


def _build_hedger(config: AgentConfig) -> Any:
    """Construct the live Polymarket hedger when delta-neutral is on.

    Importing `DirectClobTrader` lazily keeps the test suite from
    requiring `py-clob-client` for single-leg paths. Returns ``None``
    if the deps aren't installed — the agent records a blocker and
    falls back to single-leg semantics for the cycle.
    """
    from pathlib import Path

    skill_root = Path(__file__).resolve().parent.parent
    try:
        from polymarket_live import DirectClobTrader, fetch_book
    except Exception as exc:  # pragma: no cover - import guard only
        raise RuntimeError(
            f"delta_neutral mode requires py-clob-client deps: {exc}"
        )

    trader = DirectClobTrader(
        skill_root=skill_root,
        client_name="prophet-arb-bot-hedger",
    )

    class _LiveHedger:
        """Adapter from `arbitrage.hedge.Hedger` to `DirectClobTrader`."""

        def __init__(self, _trader: Any) -> None:
            self._trader = _trader
            self._prophet_cancel: Any = None

        def bind_prophet_cancel(self, order_client: Any, jwt: str) -> None:
            """Late-bind the Prophet order client so unwinds reach it.

            We can't create the Prophet client at construction time —
            it needs the JWT acquired later in the cycle.
            """
            def cancel(order_id: str) -> None:
                try:
                    order_client.cancel_order(jwt=jwt, order_id=order_id)
                except Exception:
                    # Naked exposure already accepted; cancel best-effort.
                    pass
            self._prophet_cancel = cancel

        def fetch_book(self, condition_id: str) -> dict[str, Any]:
            # `fetch_book` keys on Polymarket token_id, not condition_id.
            # The polymarket-data publisher's `/markets?condition_ids`
            # response carries token_ids; the runner resolves them in
            # `fetch_market_prices` and caches them. For the depth check
            # we re-fetch via the publisher's `/books` indirectly through
            # the live trading client. NOTE: condition_id ≠ token_id;
            # callers must pass the right one.
            return fetch_book(condition_id)

        def submit_hedge(
            self,
            *,
            condition_id: str,
            hedge_side: str,
            size_usdc: float,
            marketable_price: float,
        ) -> dict[str, Any]:
            # `condition_id` here is actually the polymarket token_id
            # for the YES outcome — see scoring.PairPrices contract.
            # The depth check fed the same id; we keep the naming
            # consistent with the Hedger protocol.
            book = fetch_book(condition_id)
            from polymarket_live import (
                fetch_fee_rate_bps,
                snap_price,
                safe_str,
            )
            tick_size = safe_str(book.get("tick_size"), "0.01")
            price = snap_price(marketable_price, tick_size, hedge_side.upper())
            neg_risk = bool(book.get("neg_risk", False))
            fee_bps = fetch_fee_rate_bps(condition_id)
            # Convert USDC notional to share count at the marketable
            # price. Polymarket's `create_order` takes `size` in shares.
            shares = size_usdc / max(price, 1e-6)
            response = self._trader.create_order(
                token_id=condition_id,
                side=hedge_side.upper(),
                price=price,
                size=shares,
                tick_size=tick_size,
                neg_risk=neg_risk,
                fee_rate_bps=fee_bps,
            )
            poly_order_id = ""
            if isinstance(response, dict):
                poly_order_id = str(response.get("orderID") or response.get("id") or "")
            return {
                "polymarket_order_id": poly_order_id,
                "filled_qty": shares,
                "fill_price": price,
            }

        def unwind_prophet(self, *, order_id: str) -> None:
            if self._prophet_cancel:
                self._prophet_cancel(order_id)

    return _LiveHedger(trader)


def cmd_run(
    *,
    config: AgentConfig,
    gateway: HttpGateway,
    yes_live: bool,
    transport: Any = None,
    hedger: Any = None,
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
    recorder.summary["pairs_pre_discover"] = len(pairs)

    # #538 — auto-discover the campaign candidate set. When disabled
    # we keep the old single-leg semantics: empty `arb_pairs` short-
    # circuits to `no_pairs_seeded` before any JWT acquisition. When
    # enabled, we always acquire the JWT (needed for the Prophet pair
    # lookup), discover live candidates, and either auto-pair them or
    # emit `pending_ui_submission` entries the agent's `/create` runbook
    # drives.
    auto_result: AutoDiscoverResult | None = None
    pending_ui_submission: list[dict] = []
    if not pairs and not config.auto_discover.enabled:
        return CycleResult(
            status="ok",
            reason="no_pairs_seeded",
            payload=recorder.finish("ok", "no_pairs_seeded"),
        )

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

    if config.auto_discover.enabled:
        prophet_client_for_discovery = MinimalProphetClient(transport=transport)
        try:
            auto_result = run_auto_discover(
                gateway=gateway,
                prophet_client=prophet_client_for_discovery,
                jwt=jwt,
                target=target,
                config=config.auto_discover,
                viewer_id=viewer_id or "",
            )
        except Exception as exc:
            recorder.record_blocker(
                f"auto_discover_failed:{type(exc).__name__}:{str(exc)[:120]}"
            )
            auto_result = None
        if auto_result is not None:
            recorder.summary["auto_discover_candidates_found"] = (
                auto_result.candidates_found
            )
            recorder.summary["auto_discover_auto_paired"] = len(
                auto_result.auto_paired
            )
            recorder.summary["auto_discover_already_paired"] = (
                auto_result.already_paired
            )
            recorder.summary["auto_discover_pending_ui"] = len(
                auto_result.pending_ui_submission
            )
            recorder.summary["auto_discover_prophet_lookup_failed"] = (
                auto_result.prophet_lookup_failed
            )
            if auto_result.sheet_path:
                recorder.summary["arb_candidates_sheet"] = auto_result.sheet_path
            pending_ui_submission = auto_result.pending_ui_submission
            # Reload pairs — auto_paired rows are now in arb_pairs.
            pairs = list_arb_pairs(target=target)

    for p in pairs:
        recorder.record_pair(p["prophet_market_id"], p["polymarket_condition_id"])
    recorder.summary["pairs_evaluated"] = len(pairs)

    if not pairs:
        # Auto-discover ran but Prophet hasn't created any matching
        # markets yet. Surface pending_ui_submission so the agent drives
        # `/create` for each row, then re-run the cycle to trade them.
        payload = recorder.finish("ok", "no_pairs_seeded_pending_ui_submission")
        if pending_ui_submission:
            payload["pending_ui_submission"] = pending_ui_submission
        return CycleResult(
            status="ok",
            reason="no_pairs_seeded_pending_ui_submission",
            payload=payload,
        )

    condition_ids = [p["polymarket_condition_id"] for p in pairs]
    polymarket_prices = fetch_market_prices(
        gateway=gateway, condition_ids=condition_ids
    )
    recorder.summary["polymarket_prices_fetched"] = len(polymarket_prices)

    order_client = ProphetOrderClient(transport=transport)

    recorder.summary["execution_mode"] = config.execution_mode
    delta_neutral = config.execution_mode == EXECUTION_MODE_DELTA_NEUTRAL
    live_hedger: Any = hedger  # tests inject; runtime constructs lazily

    open_orders_by_pair: dict[tuple[str, str, str], ProphetOrder] = {}
    existing_orders: list[ProphetOrder] = []
    try:
        existing_orders = order_client.list_user_orders(jwt=jwt, status="OPEN")
        for o in existing_orders:
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

    # Delta-neutral post-fill sweep. Any previously-open order now
    # reported with `filled_shares > 0` triggers an immediate Polymarket
    # hedge. We sweep BEFORE scoring this cycle so the hedger has a
    # chance to flatten before new exposure goes on. Skipped when not in
    # delta-neutral mode (Mode A semantics unchanged).
    hedge_handled = 0
    hedge_failures = 0
    if delta_neutral and (config.live_mode and yes_live):
        if live_hedger is None:
            try:
                live_hedger = _build_hedger(config)
                live_hedger.bind_prophet_cancel(order_client, jwt)
            except Exception as exc:
                recorder.record_blocker(
                    f"hedger_init_failed:{type(exc).__name__}:{str(exc)[:120]}"
                )
                live_hedger = None
        # Map prophet_market_id → polymarket_condition_id for hedge submission.
        pair_lookup = {
            p["prophet_market_id"]: p["polymarket_condition_id"] for p in pairs
        }
        if live_hedger is not None:
            for o in existing_orders:
                if float(getattr(o, "filled_shares", 0.0)) <= 0.0:
                    continue
                condition_id = pair_lookup.get(o.market_id)
                if not condition_id:
                    continue
                # Marketable price is best-opposing-side from Polymarket
                # book at submission time; we re-fetch inside the hedger.
                # The hedge_filled_order helper only needs a price for the
                # share-conversion; we approximate from the Prophet limit
                # (close enough for hedge sizing; the trader snaps to tick).
                marketable_price = float(getattr(o, "limit_price", 0.5))
                outcome = hedge_filled_order(
                    prophet_order=o,
                    polymarket_condition_id=condition_id,
                    hedger=live_hedger,
                    marketable_price=marketable_price,
                )
                # Persist outcome on the order row (recorder may not have
                # this order — synthesize a minimal entry so the writer
                # has something to UPSERT).
                recorder.orders.append(
                    {
                        "order_id": o.order_id,
                        "market_id": o.market_id,
                        "side": o.side,
                        "outcome": o.outcome,
                        "shares": float(o.shares),
                        "limit_price": float(o.limit_price),
                        "status": "FILLED",
                        "hedge_status": outcome.hedge_status,
                        "polymarket_order_id": outcome.polymarket_order_id,
                        "polymarket_filled_qty": outcome.polymarket_filled_qty,
                        "polymarket_fill_price": outcome.polymarket_fill_price,
                    }
                )
                if outcome.hedge_status == "hedged":
                    hedge_handled += 1
                else:
                    hedge_failures += 1
                    recorder.record_blocker(
                        f"hedge_{outcome.hedge_status}:{o.order_id}:{outcome.error or ''}"[:200]
                    )
    recorder.summary["hedges_submitted"] = hedge_handled
    recorder.summary["hedge_failures"] = hedge_failures

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
    depth_blocked = 0
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
        # Delta-neutral pre-trade depth check: don't quote Prophet
        # unless Polymarket can hedge. Skipped in single-leg mode (Mode
        # A semantics unchanged) and in dry-run where there is no live
        # hedger to consult. Defensive: if depth check infrastructure
        # fails, fall back to the single-leg path with a blocker so the
        # operator can investigate.
        if delta_neutral and live_hedger is not None:
            try:
                book = live_hedger.fetch_book(opp.polymarket_condition_id)
                hedge_side = "sell" if opp.side.lower() == "buy" else "buy"
                depth: DepthAssessment = assess_polymarket_depth(
                    book_payload=book,
                    target_size_usdc=opp.size_usdc,
                    hedge_side=hedge_side,
                    max_slippage_bps=config.max_hedge_slippage_bps,
                )
            except Exception as exc:
                recorder.record_blocker(
                    f"depth_check_failed:{type(exc).__name__}:{str(exc)[:120]}"
                )
                continue
            if not depth.sufficient:
                depth_blocked += 1
                recorder.record_blocker(
                    f"polymarket_depth_{depth.reason}:"
                    f"target={depth.target_size_usdc:.2f}:"
                    f"fillable={depth.fillable_size_usdc:.2f}:"
                    f"slip_bps={depth.realized_slippage_bps:.1f}"
                )
                continue
        actionable.append(opp)
    recorder.summary["depth_blocked"] = depth_blocked

    # Issue #524 — funds preflight. Skip the cheap-but-noisy
    # `placeOrder` loop entirely if protocol cash can't fund the
    # planned collateral. Only run when live execution is actually
    # gated on (live_mode + yes_live); dry-run cycles short-circuit
    # below in the placement loop without spending cash.
    planned_orders = actionable[: config.max_orders_per_run]
    if (config.live_mode and yes_live) and planned_orders:
        balance_client = MinimalProphetClient(transport=transport)
        try:
            cash = balance_client.cash_balance(jwt=jwt)
        except ProphetUnauthorized:
            return CycleResult(
                status="blocked",
                reason="prophet_unauthorized",
                payload=recorder.finish("blocked", "prophet_unauthorized"),
            )
        except (ProphetSchemaError, ProphetGraphQLError) as exc:
            recorder.record_blocker(
                f"cash_balance_failed:{type(exc).__name__}:{str(exc)[:120]}"
            )
            return CycleResult(
                status="blocked",
                reason="funds_preflight_unavailable",
                payload=recorder.finish("blocked", "funds_preflight_unavailable"),
            )

        if delta_neutral:
            # Two-venue preflight (#536). Each opportunity locks the same
            # USDC notional on both Prophet (LIMIT collateral) and
            # Polymarket (hedge collateral), so we check both balances
            # and return split deficits so the deposit runbook can route
            # the operator to the right venue.
            polymarket_avail = 0.0
            if live_hedger is not None:
                try:
                    # DirectClobTrader exposes `get_cash_balance` for
                    # the configured CLOB account. If it raises, fall
                    # back to 0 (blocks the cycle with a clear deficit).
                    polymarket_avail = float(
                        getattr(live_hedger, "_trader", live_hedger).get_cash_balance()
                    )
                except Exception as exc:
                    recorder.record_blocker(
                        f"polymarket_balance_failed:{type(exc).__name__}:{str(exc)[:120]}"
                    )
            preflight2 = evaluate_two_venue_funds_preflight(
                opportunities=planned_orders,
                prophet_available_usdc=cash.available_usdc,
                polymarket_available_usdc=polymarket_avail,
            )
            if not preflight2.ok:
                recorder.record_blocker(
                    f"funds_insufficient_prophet={preflight2.prophet_deficit_usdc}"
                    f"_polymarket={preflight2.polymarket_deficit_usdc}_usdc"
                )
                payload = recorder.finish("blocked", "funds_insufficient")
                payload["action"] = "deposit_required"
                payload["deposit"] = preflight2.to_deposit_envelope()
                return CycleResult(
                    status="blocked",
                    reason="funds_insufficient",
                    payload=payload,
                )
        else:
            preflight = evaluate_funds_preflight(
                opportunities=planned_orders,
                available_usdc=cash.available_usdc,
            )
            if not preflight.ok:
                recorder.record_blocker(
                    f"funds_insufficient_by_{preflight.deficit_usdc}_usdc"
                )
                payload = recorder.finish("blocked", "funds_insufficient")
                payload["action"] = "deposit_required"
                payload["deposit"] = preflight.to_deposit_envelope()
                return CycleResult(
                    status="blocked",
                    reason="funds_insufficient",
                    payload=payload,
                )

    submitted = 0
    for opp in planned_orders:
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

    def _attach_pending(payload: dict[str, Any]) -> dict[str, Any]:
        # Always surface pending_ui_submission so the agent can drive
        # Prophet `/create` for unmatched candidates in parallel with
        # the scoring loop's normal trading work.
        if pending_ui_submission:
            payload["pending_ui_submission"] = pending_ui_submission
        return payload

    if not (config.live_mode and yes_live) and len(actionable) > 0:
        return CycleResult(
            status="ok",
            reason="cycle_complete_dry_run",
            payload=_attach_pending(recorder.finish("ok", "cycle_complete_dry_run")),
        )
    if submitted == 0 and len(actionable) > 0:
        return CycleResult(
            status="ok_no_fills",
            reason="all_orders_blocked",
            payload=_attach_pending(recorder.finish("ok_no_fills", "all_orders_blocked")),
        )
    return CycleResult(
        status="ok",
        reason="cycle_complete",
        payload=_attach_pending(recorder.finish("ok", "cycle_complete")),
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
# Record-created-market (#538)
#
# After the agent drives Prophet's `/create` UI for a `pending_ui_submission`
# entry, it captures the new `prophet_market_id` and calls this command to
# persist the pair. The next `cmd_run` cycle picks the pair up via
# `list_arb_pairs` and starts arbing it.


def cmd_record_created_market(
    *,
    config: AgentConfig,
    polymarket_condition_id: str,
    prophet_market_id: str,
) -> CycleResult:
    """Persist a pair the agent just created via the Prophet UI."""
    if not polymarket_condition_id or not prophet_market_id:
        return CycleResult(
            status="blocked",
            reason="missing_ids",
            payload={
                "polymarket_condition_id": polymarket_condition_id,
                "prophet_market_id": prophet_market_id,
            },
        )
    try:
        target = _resolve_target(config)
    except Exception as exc:
        return CycleResult(
            status="blocked",
            reason="target_resolution_failed",
            payload={"error": str(exc)[:300]},
        )
    try:
        upsert_arb_pair(
            target=target,
            prophet_market_id=prophet_market_id,
            polymarket_condition_id=polymarket_condition_id,
            source_skill="auto_discover_ui",
        )
    except Exception as exc:
        return CycleResult(
            status="blocked",
            reason="persist_failed",
            payload={"error": f"{type(exc).__name__}:{str(exc)[:200]}"},
        )
    return CycleResult(
        status="ok",
        reason="pair_recorded",
        payload={
            "prophet_market_id": prophet_market_id,
            "polymarket_condition_id": polymarket_condition_id,
        },
    )


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
        choices=[
            "setup",
            "run",
            "status",
            "probe-schema",
            "record-created-market",
        ],
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
    parser.add_argument(
        "--polymarket-condition-id",
        default="",
        help="(record-created-market) Polymarket conditionId for the pair.",
    )
    parser.add_argument(
        "--prophet-market-id",
        default="",
        help="(record-created-market) Prophet market id the agent just created.",
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
    elif args.command == "record-created-market":
        result = cmd_record_created_market(
            config=config,
            polymarket_condition_id=args.polymarket_condition_id,
            prophet_market_id=args.prophet_market_id,
        )
    else:
        parser.error(f"unknown command: {args.command}")
        return 2

    return _emit(result, json_output=args.json_output)


if __name__ == "__main__":
    raise SystemExit(main())
