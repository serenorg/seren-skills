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
import time
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
from otp_worker.establish_session import (
    SessionEstablishmentFailed,
    establish_browser_session_for_create,
)
from otp_worker.playwright_client import RealBrowserSession
from otp_worker import playwright_mcp_gateway as _playwright_mcp_gateway
from otp_worker.playwright_mcp_gateway import (
    PRIVY_COMPATIBLE_ENV,
    PlaywrightMcpUnavailable,
    PlaywrightStealthGateway,
)
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
    evaluate_seed_funds_preflight,
    evaluate_two_venue_funds_preflight,
)
from polymarket_state import classify_polymarket_collateral_state
from seed_preflight_orchestration import resolve_seed_preflight_action
from arbitrage.hedge import hedge_seed_bet  # type: ignore  # re-export
from polymarket.prices import fetch_market_price, fetch_market_prices
from prophet import (
    ProphetClientError,
    ProphetGraphQLError,
    ProphetSchemaError,
    ProphetUnauthorized,
)
from prophet.client import MinimalProphetClient
from prophet.odds_session import OddsSessionTimeout
from prophet.orders import ProphetOrder, ProphetOrderClient
from progress import ProgressEmitter
from seren_cron_client import HttpGateway

DEFAULT_CONFIG_PATH = "config.json"
SKILL_SLUG = "prophet-arb-bot"
SCHEMA_PATH = SCRIPT_DIR.parent / "serendb_schema.sql"


# ---------------------------------------------------------------------------
# Config


# Issue #591: `single_leg` execution mode was removed. The arb-bot is
# delta-neutral by design — every Prophet leg must be hedged on
# Polymarket — and naked Prophet exposure (the old single_leg path) is
# directional speculation, not arbitrage. Configs that explicitly set
# `execution_mode: "single_leg"` are now rejected with a clear
# deprecation error in `AgentConfig.load` so legacy operators are
# forced to acknowledge the change rather than silently get rewritten.
EXECUTION_MODE_DELTA_NEUTRAL = "delta_neutral"
_VALID_EXECUTION_MODES = {EXECUTION_MODE_DELTA_NEUTRAL}
_REMOVED_EXECUTION_MODES = {"single_leg"}
POLYMARKET_REQUIRED_ENV_VARS = (
    "POLY_PRIVATE_KEY",
    "POLY_API_KEY",
    "POLY_PASSPHRASE",
    "POLY_SECRET",
)
_PLACEHOLDER_PROPHET_EMAILS = {
    "you@example.com",
    "your-email@example.com",
    "your.email@example.com",
    "you@your-domain.com",
}
_RESERVED_EXAMPLE_DOMAINS = {
    "example.com",
    "example.net",
    "example.org",
}
OTP_BROWSER_UNAVAILABLE_REASON = (
    "blocked_otp_browser_unavailable:"
    "seren_desktop_playwright_mcp_unavailable"
)


class PolymarketCredentialsMissing(RuntimeError):
    """Raised when delta-neutral execution cannot sign Polymarket legs."""

    def __init__(self, missing_env_vars: list[str]) -> None:
        self.missing_env_vars = missing_env_vars
        super().__init__(
            "missing Polymarket credentials: " + ", ".join(missing_env_vars)
        )


def _prophet_email_block_reason(email: str | None) -> str | None:
    normalized = (email or "").strip().lower()
    if not normalized:
        return "blocked_otp_email_missing"
    if normalized in _PLACEHOLDER_PROPHET_EMAILS:
        return "blocked_otp_email_placeholder"
    if "@" not in normalized:
        return "blocked_otp_email_invalid"
    local, domain = normalized.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        return "blocked_otp_email_invalid"
    if domain in _RESERVED_EXAMPLE_DOMAINS:
        return "blocked_otp_email_placeholder"
    return None


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
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AgentConfig":
        """Build an ``AgentConfig`` from a raw dict.

        Separated from ``load`` so the parsing + defaults logic is
        testable without writing a tempfile. ``load`` delegates here.
        """
        inputs = raw.get("inputs") or {}
        storage_raw = raw.get("storage") or {}
        scoring_raw = raw.get("scoring") or {}
        intel_raw = raw.get("intelligence") or {}
        auto_raw = raw.get("auto_discover") or {}
        execution_mode = str(
            raw.get("execution_mode") or EXECUTION_MODE_DELTA_NEUTRAL
        ).strip().lower()
        if execution_mode in _REMOVED_EXECUTION_MODES:
            # #591: refuse the legacy single_leg path. The operator must
            # explicitly migrate — either delete the field (default is
            # delta_neutral) or set it to delta_neutral.
            raise ValueError(
                f"execution_mode={execution_mode!r} is removed as of "
                f"prophet-arb-bot/#591. The arb-bot is delta-neutral only — "
                f"every Prophet leg is hedged on Polymarket. Delete the "
                f"`execution_mode` field from config.json or set it to "
                f"{EXECUTION_MODE_DELTA_NEUTRAL!r}."
            )
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
            # #633 — default tightened from 200 (2%) to 100 (1%). 2% on
            # a thin Prophet book eats most of the 3¢ min-spread floor
            # before the position is even held. Existing operator
            # configs that explicitly set 200.0 are not silently
            # mutated; only new operators picking up the default get
            # the tighter cap.
            max_hedge_slippage_bps=float(raw.get("max_hedge_slippage_bps", 100.0)),
        )


@dataclass
class CycleResult:
    status: str
    reason: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, **self.payload}


def _annotate_entry_result_with_warm_health(
    sub: CycleResult, *, warm_unhealthy: bool
) -> CycleResult:
    """Preserve the inner driver's `sub` reason; annotate warm health separately.

    Issue #672: the per-entry create-market loop used to overwrite `sub`
    with a fresh `CycleResult(reason="warm_context_corrupted")` whenever
    the post-entry health check observed that the warm Playwright context
    had lost observable Prophet auth. That clobbered every real upstream
    reason — success (`pair_created`), seed-calc rejection (`no_edge`),
    schema regressions (`ocs_session_id_not_captured`), hedge-leg failures
    (`hedge_failed_no_commit`), exception captures
    (`create_market_via_ui_unexpected` carrying `payload.error`) — and
    made `/create` failures undiagnosable.

    Now the inner sub is preserved verbatim. When the warm context is
    unhealthy we set `payload.warm_unhealthy_post_entry=True` as a
    supplemental signal, so operators can still see when the reopen was
    triggered — but the entry's primary `reason` is whatever the inner
    driver actually reported.
    """
    if not warm_unhealthy:
        return sub
    new_payload: dict[str, Any] = dict(sub.payload or {})
    new_payload["warm_unhealthy_post_entry"] = True
    return CycleResult(status=sub.status, reason=sub.reason, payload=new_payload)


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

    email_block_reason = _prophet_email_block_reason(
        config.inputs.get("prophet_email")
    )
    if email_block_reason == "blocked_otp_email_missing":
        payload["warnings"].append("inputs.prophet_email is empty")
    elif email_block_reason == "blocked_otp_email_placeholder":
        payload["warnings"].append(
            "inputs.prophet_email is still an example placeholder"
        )
    elif email_block_reason == "blocked_otp_email_invalid":
        payload["warnings"].append("inputs.prophet_email is not a valid email")
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
    email_block_reason = _prophet_email_block_reason(email)
    if email_block_reason is not None:
        return None, None, email_block_reason
    if provider not in ("gmail", "outlook"):
        return None, None, "blocked_otp_email_missing"

    # Issue #580: cold-start needs Playwright MCP. The publisher-side
    # `gateway` (HttpGateway) carries no MCP attributes, so the bundled
    # `playwright-stealth` MCP server has to be spawned as a stdio
    # subprocess. If neither the bundled binary nor an
    # `SEREN_PLAYWRIGHT_MCP_COMMAND` override is reachable, fail closed
    # with a structured reason instead of raising RuntimeError deep in
    # `_resolve_mcp_callable`.
    if (
        _playwright_mcp_gateway.PlaywrightStealthGateway._resolve_default_command()
        is None
    ):
        return (
            None,
            None,
            OTP_BROWSER_UNAVAILABLE_REASON,
        )

    facade = AuthFacade(cache=cache)
    try:
        # Issue #683: Privy provisions the embedded wallet *during* the OTP
        # login redirect (Privy is Prophet's auth provider, not a post-auth
        # step), so the OTP cold-start gateway needs the same env profile
        # the /create gateways got in #682. Until
        # serenorg/seren-desktop#1958 flips the bundled MCP's defaults to
        # Privy-compatible, this wiring is the only thing that lets a
        # fresh-cache cycle complete OTP without blocking on
        # `OtpEmailTimeout: privy:connections did not appear`. Once #1958
        # ships, this becomes a no-op (same as default).
        with PlaywrightStealthGateway(
            env_overrides=PRIVY_COMPATIBLE_ENV,
        ) as pw_gateway, RealBrowserSession(
            gateway=pw_gateway
        ) as session:
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
    except PlaywrightMcpUnavailable:
        return (
            None,
            None,
            OTP_BROWSER_UNAVAILABLE_REASON,
        )
    except (
        OtpEmailTimeout,
        EmailPublisherUnavailable,
        PrivyAuthFailed,
        IdentityMismatch,
    ) as exc:
        # Issue #571: keep the exception message in the envelope so the
        # operator can self-diagnose without re-running with a debugger.
        return None, None, _format_auth_failure_reason("blocked_otp", exc)
    except Exception as exc:
        return None, None, _format_auth_failure_reason(
            "blocked_auth_unexpected", exc
        )


