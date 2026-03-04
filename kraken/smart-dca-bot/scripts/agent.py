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
import signal
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return False

from dca_engine import build_window, should_force_fill, window_progress
from kraken_client import KrakenAPIError, KrakenClient, KrakenCredentials
from logger import AuditLogger
from optimizer import SUPPORTED_STRATEGIES, compute_rsi, decide_execution
from portfolio_manager import PortfolioManager
from position_tracker import PositionTracker
from scanner import OpportunityScanner
from seren_api_client import SerenAPIError, SerenAPIKeyManager
from serendb_store import SerenDBStore


SKILL_NAME = "smart-dca-bot"
DEFAULT_CONFIG_PATH = "config.json"
STATE_DB_PATH = Path("state/dca_runs.db")
STATE_EXPORT_PATH = Path("state/last_state_export.json")
DISCLAIMER_ACK_PATH = Path("state/disclaimer_seen.flag")

SUPPORTED_MODES = {"single_asset", "portfolio", "scanner", "opportunity_scanner"}
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


class ConfigError(RuntimeError):
    """Raised for invalid skill configuration."""


class PolicyError(RuntimeError):
    """Raised when policy guards block execution."""


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
            "signals": ["volume_spike", "mean_reversion", "momentum_breakout", "new_listing"],
            "require_approval": True,
            "base_allocations": {"XBTUSD": 0.6, "ETHUSD": 0.25, "SOLUSD": 0.15},
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
        },
        "seren": {
            "auto_register_key": True,
            "api_base_url": "https://api.serendb.com",
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


def load_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config JSON is invalid: {exc}") from exc
    return deep_merge(_default_config(), raw)


def validate_config(config: dict[str, Any]) -> list[str]:
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
    if not isinstance(allocations, dict) or not allocations:
        errors.append("portfolio.allocations must be a non-empty object")

    scanner = config.get("scanner", {})
    scanner_signals = scanner.get("signals", [])
    allowed_signals = {"volume_spike", "mean_reversion", "momentum_breakout", "new_listing"}
    unknown_signals = [signal for signal in scanner_signals if signal not in allowed_signals]
    if unknown_signals:
        errors.append(f"scanner.signals contains unknown values: {unknown_signals}")

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
    return "scanner" if raw_mode == "opportunity_scanner" else raw_mode


def show_disclaimer_if_first_run() -> bool:
    if DISCLAIMER_ACK_PATH.exists():
        return False
    DISCLAIMER_ACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    DISCLAIMER_ACK_PATH.write_text(datetime.now(tz=UTC).isoformat(), encoding="utf-8")
    print(IMPORTANT_DISCLAIMER)
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
    )


def ensure_seren_api_key(config: dict[str, Any]) -> str:
    seren_cfg = config.get("seren", {})
    manager = SerenAPIKeyManager(
        api_base_url=str(seren_cfg.get("api_base_url", "https://api.serendb.com")),
        env_file=".env",
    )
    auto_register = bool(seren_cfg.get("auto_register_key", True))
    return manager.ensure_api_key(auto_register=auto_register)


