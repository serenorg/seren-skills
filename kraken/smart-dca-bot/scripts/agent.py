#!/usr/bin/env python3
"""Kraken Smart DCA Bot runtime.

Implements three modes:
- single_asset
- portfolio
- scanner

Trading execution is local-direct to Kraken REST API.
Seren integration is used for API key bootstrap and optional SerenDB persistence.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
import sys

# --- Force unbuffered stdout so piped/background output is visible immediately ---
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
# --- End unbuffered stdout fix ---

from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return False

from dca_engine import build_window, should_force_fill, window_progress
from backtest_optimizer import optimize_invocation_config
from kraken_client import KrakenAPIError, KrakenClient, KrakenCredentials
from logger import AuditLogger
from optimizer import SUPPORTED_STRATEGIES, compute_rsi, decide_execution
from portfolio_manager import PortfolioManager
from position_tracker import PositionTracker
from scanner import OpportunityScanner
from seren_api_client import SerenAPIError, SerenAPIKeyManager
from serendb_store import SerenDBStore

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


SKILL_NAME = "smart-dca-bot"
DEFAULT_CONFIG_PATH = "config.json"
STATE_DB_PATH = Path("state/dca_runs.db")
STATE_EXPORT_PATH = Path("state/last_state_export.json")
DISCLAIMER_ACK_PATH = Path("state/disclaimer_seen.flag")

SUPPORTED_MODES = {"single_asset", "portfolio", "scanner"}
SUPPORTED_FREQUENCIES = {"daily", "weekly", "biweekly", "monthly"}
SUPPORTED_RISK_LEVELS = {"conservative", "moderate", "aggressive"}

IMPORTANT_DISCLAIMER = """IMPORTANT DISCLAIMERS — READ BEFORE USING

1. NOT FINANCIAL ADVICE: This skill is a software tool, not a financial advisor.
   It does not provide investment, financial, tax, or legal advice. All trading
   decisions are made by you. Consult a licensed financial advisor before investing.

2. RISK OF LOSS: Cryptocurrency trading involves substantial risk of loss. Prices
   can decline significantly. You may lose some or all of your invested capital.
   Only invest money you can afford to lose entirely.

3. NO GUARANTEES: Past performance does not guarantee future results. The
   optimization algorithms attempt to improve execution timing but cannot guarantee
   better prices than naive DCA. Market conditions may render optimizations
   ineffective.

4. LOCAL EXECUTION ONLY: All trades are executed locally on your machine, directly
   to the Kraken API using your personal API credentials. No trades flow through
   Seren Gateway or any third-party intermediary. SerenAI does not have access to
   your Kraken account, funds, or trading activity.

5. API KEY SECURITY: Your Kraken API keys are stored locally in your .env file and
   are never transmitted to SerenAI servers. You are responsible for securing your
   API credentials. Use IP whitelisting and withdrawal restrictions on Kraken.

6. EXCHANGE RISK: This skill depends on Kraken's API availability. Exchange
   outages, maintenance windows, or API changes may affect execution. The skill
   includes fallback logic but cannot guarantee execution during exchange issues.

7. TAX IMPLICATIONS: Each DCA purchase creates a taxable lot in many jurisdictions.
   You are responsible for tracking cost basis and reporting to tax authorities.
   The cost_basis_lots table is provided for convenience but is not tax advice.

8. REGULATORY COMPLIANCE: Cryptocurrency regulations vary by jurisdiction. You are
   responsible for ensuring compliance with all applicable laws and regulations in
   your jurisdiction.

9. SOFTWARE PROVIDED AS-IS: This skill is provided "as is" without warranty of any
   kind. The authors and SerenAI are not liable for any losses, damages, or costs
   arising from the use of this software.
"""

TRADE_EXECUTION_NOTICE = (
    "⚠️ This trade executes directly on Kraken. SerenAI does not custody your funds."
)
LIVE_SAFETY_VERSION = "2026-03-16.kraken-coinbase-live-safety-v1"
LIVE_SAFETY_STATE_PATH = Path("state/live_safety_state.json")


class ConfigError(RuntimeError):
    """Raised for invalid skill configuration."""


class PolicyError(RuntimeError):
    """Raised when policy guards block execution."""


class LiveRiskError(RuntimeError):
    """Raised when live risk controls block or halt execution."""


class LiveSafetyTimeout(TimeoutError):
    """Raised when a live run exceeds the configured timeout."""


_shutdown_requested = False


def _request_shutdown(signum: int, frame: Any) -> None:  # pragma: no cover
    del signum, frame
    global _shutdown_requested
    _shutdown_requested = True


def _default_config() -> dict[str, Any]:
    return {
        "dry_run": True,
        "inputs": {
            "mode": "single_asset",
            "asset": "XBTUSD",
            "dca_amount_usd": 50.0,
            "total_dca_amount_usd": 200.0,
            "frequency": "weekly",
            "dca_window_hours": 24,
            "execution_strategy": "vwap_optimized",
            "risk_level": "moderate",
        },
        "portfolio": {
            "allocations": {"XBTUSD": 0.6, "ETHUSD": 0.25, "SOLUSD": 0.1, "DOTUSD": 0.05},
            "rebalance_threshold_pct": 5.0,
            "sell_to_rebalance": False,
        },
        "scanner": {
            "enabled": True,
            "max_reallocation_pct": 20.0,
            "min_24h_volume_usd": 1_000_000,
            "scan_interval_hours": 6,
            "signals": ["oversold_rsi", "volume_spike", "mean_reversion", "new_listing"],
            "require_approval": True,
            "approval_action": "pending",
        },
        "risk": {
            "max_daily_spend_usd": 500.0,
            "max_notional_usd": 5000.0,
            "max_slippage_bps": 150,
        },
        "runtime": {
            "mock_market_data": True,
            "market_scan_assets": ["XBTUSD", "ETHUSD", "SOLUSD", "DOTUSD", "AVAXUSD"],
            "loop_interval_seconds": 60,
            "cancel_pending_on_shutdown": True,
            "cancel_on_error": True,
            "api_timeout_seconds": 30,
            "run_timeout_seconds": 90,
            "min_cash_reserve_usd": 0.0,
            "max_live_drawdown_pct": 0.0,
        },
        "seren": {
            "auto_register_key": True,
            "api_base_url": "https://api.serendb.com",
        },
        "backtest": {
            "auto_optimize_on_invoke": True,
            "bankroll_usd": 100.0,
            "target_pnl_pct": 25.0,
            "horizon_days": 180,
        },
    }


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _bootstrap_config_path(path: str) -> Path:
    config_path = Path(path)
    if config_path.exists():
        return config_path
    example_path = config_path.with_name("config.example.json")
    if example_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return config_path


def load_config(path: str) -> dict[str, Any]:
    config_path = _bootstrap_config_path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config JSON is invalid: {exc}") from exc
    return deep_merge(_default_config(), raw)


def validate_config(config: dict[str, Any]) -> list[str]:
    def _validate_allocation_sum(
        *,
        name: str,
        values: dict[str, Any],
    ) -> str | None:
        if not values:
            return f"{name} must be a non-empty object"
        try:
            total = sum(float(value) for value in values.values())
        except (TypeError, ValueError):
            return f"{name} must contain numeric values"
        if abs(total - 1.0) < 1e-6 or abs(total - 100.0) < 1e-3:
            return None
        return (
            f"{name} values must sum to 1.0 (fractions) or 100.0 (percent). "
            f"Current total={total:.6f}"
        )

    errors: list[str] = []
    inputs = config.get("inputs", {})

    mode = str(inputs.get("mode", "single_asset"))
    if mode not in SUPPORTED_MODES:
        errors.append(f"Unsupported mode '{mode}'.")

    frequency = str(inputs.get("frequency", "weekly"))
    if frequency not in SUPPORTED_FREQUENCIES:
        errors.append(f"Unsupported frequency '{frequency}'.")

    strategy = str(inputs.get("execution_strategy", "vwap_optimized"))
    if strategy not in SUPPORTED_STRATEGIES:
        errors.append(f"Unsupported execution_strategy '{strategy}'.")

    risk_level = str(inputs.get("risk_level", "moderate"))
    if risk_level not in SUPPORTED_RISK_LEVELS:
        errors.append(f"Unsupported risk_level '{risk_level}'.")

    dca_amount = float(inputs.get("dca_amount_usd", 0.0))
    total_dca_amount = float(inputs.get("total_dca_amount_usd", 0.0))
    if dca_amount <= 0:
        errors.append("inputs.dca_amount_usd must be > 0")
    if total_dca_amount <= 0:
        errors.append("inputs.total_dca_amount_usd must be > 0")

    window_hours = int(inputs.get("dca_window_hours", 24))
    if window_hours < 1 or window_hours > 72:
        errors.append("inputs.dca_window_hours must be between 1 and 72")

    risk = config.get("risk", {})
    if float(risk.get("max_daily_spend_usd", 0.0)) <= 0:
        errors.append("risk.max_daily_spend_usd must be > 0")
    if float(risk.get("max_notional_usd", 0.0)) <= 0:
        errors.append("risk.max_notional_usd must be > 0")
    if float(risk.get("max_slippage_bps", 0.0)) < 0:
        errors.append("risk.max_slippage_bps must be >= 0")

    portfolio = config.get("portfolio", {})
    allocations = portfolio.get("allocations", {})
    if not isinstance(allocations, dict):
        errors.append("portfolio.allocations must be a non-empty object")
    else:
        maybe_error = _validate_allocation_sum(name="portfolio.allocations", values=allocations)
        if maybe_error:
            errors.append(maybe_error)

    scanner = config.get("scanner", {})
    scanner_signals = scanner.get("signals", [])
    allowed_signals = {"oversold_rsi", "volume_spike", "mean_reversion", "new_listing"}
    unknown_signals = [signal for signal in scanner_signals if signal not in allowed_signals]
    if unknown_signals:
        errors.append(f"scanner.signals contains unknown values: {unknown_signals}")

    base_allocations = scanner.get("base_allocations")
    if base_allocations is not None:
        if not isinstance(base_allocations, dict):
            errors.append("scanner.base_allocations must be an object when provided")
        else:
            maybe_error = _validate_allocation_sum(
                name="scanner.base_allocations",
                values=base_allocations,
            )
            if maybe_error:
                errors.append(maybe_error)

    return errors


def init_state_db() -> None:
    STATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(STATE_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                session_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                target_notional_usd REAL NOT NULL,
                executed_notional_usd REAL NOT NULL,
                details_json TEXT NOT NULL
            )
            """
        )
        conn.commit()