def _format_auth_failure_reason(prefix: str, exc: BaseException) -> str:
    """Build a `<prefix>:<ExcType>[:<message>]` reason string.

    The exception message is truncated to 200 chars to bound the cron
    runner's JSON envelope size. Internal whitespace is collapsed so a
    multi-line traceback string can't break the reason field.
    """
    detail = " ".join(str(exc).split())[:200]
    if not detail:
        return f"{prefix}:{type(exc).__name__}"
    return f"{prefix}:{type(exc).__name__}:{detail}"


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

        def fetch_book(self, token_id: str) -> dict[str, Any]:
            # #631: `fetch_book` keys on Polymarket token_id (uint256
            # decimal), NOT condition_id (hex). Param name is `token_id`
            # so the type confusion that broke every delta-neutral cycle
            # pre-fix is structurally impossible — callers can only
            # arrive here with a value they explicitly labeled token_id.
            return fetch_book(token_id)

        def submit_hedge(
            self,
            *,
            token_id: str,
            hedge_side: str,
            size_usdc: float,
            marketable_price: float,
        ) -> dict[str, Any]:
            # #631: `token_id` is the uint256-decimal YES outcome
            # token_id. The CLOB's `create_order(token_id=...)` and
            # `/book?token_id=` both require this form; condition_id is
            # rejected silently. The Hedger protocol enforces the name.
            book = fetch_book(token_id)
            from polymarket_live import (
                fetch_fee_rate_bps,
                snap_price,
                safe_str,
            )
            tick_size = safe_str(book.get("tick_size"), "0.01")
            price = snap_price(marketable_price, tick_size, hedge_side.upper())
            neg_risk = bool(book.get("neg_risk", False))
            fee_bps = fetch_fee_rate_bps(token_id)
            # Convert USDC notional to share count at the marketable
            # price. Polymarket's `create_order` takes `size` in shares.
            shares = size_usdc / max(price, 1e-6)
            response = self._trader.create_order(
                token_id=token_id,
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


def _annotate_polymarket_state(
    *,
    deposit_envelope: dict,
    polymarket_avail_usdc: float,
    live_hedger: Any,
    recorder: Any,
) -> None:
    """#592 phase 1 — annotate a blocked-funds envelope with the
    Polymarket collateral state classification.

    The CLOB reports `balance: 0` whenever the address either has no
    USDC.e on-chain OR holds USDC.e without the approvals required to
    spend it. Without this annotation, the operator gets sent to
    deposit funds that may already exist on the wallet (which is the
    Issue #592 footgun).

    No-op when there's no live hedger (dry-run cycles can't read
    on-chain state) or when the trader has no resolved address.
    Failures in the on-chain probe are silently treated as
    `on_chain_usdc_e=None`, which collapses to the conservative
    `no_balance` classification — never mis-diagnoses as
    `no_approvals` without evidence.
    """
    if live_hedger is None:
        return
    trader = getattr(live_hedger, "_trader", live_hedger)
    trader_address = getattr(trader, "address", "") or ""
    if not trader_address:
        return
    on_chain_balance: float | None
    try:
        from polymarket_live import fetch_on_chain_usdc_e_balance
        on_chain_balance = fetch_on_chain_usdc_e_balance(trader_address)
    except Exception:
        on_chain_balance = None
    state = classify_polymarket_collateral_state(
        clob_balance_usdc=polymarket_avail_usdc,
        on_chain_usdc_e=on_chain_balance,
    )
    deposit_envelope["polymarket_state"] = state.kind
    deposit_envelope["polymarket_state_remediation"] = state.remediation
    deposit_envelope["polymarket_on_chain_usdc_e"] = state.on_chain_usdc_e
    recorder.record_blocker(f"polymarket_state:{state.kind}")

    # #596 — when we diagnosed `no_approvals` and the live hedger is
    # active (the same precondition the trade-signing path accepts),
    # broadcast approve()/setApprovalForAll() to the pinned Polymarket
    # spenders. The defense surface is the encoder, not a separate
    # opt-in flag: `_check_pinned_spender_or_raise` refuses any spender
    # outside `_PINNED_POLYMARKET_SPENDERS`. Re-invocation is safe —
    # `broadcast_pinned_polymarket_approvals` is idempotent.
    from polymarket_state import POLYMARKET_STATE_NO_APPROVALS
    if state.kind == POLYMARKET_STATE_NO_APPROVALS:
        from polymarket_live import auto_approve_missing_polymarket_allowances
        import os
        private_key = (os.getenv("POLY_PRIVATE_KEY") or os.getenv("WALLET_PRIVATE_KEY") or "").strip()
        result = auto_approve_missing_polymarket_allowances(
            wallet_address=trader_address,
            private_key=private_key,
        )
        deposit_envelope["polymarket_auto_approve"] = result
        recorder.record_blocker(f"polymarket_auto_approve:{result.get('status')}")

    # #605 — V2 onboarding pipeline. Post-2026-04-28 Polymarket users
    # need a Safe proxy + pUSD collateral; the V1 EOA-direct approve
    # path above is a no-op for wallets that have never used the
    # Polymarket UI to onboard. The orchestrator is idempotent — for
    # already-onboarded wallets it costs ~6 read-only RPC calls and
    # returns `skipped_already_onboarded` without signing anything.
    # Same live gate as #596: a live hedger is already wired in here,
    # which implies live_mode=true + --yes-live + delta_neutral. Pinned-
    # target defense-in-depth lives in
    # `polymarket_v2._check_v2_pinned_target_or_raise`.
    try:
        import os as _os
        from polymarket_v2_broadcast import (
            onboard_polymarket_v2,
            fetch_proxy_address_for_eoa,
            compute_wrap_all_target_usdc_e_raw,
        )
        v2_private_key = (
            _os.getenv("POLY_PRIVATE_KEY") or _os.getenv("WALLET_PRIVATE_KEY") or ""
        ).strip()
        if v2_private_key:
            # #620: target = proxy_pusd + proxy_usdc_e + eoa_usdc_e so the
            # orchestrator transfers ALL EOA USDC.e and wraps it to pUSD on
            # the proxy. The legacy `polymarket_avail_usdc * 10**6` fallback
            # locked target at the 1-USDC floor because CLOB collateral is
            # 0 until a deposit is credited — stranding operator funds and
            # leaving auto-discover seed-preflight blocked at
            # `polymarket_deficit=50.0_usdc`. Idempotency is preserved:
            # subsequent cycles with no new EOA funding produce a target
            # equal to current proxy collateral, which the orchestrator's
            # skip conditions treat as already-onboarded.
            proxy_addr_for_target = fetch_proxy_address_for_eoa(
                eoa_address=trader_address,
            )
            target_raw = compute_wrap_all_target_usdc_e_raw(
                eoa_address=trader_address,
                proxy_address=proxy_addr_for_target,
            )
            v2_result = onboard_polymarket_v2(
                eoa_address=trader_address,
                eoa_private_key=v2_private_key,
                target_usdc_e_raw=target_raw,
            )
            deposit_envelope["polymarket_v2_onboarding"] = v2_result
            recorder.record_blocker(f"polymarket_v2_onboarding:{v2_result.get('status')}")
    except Exception as exc:  # noqa: BLE001 — never crash the cycle on onboarding probe
        # #613: include the actual message, not just the class name.
        # Without it, the next operator hits the same wall — they see
        # `exception:TypeError` and no way to decide whether to file a
        # ticket, retry, or wait. Cap length so a runaway repr can't bloat
        # the run envelope or the cron's execution_results row.
        detail = str(exc).strip().replace("\n", " ")[:200] or "no_detail"
        deposit_envelope["polymarket_v2_onboarding"] = {
            "status": "failed",
            "error": f"orchestrator_exception:{type(exc).__name__}:{detail}",
        }
        recorder.record_blocker(
            f"polymarket_v2_onboarding:exception:{type(exc).__name__}:{detail}"
        )


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
    existing_pairs_count: int,
) -> CycleResult | None:
    """#542 Fix 2 — gate `pending_ui_submission` by what the operator
    can actually fund + hedge.

    Returns a blocked `CycleResult` when the operator can't fund a
    single seed AND has no existing paired arb opportunities to trade
    (deposit_required envelope), and ``None`` otherwise. Side effect:
    stashes the trimmed list under ``recorder.summary["_trimmed_pending"]``
    and records counts + drop reasons under public summary keys.

    Issue #589: when seed funding is exhausted but existing pairs are
    present, drop the pending list to empty and continue scoring —
    the existing pairs require zero seed funding and are the actual
    arb opportunities the bot trades.
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

    # Polymarket balance — only when the live hedger is wired in.
    # In dry-run cycles (`--yes-live` absent) we don't construct the
    # hedger, so the preflight treats Polymarket as effectively
    # unlimited and lets the qualifier focus on the Prophet bottleneck.
    # Issue #591 collapsed the historical single_leg branch — this
    # path now strictly serves dry-run mode.
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
        # No live hedger (dry-run or hedger init blocked) — the seed
        # preflight cannot estimate Polymarket bandwidth, so mark it
        # unlimited and let downstream depth/funds checks block at
        # trade-time if the hedge can't materialize.
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

    # #589: route the not-ok preflight through the orchestration helper
    # so existing paired arb opportunities are NOT short-circuited when
    # only the seed funding (for new pending UI submissions) is exhausted.
    action = resolve_seed_preflight_action(
        preflight=preflight,
        existing_pairs_count=existing_pairs_count,
    )
    if action.summary_blocker:
        recorder.record_blocker(action.summary_blocker)
    if action.should_block:
        payload = recorder.finish("blocked", action.block_reason or "funds_insufficient_for_seeds")
        payload["action"] = "deposit_required_for_seeds"
        deposit_envelope = action.deposit_envelope or preflight.to_deposit_envelope()
        if preflight.polymarket_deficit_usdc > 0.0:
            _annotate_polymarket_state(
                deposit_envelope=deposit_envelope,
                polymarket_avail_usdc=preflight.polymarket_available_usdc,
                live_hedger=live_hedger,
                recorder=recorder,
            )
        payload["deposit"] = deposit_envelope
        return CycleResult(
            status="blocked",
            reason=action.block_reason or "funds_insufficient_for_seeds",
            payload=payload,
        )
    if action.trimmed_pending_ui_submission is not None:
        # #589: zero fundable + existing pairs → drop pending and continue.
        # Skip the qualifier (nothing to qualify) and return early so the
        # caller falls through to the scoring loop.
        recorder.summary["_trimmed_pending"] = action.trimmed_pending_ui_submission
        recorder.summary["auto_discover_pending_ui_after_seed_preflight"] = len(
            action.trimmed_pending_ui_submission
        )
        recorder.summary["seed_dropped_count"] = len(pending)
        recorder.summary["seed_dropped_reasons"] = ["seed_preflight_skipped"] * len(pending)
        # #598: the skip branch used to bypass `_annotate_polymarket_state`,
        # which meant the #596 auto-approve broadcast never fired for
        # operators whose Polymarket wallet was stuck in `no_approvals`.
        # Mirror the gating used in the should_block branch above so the
        # diagnostic (and the idempotent auto-approve) run on every cycle
        # that observes a Polymarket-side deficit.
        if preflight.polymarket_deficit_usdc > 0.0:
            diagnostic_envelope: dict[str, Any] = {}
            _annotate_polymarket_state(
                deposit_envelope=diagnostic_envelope,
                polymarket_avail_usdc=preflight.polymarket_available_usdc,
                live_hedger=live_hedger,
                recorder=recorder,
            )
            if diagnostic_envelope:
                recorder.summary["polymarket_state_diagnostic"] = diagnostic_envelope
        return None

    # Build the depth assessor for the qualifier. Skipped in dry-run when
    # no live hedger is wired in (no hedge eligibility to check).
    depth_assessor = None
    if delta_neutral and live_hedger is not None:
        # #631: param is `token_id` (uint256 decimal) not `market_id`.
        # The qualifier reads `polymarket_yes_token_id` from each
        # pending entry and feeds it here. Polymarket CLOB's
        # `/book?token_id=` requires the uint256-decimal form.
        def _assess(token_id: str, size_usdc: float, slippage_bps: float) -> bool:
            try:
                book = live_hedger.fetch_book(token_id)
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
    recorder.summary["auto_discover_pending_ui_after_seed_preflight"] = len(
        decision.qualified
    )
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
    skip_ui_submission: bool = False,
    create_market_via_ui: Any | None = None,
    progress: ProgressEmitter | None = None,
) -> CycleResult:
    if transport is None:
        from prophet.transport import ProphetDirectTransport

        transport = ProphetDirectTransport()
    # Issue #640: progress stream. Emitter is never None during a real
    # cycle — tests inject a stub if they want to assert on stages.
    if progress is None:
        progress = ProgressEmitter()
        # Issue #693: print the resolved absolute path to stderr (bypasses
        # the `--json-output` stdout buffer) so operators tail the right
        # file. Without this, the SKILL.md "arm a Monitor on
        # state/run_progress.jsonl" instruction is path-ambiguous and
        # operators watch an empty file while events stream elsewhere.
        try:
            print(
                f"progress stream: {progress.current_path}",
                file=sys.stderr,
                flush=True,
            )
        except Exception:
            pass  # telemetry must never crash the cycle
    try:
        target = _resolve_target(config)
    except Exception as exc:
        return CycleResult(
            status="blocked",
            reason="target_resolution_failed",
            payload={"error": str(exc)[:300]},
        )

    run_id = uuid.uuid4().hex
    recorder = RunRecorder(run_id=run_id, target=target)
    recorder.summary["live_mode"] = config.live_mode and yes_live
    recorder.summary["execution_mode"] = config.execution_mode
    delta_neutral = config.execution_mode == EXECUTION_MODE_DELTA_NEUTRAL
    live_hedger: Any = hedger  # tests inject; runtime constructs lazily

    progress.emit(
        "cycle_start",
        tick_id=run_id,
        mode="run",
        yes_live=bool(yes_live),
        live_mode=bool(config.live_mode and yes_live),
        execution_mode=config.execution_mode,
    )

    def _finish(result: CycleResult) -> CycleResult:
        """Tag every return path with a `cycle_end` event."""
        progress.emit(
            "cycle_end",
            status=result.status,
            reason=result.reason,
        )
        return result

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
        return _finish(CycleResult(
            status="ok",
            reason="no_pairs_seeded",
            payload=recorder.finish("ok", "no_pairs_seeded"),
        ))

    jwt, viewer_id, jwt_source = _acquire_jwt(
        config=config, gateway=gateway, transport=transport
    )
    if jwt is None:
        return _finish(CycleResult(
            status="blocked",
            reason=jwt_source,
            payload=recorder.finish("blocked", jwt_source),
        ))
    recorder.summary["jwt_source"] = jwt_source
    if viewer_id:
        recorder.summary["prophet_viewer_id"] = viewer_id
    progress.emit("auth_ok", source=jwt_source)

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
            recorder.summary["auto_discover_raw_markets_fetched"] = (
                auto_result.raw_markets_fetched
            )
            recorder.summary["auto_discover_markets_passing_gates"] = (
                auto_result.markets_passing_gates
            )
            recorder.summary["auto_discover_candidates_evaluated_for_pairing"] = (
                auto_result.candidates_evaluated_for_pairing
            )
            recorder.summary["auto_discover_max_candidates"] = (
                auto_result.max_candidates
            )
            recorder.summary["auto_discover_candidate_sample_truncated"] = (
                auto_result.markets_passing_gates > auto_result.candidates_found
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
            if auto_result.prophet_failure_detail:
                recorder.summary["auto_discover_prophet_lookup_error"] = (
                    auto_result.prophet_failure_detail
                )
            if auto_result.sheet_path:
                recorder.summary["arb_candidates_sheet"] = auto_result.sheet_path
            pending_ui_submission = auto_result.pending_ui_submission
            # Reload pairs — auto_paired rows are now in arb_pairs.
            pairs = list_arb_pairs(target=target)
            progress.emit(
                "auto_discover_done",
                raw=auto_result.raw_markets_fetched,
                eligible=auto_result.markets_passing_gates,
                evaluated=auto_result.candidates_evaluated_for_pairing,
                paired_existing=auto_result.already_paired,
                paired_this_run=len(auto_result.auto_paired),
                pending_creation=len(pending_ui_submission),
            )

            # #542 Fix 2 — seed preflight + trim. Cap the pending list
            # by what the operator can actually fund right now on BOTH
            # venues. This is intentionally independent of live_mode /
            # --yes-live so the pending list is never misleading.
            if pending_ui_submission:
                if delta_neutral and live_hedger is None:
                    try:
                        live_hedger = _build_hedger(config)
                    except PolymarketCredentialsMissing as exc:
                        return _finish(_polymarket_creds_missing_result(exc.missing_env_vars))
                    except Exception as exc:
                        return _finish(_hedger_init_failed_result(exc))
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
                    # #589: existing pairs count is `pairs` here — the list
                    # was already refreshed above to include auto_paired
                    # rows. Passing it in lets the orchestration helper
                    # decide block-vs-continue without short-circuiting
                    # cycles that have real arb work to do.
                    existing_pairs_count=len(pairs),
                )
                if seed_block is not None:
                    # Blocked — `pending_ui_submission` is empty until the
                    # operator funds. Return early with deposit envelope.
                    progress.emit("seed_preflight_blocked", reason=seed_block.reason)
                    return _finish(seed_block)
                pending_ui_submission = recorder.summary.get(
                    "_trimmed_pending", pending_ui_submission
                )
                progress.emit(
                    "seed_preflight_ok", pending_after_trim=len(pending_ui_submission)
                )

    # #636: chain into create-market-via-ui for each pending entry. Only
    # fires under `--yes-live` so dry-run cycles never spawn a browser.
    ui_submission_results: list[dict[str, Any]] = []
    ui_submission_invoked = False
    if (
        yes_live
        and config.auto_discover.enabled
        and pending_ui_submission
        and not skip_ui_submission
    ):
        ui_submission_invoked = True
        total_entries = len(pending_ui_submission)
        if create_market_via_ui is not None:
            creator = create_market_via_ui
            for idx, entry in enumerate(pending_ui_submission, start=1):
                progress.emit(
                    "entry_start",
                    idx=idx,
                    total=total_entries,
                    question=entry.get("question", "")[:120],
                    polymarket_condition_id=entry.get("polymarket_market_id", ""),
                )
                sub = creator(
                    config=config,
                    gateway=gateway,
                    transport=transport,
                    polymarket_condition_id=entry.get("polymarket_market_id", ""),
                    question=entry.get("question", ""),
                    category_slug=entry.get("category_slug", ""),
                    initial_bet_usdc=float(entry.get("initial_bet_usdc", 1.0)),
                    progress=progress,
                    entry_idx=idx,
                    entry_total=total_entries,
                )
                ui_submission_results.append(
                    {
                        "polymarket_condition_id": entry.get("polymarket_market_id", ""),
                        "status": sub.status,
                        "reason": sub.reason,
                        "prophet_market_id": sub.payload.get("prophet_market_id", ""),
                    }
                )
                if sub.status == "ok":
                    progress.emit(
                        "pair_created",
                        idx=idx,
                        prophet_market_id=sub.payload.get("prophet_market_id", ""),
                    )
                else:
                    progress.emit("entry_blocked", idx=idx, reason=sub.reason)
        else:
            try:
                from otp_worker import create_market_ui as create_market_ui_module

                with _WarmCreateMarketUiContext(
                    config=config,
                    gateway=gateway,
                    transport=transport,
                ) as warm:
                    progress.emit("prophet_session_restored", idx=1)
                    for idx, entry in enumerate(pending_ui_submission, start=1):
                        progress.emit(
                            "entry_start",
                            idx=idx,
                            total=total_entries,
                            question=entry.get("question", "")[:120],
                            polymarket_condition_id=entry.get("polymarket_market_id", ""),
                        )
                        payload = {
                            "polymarket_condition_id": entry.get("polymarket_market_id", ""),
                            "question": entry.get("question", ""),
                        }
                        try:
                            sub = _run_create_market_via_ui_inner(
                                config=config,
                                gateway=gateway,
                                transport=transport,
                                cache_entry=warm.cache_entry,
                                polymarket_condition_id=entry.get("polymarket_market_id", ""),
                                question=entry.get("question", ""),
                                initial_bet_usdc=float(entry.get("initial_bet_usdc", 1.0)),
                                session=warm.session,
                                create_market_ui=create_market_ui_module,
                                compute_seed_intent=cmd_compute_seed_intent,
                                record_created_market=cmd_record_created_market,
                                payload=payload,
                                progress=progress,
                                entry_idx=idx,
                                entry_budget_seconds=float(
                                    config.auto_discover.create_market_entry_budget_seconds
                                ),
                            )
                        except Exception as exc:
                            payload["error"] = f"{type(exc).__name__}:{str(exc)[:200]}"
                            sub = CycleResult(
                                status="blocked",
                                reason="create_market_via_ui_unexpected",
                                payload=payload,
                            )
                        # Issue #672: do not clobber sub.reason. Preserve the
                        # inner driver's result verbatim; surface the warm
                        # health signal as supplemental payload context.
                        warm_unhealthy = not warm.is_session_healthy()
                        sub = _annotate_entry_result_with_warm_health(
                            sub, warm_unhealthy=warm_unhealthy
                        )
                        if idx < total_entries:
                            if warm_unhealthy:
                                warm.reopen()
                                progress.emit(
                                    "prophet_session_restored", idx=idx + 1
                                )
                            else:
                                try:
                                    warm.reset_for_next_entry()
                                except Exception:
                                    warm.reopen()
                                    progress.emit(
                                        "prophet_session_restored", idx=idx + 1
                                    )
                        ui_entry: dict[str, Any] = {
                            "polymarket_condition_id": entry.get("polymarket_market_id", ""),
                            "status": sub.status,
                            "reason": sub.reason,
                            "prophet_market_id": sub.payload.get("prophet_market_id", ""),
                        }
                        # Issue #672: surface the warm-health signal in the
                        # final envelope, not just in payload, so operators
                        # can see at a glance when the reopen was triggered
                        # without spelunking through nested payload fields.
                        if sub.payload.get("warm_unhealthy_post_entry"):
                            ui_entry["warm_unhealthy_post_entry"] = True
                        ui_submission_results.append(ui_entry)
                        if sub.status == "ok":
                            progress.emit(
                                "pair_created",
                                idx=idx,
                                prophet_market_id=sub.payload.get("prophet_market_id", ""),
                            )
                        else:
                            progress.emit("entry_blocked", idx=idx, reason=sub.reason)
            except PlaywrightMcpUnavailable:
                return _finish(CycleResult(
                    status="blocked",
                    reason="seren_desktop_playwright_mcp_unavailable",
                    payload=recorder.finish(
                        "blocked", "seren_desktop_playwright_mcp_unavailable"
                    ),
                ))
            except SessionEstablishmentFailed as exc:
                _finish_payload = recorder.finish("blocked", exc.reason)
                # Issue #660: surface the observable-check diagnostic so
                # operators can tell which signal failed without re-running.
                if exc.details.get("observable_check") is not None:
                    _finish_payload["observable_check"] = exc.details["observable_check"]
                # Issue #662: surface the underlying restore exception
                # when the warm-context restore path raised before
                # observability ran.
                if exc.details.get("restore_exception") is not None:
                    _finish_payload["restore_exception"] = exc.details["restore_exception"]
                # Issue #664: surface the cache_check snapshot so operators
                # can tell whether the cache-fresh guard saw a fresh entry
                # or one of (state, is_fresh, jwt_present, refresh_token_present)
                # rejected it. Closes the diagnostic gap left by #660+#662
                # when the guard bypasses the restore branch entirely.
                if exc.details.get("cache_check") is not None:
                    _finish_payload["cache_check"] = exc.details["cache_check"]
                return _finish(CycleResult(
                    status="blocked",
                    reason="prophet_session_unavailable",
                    payload=_finish_payload,
                ))
        # Newly-created markets become arb_pairs via record-created-market's
        # UPSERT inside cmd_create_market_via_ui — refresh `pairs` so the
        # scoring loop trades them this same cycle.
        pairs = list_arb_pairs(target=target)

    for p in pairs:
        recorder.record_pair(p["prophet_market_id"], p["polymarket_condition_id"])
    recorder.summary["pairs_evaluated"] = len(pairs)

    if not pairs:
        # Auto-discover ran but Prophet hasn't created any matching
        # markets yet (or every UI submission was blocked). Surface
        # pending_ui_submission + ui_submission_results so the operator
        # sees the per-entry reason.
        payload = recorder.finish("ok", "no_pairs_seeded_pending_ui_submission")
        if pending_ui_submission:
            payload["pending_ui_submission"] = pending_ui_submission
        if ui_submission_invoked:
            payload["ui_submission_results"] = ui_submission_results
        return _finish(CycleResult(
            status="ok",
            reason="no_pairs_seeded_pending_ui_submission",
            payload=payload,
        ))

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
        return _finish(CycleResult(
            status="blocked",
            reason="prophet_unauthorized",
            payload=recorder.finish("blocked", "prophet_unauthorized"),
        ))

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
                return _finish(_polymarket_creds_missing_result(exc.missing_env_vars))
            except Exception as exc:
                return _finish(_hedger_init_failed_result(exc))
        elif hasattr(live_hedger, "bind_prophet_cancel"):
            live_hedger.bind_prophet_cancel(order_client, jwt)
        # Map prophet_market_id → polymarket_condition_id for hedge submission.
        # #631: also resolve YES token_id from the cached polymarket_prices.
        # The CLOB's create_order needs the uint256-decimal token_id, not
        # the hex condition_id.
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
                # #631: token_id is required for the Polymarket leg.
                # polymarket_prices was populated above from Gamma's
                # /markets response which carries clobTokenIds. If the
                # cache miss happens (publisher returned without the
                # field), skip with a structured naked-exposure record
                # — submitting with condition_id is the very bug we're
                # closing.
                cached_price = polymarket_prices.get(condition_id)
                yes_token_id = (
                    cached_price.yes_token_id if cached_price is not None else ""
                )
                if not yes_token_id:
                    recorder.record_blocker(
                        f"hedge_token_id_missing:{o.order_id}:{condition_id}"
                    )
                    hedge_failures += 1
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
                    polymarket_yes_token_id=yes_token_id,
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
            return _finish(CycleResult(
                status="blocked",
                reason="prophet_unauthorized",
                payload=recorder.finish("blocked", "prophet_unauthorized"),
            ))
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
                # #631: pipe the YES token_id through so the pre-trade
                # depth check and hedge submission can hit Polymarket
                # CLOB's `/book?token_id=` and `create_order(token_id=)`.
                polymarket_yes_token_id=polymarket_price.yes_token_id,
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
            # #631: probe Polymarket book using the YES token_id from
            # the Opportunity. The CLOB rejects condition_id at this
            # endpoint; pre-fix this silently returned no_liquidity and
            # blocked every opportunity.
            if not opp.polymarket_yes_token_id:
                depth_blocked += 1
                recorder.record_blocker(
                    f"depth_check_token_id_missing:{opp.polymarket_condition_id}"
                )
                continue
            try:
                book = live_hedger.fetch_book(opp.polymarket_yes_token_id)
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
            return _finish(CycleResult(
                status="blocked",
                reason="prophet_unauthorized",
                payload=recorder.finish("blocked", "prophet_unauthorized"),
            ))
        except (ProphetSchemaError, ProphetGraphQLError) as exc:
            recorder.record_blocker(
                f"cash_balance_failed:{type(exc).__name__}:{str(exc)[:120]}"
            )
            return _finish(CycleResult(
                status="blocked",
                reason="funds_preflight_unavailable",
                payload=recorder.finish("blocked", "funds_preflight_unavailable"),
            ))

        # Two-venue preflight (#536). Every opportunity locks the same
        # USDC notional on both Prophet (LIMIT collateral) and Polymarket
        # (hedge collateral), so we check both balances and return split
        # deficits so the deposit runbook can route the operator to the
        # right venue. Issue #591 removed the legacy single-venue path —
        # delta-neutral is the only supported execution mode.
        polymarket_avail = 0.0
        if live_hedger is not None:
            try:
                # DirectClobTrader exposes `get_cash_balance` for the
                # configured CLOB account. If it raises, fall back to 0
                # (blocks the cycle with a clear deficit).
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
            deposit_envelope = preflight2.to_deposit_envelope()
            if preflight2.polymarket_deficit_usdc > 0.0:
                _annotate_polymarket_state(
                    deposit_envelope=deposit_envelope,
                    polymarket_avail_usdc=polymarket_avail,
                    live_hedger=live_hedger,
                    recorder=recorder,
                )
            payload["deposit"] = deposit_envelope
            return _finish(CycleResult(
                status="blocked",
                reason="funds_insufficient",
                payload=payload,
            ))

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
        if ui_submission_invoked:
            payload["ui_submission_results"] = ui_submission_results
        return payload

    if not (config.live_mode and yes_live) and len(actionable) > 0:
        return _finish(CycleResult(
            status="ok",
            reason="cycle_complete_dry_run",
            payload=_attach_pending(recorder.finish("ok", "cycle_complete_dry_run")),
        ))
    if submitted == 0 and len(actionable) > 0:
        return _finish(CycleResult(
            status="ok_no_fills",
            reason="all_orders_blocked",
            payload=_attach_pending(recorder.finish("ok_no_fills", "all_orders_blocked")),
        ))
    return _finish(CycleResult(
        status="ok",
        reason="cycle_complete",
        payload=_attach_pending(recorder.finish("ok", "cycle_complete")),
    ))


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
    gateway: HttpGateway | None = None,
    poll: Any | None = None,
    fetch_book: Any | None = None,
    resolve_yes_token_id: Any | None = None,
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

    # #631: Polymarket CLOB's `/book?token_id=` requires the YES
    # uint256-decimal token_id, not the hex condition_id. Resolve via
    # Gamma's `/markets?condition_ids=` before probing the book.
    # The resolver is injectable so tests can short-circuit it without
    # standing up a full gateway stub.
    resolver = resolve_yes_token_id or _resolve_yes_token_id
    yes_token_id = resolver(
        gateway=gateway,
        polymarket_condition_id=polymarket_condition_id,
    )
    if not yes_token_id:
        return CycleResult(
            status="blocked",
            reason="polymarket_book_unavailable",
            payload={
                **base_payload,
                "is_viable": True,
                "prophet_fair_value_bps": pricing.yes_fair_value_bps,
                "polymarket_yes_price": float(polymarket_yes_price),
                "error": "polymarket_yes_token_id_unavailable",
            },
        )
    try:
        book_payload = fetch_book(yes_token_id)
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


def _resolve_yes_token_id(
    *,
    gateway: HttpGateway | None,
    polymarket_condition_id: str,
) -> str:
    """#631: one-shot lookup of the YES token_id from Polymarket Gamma.

    Used by `cmd_record_created_market` where the cycle hasn't already
    populated a `polymarket_prices` cache. Returns "" if the gateway
    is missing or the publisher response doesn't carry clobTokenIds.
    Callers treat empty as "cannot hedge — fail closed" rather than
    falling back to condition_id (the very bug we are closing).
    """
    if gateway is None or not polymarket_condition_id:
        return ""
    try:
        price = fetch_market_price(
            gateway=gateway, condition_id=polymarket_condition_id
        )
    except Exception:
        return ""
    if price is None:
        return ""
    return price.yes_token_id or ""


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
    gateway: HttpGateway | None = None,
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
        # #631: resolve the YES token_id from Gamma's `clobTokenIds`
        # so the Polymarket unwind hits `create_order(token_id=...)`
        # with the value the CLOB requires.
        yes_token_id = _resolve_yes_token_id(
            gateway=gateway,
            polymarket_condition_id=polymarket_condition_id,
        )
        if not yes_token_id:
            payload["hedge_error"] = "polymarket_yes_token_id_unavailable"
            return CycleResult(
                status="blocked",
                reason="polymarket_token_id_unavailable",
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
            polymarket_yes_token_id=yes_token_id,
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
        # #631: resolve YES token_id before constructing the hedger so
        # we fail fast (and cheaply) on a token_id miss rather than
        # initializing the live trading client only to throw later.
        yes_token_id = _resolve_yes_token_id(
            gateway=gateway,
            polymarket_condition_id=polymarket_condition_id,
        )
        if not yes_token_id:
            payload["hedge_error"] = "polymarket_yes_token_id_unavailable"
            return CycleResult(
                status="blocked",
                reason="polymarket_token_id_unavailable",
                payload=payload,
            )
        try:
            hedger = _build_hedger(config)
        except PolymarketCredentialsMissing as exc:
            return _polymarket_creds_missing_result(exc.missing_env_vars)
        except Exception as exc:
            return _hedger_init_failed_result(exc)

        outcome = hedge_seed_bet(
            prophet_market_id="",
            polymarket_condition_id=polymarket_condition_id,
            polymarket_yes_token_id=yes_token_id,
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
# Create market via UI (#636)
#
# Replaces the legacy agent-driven Playwright runbook. The Python
# subprocess restores the Privy session into a Python-owned browser via
# `privy_restore`, drives `/create` through the bet form, hedges on
# Polymarket via `cmd_record_created_market`, clicks Prophet Confirm
# (Privy's embedded wallet auto-signs `createMarketWithBet` in-browser),
# captures the new prophet_market_id from the redirected URL, and
# persists the pair. Unwinds the Polymarket hedge on Confirm failure.


def cmd_create_market_via_ui(
    *,
    config: AgentConfig,
    gateway: HttpGateway,
    transport: Any,
    polymarket_condition_id: str,
    question: str,
    category_slug: str = "",
    initial_bet_usdc: float = 1.0,
    # Injectable seams — tests stub these without touching MCP/Playwright.
    open_session_factory: Any | None = None,
    restore_session: Any | None = None,
    establish_session: Any | None = None,
    create_market_ui: Any | None = None,
    compute_seed_intent: Any | None = None,
    record_created_market: Any | None = None,
    sleep: Any = time.sleep,
    # Issue #652: per-entry wall-clock budget guard for the /create
    # driver. Sourced from config.auto_discover by default; tests inject
    # a small budget plus a fake `now` to trip the guard deterministically.
    now: Any = time.monotonic,
    # Issue #640: optional progress emitter + per-entry index for the
    # chat-side Monitor. The parent cmd_run passes these in; standalone
    # `agent.py --command create-market-via-ui` invocations leave them
    # None and the helpers no-op cleanly.
    progress: ProgressEmitter | None = None,
    entry_idx: int = 0,
    entry_total: int = 0,
) -> CycleResult:
    """Drive Prophet's `/create` UI autonomously for one candidate market.

    Issue #638: this command opens ONE browser. `establish_session` plants
    Privy state into that browser via `playwright_add_init_script` (or
    falls through to OTP cold-start on the same browser if the cache is
    stale or restore verification fails). The legacy "acquire JWT in
    browser A, then drive `/create` in browser B" pattern is gone.

    Returns one of:
      - status=ok,      reason=pair_created                 (success)
      - status=blocked, reason=prophet_session_unavailable
      - status=blocked, reason=seren_desktop_playwright_mcp_unavailable
      - status=blocked, reason=<upstream compute-seed-intent reason>
      - status=blocked, reason=hedge_failed_no_commit
      - status=blocked, reason=prophet_confirm_failed
      - status=blocked, reason=ocs_session_id_not_captured
    """
    payload: dict[str, Any] = {
        "polymarket_condition_id": polymarket_condition_id,
        "question": question,
    }
    if not polymarket_condition_id or not question:
        return CycleResult(
            status="blocked",
            reason="missing_required_args",
            payload=payload,
        )

    if compute_seed_intent is None:
        compute_seed_intent = cmd_compute_seed_intent
    if record_created_market is None:
        record_created_market = cmd_record_created_market
    if create_market_ui is None:
        from otp_worker import create_market_ui as create_market_ui  # type: ignore
    if restore_session is None:
        from otp_worker.privy_restore import restore_privy_session as restore_session
    if establish_session is None:
        establish_session = establish_browser_session_for_create

    if open_session_factory is None:
        if _playwright_mcp_gateway.PlaywrightStealthGateway._resolve_default_command() is None:
            return CycleResult(
                status="blocked",
                reason="seren_desktop_playwright_mcp_unavailable",
                payload=payload,
            )
        open_session_factory = _default_browser_session_factory

    inputs = getattr(config, "inputs", {}) or {}
    email = str(inputs.get("prophet_email") or "")
    provider = str(inputs.get("email_provider") or "")

    try:
        with open_session_factory() as session:
            try:
                cache_entry = establish_session(
                    session=session,
                    email=email,
                    provider=provider,
                    seren_user_id="",
                    bounty_id="",
                    config_gateway=gateway,
                    transport=transport,
                    pw_gateway=None,
                    restore=restore_session,
                )
            except SessionEstablishmentFailed as exc:
                payload["auth_source"] = exc.reason
                # Issue #660: same observable_check diagnostic for the
                # per-entry create-market-via-ui path.
                if exc.details.get("observable_check") is not None:
                    payload["observable_check"] = exc.details["observable_check"]
                # Issue #662: surface the underlying restore exception
                # so operators can identify which MCP call failed.
                if exc.details.get("restore_exception") is not None:
                    payload["restore_exception"] = exc.details["restore_exception"]
                # Issue #664: surface the cache_check snapshot so the
                # per-entry blocked envelope shows what the guard saw.
                if exc.details.get("cache_check") is not None:
                    payload["cache_check"] = exc.details["cache_check"]
                return CycleResult(
                    status="blocked",
                    reason="prophet_session_unavailable",
                    payload=payload,
                )

            if (
                cache_entry is None
                or not getattr(cache_entry, "jwt", "")
                or not getattr(cache_entry, "refresh_token", "")
            ):
                return CycleResult(
                    status="blocked",
                    reason="prophet_session_unavailable",
                    payload=payload,
                )

            if progress is not None:
                progress.emit(
                    "prophet_session_restored",
                    idx=entry_idx,
                )
            return _run_create_market_via_ui_inner(
                config=config,
                gateway=gateway,
                transport=transport,
                cache_entry=cache_entry,
                polymarket_condition_id=polymarket_condition_id,
                question=question,
                initial_bet_usdc=initial_bet_usdc,
                session=session,
                create_market_ui=create_market_ui,
                compute_seed_intent=compute_seed_intent,
                record_created_market=record_created_market,
                payload=payload,
                progress=progress,
                entry_idx=entry_idx,
                entry_budget_seconds=float(
                    config.auto_discover.create_market_entry_budget_seconds
                ),
                now=now,
            )
    except PlaywrightMcpUnavailable:
        return CycleResult(
            status="blocked",
            reason="seren_desktop_playwright_mcp_unavailable",
            payload=payload,
        )
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}:{str(exc)[:200]}"
        return CycleResult(
            status="blocked",
            reason="create_market_via_ui_unexpected",
            payload=payload,
        )


def _default_browser_session_factory() -> Any:
    """Spawn a fresh Python-owned Playwright MCP browser session.

    Returned object is a context manager that yields a `RealBrowserSession`
    and tears down the gateway on exit. Tests inject a stub factory.
    """

    class _SessionScope:
        def __enter__(self) -> Any:
            # Issue #681: standalone per-entry `/create` runs (used by
            # --command create-market-via-ui) must launch with the
            # Privy-compatible profile so the embedded wallet provisions.
            # Mirrors the warm-context wiring in
            # `_WarmCreateMarketUiContext._open()`.
            self._gw = PlaywrightStealthGateway(
                env_overrides=PRIVY_COMPATIBLE_ENV,
            ).__enter__()
            self._session = RealBrowserSession(gateway=self._gw).__enter__()
            return self._session

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            try:
                self._session.__exit__(exc_type, exc, tb)
            finally:
                self._gw.__exit__(exc_type, exc, tb)

    return _SessionScope()


class _WarmCreateMarketUiContext:
    """Cycle-scoped Playwright context for #654 pending-ui batches."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        gateway: HttpGateway,
        transport: Any,
    ) -> None:
        if _playwright_mcp_gateway.PlaywrightStealthGateway._resolve_default_command() is None:
            raise PlaywrightMcpUnavailable(
                "No playwright-stealth MCP command resolvable."
            )
        self._config = config
        self._gateway = gateway
        self._transport = transport
        self._pw_gateway: Any | None = None
        self._session_scope: Any | None = None
        self.session: Any | None = None
        self.cache_entry: Any | None = None

    def __enter__(self) -> "_WarmCreateMarketUiContext":
        self._open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._close(exc_type, exc, tb)

    def reopen(self) -> None:
        self._close(None, None, None)
        self._open()

    def reset_for_next_entry(self) -> None:
        if self._pw_gateway is None:
            raise RuntimeError("warm Playwright gateway is not open")
        self._pw_gateway.reset_for_next_entry()

    def is_session_healthy(self) -> bool:
        if self._pw_gateway is None:
            return False
        try:
            return bool(self._pw_gateway.is_session_healthy())
        except Exception:
            return False

    def _open(self) -> None:
        # Issue #681: spawn the cycle-scoped `/create` MCP child with the
        # Privy-compatible env profile (HEADLESS=0, two stealth evasions
        # dropped, page-init patch off). Older Desktop builds ignore these
        # vars and the child launches stealth-on; the skill still fails
        # closed downstream on `prophet_session_unavailable` in that case.
        pw_gateway = PlaywrightStealthGateway(
            env_overrides=PRIVY_COMPATIBLE_ENV,
        ).__enter__()
        try:
            session_scope = RealBrowserSession(gateway=pw_gateway).__enter__()
        except Exception:
            pw_gateway.__exit__(None, None, None)
            raise
        inputs = getattr(self._config, "inputs", {}) or {}
        email = str(inputs.get("prophet_email") or "")
        provider = str(inputs.get("email_provider") or "")
        try:
            cache_entry = establish_browser_session_for_create(
                session=session_scope,
                email=email,
                provider=provider,
                seren_user_id="",
                bounty_id="",
                config_gateway=self._gateway,
                transport=self._transport,
                pw_gateway=pw_gateway,
            )
        except Exception:
            try:
                session_scope.__exit__(None, None, None)
            finally:
                pw_gateway.__exit__(None, None, None)
            raise
        # Issue #670: #666 retired the Privy localStorage refresh-token
        # mechanism server-side and dropped the refresh_token requirement
        # from `establish_browser_session_for_create` (establish_session.py:122).
        # The wrapper carried a duplicate refresh-token check that #666
        # missed, which rejected every JWT-only cache entry returned by
        # the inner establish call. The wrapper's role is to fail closed
        # when there's no usable session — that's a missing JWT, not a
        # missing refresh token.
        if cache_entry is None or not getattr(cache_entry, "jwt", ""):
            try:
                session_scope.__exit__(None, None, None)
            finally:
                pw_gateway.__exit__(None, None, None)
            raise SessionEstablishmentFailed("prophet_session_unavailable")
        self._pw_gateway = pw_gateway
        self._session_scope = session_scope
        self.session = session_scope
        self.cache_entry = cache_entry

    def _close(self, exc_type: Any, exc: Any, tb: Any) -> None:
        session_scope = self._session_scope
        pw_gateway = self._pw_gateway
        self._session_scope = None
        self._pw_gateway = None
        self.session = None
        self.cache_entry = None
        try:
            if session_scope is not None:
                session_scope.__exit__(exc_type, exc, tb)
        finally:
            if pw_gateway is not None:
                pw_gateway.__exit__(exc_type, exc, tb)