def _mock_snapshot(pair: str) -> dict[str, Any]:
    seed = sum(ord(ch) for ch in pair)
    base_price = 80.0 + (seed % 300)
    price = base_price * (1.0 + (seed % 7) / 100.0)
    vwap = price * (1.0 + ((seed % 5) - 2) / 1000.0)
    bid = price * 0.999
    ask = price * 1.001
    low_24h = price * 0.975
    high_24h = price * 1.03
    candles = [round(price * (0.99 + i * 0.001), 6) for i in range(20)]
    candles[-1] = round(price, 6)
    return {
        "pair": pair,
        "price": round(price, 6),
        "vwap": round(vwap, 6),
        "bid": round(bid, 6),
        "ask": round(ask, 6),
        "low_24h": round(low_24h, 6),
        "high_24h": round(high_24h, 6),
        "volume_24h_usd": float((seed % 20 + 1) * 450000),
        "depth_score": round(0.45 + (seed % 50) / 100.0, 4),
        "candles": candles,
        "new_listing_days": int(seed % 45),
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

    ohlc = client.get_ohlc(pair, interval=15)
    ohlc_key = next((k for k in ohlc.keys() if k != "last"), pair)
    candles_raw = ohlc.get(ohlc_key, [])
    closes = [_float(item[4]) for item in candles_raw[-20:]]
    if not closes:
        closes = [price] * 20

    volume = _float(row.get("v", [0, 0])[1]) * price

    return {
        "pair": pair,
        "price": price,
        "vwap": vwap,
        "bid": bid,
        "ask": ask,
        "low_24h": low_24h,
        "high_24h": high_24h,
        "volume_24h_usd": volume,
        "depth_score": depth_score,
        "candles": closes,
        "new_listing_days": 999,
    }


def apply_risk_policy(
    *,
    config: dict[str, Any],
    notional_usd: float,
    expected_slippage_bps: float,
) -> None:
    risk = config.get("risk", {})
    max_daily = _float(risk.get("max_daily_spend_usd"), 500.0)
    max_notional = _float(risk.get("max_notional_usd"), 5000.0)
    max_slippage = _float(risk.get("max_slippage_bps"), 150.0)

    if notional_usd > max_notional:
        raise PolicyError(
            f"notional_usd={notional_usd:.2f} exceeds risk.max_notional_usd={max_notional:.2f}"
        )

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
) -> dict[str, Any]:
    if dry_run or client is None:
        qty = notional_usd / max(execution_price_hint, 1e-9)
        return {
            "status": "simulated",
            "order_id": "dry-run",
            "pair": pair,
            "executed_notional_usd": round(notional_usd, 2),
            "executed_price": round(execution_price_hint, 8),
            "executed_quantity": round(qty, 8),
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
        return {
            "status": "ok",
            "order_id": txid,
            "pair": pair,
            "executed_notional_usd": round(notional_usd, 2),
            "executed_price": round(limit_price, 8),
            "executed_quantity": round(notional_usd / max(limit_price, 1e-9), 8),
        }

    payload = _build_market_order_payload(pair, notional_usd, execution_price_hint)
    result = client.add_order(
        pair=payload["pair"],
        ordertype=payload["ordertype"],
        side=payload["side"],
        volume=payload["volume"],
    )
    txid = result.get("txid", [""])[0] if isinstance(result.get("txid"), list) else ""
    return {
        "status": "ok",
        "order_id": txid,
        "pair": pair,
        "executed_notional_usd": round(notional_usd, 2),
        "executed_price": round(execution_price_hint, 8),
        "executed_quantity": round(notional_usd / max(execution_price_hint, 1e-9), 8),
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

        apply_risk_policy(
            config=config,
            notional_usd=notional,
            expected_slippage_bps=_estimated_slippage_bps(snap),
        )

        executed = execute_order(
            client=client,
            dry_run=dry_run,
            pair=pair,
            notional_usd=notional,
            decision_order_type=decision.order_type,
            limit_price=decision.limit_price,
            execution_price_hint=_float(snap["price"]),
        )
        execution_id = str(uuid.uuid4())
        lot = tracker.add_buy_lot(
            asset=pair,
            quantity=_float(executed["executed_quantity"]),
            cost_basis_usd=_float(executed["executed_notional_usd"]),
            execution_id=execution_id,
        )
        store.persist_cost_basis_lot(lot.__dict__)

        row = {
            "execution_id": execution_id,
            "mode": "portfolio",
            "asset": pair,
            "target_amount_usd": round(notional, 2),
            "executed_amount_usd": round(_float(executed["executed_notional_usd"]), 2),
            "executed_price": round(_float(executed["executed_price"]), 8),
            "vwap_at_execution": round(_float(snap["vwap"]), 8),
            "savings_vs_naive_bps": int(((_float(snap["vwap"]) - _float(executed["executed_price"])) / max(_float(snap["vwap"]), 1e-9)) * 10000),
            "strategy": strategy,
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
            "executed_at": _now().isoformat(),
            "status": executed["status"],
            "kraken_order_id": executed["order_id"],
            "metadata": {"decision": decision.__dict__, "session_id": session_id},
        }
        store.persist_execution(row)
        logger.log_execution(row)
        logger.log_order({**executed, "decision": decision.__dict__})
        executions.append({"pair": pair, "executed": executed, "decision": decision.__dict__})

    total_value = sum(
        _float(balances.get(pair[:3], 0.0)) * _float(prices.get(pair, 0.0))
        for pair in targets
    )
    snapshot_row = {
        "snapshot_id": str(uuid.uuid4()),
        "total_value_usd": round(total_value, 2),
        "allocations": current,
        "target_allocations": targets,
        "drift_max_pct": round(_float(plan.get("max_abs_drift_pct", 0.0)), 4),
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
    executed = execute_order(
        client=client,
        dry_run=dry_run,
        pair=pair,
        notional_usd=amount,
        decision_order_type=decision.order_type,
        limit_price=decision.limit_price,
        execution_price_hint=_float(snapshot["price"]),
    )

    execution_id = str(uuid.uuid4())
    lot = tracker.add_buy_lot(
        asset=pair,
        quantity=_float(executed["executed_quantity"]),
        cost_basis_usd=_float(executed["executed_notional_usd"]),
        execution_id=execution_id,
    )
    store.persist_cost_basis_lot(lot.__dict__)

    vwap = _float(snapshot["vwap"])
    executed_price = _float(executed["executed_price"])
    savings_bps = int(((vwap - executed_price) / max(vwap, 1e-9)) * 10000)

    row = {
        "execution_id": execution_id,
        "mode": "single_asset",
        "asset": pair,
        "target_amount_usd": round(amount, 2),
        "executed_amount_usd": round(_float(executed["executed_notional_usd"]), 2),
        "executed_price": round(executed_price, 8),
        "vwap_at_execution": round(vwap, 8),
        "savings_vs_naive_bps": savings_bps,
        "strategy": strategy,
        "window_start": window.start.isoformat(),
        "window_end": window.end.isoformat(),
        "executed_at": _now().isoformat(),
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
        price_change_7d = price_change_24h * 2.8
        volume_ratio = 1.0 + (sum(ord(ch) for ch in pair) % 5)
        rows.append(
            {
                "asset": pair,
                "volume_24h_usd": float(snap.get("volume_24h_usd", 0.0)),
                "volume_ratio": float(volume_ratio),
                "rsi_14": float(rsi),
                "price_change_24h_pct": float(price_change_24h),
                "price_change_7d_pct": float(price_change_7d),
                "ma50_breakout": bool(price_change_24h > 2.0),
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
) -> dict[str, Any]:
    scanner_cfg = config.get("scanner", {})
    base_allocations = scanner_cfg.get("base_allocations", {"XBTUSD": 0.6, "ETHUSD": 0.25, "SOLUSD": 0.15})

    scanner = OpportunityScanner(
        min_24h_volume_usd=_float(scanner_cfg.get("min_24h_volume_usd", 1_000_000)),
        max_reallocation_pct=_float(scanner_cfg.get("max_reallocation_pct", 20.0)),
        enabled_signals=list(scanner_cfg.get("signals", [])),
    )

    rows = _scanner_market_rows(config=config, client=client)
    signals = scanner.scan(rows, base_allocations)
    signal_payloads = [signal.to_dict() for signal in signals]

    require_approval = bool(scanner_cfg.get("require_approval", True))
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

    for payload in signal_payloads:
        store.persist_scanner_signal(payload, user_action="pending_approval" if require_approval else "auto_applied")
        logger.log_scanner(payload)

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

        apply_risk_policy(
            config=config,
            notional_usd=notional,
            expected_slippage_bps=_estimated_slippage_bps(snap),
        )

        executed = execute_order(
            client=client,
            dry_run=dry_run,
            pair=pair,
            notional_usd=notional,
            decision_order_type=decision.order_type,
            limit_price=decision.limit_price,
            execution_price_hint=_float(snap["price"]),
        )

        execution_id = str(uuid.uuid4())
        lot = tracker.add_buy_lot(
            asset=pair,
            quantity=_float(executed["executed_quantity"]),
            cost_basis_usd=_float(executed["executed_notional_usd"]),
            execution_id=execution_id,
            source="scanner_dca",
        )
        store.persist_cost_basis_lot(lot.__dict__)

        row = {
            "execution_id": execution_id,
            "mode": "scanner",
            "asset": pair,
            "target_amount_usd": round(notional, 2),
            "executed_amount_usd": round(_float(executed["executed_notional_usd"]), 2),
            "executed_price": round(_float(executed["executed_price"]), 8),
            "vwap_at_execution": round(_float(snap["vwap"]), 8),
            "savings_vs_naive_bps": int(((_float(snap["vwap"]) - _float(executed["executed_price"])) / max(_float(snap["vwap"]), 1e-9)) * 10000),
            "strategy": strategy,
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
            "executed_at": _now().isoformat(),
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
        "effective_allocations": effective_allocations,
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
    order_ids: list[str] = []
    execution = payload.get("execution")
    if isinstance(execution, dict):
        order_id = str(execution.get("order_id", "")).strip()
        if order_id and order_id != "dry-run":
            order_ids.append(order_id)
    for row in payload.get("executions", []):
        if not isinstance(row, dict):
            continue
        block = row.get("execution") or row.get("executed") or {}
        if isinstance(block, dict):
            order_id = str(block.get("order_id", "")).strip()
            if order_id and order_id != "dry-run":
                order_ids.append(order_id)
    return order_ids


def run_once(
    *,
    config_path: str,
    allow_live: bool,
    accept_risk_disclaimer: bool,
) -> dict[str, Any]:
    load_dotenv()
    show_disclaimer_if_first_run()

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

    session_id = str(uuid.uuid4())
    store.create_session(session_id, mode, config)

    logger.log_event(
        "run_started",
        {
            "session_id": session_id,
            "mode": mode,
            "dry_run": dry_run,
            "serendb_enabled": store.enabled,
        },
    )

    try:
        client = build_kraken_client(config)
        if mode == "single_asset":
            payload = _single_asset_mode(
                config=config,
                client=client,
                dry_run=dry_run,
                store=store,
                tracker=tracker,
                logger=logger,
                session_id=session_id,
            )
        elif mode == "portfolio":
            payload = _portfolio_mode(
                config=config,
                client=client,
                dry_run=dry_run,
                store=store,
                tracker=tracker,
                logger=logger,
                session_id=session_id,
            )
        else:
            payload = _scanner_mode(
                config=config,
                client=client,
                dry_run=dry_run,
                store=store,
                tracker=tracker,
                logger=logger,
                session_id=session_id,
            )

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
            "pending_order_ids": _collect_order_ids(payload),
            "payload": payload,
            "disclaimer": IMPORTANT_DISCLAIMER,
        }
        logger.log_event("run_completed", {"session_id": session_id, "mode": mode, "status": "ok"})
        return result

    except (ConfigError, PolicyError, KrakenAPIError) as exc:
        logger.log_error("run_once", str(exc), {"session_id": session_id, "mode": mode})
        persist_local_run(
            session_id=session_id,
            mode=mode,
            status="error",
            target_notional_usd=_float(config["inputs"].get("dca_amount_usd", 0.0)),
            executed_notional_usd=0.0,
            details={"error": str(exc)},
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
            "error_code": "runtime_error",
            "message": str(exc),
            "mode": mode,
            "session_id": session_id,
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


def run_loop(*, config_path: str, allow_live: bool, accept_risk_disclaimer: bool) -> int:
    load_dotenv()
    config = load_config(config_path)
    interval = int(config.get("runtime", {}).get("loop_interval_seconds", 60))
    dry_run = bool(config.get("dry_run", True))

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    pending_state: dict[str, Any] = {"runs": [], "cancelled_on_shutdown": []}
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

    init_db = sub.add_parser("init-db", help="Initialize SerenDB schema")
    init_db.add_argument("--config", default=DEFAULT_CONFIG_PATH)

    export_cmd = sub.add_parser("export-state", help="Export local runtime state")
    export_cmd.add_argument("--output", default=str(STATE_EXPORT_PATH))

    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--accept-risk-disclaimer", action="store_true")
    return parser.parse_args()


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