def local_daily_spend() -> float:
    init_state_db()
    day_prefix = datetime.now(tz=UTC).date().isoformat()
    with sqlite3.connect(STATE_DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(executed_notional_usd), 0)
            FROM runs
            WHERE substr(created_at, 1, 10) = ?
              AND status IN ('ok', 'simulated')
            """,
            (day_prefix,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def persist_local_run(
    *,
    session_id: str,
    mode: str,
    status: str,
    target_notional_usd: float,
    executed_notional_usd: float,
    details: dict[str, Any],
) -> None:
    init_state_db()
    with sqlite3.connect(STATE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO runs (
                created_at, session_id, mode, status,
                target_notional_usd, executed_notional_usd, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(tz=UTC).isoformat(),
                session_id,
                mode,
                status,
                target_notional_usd,
                executed_notional_usd,
                json.dumps(details, sort_keys=True),
            ),
        )
        conn.commit()


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_mode(raw_mode: str) -> str:
    return raw_mode


def show_disclaimer_if_first_run(*, accept_risk_disclaimer: bool) -> bool:
    if DISCLAIMER_ACK_PATH.exists():
        return False
    print(IMPORTANT_DISCLAIMER)
    if not accept_risk_disclaimer:
        raise PolicyError(
            "First run requires explicit acknowledgment. Re-run with --accept-risk-disclaimer."
        )
    DISCLAIMER_ACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    DISCLAIMER_ACK_PATH.write_text(datetime.now(tz=UTC).isoformat(), encoding="utf-8")
    return True


def build_kraken_client(config: dict[str, Any]) -> KrakenClient | None:
    runtime = config.get("runtime", {})
    if bool(runtime.get("mock_market_data", True)):
        return None

    api_key = (os.getenv("KRAKEN_API_KEY") or "").strip()
    api_secret = (os.getenv("KRAKEN_API_SECRET") or "").strip()
    if not api_key or not api_secret:
        raise ConfigError(
            "KRAKEN_API_KEY and KRAKEN_API_SECRET are required when runtime.mock_market_data=false"
        )

    return KrakenClient(
        credentials=KrakenCredentials(api_key=api_key, api_secret=api_secret),
        base_url=os.getenv("KRAKEN_API_BASE_URL", "https://api.kraken.com"),
        timeout_seconds=int(runtime.get("api_timeout_seconds", 30)),
    )


def _load_live_safety_state() -> dict[str, Any]:
    try:
        payload = json.loads(LIVE_SAFETY_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("runtime_version", LIVE_SAFETY_VERSION)
    return payload


def _persist_live_safety_state(payload: dict[str, Any]) -> dict[str, Any]:
    state = dict(payload)
    state["runtime_version"] = LIVE_SAFETY_VERSION
    state["updated_at"] = _now().isoformat()
    LIVE_SAFETY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIVE_SAFETY_STATE_PATH.write_text(
        json.dumps(state, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return state


def _run_with_timeout(label: str, timeout_seconds: float, fn):
    timeout = float(timeout_seconds)
    if timeout <= 0 or not hasattr(signal, "SIGALRM"):
        return fn()

    previous_handler = signal.getsignal(signal.SIGALRM)

    def _handle_timeout(signum: int, frame: Any) -> None:  # pragma: no cover
        del signum, frame
        raise LiveSafetyTimeout(f"{label} timed out after {timeout:.2f}s")

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _tracked_pairs_for_live_risk(config: dict[str, Any]) -> list[str]:
    pairs: set[str] = set()
    inputs = config.get("inputs", {})
    asset = str(inputs.get("asset", "")).upper().strip()
    if asset:
        pairs.add(asset)

    for key in config.get("portfolio", {}).get("allocations", {}):
        text = str(key).upper().strip()
        if text:
            pairs.add(text)

    for key in config.get("scanner", {}).get("base_allocations", {}):
        text = str(key).upper().strip()
        if text:
            pairs.add(text)

    for pair in config.get("runtime", {}).get("market_scan_assets", []):
        text = str(pair).upper().strip()
        if text:
            pairs.add(text)

    return sorted(pairs)


def _quote_cash_balance_usd(balances: dict[str, float]) -> float:
    total = 0.0
    for key in ("ZUSD", "USD", "USDC"):
        total += _float(balances.get(key), 0.0)
    return total


def _compute_live_risk(
    *,
    config: dict[str, Any],
    client: KrakenClient | None,
    live_state: dict[str, Any],
) -> dict[str, Any]:
    if client is None:
        return {
            "runtime_version": LIVE_SAFETY_VERSION,
            "quote_balance_usd": 0.0,
            "current_equity_usd": 0.0,
            "peak_equity_usd": 0.0,
            "drawdown_usd": 0.0,
            "drawdown_pct": 0.0,
            "tracked_pairs": [],
        }

    balances = {str(key).upper(): _float(value) for key, value in client.get_balance().items()}
    tracked_pairs = _tracked_pairs_for_live_risk(config)
    prices: dict[str, float] = {}
    for pair in tracked_pairs:
        try:
            prices[pair] = _float(get_market_snapshot(client, pair).get("price", 0.0))
        except Exception:  # noqa: BLE001
            continue

    holdings_value = sum(
        PortfolioManager._lookup_balance(balances, pair) * _float(prices.get(pair), 0.0)
        for pair in tracked_pairs
    )
    quote_balance = _quote_cash_balance_usd(balances)
    current_equity = quote_balance + holdings_value
    prior_peak = _float(live_state.get("peak_equity_usd"), current_equity)
    peak_equity = max(prior_peak, current_equity)
    drawdown_usd = max(peak_equity - current_equity, 0.0)
    drawdown_pct = (drawdown_usd / peak_equity * 100.0) if peak_equity > 0 else 0.0
    return {
        "runtime_version": LIVE_SAFETY_VERSION,
        "quote_balance_usd": round(quote_balance, 2),
        "current_equity_usd": round(current_equity, 2),
        "peak_equity_usd": round(peak_equity, 2),
        "drawdown_usd": round(drawdown_usd, 2),
        "drawdown_pct": round(drawdown_pct, 4),
        "tracked_pairs": tracked_pairs,
    }


def _planned_notional_usd(config: dict[str, Any], mode: str) -> float:
    inputs = config.get("inputs", {})
    if mode == "portfolio":
        return _float(inputs.get("total_dca_amount_usd", inputs.get("dca_amount_usd", 0.0)))
    return _float(inputs.get("dca_amount_usd", 0.0))


def _enforce_live_safety(
    *,
    config: dict[str, Any],
    client: KrakenClient | None,
    mode: str,
) -> dict[str, Any]:
    live_state = _load_live_safety_state()
    live_risk = _compute_live_risk(config=config, client=client, live_state=live_state)
    _persist_live_safety_state(live_risk)

    runtime = config.get("runtime", {})
    min_cash_reserve = _float(runtime.get("min_cash_reserve_usd", 0.0))
    planned_notional = _planned_notional_usd(config, mode)
    if min_cash_reserve > 0 and live_risk["quote_balance_usd"] - planned_notional < min_cash_reserve:
        raise LiveRiskError(
            "live cash reserve would be breached: "
            f"quote_balance={live_risk['quote_balance_usd']:.2f} "
            f"- planned_notional={planned_notional:.2f} < reserve={min_cash_reserve:.2f}"
        )

    max_drawdown_pct = _float(runtime.get("max_live_drawdown_pct", 0.0))
    if max_drawdown_pct > 0 and live_risk["drawdown_pct"] > max_drawdown_pct:
        raise LiveRiskError(
            f"live drawdown {live_risk['drawdown_pct']:.2f}% exceeds cap {max_drawdown_pct:.2f}%"
        )

    return live_risk


def _cancel_orders_on_error(
    *,
    config: dict[str, Any],
    client: KrakenClient | None,
    execution_context: dict[str, Any],
) -> list[dict[str, Any]]:
    if client is None or not bool(config.get("runtime", {}).get("cancel_on_error", True)):
        return []

    cancelled: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_order_id in execution_context.get("order_ids", []):
        order_id = str(raw_order_id).strip()
        if not order_id or order_id in seen or order_id == "dry-run":
            continue
        seen.add(order_id)
        try:
            cancelled.append({"order_id": order_id, "result": client.cancel_order(order_id)})
        except Exception as exc:  # noqa: BLE001
            cancelled.append({"order_id": order_id, "error": str(exc)})
    return cancelled


def ensure_seren_api_key(config: dict[str, Any]) -> str:
    seren_cfg = config.get("seren", {})
    manager = SerenAPIKeyManager(
        api_base_url=str(seren_cfg.get("api_base_url", "https://api.serendb.com")),
        env_file=".env",
    )
    auto_register = bool(seren_cfg.get("auto_register_key", True))
    return manager.ensure_api_key(auto_register=auto_register)


def _mock_snapshot(pair: str) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    seed = sum(ord(ch) for ch in pair) + (now.hour * 60) + now.minute
    rng = random.Random(seed)
    base_price = 80.0 + (seed % 300)
    price = base_price * (1.0 + rng.uniform(-0.015, 0.02))
    vwap = price * (1.0 + rng.uniform(-0.003, 0.003))
    bid = price * 0.999
    ask = price * 1.001
    low_24h = price * 0.975
    high_24h = price * 1.03
    candles = [round(price * (1.0 + rng.uniform(-0.02, 0.02)), 6) for _ in range(20)]
    candles[-1] = round(price, 6)

    daily_closes: list[float] = []
    daily_volumes_usd: list[float] = []
    cursor = price * (1.0 + rng.uniform(-0.1, 0.1))
    for _ in range(35):
        cursor *= 1.0 + rng.uniform(-0.04, 0.04)
        close = round(cursor, 6)
        daily_closes.append(close)
        daily_volumes_usd.append(float(rng.uniform(900_000, 8_000_000)))

    price_7d_ago = daily_closes[-8] if len(daily_closes) >= 8 else daily_closes[0]
    avg_volume_30d = sum(daily_volumes_usd[-30:]) / max(len(daily_volumes_usd[-30:]), 1)
    volume_24h = daily_volumes_usd[-1]

    return {
        "pair": pair,
        "price": round(price, 6),
        "vwap": round(vwap, 6),
        "bid": round(bid, 6),
        "ask": round(ask, 6),
        "low_24h": round(low_24h, 6),
        "high_24h": round(high_24h, 6),
        "volume_24h_usd": float(volume_24h),
        "avg_volume_30d_usd": float(avg_volume_30d),
        "price_7d_ago": float(price_7d_ago),
        "depth_score": round(0.45 + (seed % 50) / 100.0, 4),
        "candles": candles,
        "daily_closes": daily_closes,
        "new_listing_days": int(rng.randint(5, 500)),
    }


def get_market_snapshot(client: KrakenClient | None, pair: str) -> dict[str, Any]:
    if client is None:
        return _mock_snapshot(pair)

    ticker = client.get_ticker(pair)
    key = next(iter(ticker.keys()))
    row = ticker[key]
    price = _float(row.get("c", [0])[0])
    vwap = _float(row.get("p", [price, price])[1], fallback=price)
    bid = _float(row.get("b", [price])[0], fallback=price)
    ask = _float(row.get("a", [price])[0], fallback=price)
    low_24h = _float(row.get("l", [price, price])[1], fallback=price)
    high_24h = _float(row.get("h", [price, price])[1], fallback=price)

    depth = client.get_depth(pair, 25)
    depth_key = next(iter(depth.keys()))
    bids = depth[depth_key].get("bids", [])
    asks = depth[depth_key].get("asks", [])
    bid_depth = sum(_float(level[1]) for level in bids[:10])
    ask_depth = sum(_float(level[1]) for level in asks[:10])
    depth_score = 0.5
    if bid_depth + ask_depth > 0:
        depth_score = min((2.0 * min(bid_depth, ask_depth)) / (bid_depth + ask_depth), 1.0)

    ohlc_15m = client.get_ohlc(pair, interval=15)
    ohlc_15m_key = next((k for k in ohlc_15m.keys() if k != "last"), pair)
    candles_raw = ohlc_15m.get(ohlc_15m_key, [])
    closes = [_float(item[4]) for item in candles_raw[-20:]]
    if not closes:
        closes = [price] * 20

    ohlc_daily = client.get_ohlc(pair, interval=1440)
    ohlc_daily_key = next((k for k in ohlc_daily.keys() if k != "last"), pair)
    daily_raw = ohlc_daily.get(ohlc_daily_key, [])
    daily_closes = [_float(item[4], fallback=price) for item in daily_raw]
    if not daily_closes:
        daily_closes = [price] * 30

    daily_volume_usd = [
        _float(item[6]) * _float(item[4], fallback=price)
        for item in daily_raw
    ]
    volume = _float(row.get("v", [0, 0])[1]) * price
    if not daily_volume_usd:
        daily_volume_usd = [volume] * min(len(daily_closes), 30)
    avg_volume_30d = sum(daily_volume_usd[-30:]) / max(len(daily_volume_usd[-30:]), 1)
    price_7d_ago = daily_closes[-8] if len(daily_closes) >= 8 else daily_closes[0]
    listing_days = 999
    if daily_raw:
        first_ts = int(_float(daily_raw[0][0]))
        if first_ts > 0:
            listing_days = int(
                max((datetime.now(tz=UTC).timestamp() - first_ts) / 86400.0, 0.0)
            )

    return {
        "pair": pair,
        "price": price,
        "vwap": vwap,
        "bid": bid,
        "ask": ask,
        "low_24h": low_24h,
        "high_24h": high_24h,
        "volume_24h_usd": volume,
        "avg_volume_30d_usd": avg_volume_30d,
        "price_7d_ago": price_7d_ago,
        "depth_score": depth_score,
        "candles": closes,
        "daily_closes": daily_closes,
        "new_listing_days": listing_days,
    }


def apply_risk_policy(
    *,
    config: dict[str, Any],
    notional_usd: float,
    expected_slippage_bps: float,
    check_daily_cap: bool = True,
) -> None:
    risk = config.get("risk", {})
    max_daily = _float(risk.get("max_daily_spend_usd"), 500.0)
    max_notional = _float(risk.get("max_notional_usd"), 5000.0)
    max_slippage = _float(risk.get("max_slippage_bps"), 150.0)

    if notional_usd > max_notional:
        raise PolicyError(
            f"notional_usd={notional_usd:.2f} exceeds risk.max_notional_usd={max_notional:.2f}"
        )

    if check_daily_cap:
        spent = local_daily_spend()
        if spent + notional_usd > max_daily:
            raise PolicyError(
                "daily spend cap exceeded: "
                f"spent={spent:.2f} + requested={notional_usd:.2f} > {max_daily:.2f}"
            )

    if expected_slippage_bps > max_slippage:
        raise PolicyError(
            f"expected slippage {expected_slippage_bps:.2f} bps exceeds cap {max_slippage:.2f} bps"
        )


def enforce_plan_daily_budget(*, config: dict[str, Any], planned_notional_usd: float) -> None:
    risk = config.get("risk", {})
    max_daily = _float(risk.get("max_daily_spend_usd"), 500.0)
    spent = local_daily_spend()
    if spent + planned_notional_usd > max_daily:
        raise PolicyError(
            "daily spend cap exceeded for execution plan: "
            f"spent={spent:.2f} + planned={planned_notional_usd:.2f} > {max_daily:.2f}"
        )


def _estimated_slippage_bps(snapshot: dict[str, Any]) -> float:
    bid = _float(snapshot.get("bid"))
    ask = _float(snapshot.get("ask"))
    mid = (bid + ask) / 2.0 if (bid and ask) else 0.0
    if mid <= 0:
        return 0.0
    return ((ask - bid) / mid) * 10000.0


def _build_market_order_payload(pair: str, notional_usd: float, execution_price: float) -> dict[str, str]:
    volume = notional_usd / max(execution_price, 1e-9)
    return {
        "pair": pair,
        "ordertype": "market",
        "side": "buy",
        "volume": f"{volume:.8f}",
    }


def execute_order(
    *,
    client: KrakenClient | None,
    dry_run: bool,
    pair: str,
    notional_usd: float,
    decision_order_type: str,
    limit_price: float | None,
    execution_price_hint: float,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    print(TRADE_EXECUTION_NOTICE)
    if dry_run or client is None:
        qty = notional_usd / max(execution_price_hint, 1e-9)
        return {
            "status": "simulated",
            "order_id": "dry-run",
            "pair": pair,
            "requested_notional_usd": round(notional_usd, 2),
            "executed_notional_usd": round(notional_usd, 2),
            "executed_price": round(execution_price_hint, 8),
            "executed_quantity": round(qty, 8),
            "execution_notice": TRADE_EXECUTION_NOTICE,
        }

    if decision_order_type == "limit" and limit_price is not None:
        payload = client.add_order(
            pair=pair,
            ordertype="limit",
            side="buy",
            volume=f"{(notional_usd / max(limit_price, 1e-9)):.8f}",
            price=f"{limit_price:.8f}",
        )
        txid = payload.get("txid", [""])[0] if isinstance(payload.get("txid"), list) else ""
        if execution_context is not None and txid:
            execution_context.setdefault("order_ids", []).append(txid)
        return {
            "status": "pending",
            "order_id": txid,
            "pair": pair,
            "requested_notional_usd": round(notional_usd, 2),
            "executed_notional_usd": 0.0,
            "executed_price": None,
            "executed_quantity": 0.0,
            "limit_price": round(limit_price, 8),
            "execution_notice": TRADE_EXECUTION_NOTICE,
        }

    payload = _build_market_order_payload(pair, notional_usd, execution_price_hint)
    result = client.add_order(
        pair=payload["pair"],
        ordertype=payload["ordertype"],
        side=payload["side"],
        volume=payload["volume"],
    )
    txid = result.get("txid", [""])[0] if isinstance(result.get("txid"), list) else ""
    if execution_context is not None and txid:
        execution_context.setdefault("order_ids", []).append(txid)
    return {
        "status": "ok",
        "order_id": txid,
        "pair": pair,
        "requested_notional_usd": round(notional_usd, 2),
        "executed_notional_usd": round(notional_usd, 2),
        "executed_price": round(execution_price_hint, 8),
        "executed_quantity": round(notional_usd / max(execution_price_hint, 1e-9), 8),
        "execution_notice": TRADE_EXECUTION_NOTICE,
    }


def execute_decision_order(
    *,
    client: KrakenClient | None,
    dry_run: bool,
    pair: str,
    notional_usd: float,
    decision_order_type: str,
    limit_price: float | None,
    execution_price_hint: float,
    decision_slices: list[float] | None = None,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not decision_slices or len(decision_slices) <= 1:
        return execute_order(
            client=client,
            dry_run=dry_run,
            pair=pair,
            notional_usd=notional_usd,
            decision_order_type=decision_order_type,
            limit_price=limit_price,
            execution_price_hint=execution_price_hint,
            execution_context=execution_context,
        )

    positive_slices = [float(value) for value in decision_slices if float(value) > 0]
    weight_total = sum(positive_slices)
    if weight_total <= 0:
        return execute_order(
            client=client,
            dry_run=dry_run,
            pair=pair,
            notional_usd=notional_usd,
            decision_order_type=decision_order_type,
            limit_price=limit_price,
            execution_price_hint=execution_price_hint,
            execution_context=execution_context,
        )

    child_orders: list[dict[str, Any]] = []
    for weight in positive_slices:
        child_notional = round(notional_usd * (weight / weight_total), 2)
        if child_notional <= 0:
            continue
        child_orders.append(
            execute_order(
                client=client,
                dry_run=dry_run,
                pair=pair,
                notional_usd=child_notional,
                decision_order_type=decision_order_type,
                limit_price=limit_price,
                execution_price_hint=execution_price_hint,
                execution_context=execution_context,
            )
        )

    executed_notional = sum(_float(row.get("executed_notional_usd", 0.0)) for row in child_orders)
    executed_quantity = sum(_float(row.get("executed_quantity", 0.0)) for row in child_orders)
    weighted_price = (
        executed_notional / max(executed_quantity, 1e-9)
        if executed_notional > 0 and executed_quantity > 0
        else None
    )
    statuses = [str(row.get("status", "")) for row in child_orders]
    if any(status == "pending" for status in statuses):
        status = "pending"
    elif any(status == "simulated" for status in statuses):
        status = "simulated"
    else:
        status = "ok"

    order_ids = [str(row.get("order_id", "")).strip() for row in child_orders if str(row.get("order_id", "")).strip()]
    return {
        "status": status,
        "order_id": ",".join(order_ids),
        "pair": pair,
        "requested_notional_usd": round(notional_usd, 2),
        "executed_notional_usd": round(executed_notional, 2),
        "executed_price": round(weighted_price, 8) if weighted_price is not None else None,
        "executed_quantity": round(executed_quantity, 8),
        "execution_notice": TRADE_EXECUTION_NOTICE,
        "slice_count": len(child_orders),
        "child_orders": child_orders,
    }


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _portfolio_mode(
    *,
    config: dict[str, Any],
    client: KrakenClient | None,
    dry_run: bool,
    store: SerenDBStore,
    tracker: PositionTracker,
    logger: AuditLogger,
    session_id: str,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inputs = config["inputs"]
    total_amount = _float(inputs.get("total_dca_amount_usd", inputs.get("dca_amount_usd", 0.0)))
    strategy = str(inputs.get("execution_strategy", "vwap_optimized"))

    manager = PortfolioManager()
    targets = manager.normalize_allocations(config["portfolio"]["allocations"])
    threshold = _float(config["portfolio"].get("rebalance_threshold_pct", 5.0))

    prices: dict[str, float] = {}
    snapshots: dict[str, dict[str, Any]] = {}
    for pair in targets:
        snap = get_market_snapshot(client, pair)
        snapshots[pair] = snap
        prices[pair] = _float(snap["price"])

    balances: dict[str, float] = {}
    if client is not None:
        try:
            balances = {k.upper(): _float(v) for k, v in client.get_balance().items()}
        except KrakenAPIError as exc:
            logger.log_error("kraken_balance", str(exc), {})
            balances = {}

    current = manager.current_allocations(balances, prices, targets)
    plan = manager.build_dca_buy_plan(
        total_dca_amount_usd=total_amount,
        targets=targets,
        current=current,
        rebalance_threshold_pct=threshold,
    )

    window = build_window(
        now=_now(),
        frequency=str(inputs.get("frequency", "weekly")),
        window_hours=int(inputs.get("dca_window_hours", 24)),
    )
    progress = window_progress(window, _now())
    forced = should_force_fill(window, _now())

    executions: list[dict[str, Any]] = []
    approved_orders: list[tuple[dict[str, Any], dict[str, Any], Any]] = []
    for order in plan["orders"]:
        pair = str(order["asset"])
        notional = _float(order["notional_usd"])
        snap = snapshots[pair]
        decision = decide_execution(
            strategy=strategy,
            snapshot=snap,
            window_progress=progress,
            force_fill=forced,
        )
        if not decision.should_execute:
            executions.append({"pair": pair, "status": "deferred", "decision": decision.__dict__})
            continue
        approved_orders.append((order, snap, decision))

    if approved_orders:
        enforce_plan_daily_budget(
            config=config,
            planned_notional_usd=sum(_float(order["notional_usd"]) for order, _, _ in approved_orders),
        )

    for order, snap, decision in approved_orders:
        pair = str(order["asset"])
        notional = _float(order["notional_usd"])
        apply_risk_policy(
            config=config,
            notional_usd=notional,
            expected_slippage_bps=_estimated_slippage_bps(snap),
            check_daily_cap=False,
        )

        executed = execute_decision_order(
            client=client,
            dry_run=dry_run,
            pair=pair,
            notional_usd=notional,
            decision_order_type=decision.order_type,
            limit_price=decision.limit_price,
            execution_price_hint=_float(snap["price"]),
            decision_slices=decision.slices,
            execution_context=execution_context,
        )
        execution_id = str(uuid.uuid4())
        executed_notional = _float(executed.get("executed_notional_usd", 0.0))
        executed_price = _float(executed.get("executed_price", 0.0))
        if executed_notional > 0:
            lot = tracker.add_buy_lot(
                asset=pair,
                quantity=_float(executed["executed_quantity"]),
                cost_basis_usd=executed_notional,
                execution_id=execution_id,
            )
            store.persist_cost_basis_lot(lot.__dict__)

        vwap = _float(snap["vwap"])
        savings_bps = (
            int(((vwap - executed_price) / max(vwap, 1e-9)) * 10000)
            if executed_price > 0
            else None
        )
        row = {
            "execution_id": execution_id,
            "mode": "portfolio",
            "asset": pair,
            "target_amount_usd": round(notional, 2),
            "executed_amount_usd": round(executed_notional, 2) if executed_notional > 0 else None,
            "executed_price": round(executed_price, 8) if executed_price > 0 else None,
            "vwap_at_execution": round(vwap, 8),
            "savings_vs_naive_bps": savings_bps,
            "strategy": strategy,
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
            "executed_at": _now().isoformat() if executed_notional > 0 else None,
            "status": executed["status"],
            "kraken_order_id": executed["order_id"],
            "metadata": {"decision": decision.__dict__, "session_id": session_id},
        }
        store.persist_execution(row)
        logger.log_execution(row)
        logger.log_order({**executed, "decision": decision.__dict__})
        executions.append({"pair": pair, "executed": executed, "decision": decision.__dict__})

    total_value = sum(
        PortfolioManager._lookup_balance(balances, pair) * _float(prices.get(pair, 0.0))
        for pair in targets
    )
    snapshot_row = {
        "snapshot_id": str(uuid.uuid4()),
        "total_value_usd": round(total_value, 2),
        "allocations": current,
        "target_allocations": targets,
        "drift_max_pct": round(_float(plan.get("max_abs_drift_pct", 0.0)), 4),
        "metadata": {"session_id": session_id},
    }
    store.persist_portfolio_snapshot(snapshot_row)
    logger.log_portfolio(snapshot_row)

    return {
        "mode": "portfolio",
        "status": "ok",
        "plan": plan,
        "executions": executions,
        "snapshot": snapshot_row,
    }


def _single_asset_mode(
    *,
    config: dict[str, Any],
    client: KrakenClient | None,
    dry_run: bool,
    store: SerenDBStore,
    tracker: PositionTracker,
    logger: AuditLogger,
    session_id: str,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inputs = config["inputs"]
    pair = str(inputs.get("asset", "XBTUSD")).upper()
    amount = _float(inputs.get("dca_amount_usd", 0.0))
    strategy = str(inputs.get("execution_strategy", "vwap_optimized"))

    snapshot = get_market_snapshot(client, pair)
    window = build_window(
        now=_now(),
        frequency=str(inputs.get("frequency", "weekly")),
        window_hours=int(inputs.get("dca_window_hours", 24)),
    )
    progress = window_progress(window, _now())
    forced = should_force_fill(window, _now())

    decision = decide_execution(
        strategy=strategy,
        snapshot=snapshot,
        window_progress=progress,
        force_fill=forced,
    )

    if not decision.should_execute:
        return {
            "mode": "single_asset",
            "status": "ok",
            "decision": decision.__dict__,
            "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
            "message": "No execution yet; continue monitoring active DCA window.",
        }

    apply_risk_policy(
        config=config,
        notional_usd=amount,
        expected_slippage_bps=_estimated_slippage_bps(snapshot),
    )
    executed = execute_decision_order(
        client=client,
        dry_run=dry_run,
        pair=pair,
        notional_usd=amount,
        decision_order_type=decision.order_type,
        limit_price=decision.limit_price,
        execution_price_hint=_float(snapshot["price"]),
        decision_slices=decision.slices,
        execution_context=execution_context,
    )

    execution_id = str(uuid.uuid4())
    executed_notional = _float(executed.get("executed_notional_usd", 0.0))
    executed_price = _float(executed.get("executed_price", 0.0))
    if executed_notional > 0:
        lot = tracker.add_buy_lot(
            asset=pair,
            quantity=_float(executed["executed_quantity"]),
            cost_basis_usd=executed_notional,
            execution_id=execution_id,
        )
        store.persist_cost_basis_lot(lot.__dict__)

    vwap = _float(snapshot["vwap"])
    savings_bps = (
        int(((vwap - executed_price) / max(vwap, 1e-9)) * 10000)
        if executed_price > 0
        else None
    )

    row = {
        "execution_id": execution_id,
        "mode": "single_asset",
        "asset": pair,
        "target_amount_usd": round(amount, 2),
        "executed_amount_usd": round(executed_notional, 2) if executed_notional > 0 else None,
        "executed_price": round(executed_price, 8) if executed_price > 0 else None,
        "vwap_at_execution": round(vwap, 8),
        "savings_vs_naive_bps": savings_bps,
        "strategy": strategy,
        "window_start": window.start.isoformat(),
        "window_end": window.end.isoformat(),
        "executed_at": _now().isoformat() if executed_notional > 0 else None,
        "status": executed["status"],
        "kraken_order_id": executed["order_id"],
        "metadata": {"decision": decision.__dict__, "session_id": session_id},
    }
    store.persist_execution(row)
    logger.log_execution(row)
    logger.log_order({**executed, "decision": decision.__dict__})

    return {
        "mode": "single_asset",
        "status": "ok",
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "decision": decision.__dict__,
        "execution": executed,
        "savings_vs_naive_bps": savings_bps,
        "market_snapshot": snapshot,
    }


def _scanner_market_rows(
    *,
    config: dict[str, Any],
    client: KrakenClient | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scan_assets = list(config.get("runtime", {}).get("market_scan_assets", []))
    for pair in scan_assets:
        snap = get_market_snapshot(client, pair)
        closes = [float(v) for v in snap.get("candles", [])]
        rsi = compute_rsi(closes, period=14)
        price_change_24h = ((snap["price"] - snap["vwap"]) / max(snap["vwap"], 1e-9)) * 100.0
        price_7d_ago = _float(snap.get("price_7d_ago"), fallback=_float(snap["price"]))
        price_change_7d = (
            ((_float(snap["price"]) - price_7d_ago) / max(price_7d_ago, 1e-9)) * 100.0
        )
        avg_volume_30d = _float(snap.get("avg_volume_30d_usd"), fallback=0.0)
        volume_ratio = (
            _float(snap.get("volume_24h_usd"), fallback=0.0) / max(avg_volume_30d, 1e-9)
            if avg_volume_30d > 0
            else 1.0
        )
        daily_closes = [float(v) for v in snap.get("daily_closes", [])]
        if len(daily_closes) >= 20:
            sma20 = sum(daily_closes[-20:]) / 20.0
        else:
            sma20 = _float(snap["price"])
        rows.append(
            {
                "asset": pair,
                "price": float(snap.get("price", 0.0)),
                "volume_24h_usd": float(snap.get("volume_24h_usd", 0.0)),
                "volume_ratio": float(volume_ratio),
                "rsi_14": float(rsi),
                "price_change_24h_pct": float(price_change_24h),
                "price_change_7d_pct": float(price_change_7d),
                "sma20": float(sma20),
                "new_listing_days": int(snap.get("new_listing_days", 999)),
                "accumulation_score": float(min(max((50.0 - abs(rsi - 50.0)) / 50.0, 0.0), 1.0)),
            }
        )
    return rows


def _scanner_mode(
    *,
    config: dict[str, Any],
    client: KrakenClient | None,
    dry_run: bool,
    store: SerenDBStore,
    tracker: PositionTracker,
    logger: AuditLogger,
    session_id: str,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scanner_cfg = config.get("scanner", {})
    portfolio_allocations = config.get("portfolio", {}).get("allocations", {})
    if scanner_cfg.get("base_allocations"):
        base_allocations = scanner_cfg["base_allocations"]
        allocation_source = "scanner.base_allocations"
    elif portfolio_allocations:
        base_allocations = portfolio_allocations
        allocation_source = "portfolio.allocations"
    else:
        base_allocations = {"XBTUSD": 0.6, "ETHUSD": 0.25, "SOLUSD": 0.15}
        allocation_source = "scanner.defaults"

    scanner = OpportunityScanner(
        min_24h_volume_usd=_float(scanner_cfg.get("min_24h_volume_usd", 1_000_000)),
        max_reallocation_pct=_float(scanner_cfg.get("max_reallocation_pct", 20.0)),
        enabled_signals=list(scanner_cfg.get("signals", [])),
    )

    rows = _scanner_market_rows(config=config, client=client)
    signals = scanner.scan(rows, base_allocations)
    signal_payloads = [signal.to_dict() for signal in signals]

    require_approval = bool(scanner_cfg.get("require_approval", True))
    approval_action = str(scanner_cfg.get("approval_action", "pending")).strip().lower()
    effective_allocations = PortfolioManager.normalize_allocations(base_allocations)

    if (not require_approval) and signal_payloads:
        top = signal_payloads[0]
        reallocation = _float(top["reallocation_pct"], 0.0) / 100.0
        source_asset = max(effective_allocations.items(), key=lambda row: row[1])[0]
        target_asset = str(top["asset"])
        if source_asset != target_asset and reallocation > 0:
            effective_allocations[source_asset] = max(effective_allocations[source_asset] - reallocation, 0.0)
            effective_allocations[target_asset] = effective_allocations.get(target_asset, 0.0) + reallocation
            effective_allocations = PortfolioManager.normalize_allocations(effective_allocations)

    approval_state = "auto_applied"
    if require_approval and signal_payloads:
        if approval_action == "approve":
            top = signal_payloads[0]
            reallocation = _float(top["reallocation_pct"], 0.0) / 100.0
            source_asset = max(effective_allocations.items(), key=lambda row: row[1])[0]
            target_asset = str(top["asset"])
            if source_asset != target_asset and reallocation > 0:
                effective_allocations[source_asset] = max(effective_allocations[source_asset] - reallocation, 0.0)
                effective_allocations[target_asset] = effective_allocations.get(target_asset, 0.0) + reallocation
                effective_allocations = PortfolioManager.normalize_allocations(effective_allocations)
            approval_state = "approved"
        elif approval_action == "modify":
            top = signal_payloads[0]
            override_pct = _float(
                scanner_cfg.get("approval_reallocation_pct"),
                fallback=_float(top["reallocation_pct"], 0.0),
            )
            reallocation = min(max(override_pct, 0.0), _float(scanner_cfg.get("max_reallocation_pct", 20.0))) / 100.0
            source_asset = max(effective_allocations.items(), key=lambda row: row[1])[0]
            target_asset = str(top["asset"])
            if source_asset != target_asset and reallocation > 0:
                effective_allocations[source_asset] = max(effective_allocations[source_asset] - reallocation, 0.0)
                effective_allocations[target_asset] = effective_allocations.get(target_asset, 0.0) + reallocation
                effective_allocations = PortfolioManager.normalize_allocations(effective_allocations)
            approval_state = "modified"
        elif approval_action == "skip":
            approval_state = "skipped"
        else:
            approval_state = "pending_approval"

    for payload in signal_payloads:
        payload["metadata"] = {"session_id": session_id}
        store.persist_scanner_signal(payload, user_action=approval_state)
        logger.log_scanner(payload)

    if require_approval and approval_state == "pending_approval":
        return {
            "mode": "scanner",
            "status": "approval_required",
            "signals": signal_payloads,
            "require_approval": require_approval,
            "approval_action": approval_action,
            "effective_allocations": effective_allocations,
            "allocation_source": allocation_source,
            "executions": [],
            "plan": {"mode": "pending_approval", "orders": [], "drift": [], "max_abs_drift_pct": 0.0},
        }

    total_amount = _float(config["inputs"].get("dca_amount_usd", 0.0))
    pm = PortfolioManager()
    plan = pm.build_dca_buy_plan(
        total_dca_amount_usd=total_amount,
        targets=effective_allocations,
        current=effective_allocations,
        rebalance_threshold_pct=100.0,
    )

    execution_rows: list[dict[str, Any]] = []
    strategy = str(config["inputs"].get("execution_strategy", "vwap_optimized"))
    window = build_window(
        now=_now(),
        frequency=str(config["inputs"].get("frequency", "weekly")),
        window_hours=int(config["inputs"].get("dca_window_hours", 24)),
    )
    progress = window_progress(window, _now())

    approved_orders: list[tuple[dict[str, Any], dict[str, Any], Any]] = []
    for order in plan["orders"]:
        pair = str(order["asset"])
        notional = _float(order["notional_usd"])
        snap = get_market_snapshot(client, pair)
        decision = decide_execution(
            strategy=strategy,
            snapshot=snap,
            window_progress=progress,
            force_fill=should_force_fill(window, _now()),
        )
        if not decision.should_execute:
            continue
        approved_orders.append((order, snap, decision))

    if approved_orders:
        enforce_plan_daily_budget(
            config=config,
            planned_notional_usd=sum(_float(order["notional_usd"]) for order, _, _ in approved_orders),
        )

    for order, snap, decision in approved_orders:
        pair = str(order["asset"])
        notional = _float(order["notional_usd"])
        apply_risk_policy(
            config=config,
            notional_usd=notional,
            expected_slippage_bps=_estimated_slippage_bps(snap),
            check_daily_cap=False,
        )

        executed = execute_decision_order(
            client=client,
            dry_run=dry_run,
            pair=pair,
            notional_usd=notional,
            decision_order_type=decision.order_type,
            limit_price=decision.limit_price,
            execution_price_hint=_float(snap["price"]),
            decision_slices=decision.slices,
            execution_context=execution_context,
        )

        execution_id = str(uuid.uuid4())
        executed_notional = _float(executed.get("executed_notional_usd", 0.0))
        executed_price = _float(executed.get("executed_price", 0.0))
        if executed_notional > 0:
            lot = tracker.add_buy_lot(
                asset=pair,
                quantity=_float(executed["executed_quantity"]),
                cost_basis_usd=executed_notional,
                execution_id=execution_id,
                source="scanner_dca",
            )
            store.persist_cost_basis_lot(lot.__dict__)

        vwap = _float(snap["vwap"])
        savings_bps = (
            int(((vwap - executed_price) / max(vwap, 1e-9)) * 10000)
            if executed_price > 0
            else None
        )
        row = {
            "execution_id": execution_id,
            "mode": "scanner",
            "asset": pair,
            "target_amount_usd": round(notional, 2),
            "executed_amount_usd": round(executed_notional, 2) if executed_notional > 0 else None,
            "executed_price": round(executed_price, 8) if executed_price > 0 else None,
            "vwap_at_execution": round(vwap, 8),
            "savings_vs_naive_bps": savings_bps,
            "strategy": strategy,
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
            "executed_at": _now().isoformat() if executed_notional > 0 else None,
            "status": executed["status"],
            "kraken_order_id": executed["order_id"],
            "metadata": {
                "decision": decision.__dict__,
                "session_id": session_id,
                "require_approval": require_approval,
            },
        }
        store.persist_execution(row)
        logger.log_execution(row)
        execution_rows.append({"execution": executed, "decision": decision.__dict__})

    return {
        "mode": "scanner",
        "status": "ok",
        "signals": signal_payloads,
        "require_approval": require_approval,
        "approval_action": approval_action,
        "effective_allocations": effective_allocations,
        "allocation_source": allocation_source,
        "executions": execution_rows,
        "plan": plan,
    }


def _enforce_live_guards(dry_run: bool, allow_live: bool, accept_risk_disclaimer: bool) -> None:
    if dry_run:
        return
    if not allow_live:
        raise PolicyError("Live mode requested but --allow-live was not provided")
    if not accept_risk_disclaimer:
        raise PolicyError(
            "Live mode requested but --accept-risk-disclaimer was not provided"
        )


def _collect_order_ids(payload: dict[str, Any]) -> list[str]:
    def _extract_ids(block: dict[str, Any]) -> list[str]:
        ids: list[str] = []
        raw_id = str(block.get("order_id", "")).strip()
        if raw_id and raw_id != "dry-run":
            ids.extend([chunk for chunk in raw_id.split(",") if chunk and chunk != "dry-run"])
        for child in block.get("child_orders", []):
            if isinstance(child, dict):
                child_id = str(child.get("order_id", "")).strip()
                if child_id and child_id != "dry-run":
                    ids.extend([chunk for chunk in child_id.split(",") if chunk and chunk != "dry-run"])
        return ids

    order_ids: list[str] = []
    execution = payload.get("execution")
    if isinstance(execution, dict):
        order_ids.extend(_extract_ids(execution))
    for row in payload.get("executions", []):
        if not isinstance(row, dict):
            continue
        block = row.get("execution") or row.get("executed") or {}
        if isinstance(block, dict):
            order_ids.extend(_extract_ids(block))
    return order_ids


def _persist_config(config_path: str, config: dict[str, Any]) -> None:
    path = Path(config_path)
    path.write_text(json.dumps(config, sort_keys=True, indent=2), encoding="utf-8")


def run_once(
    *,
    config_path: str,
    allow_live: bool,
    accept_risk_disclaimer: bool,
) -> dict[str, Any]:
    load_dotenv()
    try:
        show_disclaimer_if_first_run(accept_risk_disclaimer=accept_risk_disclaimer)
    except PolicyError as exc:
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "policy_violation",
            "message": str(exc),
            "disclaimer": IMPORTANT_DISCLAIMER,
        }

    config = load_config(config_path)
    errors = validate_config(config)
    if errors:
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "validation_error",
            "errors": errors,
            "disclaimer": IMPORTANT_DISCLAIMER,
        }

    mode = _coerce_mode(str(config["inputs"].get("mode", "single_asset")))
    dry_run = bool(config.get("dry_run", True))
    try:
        _enforce_live_guards(dry_run, allow_live, accept_risk_disclaimer)
    except PolicyError as exc:
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "policy_violation",
            "message": str(exc),
            "mode": mode,
            "disclaimer": IMPORTANT_DISCLAIMER,
        }

    try:
        seren_api_key = ensure_seren_api_key(config)
    except SerenAPIError as exc:
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "seren_api_key_error",
            "message": str(exc),
            "disclaimer": IMPORTANT_DISCLAIMER,
        }

    logger = AuditLogger("logs")
    tracker = PositionTracker("state/cost_basis_lots.json")
    store = SerenDBStore(os.getenv("SERENDB_URL"))
    if store.enabled:
        store.ensure_schema()

    client: KrakenClient | None = None
    live_risk: dict[str, Any] | None = None
    execution_context: dict[str, Any] = {"order_ids": []}
    optimization: dict[str, Any] | None = None
    session_id = str(uuid.uuid4())

    try:
        client = build_kraken_client(config)
        if dry_run:
            optimized = optimize_invocation_config(
                config=config,
                get_snapshot=lambda pair: get_market_snapshot(client, pair),
            )
            config = optimized["config"]
            optimization = optimized["summary"]
            _persist_config(config_path, config)

        store.create_session(session_id, mode, config)

        logger.log_event(
            "run_started",
            {
                "session_id": session_id,
                "mode": mode,
                "dry_run": dry_run,
                "serendb_enabled": store.enabled,
                "runtime_version": LIVE_SAFETY_VERSION,
                "optimization": optimization,
            },
        )
        if (not dry_run) and client is not None:
            live_risk = _enforce_live_safety(config=config, client=client, mode=mode)

        def _execute_mode() -> dict[str, Any]:
            if mode == "single_asset":
                return _single_asset_mode(
                    config=config,
                    client=client,
                    dry_run=dry_run,
                    store=store,
                    tracker=tracker,
                    logger=logger,
                    session_id=session_id,
                    execution_context=execution_context,
                )
            if mode == "portfolio":
                return _portfolio_mode(
                    config=config,
                    client=client,
                    dry_run=dry_run,
                    store=store,
                    tracker=tracker,
                    logger=logger,
                    session_id=session_id,
                    execution_context=execution_context,
                )
            return _scanner_mode(
                config=config,
                client=client,
                dry_run=dry_run,
                store=store,
                tracker=tracker,
                logger=logger,
                session_id=session_id,
                execution_context=execution_context,
            )

        if (not dry_run) and client is not None:
            payload = _run_with_timeout(
                "run_once_execution",
                _float(config.get("runtime", {}).get("run_timeout_seconds", 90.0), 90.0),
                _execute_mode,
            )
            live_risk = _enforce_live_safety(config=config, client=client, mode=mode)
            payload["live_risk"] = live_risk
        else:
            payload = _execute_mode()

        executed_notional = 0.0
        if isinstance(payload.get("execution"), dict):
            executed_notional += _float(payload["execution"].get("executed_notional_usd", 0.0))
        for row in payload.get("executions", []):
            if isinstance(row, dict):
                executed = row.get("executed") or row.get("execution") or {}
                executed_notional += _float(executed.get("executed_notional_usd", 0.0))

        target_notional = _float(config["inputs"].get("dca_amount_usd", 0.0))
        if mode == "portfolio":
            target_notional = _float(config["inputs"].get("total_dca_amount_usd", target_notional))

        persist_local_run(
            session_id=session_id,
            mode=mode,
            status="ok",
            target_notional_usd=target_notional,
            executed_notional_usd=executed_notional,
            details=payload,
        )
        store.close_session(
            session_id=session_id,
            status="ok",
            total_invested_usd=executed_notional,
            total_savings_bps=0,
        )

        result = {
            "status": "ok",
            "skill": SKILL_NAME,
            "execution_model": "local_direct_kraken_api",
            "dry_run": dry_run,
            "mode": mode,
            "session_id": session_id,
            "seren_api_key_present": bool(seren_api_key),
            "pending_order_ids": sorted(
                {
                    *[oid for oid in _collect_order_ids(payload) if oid],
                    *[str(oid).strip() for oid in execution_context.get("order_ids", []) if str(oid).strip()],
                }
            ),
            "payload": payload,
            "optimization": optimization,
            "runtime_version": LIVE_SAFETY_VERSION,
            "disclaimer": IMPORTANT_DISCLAIMER,
        }
        logger.log_event("run_completed", {"session_id": session_id, "mode": mode, "status": "ok"})
        return result

    except (ConfigError, PolicyError, KrakenAPIError, LiveRiskError, LiveSafetyTimeout) as exc:
        cancelled_on_error = _cancel_orders_on_error(
            config=config,
            client=client,
            execution_context=execution_context,
        )
        logger.log_error("run_once", str(exc), {"session_id": session_id, "mode": mode})
        persist_local_run(
            session_id=session_id,
            mode=mode,
            status="error",
            target_notional_usd=_float(config["inputs"].get("dca_amount_usd", 0.0)),
            executed_notional_usd=0.0,
            details={
                "error": str(exc),
                "cancelled_on_error": cancelled_on_error,
                "live_risk": live_risk,
                "runtime_version": LIVE_SAFETY_VERSION,
            },
        )
        store.close_session(
            session_id=session_id,
            status="error",
            total_invested_usd=0.0,
            total_savings_bps=0,
        )
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": (
                "live_safety_error"
                if isinstance(exc, (LiveRiskError, LiveSafetyTimeout))
                else "runtime_error"
            ),
            "message": str(exc),
            "mode": mode,
            "session_id": session_id,
            "cancelled_on_error": cancelled_on_error,
            "live_risk": live_risk,
            "optimization": optimization,
            "runtime_version": LIVE_SAFETY_VERSION,
            "disclaimer": IMPORTANT_DISCLAIMER,
        }
    finally:
        store.close()


def export_state(path: str) -> dict[str, Any]:
    payload = {
        "exported_at": _now().isoformat(),
        "local_daily_spend_usd": local_daily_spend(),
        "lots_file": str(Path("state/cost_basis_lots.json")),
        "runs_db": str(STATE_DB_PATH),
    }
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    return payload


def _load_pending_order_ids(path: Path = STATE_EXPORT_PATH) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    order_ids = payload.get("pending_order_ids", [])
    if not isinstance(order_ids, list):
        return []
    return sorted({str(order_id).strip() for order_id in order_ids if str(order_id).strip()})


def stop_trading(*, config_path: str) -> dict[str, Any]:
    load_dotenv()
    config = load_config(config_path)
    dry_run = bool(config.get("dry_run", True))
    pending_order_ids = _load_pending_order_ids()
    cancelled_orders: list[dict[str, Any]] = []

    if dry_run:
        return {
            "status": "ok",
            "skill": SKILL_NAME,
            "dry_run": True,
            "pending_order_ids": pending_order_ids,
            "cancelled_orders": cancelled_orders,
            "message": "stop trading completed in dry-run mode; no live orders were cancelled.",
        }

    client = build_kraken_client(config)
    if client is None:
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "client_unavailable",
            "message": "stop trading failed because Kraken client credentials are missing.",
            "pending_order_ids": pending_order_ids,
        }

    for order_id in pending_order_ids:
        try:
            result = client.cancel_order(order_id)
            cancelled_orders.append({"order_id": order_id, "result": result})
        except Exception as exc:  # noqa: BLE001
            cancelled_orders.append({"order_id": order_id, "error": str(exc)})

    return {
        "status": "ok",
        "skill": SKILL_NAME,
        "dry_run": False,
        "pending_order_ids": pending_order_ids,
        "cancelled_orders": cancelled_orders,
        "message": (
            "stop trading cancelled pending orders and left held spot positions untouched "
            "for the operator to liquidate separately if needed."
        ),
    }


def run_loop(*, config_path: str, allow_live: bool, accept_risk_disclaimer: bool) -> int:
    load_dotenv()
    config = load_config(config_path)
    interval = int(config.get("runtime", {}).get("loop_interval_seconds", 60))
    dry_run = bool(config.get("dry_run", True))

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    pending_state: dict[str, Any] = {"runs": [], "cancelled_on_shutdown": [], "pending_order_ids": []}
    pending_order_ids: set[str] = set()

    while not _shutdown_requested:
        result = run_once(
            config_path=config_path,
            allow_live=allow_live,
            accept_risk_disclaimer=accept_risk_disclaimer,
        )
        pending_state["runs"].append(
            {
                "at": _now().isoformat(),
                "status": result.get("status"),
                "mode": result.get("mode") or result.get("payload", {}).get("mode"),
                "session_id": result.get("session_id"),
            }
        )
        for order_id in result.get("pending_order_ids", []):
            if isinstance(order_id, str) and order_id.strip():
                pending_order_ids.add(order_id.strip())
        if _shutdown_requested:
            break
        time.sleep(max(interval, 1))

    if (not dry_run) and bool(config.get("runtime", {}).get("cancel_pending_on_shutdown", True)):
        client = build_kraken_client(config)
        if client is not None:
            for order_id in sorted(pending_order_ids):
                try:
                    cancel_result = client.cancel_order(order_id)
                    pending_state["cancelled_on_shutdown"].append(
                        {"order_id": order_id, "result": cancel_result}
                    )
                except Exception as exc:  # noqa: BLE001
                    pending_state["cancelled_on_shutdown"].append(
                        {"order_id": order_id, "error": str(exc)}
                    )

    STATE_EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    pending_state["pending_order_ids"] = sorted(pending_order_ids)
    STATE_EXPORT_PATH.write_text(
        json.dumps(pending_state, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kraken Smart DCA Bot")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run a single DCA cycle")
    run.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    run.add_argument("--allow-live", action="store_true")
    run.add_argument("--accept-risk-disclaimer", action="store_true")

    loop = sub.add_parser("loop", help="Run continuously until interrupted")
    loop.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    loop.add_argument("--allow-live", action="store_true")
    loop.add_argument("--accept-risk-disclaimer", action="store_true")

    stop_cmd = sub.add_parser("stop-trading", help="Stop trading and cancel tracked pending orders")
    stop_cmd.add_argument("--config", default=DEFAULT_CONFIG_PATH)

    init_db = sub.add_parser("init-db", help="Initialize SerenDB schema")
    init_db.add_argument("--config", default=DEFAULT_CONFIG_PATH)

    export_cmd = sub.add_parser("export-state", help="Export local runtime state")
    export_cmd.add_argument("--output", default=str(STATE_EXPORT_PATH))

    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--accept-risk-disclaimer", action="store_true")
    return parser.parse_args()



# ---------------------------------------------------------------------------
# SerenBucks balance helpers
# ---------------------------------------------------------------------------



def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _runtime_api_key() -> str:
    """Return the Seren API key from environment (Desktop or .env)."""
    for env_name in ("API_KEY", "SEREN_API_KEY"):
        token = (os.getenv(env_name) or "").strip()
        if token:
            return token
    return ""


def _check_serenbucks_balance(api_key: str) -> float:
    """Check SerenBucks balance. Returns balance in USD or 0.0 on error."""
    try:
        request = Request(
            "https://api.serendb.com/wallet/balance",
            headers={
                "User-Agent": "seren-kraken-smart-dca-bot/1.0",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            sb = data.get("data") or data.get("serenbucks") or {}
            raw = sb.get("balance_usd") or sb.get("funded_balance_usd") or "0"
            return _safe_float(str(raw).replace("$", "").replace(",", ""), 0.0)
    except Exception as exc:
        print(f"WARNING: could not fetch SerenBucks balance: {exc}", file=sys.stderr)
        return 0.0

def main() -> int:
    args = parse_args()

    command = args.command or "run"
    if command == "init-db":
        load_dotenv()
        config = load_config(args.config)
        ensure_seren_api_key(config)
        store = SerenDBStore(os.getenv("SERENDB_URL"))
        if not store.enabled:
            print(json.dumps({"status": "error", "message": "SerenDB disabled: set SERENDB_URL"}))
            return 1
        store.ensure_schema()
        store.close()
        print(json.dumps({"status": "ok", "message": "SerenDB schema initialized"}))
        return 0

    if command == "export-state":
        payload = export_state(args.output)
        print(json.dumps({"status": "ok", "payload": payload}, sort_keys=True))
        return 0

    if command == "stop-trading":
        result = stop_trading(config_path=args.config)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("status") == "ok" else 1

    if command == "loop":
        return run_loop(
            config_path=args.config,
            allow_live=bool(args.allow_live),
            accept_risk_disclaimer=bool(args.accept_risk_disclaimer),
        )

    result = run_once(
        config_path=args.config,
        allow_live=bool(args.allow_live),
        accept_risk_disclaimer=bool(args.accept_risk_disclaimer),
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