def _run_create_market_via_ui_inner(
    *,
    config: AgentConfig,
    gateway: HttpGateway,
    transport: Any,
    cache_entry: Any,
    polymarket_condition_id: str,
    question: str,
    initial_bet_usdc: float,
    session: Any,
    create_market_ui: Any,
    compute_seed_intent: Any,
    record_created_market: Any,
    payload: dict[str, Any],
    progress: ProgressEmitter | None = None,
    entry_idx: int = 0,
    # Issue #652: per-entry wall-clock budget guard. Replaces per-
    # `tools/call` timeout policing — the gateway's per-call ceiling is
    # now 180s and exists only to detect a dead MCP stdio stream. This
    # 5-minute default covers Prophet's 60-180s AI seed calc plus every
    # Playwright round-trip with comfortable headroom on a contended host.
    entry_budget_seconds: float = 300.0,
    now: Any = time.monotonic,
) -> CycleResult:
    # `last_stage` and `hedge_committed` live in single-element lists so
    # the inner helpers can mutate them without `nonlocal` ceremony.
    last_stage: list[str] = ["start"]
    hedge_committed: list[bool] = [False]
    start_monotonic = float(now())

    def _emit(stage: str, **kw: Any) -> None:
        last_stage[0] = stage
        if progress is not None:
            progress.emit(stage, idx=entry_idx, **kw)

    def _budget_exceeded() -> CycleResult | None:
        """Return a blocked result if the entry has overrun its budget.

        When the hedge has already committed, unwind the Polymarket leg
        via `record_created_market(prophet_confirm_declined=True)` before
        returning. Naked Polymarket exposure must never be left behind.
        """
        elapsed = float(now()) - start_monotonic
        if elapsed <= entry_budget_seconds:
            return None
        payload["entry_budget_seconds"] = entry_budget_seconds
        payload["entry_elapsed_seconds"] = round(elapsed, 3)
        payload["entry_last_stage"] = last_stage[0]
        if hedge_committed[0]:
            seed_side_val = str(payload.get("seed_side") or "")
            hedge_price_val = float(payload.get("hedge_price") or 0.0)
            unwind_price = _opposite_marketable_price(seed_side_val, hedge_price_val)
            try:
                unwind = record_created_market(
                    config=config,
                    polymarket_condition_id=polymarket_condition_id,
                    prophet_market_id="",
                    prophet_seed_side=seed_side_val,
                    polymarket_marketable_price=unwind_price,
                    seed_size_usdc=float(initial_bet_usdc),
                    prophet_confirm_declined=True,
                    gateway=gateway,
                )
                payload["unwind_status"] = unwind.payload.get("hedge_status") or ""
            except Exception as exc:
                # Don't crash the cycle on an unwind error — surface it
                # so the operator can clean up out-of-band.
                payload["unwind_status"] = f"unwind_error:{type(exc).__name__}"
        return CycleResult(
            status="blocked",
            reason="create_market_via_ui_entry_budget_exceeded",
            payload=payload,
        )

    # 1. Drive `/create` through Validate + Create Market. The fetch
    # capture is installed before the Create Market click that fires
    # `startOddsCalculation`, so the OCS sessionId is observable from
    # `window.__seren_capture__`. The session arrives already
    # authenticated — `establish_browser_session_for_create` either
    # planted Privy state via `add_init_script` or drove the OTP modal
    # on this same browser before we got here (issue #638).
    create_market_ui.open_create_form(session, question=question)
    last_stage[0] = "open_create_form"
    if (result := _budget_exceeded()) is not None:
        return result
    ocs_id = create_market_ui.poll_for_ocs_id(session)
    last_stage[0] = "poll_for_ocs_id"
    if (result := _budget_exceeded()) is not None:
        return result
    if not ocs_id:
        return CycleResult(
            status="blocked",
            reason="ocs_session_id_not_captured",
            payload=payload,
        )
    payload["odds_session_id"] = ocs_id
    _emit("ocs_session_captured")

    # 3. Compute the seed-side decision (poll AI fair value + Polymarket book).
    # The AI calc runs 60–180s; heartbeat keeps the chat-side Monitor alive.
    if progress is not None and entry_idx:
        with progress.heartbeat(idx=entry_idx, current="ai_calc"):
            intent = compute_seed_intent(
                config=config,
                polymarket_condition_id=polymarket_condition_id,
                odds_session_id=ocs_id,
                polymarket_yes_price=0.0,
                transport=transport,
                jwt=cache_entry.jwt,
                gateway=gateway,
            )
    else:
        intent = compute_seed_intent(
            config=config,
            polymarket_condition_id=polymarket_condition_id,
            odds_session_id=ocs_id,
            polymarket_yes_price=0.0,
            transport=transport,
            jwt=cache_entry.jwt,
            gateway=gateway,
        )
    if intent.status != "ok":
        payload.update(intent.payload)
        return CycleResult(
            status="blocked",
            reason=intent.reason,
            payload=payload,
        )

    seed_side = intent.payload.get("seed_side") or ""
    hedge_price = float(intent.payload.get("hedge_price") or 0.0)
    payload["seed_side"] = seed_side
    payload["hedge_price"] = hedge_price
    _emit("ai_calc_done", seed_side=seed_side, hedge_price=hedge_price)
    if (result := _budget_exceeded()) is not None:
        return result

    # 4. Fill the bet form (do NOT click Confirm yet).
    create_market_ui.fill_bet_form(
        session, seed_side=seed_side, bet_usdc=float(initial_bet_usdc)
    )
    last_stage[0] = "fill_bet_form"
    if (result := _budget_exceeded()) is not None:
        return result

    # 5. Submit the Polymarket hedge first.
    _emit("hedge_submitted", price=hedge_price, qty=float(initial_bet_usdc))
    hedge_result = record_created_market(
        config=config,
        polymarket_condition_id=polymarket_condition_id,
        prophet_market_id="",
        prophet_seed_side=seed_side,
        polymarket_marketable_price=hedge_price,
        seed_size_usdc=float(initial_bet_usdc),
        prophet_confirm_declined=False,
        gateway=gateway,
    )
    hedge_status = hedge_result.payload.get("hedge_status") or ""
    payload["hedge_status"] = hedge_status
    if hedge_status != "hedged":
        return CycleResult(
            status="blocked",
            reason="hedge_failed_no_commit",
            payload=payload,
        )
    # Hedge has committed — from here, any abort must unwind the leg.
    hedge_committed[0] = True
    _emit("hedge_filled", price=hedge_price)
    if (result := _budget_exceeded()) is not None:
        return result

    # 6. Hedge filled → click Prophet Confirm.
    _emit("prophet_confirm_clicked")
    create_market_ui.click_prophet_confirm(session)
    last_stage[0] = "click_prophet_confirm"
    if (result := _budget_exceeded()) is not None:
        return result
    prophet_market_id = create_market_ui.wait_for_market_redirect(session)
    last_stage[0] = "wait_for_market_redirect"
    if (result := _budget_exceeded()) is not None:
        return result
    if not prophet_market_id:
        # 6a. Confirm timed out / failed → unwind the hedge.
        unwind_price = _opposite_marketable_price(seed_side, hedge_price)
        unwind = record_created_market(
            config=config,
            polymarket_condition_id=polymarket_condition_id,
            prophet_market_id="",
            prophet_seed_side=seed_side,
            polymarket_marketable_price=unwind_price,
            seed_size_usdc=float(initial_bet_usdc),
            prophet_confirm_declined=True,
            gateway=gateway,
        )
        payload["unwind_status"] = unwind.payload.get("hedge_status") or ""
        return CycleResult(
            status="blocked",
            reason="prophet_confirm_failed",
            payload=payload,
        )

    # 7. UPSERT the pair.
    persist = record_created_market(
        config=config,
        polymarket_condition_id=polymarket_condition_id,
        prophet_market_id=prophet_market_id,
        gateway=gateway,
    )
    payload["prophet_market_id"] = prophet_market_id
    if persist.status != "ok":
        payload["persist_reason"] = persist.reason
        return CycleResult(
            status="blocked",
            reason="persist_failed",
            payload=payload,
        )
    return CycleResult(
        status="ok",
        reason="pair_created",
        payload=payload,
    )


