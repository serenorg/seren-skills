"""prophet-arb-bot agent — Mode A operator-arb runner.

Run a single arb cycle. Best-guess Prophet placeOrder mutation is in
`scripts/prophet/orders.py` (§3 ADR — fixture-validated in a follow-on PR).

Commands:
  --command setup           Apply SerenDB schema and seed arb_pairs from
                            inputs.manual_pairs. Idempotent.
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
    SeedIntent,
    assess_polymarket_depth,
    derive_seed_intent,
    hedge_filled_order,
    unwind_seed_hedge_after_prophet_decline,
)
from arbitrage.intelligence import IntelligenceConfig, assess_pair_health
from arbitrage.scoring import (
    Opportunity,
    PairPrices,
    ScoringConfig,
    score_batch,
)
from config_bootstrap import bootstrap_config_if_missing
from db import ResolvedTarget, get_target
from discovery import AutoDiscoverConfig, AutoDiscoverResult, run_auto_discover
from discovery.seed_qualifier import (
    QualifierDecision,
    qualify_and_trim_pending,
)
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
    list_arb_pairs,
    list_open_orders,
    list_recent_runs,
    upsert_arb_pair,
)
from funds_preflight import (
    evaluate_funds_preflight,
    evaluate_seed_funds_preflight,
    evaluate_two_venue_funds_preflight,
)
from arbitrage.hedge import hedge_seed_bet  # type: ignore  # re-export
from polymarket.prices import fetch_market_prices
from prophet import (
    ProphetClientError,
    ProphetGraphQLError,
    ProphetSchemaError,
    ProphetUnauthorized,
)
from prophet.client import MinimalProphetClient
from prophet.odds_session import OddsSessionTimeout
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
POLYMARKET_REQUIRED_ENV_VARS = (
    "POLY_PRIVATE_KEY",
    "POLY_API_KEY",
    "POLY_PASSPHRASE",
    "POLY_SECRET",
)


class PolymarketCredentialsMissing(RuntimeError):
    """Raised when delta-neutral execution cannot sign Polymarket legs."""

    def __init__(self, missing_env_vars: list[str]) -> None:
        self.missing_env_vars = missing_env_vars
        super().__init__(
            "missing Polymarket credentials: " + ", ".join(missing_env_vars)
        )


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

    if payload["pairs_seeded_manual"] == 0:
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

    cache = SessionCache()
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


def _missing_polymarket_env_vars() -> list[str]:
    return [name for name in POLYMARKET_REQUIRED_ENV_VARS if not os.environ.get(name)]


def _polymarket_creds_missing_result(missing_env_vars: list[str]) -> CycleResult:
    return CycleResult(
        status="blocked",
        reason="polymarket_creds_missing",
        payload={
            "payload": {
                "missing_env_vars": list(missing_env_vars),
                "action": "set_polymarket_credentials",
            }
        },
    )


def _hedger_init_failed_result(exc: Exception) -> CycleResult:
    return CycleResult(
        status="blocked",
        reason="hedger_init_failed",
        payload={"payload": {"error": f"{type(exc).__name__}:{str(exc)[:200]}"}},
    )


def _build_hedger(config: AgentConfig) -> Any:
    """Construct the live Polymarket hedger when delta-neutral is on.

    Importing `DirectClobTrader` lazily keeps the test suite from
    requiring `py-clob-client` for single-leg paths. Delta-neutral does
    not silently fall back to single-leg semantics: missing credentials
    raise a typed exception so the caller can return the exact blocked
    envelope.
    """
    from pathlib import Path

    missing = _missing_polymarket_env_vars()
    if missing:
        raise PolymarketCredentialsMissing(missing)

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


def _apply_seed_preflight_and_trim(
    *,
    pending: list[dict],
    initial_bet_usdc: float,
    delta_neutral: bool,
    live_hedger: Any,
    max_hedge_slippage_bps: float,
    transport: Any,
    jwt: str | None,
    gateway: Any,
    recorder: Any,
) -> CycleResult | None:
    """#542 Fix 2 — gate `pending_ui_submission` by what the operator
    can actually fund + hedge.

    Returns a blocked `CycleResult` when the operator can't fund a
    single seed (deposit_required envelope) and ``None`` otherwise.
    Side effect: stashes the trimmed list under
    ``recorder.summary["_trimmed_pending"]`` and records counts +
    drop reasons under public summary keys.
    """
    # Prophet balance — same path the trading-side preflight uses.
    balance_client = MinimalProphetClient(transport=transport)
    try:
        cash = balance_client.cash_balance(jwt=jwt)
        prophet_avail = float(cash.available_usdc)
    except Exception as exc:
        recorder.record_blocker(
            f"seed_preflight_cash_failed:{type(exc).__name__}:{str(exc)[:120]}"
        )
        # Conservative fallback — if we can't read the Prophet balance,
        # treat as 0 so we block rather than silently emit unfundable
        # entries. The operator can re-run after the transient lifts.
        prophet_avail = 0.0

    # Polymarket balance — only when delta-neutral and the live
    # hedger is wired in. In single-leg mode, no Polymarket spend.
    polymarket_avail: float
    if delta_neutral and live_hedger is not None:
        try:
            polymarket_avail = float(
                getattr(live_hedger, "_trader", live_hedger).get_cash_balance()
            )
        except Exception as exc:
            recorder.record_blocker(
                f"seed_preflight_polymarket_balance_failed:"
                f"{type(exc).__name__}:{str(exc)[:120]}"
            )
            polymarket_avail = 0.0
    else:
        # In single-leg mode the Polymarket side doesn't consume
        # capital at seed-time, so we mark it as effectively
        # unlimited (the funds preflight ignores the side that
        # isn't spending).
        polymarket_avail = float("inf")

    preflight = evaluate_seed_funds_preflight(
        candidate_count=len(pending),
        initial_bet_usdc=float(initial_bet_usdc),
        prophet_available_usdc=prophet_avail,
        polymarket_available_usdc=polymarket_avail,
    )

    recorder.summary["seed_preflight_max_fundable"] = preflight.max_fundable_count
    recorder.summary["seed_preflight_prophet_avail"] = preflight.prophet_available_usdc
    if delta_neutral:
        recorder.summary["seed_preflight_polymarket_avail"] = (
            preflight.polymarket_available_usdc
        )

    if not preflight.ok:
        recorder.record_blocker(
            f"funds_insufficient_for_seeds:"
            f"prophet_deficit={preflight.prophet_deficit_usdc}_usdc;"
            f"polymarket_deficit={preflight.polymarket_deficit_usdc}_usdc"
        )
        payload = recorder.finish("blocked", "funds_insufficient_for_seeds")
        payload["action"] = "deposit_required_for_seeds"
        payload["deposit"] = preflight.to_deposit_envelope()
        return CycleResult(
            status="blocked",
            reason="funds_insufficient_for_seeds",
            payload=payload,
        )

    # Build the depth assessor only in delta-neutral mode — single-leg
    # has no Polymarket hedge, so depth ineligibility doesn't apply.
    depth_assessor = None
    if delta_neutral and live_hedger is not None:
        def _assess(market_id: str, size_usdc: float, slippage_bps: float) -> bool:
            try:
                book = live_hedger.fetch_book(market_id)
            except Exception:
                return False
            verdict = assess_polymarket_depth(
                book_payload=book,
                target_size_usdc=size_usdc,
                # Seeds are buys on Prophet by default; the hedge
                # sells on Polymarket so we consume bids. Production
                # code can refine per-candidate when seed side varies.
                hedge_side="sell",
                max_slippage_bps=slippage_bps,
            )
            return bool(verdict.sufficient)

        depth_assessor = _assess

    decision = qualify_and_trim_pending(
        pending=pending,
        max_fundable_count=preflight.max_fundable_count,
        initial_bet_usdc=float(initial_bet_usdc),
        depth_assessor=depth_assessor,
        max_hedge_slippage_bps=float(max_hedge_slippage_bps),
    )

    recorder.summary["_trimmed_pending"] = decision.qualified
    recorder.summary["seed_dropped_count"] = len(decision.dropped)
    if decision.dropped:
        recorder.summary["seed_dropped_reasons"] = [
            d.get("reason", "unknown") for d in decision.dropped
        ]
    return None


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
    recorder.summary["execution_mode"] = config.execution_mode
    delta_neutral = config.execution_mode == EXECUTION_MODE_DELTA_NEUTRAL
    live_hedger: Any = hedger  # tests inject; runtime constructs lazily

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

            # #542 Fix 2 — seed preflight + trim. Cap the pending list
            # by what the operator can actually fund right now on BOTH
            # venues. This is intentionally independent of live_mode /
            # --yes-live so the pending list is never misleading.
            if pending_ui_submission:
                if delta_neutral and live_hedger is None:
                    try:
                        live_hedger = _build_hedger(config)
                    except PolymarketCredentialsMissing as exc:
                        return _polymarket_creds_missing_result(exc.missing_env_vars)
                    except Exception as exc:
                        return _hedger_init_failed_result(exc)
                seed_block = _apply_seed_preflight_and_trim(
                    pending=pending_ui_submission,
                    initial_bet_usdc=config.auto_discover.initial_bet_usdc,
                    delta_neutral=delta_neutral,
                    live_hedger=live_hedger,
                    max_hedge_slippage_bps=config.max_hedge_slippage_bps,
                    transport=transport,
                    jwt=jwt,
                    gateway=gateway,
                    recorder=recorder,
                )
                if seed_block is not None:
                    # Blocked — `pending_ui_submission` is empty until the
                    # operator funds. Return early with deposit envelope.
                    return seed_block
                pending_ui_submission = recorder.summary.get(
                    "_trimmed_pending", pending_ui_submission
                )

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
            except PolymarketCredentialsMissing as exc:
                return _polymarket_creds_missing_result(exc.missing_env_vars)
            except Exception as exc:
                return _hedger_init_failed_result(exc)
        elif hasattr(live_hedger, "bind_prophet_cancel"):
            live_hedger.bind_prophet_cancel(order_client, jwt)
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
# Compute-seed-intent (#548)
#
# After the agent clicks "Create Market" on Prophet's `/create` UI, the
# `startOddsCalculation` response carries an `OddsCalculationSession` id.
# Prophet's 6-model AI calc runs for 60–180s and the resulting
# `pricing.yesFairValueBps` is the sharpest signal we have for the
# per-market seed-side decision. The agent invokes this command between
# Create-Market and the Polymarket-first `record-created-market` call to
# get back a structured `{seed_side, hedge_side, hedge_price, edge_bps}`
# the per-market hedge needs.


def cmd_compute_seed_intent(
    *,
    config: AgentConfig,
    polymarket_condition_id: str,
    odds_session_id: str,
    polymarket_yes_price: float,
    transport: Any,
    jwt: str,
    poll: Any | None = None,
    fetch_book: Any | None = None,
    poll_interval_s: float = 2.0,
    poll_timeout_s: float = 180.0,
) -> CycleResult:
    """Run the per-market seed-side decision using Prophet's AI fair value.

    Returns a `seed_intent_ready` envelope on success and a structured
    blocked envelope (`odds_session_not_completed`,
    `prophet_market_not_viable`, `no_edge`, `polymarket_book_unavailable`)
    otherwise. Callers feed the success payload's `seed_side` and
    `hedge_price` into `record-created-market` for the
    Polymarket-first hedge submission.

    `poll` and `fetch_book` are injectable for tests; default wiring is
    bound lazily so this command can be parsed and called without
    importing the live polymarket transport.
    """
    if poll is None:
        from prophet.odds_session import poll_odds_session as poll
    if fetch_book is None:
        from polymarket_live import fetch_book as fetch_book  # type: ignore

    # #553 — wrap the poll's two typed faults so the agent never sees a
    # raw traceback. ProphetUnauthorized → JWT is stale, agent should
    # refresh and retry the same call. OddsSessionTimeout → Prophet's
    # AI calc never finished, agent should abandon the candidate.
    try:
        session = poll(
            transport,
            jwt=jwt,
            session_id=odds_session_id,
            interval_s=poll_interval_s,
            timeout_s=poll_timeout_s,
        )
    except ProphetUnauthorized as exc:
        return CycleResult(
            status="blocked",
            reason="prophet_unauthorized",
            payload={
                "polymarket_condition_id": polymarket_condition_id,
                "odds_session_id": odds_session_id,
                "action": "refresh_jwt",
                "error": f"ProphetUnauthorized:{str(exc)[:200]}",
            },
        )
    except OddsSessionTimeout as exc:
        return CycleResult(
            status="blocked",
            reason="odds_session_timeout",
            payload={
                "polymarket_condition_id": polymarket_condition_id,
                "odds_session_id": odds_session_id,
                "timeout_s": float(poll_timeout_s),
                "error": f"OddsSessionTimeout:{str(exc)[:200]}",
            },
        )

    base_payload: dict[str, Any] = {
        "session_id": session.id,
        "session_status": session.status,
        "polymarket_condition_id": polymarket_condition_id,
    }

    if session.status != "COMPLETED" or session.pricing is None:
        return CycleResult(
            status="blocked",
            reason="odds_session_not_completed",
            payload={
                **base_payload,
                "rejection_reason": session.rejection_reason,
                "completed_models": session.completed_models,
                "total_models": session.total_models,
            },
        )

    pricing = session.pricing
    if not pricing.is_viable:
        prophet_pct = pricing.yes_fair_value_bps / 100.0
        return CycleResult(
            status="blocked",
            reason="prophet_market_not_viable",
            payload={
                **base_payload,
                "is_viable": False,
                "prophet_fair_value_bps": pricing.yes_fair_value_bps,
                "confidence_bps": pricing.confidence_bps,
                "edge_summary": (
                    f"Prophet fair value {prophet_pct:.1f}% but isViable=false"
                ),
            },
        )

    try:
        book_payload = fetch_book(polymarket_condition_id)
    except Exception as exc:
        return CycleResult(
            status="blocked",
            reason="polymarket_book_unavailable",
            payload={
                **base_payload,
                "is_viable": True,
                "prophet_fair_value_bps": pricing.yes_fair_value_bps,
                "polymarket_yes_price": float(polymarket_yes_price),
                "error": f"{type(exc).__name__}:{str(exc)[:200]}",
            },
        )

    # #551 Fix 1 — auto-derive YES price from book midpoint when omitted.
    # Caller passes 0.0 (CLI default) to delegate price discovery here.
    resolved_poly_yes, price_source = _resolve_polymarket_yes_price(
        polymarket_yes_price=polymarket_yes_price,
        book_payload=book_payload,
    )
    if resolved_poly_yes <= 0.0:
        return CycleResult(
            status="blocked",
            reason="polymarket_book_unavailable",
            payload={
                **base_payload,
                "is_viable": True,
                "prophet_fair_value_bps": pricing.yes_fair_value_bps,
                "polymarket_yes_price": 0.0,
                "polymarket_yes_price_source": price_source,
                "error": "empty_book_midpoint_unavailable",
            },
        )

    intent = derive_seed_intent(
        prophet_fair_value_bps=pricing.yes_fair_value_bps,
        polymarket_yes_price=resolved_poly_yes,
        book_payload=book_payload,
    )
    prophet_pct = pricing.yes_fair_value_bps / 100.0
    poly_pct = resolved_poly_yes * 100.0
    if intent is None:
        # Either zero edge or the side we need has no book depth. Surface
        # both so the agent's log carries the actionable explanation.
        edge_gap_bps = abs(prophet_pct * 100.0 - poly_pct * 100.0)
        return CycleResult(
            status="blocked",
            reason="no_edge",
            payload={
                **base_payload,
                "is_viable": True,
                "prophet_fair_value_bps": pricing.yes_fair_value_bps,
                "polymarket_yes_price": resolved_poly_yes,
                "polymarket_yes_price_source": price_source,
                "best_bid": float(book_payload.get("best_bid") or 0.0),
                "best_ask": float(book_payload.get("best_ask") or 0.0),
                "edge_summary": (
                    f"Prophet {prophet_pct:.1f}% vs Polymarket {poly_pct:.1f}% "
                    f"→ {edge_gap_bps:.0f} bps no_edge"
                ),
            },
        )

    direction = "BUY YES on Prophet" if intent.seed_side == "buy" else "SELL YES on Prophet"
    return CycleResult(
        status="ok",
        reason="seed_intent_ready",
        payload={
            **base_payload,
            "is_viable": True,
            "seed_side": intent.seed_side,
            "hedge_side": intent.hedge_side,
            "hedge_price": intent.hedge_price,
            "tick_size": intent.tick_size,
            "edge_bps": intent.edge_bps,
            "prophet_fair_value_bps": pricing.yes_fair_value_bps,
            "polymarket_yes_price": resolved_poly_yes,
            "polymarket_yes_price_source": price_source,
            "confidence_bps": pricing.confidence_bps,
            "best_bid": float(book_payload.get("best_bid") or 0.0),
            "best_ask": float(book_payload.get("best_ask") or 0.0),
            "edge_summary": (
                f"Prophet {prophet_pct:.1f}% vs Polymarket {poly_pct:.1f}% "
                f"→ {intent.edge_bps:.0f} bps edge ({direction})"
            ),
        },
    )


def _resolve_polymarket_yes_price(
    *,
    polymarket_yes_price: float,
    book_payload: dict[str, Any],
) -> tuple[float, str]:
    """Return ``(resolved_price, source)`` for the YES price the seed-intent
    derivation should use.

    Operator-supplied values (> 0) win. Otherwise derive from the book
    midpoint, or fall back to whichever side of the book has liquidity.
    Returns ``(0.0, "empty_book")`` when neither bid nor ask is usable —
    the caller surfaces this as ``polymarket_book_unavailable``.
    """
    explicit = float(polymarket_yes_price)
    if explicit > 0.0:
        return explicit, "caller_supplied"
    best_bid = float(book_payload.get("best_bid") or 0.0)
    best_ask = float(book_payload.get("best_ask") or 0.0)
    if best_bid > 0.0 and best_ask > 0.0:
        return (best_bid + best_ask) / 2.0, "book_midpoint"
    if best_bid > 0.0:
        return best_bid, "book_best_bid_only"
    if best_ask > 0.0:
        return best_ask, "book_best_ask_only"
    return 0.0, "empty_book"


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
    prophet_seed_side: str = "",
    polymarket_marketable_price: float = 0.0,
    seed_size_usdc: float | None = None,
    prophet_confirm_declined: bool = False,
) -> CycleResult:
    """Handle one agent-driven Prophet `/create` result.

    Delta-neutral seed creation is Polymarket-first:

    1. Call this command *before* Prophet Confirm with a condition id,
       seed side, and marketable price but no prophet_market_id. It
       submits the Polymarket hedge and returns either
       ``hedge_status='hedged'`` (agent may click Confirm) or
       ``hedge_status='hedge_failed_no_commit'`` (agent must abort).
    2. If Prophet Confirm succeeds, call again with the captured
       prophet_market_id to persist the pair.
    3. If Prophet Confirm fails/declines after a successful hedge, call
       with ``prophet_confirm_declined=True`` to reverse the Polymarket
       hedge.
    """
    if not polymarket_condition_id:
        return CycleResult(
            status="blocked",
            reason="missing_ids",
            payload={
                "polymarket_condition_id": polymarket_condition_id,
                "prophet_market_id": prophet_market_id,
            },
        )
    payload: dict[str, Any] = {
        "prophet_market_id": prophet_market_id,
        "polymarket_condition_id": polymarket_condition_id,
    }

    delta_neutral = config.execution_mode == EXECUTION_MODE_DELTA_NEUTRAL
    wants_seed_action = (
        delta_neutral and prophet_seed_side and polymarket_marketable_price > 0
    )
    size = float(seed_size_usdc) if seed_size_usdc is not None else float(
        config.auto_discover.initial_bet_usdc
    )

    if prophet_confirm_declined:
        if not wants_seed_action:
            return CycleResult(
                status="blocked",
                reason="missing_seed_hedge_inputs",
                payload=payload,
            )
        try:
            hedger = _build_hedger(config)
        except PolymarketCredentialsMissing as exc:
            return _polymarket_creds_missing_result(exc.missing_env_vars)
        except Exception as exc:
            return _hedger_init_failed_result(exc)

        outcome = unwind_seed_hedge_after_prophet_decline(
            polymarket_condition_id=polymarket_condition_id,
            prophet_seed_side=prophet_seed_side,
            size_usdc=size,
            marketable_price=float(polymarket_marketable_price),
            hedger=hedger,
        )
        payload["hedge_status"] = outcome.hedge_status
        payload["polymarket_order_id"] = outcome.polymarket_order_id
        payload["polymarket_filled_qty"] = outcome.polymarket_filled_qty
        payload["polymarket_fill_price"] = outcome.polymarket_fill_price
        if outcome.error:
            payload["hedge_error"] = outcome.error
        return CycleResult(
            status=(
                "ok"
                if outcome.hedge_status == "unwound_after_prophet_decline"
                else "blocked"
            ),
            reason="prophet_confirm_declined",
            payload=payload,
        )

    if wants_seed_action and not prophet_market_id:
        try:
            hedger = _build_hedger(config)
        except PolymarketCredentialsMissing as exc:
            return _polymarket_creds_missing_result(exc.missing_env_vars)
        except Exception as exc:
            return _hedger_init_failed_result(exc)

        outcome = hedge_seed_bet(
            prophet_market_id="",
            polymarket_condition_id=polymarket_condition_id,
            prophet_seed_side=prophet_seed_side,
            size_usdc=size,
            marketable_price=float(polymarket_marketable_price),
            hedger=hedger,
        )
        payload["hedge_status"] = outcome.hedge_status
        payload["polymarket_order_id"] = outcome.polymarket_order_id
        payload["polymarket_filled_qty"] = outcome.polymarket_filled_qty
        payload["polymarket_fill_price"] = outcome.polymarket_fill_price
        if outcome.error:
            payload["hedge_error"] = outcome.error
        if outcome.hedge_status != "hedged":
            return CycleResult(
                status="blocked",
                reason="seed_hedge_failed_no_commit",
                payload=payload,
            )
        payload["next_action"] = "click_prophet_confirm"
        return CycleResult(
            status="ok",
            reason="seed_hedge_ready_for_prophet_confirm",
            payload=payload,
        )

    if not prophet_market_id:
        return CycleResult(
            status="blocked",
            reason="missing_ids",
            payload=payload,
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
        payload=payload,
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
            "compute-seed-intent",
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
    parser.add_argument(
        "--prophet-email",
        default="",
        help=(
            "(setup) Persisted to inputs.prophet_email on first-run "
            "bootstrap. Ignored if config.json already exists."
        ),
    )
    parser.add_argument(
        "--email-provider",
        default="",
        choices=["", "gmail", "outlook"],
        help=(
            "(setup) Persisted to inputs.email_provider on first-run "
            "bootstrap. Ignored if config.json already exists."
        ),
    )
    parser.add_argument(
        "--prophet-seed-side",
        default="",
        choices=["", "buy", "sell"],
        help=(
            "(record-created-market) Operator side on the Prophet seed bet. "
            "Required to dispatch the delta-neutral hedge in delta_neutral mode."
        ),
    )
    parser.add_argument(
        "--polymarket-marketable-price",
        default=0.0,
        type=float,
        help=(
            "(record-created-market) Marketable Polymarket price for the seed "
            "hedge. Required in delta_neutral mode."
        ),
    )
    parser.add_argument(
        "--seed-size-usdc",
        default=None,
        type=float,
        help=(
            "(record-created-market) Seed notional to hedge/unwind. "
            "Defaults to auto_discover.initial_bet_usdc."
        ),
    )
    parser.add_argument(
        "--prophet-confirm-declined",
        action="store_true",
        help=(
            "(record-created-market) Prophet Confirm failed or was declined "
            "after the Polymarket seed hedge filled; unwind the Polymarket leg."
        ),
    )
    parser.add_argument(
        "--odds-session-id",
        default="",
        help=(
            "(compute-seed-intent) Prophet OddsCalculationSession id from "
            "the startOddsCalculation response. Required."
        ),
    )
    parser.add_argument(
        "--polymarket-yes-price",
        default=0.0,
        type=float,
        help=(
            "(compute-seed-intent) Current Polymarket YES price. Optional — "
            "omit (or pass 0.0) to auto-derive from the book midpoint (#551)."
        ),
    )
    parser.add_argument(
        "--poll-interval-s",
        default=2.0,
        type=float,
        help="(compute-seed-intent) Seconds between odds-session polls.",
    )
    parser.add_argument(
        "--poll-timeout-s",
        default=180.0,
        type=float,
        help="(compute-seed-intent) Total seconds to wait for COMPLETED.",
    )
    args = parser.parse_args(argv)

    if args.command == "probe-schema":
        from prophet.schema_probe import main as probe_main  # type: ignore

        # Pass an explicit empty argv so the probe's argparse does not
        # re-parse the parent agent's --config / --command flags.
        return probe_main([])

    # #542 Fix 1 — zero-friction first-run. If config.json is absent,
    # copy from config.example.json and persist optional flags. Existing
    # configs are never overwritten.
    skill_root = SCRIPT_DIR.parent
    bootstrap_config_if_missing(
        config_path=args.config,
        example_path=str(skill_root / "config.example.json"),
        prophet_email=args.prophet_email or None,
        email_provider=args.email_provider or None,
    )

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
            prophet_seed_side=args.prophet_seed_side,
            polymarket_marketable_price=args.polymarket_marketable_price,
            seed_size_usdc=args.seed_size_usdc,
            prophet_confirm_declined=args.prophet_confirm_declined,
        )
    elif args.command == "compute-seed-intent":
        jwt = os.environ.get("PROPHET_SESSION_TOKEN", "")
        if not jwt:
            result = CycleResult(
                status="blocked",
                reason="missing_session_token",
                payload={
                    "action": "export_PROPHET_SESSION_TOKEN",
                },
            )
        elif not args.odds_session_id or not args.polymarket_condition_id:
            result = CycleResult(
                status="blocked",
                reason="missing_required_args",
                payload={
                    "odds_session_id": args.odds_session_id,
                    "polymarket_condition_id": args.polymarket_condition_id,
                    "polymarket_yes_price": args.polymarket_yes_price,
                },
            )
        else:
            result = cmd_compute_seed_intent(
                config=config,
                polymarket_condition_id=args.polymarket_condition_id,
                odds_session_id=args.odds_session_id,
                polymarket_yes_price=args.polymarket_yes_price,
                transport=transport,
                jwt=jwt,
                poll_interval_s=args.poll_interval_s,
                poll_timeout_s=args.poll_timeout_s,
            )
    else:
        parser.error(f"unknown command: {args.command}")
        return 2

    return _emit(result, json_output=args.json_output)


if __name__ == "__main__":
    raise SystemExit(main())
