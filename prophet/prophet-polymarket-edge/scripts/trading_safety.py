"""Trading safety gates for prophet-polymarket-edge.

These gates encode the three preconditions that MUST hold before any
Polymarket execution path can be safely enabled in this skill. They mirror
the guard patterns already enforced by the other Polymarket trading skills
in this repo, so a future PR that re-enables `--yes-live` cannot do so
without satisfying all three.

The contract:

1. Signal-calibration gate — backtest sample size at or above the minimum
   AND backtest net return strictly positive. Mirrors
   `liquidity-paired-basis-maker` `insufficient_sample_size` +
   `backtest_gate_blocked` (`scripts/agent.py:1445-1465`, `:2497-2513`).

2. Risk-framework gate — explicit Kelly fraction, position cap, midpoint
   safe band, 24h volume floor, resolution-buffer window, and hold-cycle
   limit, all within sane bounds. Mirrors `maker-rebate-bot`
   `StrategyParams` (`scripts/agent.py:76-99`).

3. Execution-path gate — `py_clob_client` importable and the four
   `POLY_*` credentials present. Mirrors `maker-rebate-bot`
   `DirectClobTrader` (`scripts/polymarket_live.py:2049-2087`).

V1 contract: every gate trips closed today because none of the
preconditions are present. Surface C remains read-only and `--yes-live`
remains rejected. The structured `trading_safety_blocked` payload makes
the contract machine-checkable so a future trading-enable PR must clear
all three gates before its code can run.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# Mirrors `liquidity-paired-basis-maker/scripts/agent.py:117`.
DEFAULT_MIN_BACKTEST_EVENTS = 120

# Mirrors `maker-rebate-bot/scripts/agent.py:76-99`. These are the *bounds*
# any future config must respect — the gate does not enforce a single
# default, only that user-supplied values are inside the safe envelope.
RISK_BOUND_MAX_KELLY_FRACTION_HARD_CAP = 0.10
RISK_BOUND_MIN_MID_PRICE_FLOOR = 0.30
RISK_BOUND_MAX_MID_PRICE_CEIL = 0.70
RISK_BOUND_MIN_DAILY_VOLUME_USD_FLOOR = 5000.0
RISK_BOUND_MIN_SECONDS_TO_RESOLUTION_FLOOR = 14 * 24 * 60 * 60
RISK_BOUND_MIN_HOLD_CYCLES_FLOOR = 1

REQUIRED_RISK_FIELDS: Tuple[str, ...] = (
    "max_kelly_fraction",
    "max_position_notional_usd",
    "min_mid_price",
    "max_mid_price",
    "min_daily_volume_usd",
    "min_seconds_to_resolution",
    "max_inventory_hold_cycles",
)

REQUIRED_POLY_ENV_VARS: Tuple[str, ...] = (
    "POLY_API_KEY",
    "POLY_PASSPHRASE",
    "POLY_SECRET",
)
# Either POLY_PRIVATE_KEY or WALLET_PRIVATE_KEY is acceptable — mirrors
# `maker-rebate-bot/scripts/polymarket_live.py:2074`.
POLY_PRIVATE_KEY_ENV_VARS: Tuple[str, ...] = ("POLY_PRIVATE_KEY", "WALLET_PRIVATE_KEY")


@dataclass(frozen=True)
class GateResult:
    passed: bool
    error_code: Optional[str] = None
    missing: List[str] = field(default_factory=list)
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "error_code": self.error_code,
            "missing": list(self.missing),
            "message": self.message,
        }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def check_signal_calibration_gate(
    config: Optional[Dict[str, Any]],
    *,
    min_events: int = DEFAULT_MIN_BACKTEST_EVENTS,
) -> GateResult:
    """Block live execution unless a backtest with sample-size minimum
    AND positive return is present in the config.

    Mirrors the comparison skills' two-step gate: sample-size first
    (`insufficient_sample_size`), then return sign (`backtest_gate_blocked`).
    """
    backtest = (config or {}).get("backtest") if isinstance(config, dict) else None
    if not isinstance(backtest, dict) or not backtest:
        return GateResult(
            passed=False,
            error_code="insufficient_sample_size",
            missing=["backtest"],
            message=(
                "No backtest results in config. Provide config.backtest.events "
                f"(>= {min_events}) and config.backtest.results.return_pct (> 0). "
                "Disagreement metrics from /api/oracle/divergence + "
                "/api/oracle/consensus are not predictive without resolution-grounded "
                "backtest evidence."
            ),
        )

    events = _safe_int(backtest.get("events"), default=0)
    if events < int(min_events):
        return GateResult(
            passed=False,
            error_code="insufficient_sample_size",
            missing=[f"backtest.events>={min_events}"],
            message=(
                f"Backtest sample too small for decision-grade metrics. "
                f"Required at least {min_events}, observed {events}."
            ),
        )

    results = backtest.get("results") if isinstance(backtest.get("results"), dict) else {}
    return_pct = _safe_float(results.get("return_pct"), default=0.0)
    if return_pct <= 0.0:
        return GateResult(
            passed=False,
            error_code="backtest_gate_blocked",
            missing=["backtest.results.return_pct>0"],
            message=(
                f"Backtest return_pct {return_pct} is not positive. Trading cannot "
                "be enabled on a signal whose backtest does not show net edge after "
                "fees and slippage."
            ),
        )

    return GateResult(passed=True)


def check_risk_framework_gate(config: Optional[Dict[str, Any]]) -> GateResult:
    """Block live execution unless the config carries the required risk
    parameters within sane bounds."""
    risk = (config or {}).get("risk") if isinstance(config, dict) else None
    if not isinstance(risk, dict) or not risk:
        return GateResult(
            passed=False,
            error_code="risk_framework_missing",
            missing=list(REQUIRED_RISK_FIELDS),
            message=(
                "config.risk block is missing. The risk framework must specify "
                "Kelly fraction, position caps, midpoint safe band, 24h volume "
                "floor, resolution-buffer window, and hold-cycle limit before any "
                "Polymarket execution can run."
            ),
        )

    missing = [f for f in REQUIRED_RISK_FIELDS if f not in risk or risk.get(f) is None]
    if missing:
        return GateResult(
            passed=False,
            error_code="risk_framework_missing",
            missing=missing,
            message=f"config.risk is missing required fields: {missing}",
        )

    kelly = _safe_float(risk.get("max_kelly_fraction"))
    if not (0.0 < kelly <= RISK_BOUND_MAX_KELLY_FRACTION_HARD_CAP):
        return GateResult(
            passed=False,
            error_code="risk_framework_unsafe",
            missing=["max_kelly_fraction"],
            message=(
                f"max_kelly_fraction must be in (0, {RISK_BOUND_MAX_KELLY_FRACTION_HARD_CAP}]; "
                f"got {kelly}."
            ),
        )

    min_mid = _safe_float(risk.get("min_mid_price"))
    max_mid = _safe_float(risk.get("max_mid_price"))
    if min_mid < RISK_BOUND_MIN_MID_PRICE_FLOOR or max_mid > RISK_BOUND_MAX_MID_PRICE_CEIL or min_mid >= max_mid:
        return GateResult(
            passed=False,
            error_code="risk_framework_unsafe",
            missing=["min_mid_price", "max_mid_price"],
            message=(
                f"midpoint safe band must satisfy "
                f"{RISK_BOUND_MIN_MID_PRICE_FLOOR} <= min_mid_price < max_mid_price <= "
                f"{RISK_BOUND_MAX_MID_PRICE_CEIL}; got [{min_mid}, {max_mid}]."
            ),
        )

    min_volume = _safe_float(risk.get("min_daily_volume_usd"))
    if min_volume < RISK_BOUND_MIN_DAILY_VOLUME_USD_FLOOR:
        return GateResult(
            passed=False,
            error_code="risk_framework_unsafe",
            missing=["min_daily_volume_usd"],
            message=(
                f"min_daily_volume_usd must be >= "
                f"{RISK_BOUND_MIN_DAILY_VOLUME_USD_FLOOR}; got {min_volume}."
            ),
        )

    min_seconds = _safe_int(risk.get("min_seconds_to_resolution"))
    if min_seconds < RISK_BOUND_MIN_SECONDS_TO_RESOLUTION_FLOOR:
        return GateResult(
            passed=False,
            error_code="risk_framework_unsafe",
            missing=["min_seconds_to_resolution"],
            message=(
                f"min_seconds_to_resolution must be >= "
                f"{RISK_BOUND_MIN_SECONDS_TO_RESOLUTION_FLOOR} (14 days); "
                f"got {min_seconds}."
            ),
        )

    hold_cycles = _safe_int(risk.get("max_inventory_hold_cycles"))
    if hold_cycles < RISK_BOUND_MIN_HOLD_CYCLES_FLOOR:
        return GateResult(
            passed=False,
            error_code="risk_framework_unsafe",
            missing=["max_inventory_hold_cycles"],
            message=(
                f"max_inventory_hold_cycles must be >= "
                f"{RISK_BOUND_MIN_HOLD_CYCLES_FLOOR}; got {hold_cycles}."
            ),
        )

    cap = _safe_float(risk.get("max_position_notional_usd"))
    if cap <= 0.0:
        return GateResult(
            passed=False,
            error_code="risk_framework_unsafe",
            missing=["max_position_notional_usd"],
            message=f"max_position_notional_usd must be > 0; got {cap}.",
        )

    return GateResult(passed=True)


def check_execution_path_gate(
    config: Optional[Dict[str, Any]] = None,
    *,
    env: Optional[Dict[str, str]] = None,
) -> GateResult:
    """Block live execution unless py-clob-client is importable AND the
    four POLY_* credentials are present.

    The optional `env` arg allows tests to inject a controlled environment
    without mutating `os.environ`.
    """
    e = env if env is not None else os.environ

    try:
        importlib.import_module("py_clob_client")
    except ImportError:
        return GateResult(
            passed=False,
            error_code="clob_client_missing",
            missing=["py_clob_client"],
            message=(
                "py-clob-client is not installed. Live Polymarket execution requires "
                "either standing up py-clob-client inside Prophet (mirroring "
                "polymarket/maker-rebate-bot/scripts/polymarket_live.py:2049-2087) "
                "or delegating execution to that skill."
            ),
        )

    private_key_present = any(bool(e.get(name)) for name in POLY_PRIVATE_KEY_ENV_VARS)
    missing: List[str] = []
    if not private_key_present:
        missing.append(" or ".join(POLY_PRIVATE_KEY_ENV_VARS))
    for name in REQUIRED_POLY_ENV_VARS:
        if not e.get(name):
            missing.append(name)
    if missing:
        return GateResult(
            passed=False,
            error_code="poly_credentials_missing",
            missing=missing,
            message=(
                "Live Polymarket execution requires "
                "POLY_PRIVATE_KEY (or WALLET_PRIVATE_KEY), POLY_API_KEY, "
                "POLY_PASSPHRASE, and POLY_SECRET. Missing: " + ", ".join(missing)
            ),
        )

    return GateResult(passed=True)


def evaluate_trading_safety_gates(
    config: Optional[Dict[str, Any]] = None,
    *,
    env: Optional[Dict[str, str]] = None,
    min_events: int = DEFAULT_MIN_BACKTEST_EVENTS,
) -> Dict[str, Any]:
    """Run the three gates in canonical order and return a structured
    payload listing every failed gate.

    The return shape is suitable for emitting on stderr / persisting as
    a `trading_safety_blocked` event so a future trading-enable PR can be
    audited against a concrete checklist.
    """
    gates = [
        ("signal_calibration", check_signal_calibration_gate(config, min_events=min_events)),
        ("risk_framework", check_risk_framework_gate(config)),
        ("execution_path", check_execution_path_gate(config, env=env)),
    ]
    blockers = [
        {"gate": name, **result.to_dict()}
        for name, result in gates
        if not result.passed
    ]
    return {
        "status": "ok" if not blockers else "trading_safety_blocked",
        "passed": not blockers,
        "gates": [{"gate": name, **result.to_dict()} for name, result in gates],
        "blockers": blockers,
    }