def _opposite_marketable_price(seed_side: str, hedge_price: float) -> float:
    """Approximate the opposing-side marketable price for an unwind.

    The hedger snaps to tick and re-fetches the live book, so this only
    needs to be in the right ballpark to clear the hedger's slippage gate.
    """
    if hedge_price <= 0.0:
        return 0.0
    flipped = 1.0 - hedge_price
    if flipped <= 0.0 or flipped >= 1.0:
        return hedge_price
    return flipped


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
    # Issue #568: load `.env` from the skill root before any auth check.
    # Without this, `db.py:184` raises `RuntimeError: SEREN_API_KEY is
    # required` even when the operator has a valid key on disk — which
    # was the proximate cause of issue #567's Windows failure cascade.
    from polymarket_live import maybe_load_dotenv

    maybe_load_dotenv(SCRIPT_DIR.parent)

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
            "create-market-via-ui",
            "reset-playwright-mcp",
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
    parser.add_argument(
        "--question",
        default="",
        help=(
            "(create-market-via-ui) Question text to type into Prophet's "
            "/create form. Required."
        ),
    )
    parser.add_argument(
        "--category-slug",
        default="",
        help="(create-market-via-ui) Optional category slug; Prophet's AI infers it.",
    )
    parser.add_argument(
        "--initial-bet-usdc",
        default=1.0,
        type=float,
        help="(create-market-via-ui) Seed bet size in USDC.",
    )
    parser.add_argument(
        "--skip-ui-submission",
        action="store_true",
        help=(
            "(run) Skip the automatic create-market-via-ui chain on "
            "pending_ui_submission entries (preserves pre-#636 behavior)."
        ),
    )
    args = parser.parse_args(argv)

    if args.command == "probe-schema":
        from prophet.schema_probe import main as probe_main  # type: ignore

        # Pass an explicit empty argv so the probe's argparse does not
        # re-parse the parent agent's --config / --command flags.
        return probe_main([])

    if args.command == "reset-playwright-mcp":
        # Issue #647: operator hatch to reclaim stale playwright-stealth MCP
        # processes without running a full cycle. Auto-cleanup also runs on
        # every `__enter__` of the gateway; this command is for when the
        # operator wants to clear contention up-front and inspect what was
        # killed before retrying `--command run --yes-live`.
        from otp_worker.playwright_mcp_lifecycle import (
            kill_stale_playwright_mcp_processes,
        )

        report = kill_stale_playwright_mcp_processes(grace_seconds=1.0)
        result = CycleResult(
            status="ok",
            reason="playwright_mcp_reset",
            payload=report.to_dict(),
        )
        return _emit(result, json_output=args.json_output)

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
            skip_ui_submission=args.skip_ui_submission,
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
            # #631: gateway is needed to resolve the YES token_id from
            # Polymarket Gamma before any seed hedge / unwind submits
            # to the CLOB. cmd_run already had this — cmd_record_created
            # _market did not.
            gateway=gateway,
        )
    elif args.command == "create-market-via-ui":
        if not args.polymarket_condition_id or not args.question:
            result = CycleResult(
                status="blocked",
                reason="missing_required_args",
                payload={
                    "polymarket_condition_id": args.polymarket_condition_id,
                    "question": args.question,
                },
            )
        else:
            result = cmd_create_market_via_ui(
                config=config,
                gateway=gateway,
                transport=transport,
                polymarket_condition_id=args.polymarket_condition_id,
                question=args.question,
                category_slug=args.category_slug,
                initial_bet_usdc=args.initial_bet_usdc,
            )
    elif args.command == "compute-seed-intent":
        if not args.odds_session_id or not args.polymarket_condition_id:
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
            jwt = os.environ.get("PROPHET_SESSION_TOKEN", "").strip()
            jwt_source = "env" if jwt else ""
            if not jwt:
                jwt, _, jwt_source = _acquire_jwt(
                    config=config,
                    gateway=gateway,
                    transport=transport,
                )
            if not jwt:
                result = CycleResult(
                    status="blocked",
                    reason=jwt_source,
                    payload={
                        "action": "retry_after_seren_desktop_playwright_auth_available"
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
                    # #631: cmd_compute_seed_intent now resolves the YES
                    # token_id via Gamma before probing Polymarket's
                    # /book endpoint. Without the gateway it fails fast
                    # with polymarket_book_unavailable rather than
                    # silently passing condition_id and getting an
                    # empty book (the pre-fix bug).
                    gateway=gateway,
                    poll_interval_s=args.poll_interval_s,
                    poll_timeout_s=args.poll_timeout_s,
                )
    else:
        parser.error(f"unknown command: {args.command}")
        return 2

    return _emit(result, json_output=args.json_output)


if __name__ == "__main__":
    raise SystemExit(main())
