#!/usr/bin/env python3
from __future__ import annotations

"""
Kraken Grid Trading Bot - Automated grid trading on Kraken via Seren Gateway

Usage:
    python scripts/agent.py setup --config config.json
    python scripts/agent.py dry-run --config config.json
    python scripts/agent.py start --config config.json
    python scripts/agent.py status --config config.json
    python scripts/agent.py stop --config config.json
"""

import argparse
import json
import os
import signal
import sys
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
from dotenv import load_dotenv

from adaptive_runtime import (
    AdaptiveStateStore,
    RuntimeLockError,
    build_review_report,
    compute_adaptive_decision,
    compute_market_metrics,
    resolve_adaptive_settings,
    runtime_lock,
    summarize_window,
    update_cycle_state,
)
from seren_client import SerenClient
from grid_manager import GridManager, optimize_backtest_configuration
from position_tracker import PositionTracker
from logger import GridTraderLogger
from serendb_store import SerenDBStore
import pair_selector
from urllib.request import Request, urlopen


LIVE_SAFETY_VERSION = "2026-03-16.kraken-coinbase-live-safety-v1"
LIVE_RISK_STATE_PATH = Path("state/live_risk.json")


class LiveRiskError(RuntimeError):
    """Raised when live risk controls halt execution."""


class LiveSafetyTimeout(TimeoutError):
    """Raised when a live operation exceeds the configured timeout."""


def _get_seren_api_key() -> str | None:
    return os.getenv("SEREN_API_KEY") or os.getenv("API_KEY")


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _build_store_from_env() -> SerenDBStore:
    api_key = _get_seren_api_key()
    if not api_key:
        raise ValueError("SEREN_API_KEY is required (or API_KEY when launched by Seren Desktop).")

    return SerenDBStore(
        api_key=api_key,
        project_name=os.getenv("SERENDB_PROJECT_NAME"),
        database_name=os.getenv("SERENDB_DATABASE"),
        branch_name=os.getenv("SERENDB_BRANCH"),
        project_region=os.getenv("SERENDB_REGION", "aws-us-east-1"),
        auto_create=_env_flag("SERENDB_AUTO_CREATE", default=True),
        mcp_command=os.getenv("SEREN_MCP_COMMAND", "seren-mcp"),
    )


def _bootstrap_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.exists():
        return path

    example_path = path.with_name("config.example.json")
    if example_path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")

    return path


class KrakenGridTrader:
    """Kraken Grid Trading Bot"""

    def __init__(self, config_path: str, dry_run: bool = False):
        """
        Initialize grid trader

        Args:
            config_path: Path to config JSON file
            dry_run: If True, simulate trades without placing real orders
        """
        # Load environment
        load_dotenv()

        self.config_path = config_path
        # Load config
        self.config = self._load_config(config_path)
        self.is_dry_run = dry_run
        self.backtest_optimization: Optional[Dict[str, Any]] = None

        # Initialize clients
        api_key = os.getenv('SEREN_API_KEY')
        if not api_key:
            raise ValueError("SEREN_API_KEY environment variable is required")

        self.seren = SerenClient(api_key=api_key)
        self.logger = GridTraderLogger(logs_dir='logs')
        self.store: Optional[SerenDBStore] = None
        self.session_id = str(uuid.uuid4())
        self._session_started = False

        # Initialize components
        self.grid = None
        self.tracker = None
        self.running = False
        self.active_orders = {}  # order_id -> order_details
        self.live_risk_state = self._load_live_risk_state()
        self.adaptive_settings = resolve_adaptive_settings(self.config)
        self.adaptive_store = AdaptiveStateStore(self.adaptive_settings)
        self.current_adaptive_decision = None
        self._cycle_deadline_at: Optional[float] = None

        try:
            self.store = _build_store_from_env()
            self.store.ensure_schema()
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: SerenDB persistence unavailable: {exc}", file=sys.stderr)
            self.store = None

    def close(self):
        """Close any external resources."""
        self.adaptive_store.save()
        if self.store is None:
            return
        try:
            self.store.close()
        finally:
            self.store = None

    def _store_call(self, context: str, fn):
        """Execute a store operation safely without interrupting trading."""
        if self.store is None:
            return
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: SerenDB persistence failed ({context}): {exc}", file=sys.stderr)
            try:
                self.store.close()
            finally:
                self.store = None

    def _ensure_session_started(self):
        """Create a persistence session once a trading pair is known."""
        if self.store is None or self._session_started:
            return

        campaign_name = str(self.config.get("campaign_name", "kraken-grid-trader"))
        trading_pair = str(self.config.get("trading_pair") or "UNKNOWN")

        self._store_call(
            "create_session",
            lambda: self.store.create_session(
                session_id=self.session_id,
                campaign_name=campaign_name,
                trading_pair=trading_pair,
                dry_run=self.is_dry_run,
            ),
        )
        self._store_call(
            "session_started_event",
            lambda: self.store.save_event(
                self.session_id,
                "session_started",
                {
                    "campaign_name": campaign_name,
                    "trading_pair": trading_pair,
                    "dry_run": self.is_dry_run,
                    "runtime_version": LIVE_SAFETY_VERSION,
                },
            ),
        )
        self._session_started = True

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        with open(_bootstrap_config_path(config_path), 'r') as f:
            config = json.load(f)

        # Validate required fields.
        # 'trading_pair' is optional when 'pairs' (list) is provided — pair selection
        # happens at setup() time once the live Seren client is available.
        required = ['campaign_name', 'strategy', 'risk_management']
        for field in required:
            if field not in config:
                raise ValueError(f"Missing required config field: {field}")

        if 'trading_pair' not in config and 'pairs' not in config:
            raise ValueError("Config must contain either 'trading_pair' or 'pairs'")

        execution = config.setdefault('execution', {})
        execution.setdefault('dry_run', True)
        execution.setdefault('log_level', 'INFO')
        execution.setdefault('cancel_on_error', True)
        execution.setdefault('operation_timeout_seconds', 30)
        execution.setdefault('cycle_timeout_seconds', 90)
        risk = config.setdefault('risk_management', {})
        risk.setdefault('min_quote_reserve_usd', 0.0)
        risk.setdefault('max_live_drawdown_pct', 0.0)
        risk.setdefault('max_position_size', 1.0)
        risk.setdefault('max_open_orders', 40)

        return config

    def _persist_config(self) -> None:
        config_path = Path(self.config_path)
        config_path.write_text(
            json.dumps(self.config, sort_keys=True, indent=2),
            encoding='utf-8',
        )

    def _apply_backtest_optimization(self) -> None:
        optimization = optimize_backtest_configuration(self.config)
        summary = optimization.get('summary', {})
        if not summary.get('applied'):
            self.backtest_optimization = summary
            return

        updated = optimization['config']
        if json.dumps(updated, sort_keys=True) != json.dumps(self.config, sort_keys=True):
            self.config = updated
            self._persist_config()
        else:
            self.config = updated
        self.backtest_optimization = summary

    def _load_live_risk_state(self) -> Dict[str, Any]:
        try:
            payload = json.loads(LIVE_RISK_STATE_PATH.read_text(encoding='utf-8'))
        except FileNotFoundError:
            payload = {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault('runtime_version', LIVE_SAFETY_VERSION)
        return payload

    def _persist_live_risk_state(self, payload: Dict[str, Any]) -> None:
        state = dict(payload)
        state['runtime_version'] = LIVE_SAFETY_VERSION
        state['updated_at'] = datetime.utcnow().isoformat()
        LIVE_RISK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LIVE_RISK_STATE_PATH.write_text(
            json.dumps(state, sort_keys=True, indent=2),
            encoding='utf-8',
        )
        self.live_risk_state = state

    def _adaptive_lock_path(self) -> str:
        return str(self.adaptive_settings.get('lock_path', 'state/runtime.lock'))

    def _current_grid_parameters(self) -> Dict[str, Any]:
        strategy = self.config['strategy']
        return {
            'grid_levels': int(strategy['grid_levels']),
            'grid_spacing_percent': float(strategy['grid_spacing_percent']),
            'order_size_percent': float(strategy['order_size_percent']),
            'max_open_orders': int(self.config['risk_management']['max_open_orders']),
            'risk_multiplier': 1.0,
            'dynamic_range': dict(strategy['price_range']),
        }

    def _build_grid_from_parameters(self, params: Dict[str, Any], price_range: Dict[str, float]) -> GridManager:
        strategy = self.config['strategy']
        grid_levels = int(params.get('grid_levels', strategy['grid_levels']))
        order_size_percent = float(params.get('order_size_percent', strategy['order_size_percent']))
        spacing_percent = float(params.get('grid_spacing_percent', strategy['grid_spacing_percent']))
        order_size_usd = float(strategy['bankroll']) * (order_size_percent / 100.0)
        return GridManager(
            min_price=float(price_range['min']),
            max_price=float(price_range['max']),
            grid_levels=grid_levels,
            spacing_percent=spacing_percent,
            order_size_usd=order_size_usd,
        )

    def _sync_active_orders(self, current_open_orders: Dict[str, Any]) -> Dict[str, Any]:
        previous_known = dict(self.adaptive_store.state.get('known_open_orders', {}))
        hydrated: Dict[str, Dict[str, Any]] = {}
        if self.tracker is not None:
            self.tracker.open_orders = {}

        for order_id, order_data in current_open_orders.items():
            descr = order_data.get('descr', {})
            prior = previous_known.get(order_id, {})
            details = {
                'side': descr.get('type') or prior.get('side') or 'buy',
                'price': float(descr.get('price') or prior.get('price') or 0.0),
                'volume': float(order_data.get('vol') or prior.get('volume') or 0.0),
                'placed_at': prior.get('placed_at') or datetime.utcnow().isoformat(),
            }
            hydrated[order_id] = details
            if self.tracker is not None:
                self.tracker.open_orders[order_id] = dict(details)

        self.active_orders = hydrated
        return previous_known

    def _persist_known_open_orders(self) -> None:
        self.adaptive_store.state['known_open_orders'] = {
            order_id: {
                'side': details.get('side'),
                'price': float(details.get('price', 0.0)),
                'volume': float(details.get('volume', 0.0)),
                'placed_at': details.get('placed_at') or datetime.utcnow().isoformat(),
            }
            for order_id, details in self.active_orders.items()
        }

    def _get_market_snapshot(self, pair: str) -> Dict[str, float]:
        return self._call_with_timeout(
            'get_market_snapshot',
            lambda: self.seren.get_market_snapshot(pair),
        )

    def _apply_adaptive_grid(
        self,
        *,
        current_price: float,
        market_snapshot: Dict[str, float],
        live_risk: Dict[str, Any],
    ):
        market_metrics = compute_market_metrics(
            recent_cycles=list(self.adaptive_store.state.get('recent_cycles', [])),
            current_price=current_price,
            bid=float(market_snapshot.get('bid', current_price)),
            ask=float(market_snapshot.get('ask', current_price)),
            high=float(market_snapshot.get('high', current_price)),
            low=float(market_snapshot.get('low', current_price)),
        )
        decision = compute_adaptive_decision(
            store=self.adaptive_store,
            config=self.config,
            market_metrics=market_metrics,
            live_risk=live_risk,
            current_price=current_price,
        )
        accepted_params = dict(decision.accepted_params)
        dynamic_range = dict(accepted_params.get('dynamic_range') or decision.dynamic_range)
        self.grid = self._build_grid_from_parameters(accepted_params, dynamic_range)
        self.current_adaptive_decision = decision
        return decision, market_metrics

    def _rolling_window_metrics(self) -> Dict[str, Any]:
        recent_cycles = list(self.adaptive_store.state.get('recent_cycles', []))
        return {
            'last_50': summarize_window(recent_cycles[-50:]),
            'last_200': summarize_window(recent_cycles[-200:]),
        }

    def _cancel_open_buy_orders(self) -> int:
        if self.is_dry_run:
            return 0
        cancelled = 0
        for order_id, details in list(self.active_orders.items()):
            if details.get('side') != 'buy':
                continue
            self._call_with_timeout(
                'cancel_order',
                lambda order_id=order_id: self.seren.cancel_order(order_id),
            )
            cancelled += 1
            self.logger.log_order(
                order_id=order_id,
                order_type='limit',
                side='buy',
                price=float(details.get('price', 0.0)),
                volume=float(details.get('volume', 0.0)),
                status='cancelled',
                extra={'reason': 'adaptive_safety_pause'},
            )
            self.active_orders.pop(order_id, None)
            if self.tracker is not None:
                self.tracker.remove_open_order(order_id)
        return cancelled

    def _operation_timeout_seconds(self) -> float:
        return float(self.config.get('execution', {}).get('operation_timeout_seconds', 30))

    def _cycle_timeout_seconds(self) -> float:
        return float(self.config.get('execution', {}).get('cycle_timeout_seconds', 90))

    def _cancel_on_error(self) -> bool:
        return bool(self.config.get('execution', {}).get('cancel_on_error', True))

    def _call_with_timeout(self, label: str, fn, timeout_seconds: Optional[float] = None):
        timeout = float(timeout_seconds or self._operation_timeout_seconds())
        if self._cycle_deadline_at is not None:
            remaining = self._cycle_deadline_at - time.monotonic()
            if remaining <= 0:
                raise LiveSafetyTimeout(f"{label} exceeded cycle timeout")
            timeout = min(timeout, remaining)
        if timeout <= 0 or not hasattr(signal, 'SIGALRM'):
            return fn()

        previous_handler = signal.getsignal(signal.SIGALRM)

        def _handle_timeout(signum, frame):  # noqa: ANN001,ARG001
            raise LiveSafetyTimeout(f"{label} timed out after {timeout:.2f}s")

        signal.signal(signal.SIGALRM, _handle_timeout)
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            return fn()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)

    def _build_live_risk(self, current_price: float, base_balance: float, usd_balance: float) -> Dict[str, Any]:
        current_equity = (base_balance * current_price) + usd_balance
        prior_peak = float(self.live_risk_state.get('peak_equity_usd', current_equity))
        peak_equity = max(prior_peak, current_equity)
        drawdown_usd = max(peak_equity - current_equity, 0.0)
        drawdown_pct = (drawdown_usd / peak_equity * 100.0) if peak_equity > 0 else 0.0
        return {
            'peak_equity_usd': round(peak_equity, 2),
            'current_equity_usd': round(current_equity, 2),
            'drawdown_usd': round(drawdown_usd, 2),
            'drawdown_pct': round(drawdown_pct, 4),
            'quote_balance_usd': round(usd_balance, 2),
            'base_balance': round(base_balance, 8),
        }

    def _enforce_live_risk(self, current_price: float, base_balance: float, usd_balance: float) -> Dict[str, Any]:
        live_risk = self._build_live_risk(current_price, base_balance, usd_balance)
        self._persist_live_risk_state(live_risk)
        max_drawdown_pct = float(self.config.get('risk_management', {}).get('max_live_drawdown_pct', 0.0))
        if (not self.is_dry_run) and max_drawdown_pct > 0 and live_risk['drawdown_pct'] > max_drawdown_pct:
            raise LiveRiskError(
                f"live drawdown {live_risk['drawdown_pct']:.2f}% exceeds cap {max_drawdown_pct:.2f}%"
            )
        return live_risk

    def _halt_live_trading(self, reason: str, details: Dict[str, Any]) -> None:
        self.running = False
        if details:
            self.logger.log_error(
                operation=reason,
                error_type=str(details.get('error_type', reason)),
                error_message=str(details.get('error_message', reason)),
                context=details,
            )
        self._store_call(
            f"{reason}_event",
            lambda: self.store.save_event(
                self.session_id,
                reason,
                {
                    'runtime_version': LIVE_SAFETY_VERSION,
                    **details,
                },
            ),
        )

        if self.is_dry_run or not self._cancel_on_error():
            return

        try:
            cancelled = self._call_with_timeout(
                'cancel_all_orders',
                lambda: self.seren.cancel_all_orders(),
            )
            self.active_orders.clear()
            self.adaptive_store.state['known_open_orders'] = {}
            self._store_call(
                f"{reason}_cancelled_orders_event",
                lambda: self.store.save_event(
                    self.session_id,
                    f"{reason}_cancelled_orders",
                    {'cancel_result': cancelled},
                ),
            )
        except Exception as cancel_exc:  # noqa: BLE001
            self._store_call(
                f"{reason}_cancel_error_event",
                lambda: self.store.save_event(
                    self.session_id,
                    f"{reason}_cancel_error",
                    {
                        'error_type': type(cancel_exc).__name__,
                        'error_message': str(cancel_exc),
                    },
                ),
            )

    def _select_trading_pair(self):
        """
        Score all configured candidate pairs and pick the best one for grid trading.
        Updates self.config['trading_pair'] with the winner.
        """
        candidates = self.config.get('pairs', [])
        if not candidates:
            return  # single-pair mode — nothing to do

        print("\nScanning candidate pairs for best grid opportunity...")
        best_pair, best_score, all_scores = pair_selector.select_best_pair(self.seren, candidates)

        print(f"\n{'Pair':<12} {'Score':>6}  {'ATR%':>6}  {'Vol $24h':>12}  {'Spread%':>8}  {'Price':>10}")
        print("-" * 62)
        for s in all_scores:
            if s['error']:
                print(f"{s['pair']:<12}  ERROR: {s['error']}")
            else:
                marker = " ◀ selected" if s['pair'] == best_pair else ""
                print(
                    f"{s['pair']:<12} {s['score']:>6.3f}  {s['atr_pct']:>5.1f}%  "
                    f"${s['volume_usd_24h']:>11,.0f}  {s['spread_pct']:>7.4f}%  "
                    f"${s['current_price']:>9,.2f}{marker}"
                )

        self.config['trading_pair'] = best_pair
        print(f"\n✓ Selected pair: {best_pair} (score: {best_score['score']:.3f})\n")
        self._ensure_session_started()
        self._store_call(
            "pair_selected_event",
            lambda: self.store.save_event(
                self.session_id,
                "pair_selected",
                {
                    "selected_pair": best_pair,
                    "score": best_score['score'],
                    "all_scores": all_scores,
                },
            ),
        )

    def setup(self, *, optimize_backtest: bool = True):
        """Phase 1: Setup and validate configuration"""
        print("\n============================================================")
        print("KRAKEN GRID TRADER - SETUP")
        print("============================================================\n")

        if optimize_backtest:
            self._apply_backtest_optimization()

        # Auto-select the best pair from the candidate list (if configured)
        self._select_trading_pair()

        campaign = self.config['campaign_name']
        pair = self.config['trading_pair']
        self._ensure_session_started()
        strategy = self.config['strategy']
        risk = self.config['risk_management']

        print(f"Campaign:        {campaign}")
        print(f"Trading Pair:    {pair}")
        print(f"Bankroll:        ${strategy['bankroll']:,.2f}")
        accepted_params = self.adaptive_store.accepted_params(self._current_grid_parameters())
        accepted_range = dict(accepted_params.get('dynamic_range') or strategy['price_range'])
        print(f"Grid Levels:     {strategy['grid_levels']}")
        print(f"Grid Spacing:    {accepted_params.get('grid_spacing_percent', strategy['grid_spacing_percent'])}%")
        print(f"Order Size:      {accepted_params.get('order_size_percent', strategy['order_size_percent'])}% of bankroll")
        print(f"Price Range:     ${accepted_range['min']:,.0f} - ${accepted_range['max']:,.0f}")
        print(f"Scan Interval:   {strategy['scan_interval_seconds']}s")
        print(f"Stop Loss:       ${risk['stop_loss_bankroll']:,.2f}")
        if self.backtest_optimization and self.backtest_optimization.get('applied'):
            print(
                "Backtest Target: "
                f"{self.backtest_optimization['modeled_pnl_pct']}% / "
                f"{self.backtest_optimization['target_pnl_pct']}% monthly target "
                f"(attempts={self.backtest_optimization['attempt_count']})"
            )

        # Initialize grid manager
        self.grid = self._build_grid_from_parameters(accepted_params, accepted_range)

        # Initialize position tracker
        self.tracker = PositionTracker(initial_bankroll=strategy['bankroll'])

        # Get current price
        print("\nFetching current market data...")
        market_snapshot = self._get_market_snapshot(pair)
        current_price = float(market_snapshot['current_price'])
        preview_live_risk = self._build_live_risk(current_price, 0.0, float(strategy['bankroll']))
        decision, _ = self._apply_adaptive_grid(
            current_price=current_price,
            market_snapshot=market_snapshot,
            live_risk=preview_live_risk,
        )

        print(f"Current Price:   ${current_price:,.2f}")
        print(f"Adaptive Regime: {decision.regime_tag}")
        print(f"Risk Multiplier: {decision.accepted_params.get('risk_multiplier', 1.0):.2f}x")

        # Validate price range
        min_price = strategy['price_range']['min']
        max_price = strategy['price_range']['max']
        price_range_width = max_price - min_price
        tolerance_pct = 0.05  # 5% tolerance outside range

        if current_price < min_price * (1 - tolerance_pct):
            print(f"\n⚠️  WARNING: Current price (${current_price:,.2f}) is significantly BELOW configured range")
            print(f"   Configured range: ${min_price:,.0f} - ${max_price:,.0f}")
            print(f"   This will result in ONE-SIDED GRID behavior (all sell orders, no buys).")
            print(f"   Consider updating config.json price_range to include current price.\n")
        elif current_price > max_price * (1 + tolerance_pct):
            print(f"\n⚠️  WARNING: Current price (${current_price:,.2f}) is significantly ABOVE configured range")
            print(f"   Configured range: ${min_price:,.0f} - ${max_price:,.0f}")
            print(f"   This will result in ONE-SIDED GRID behavior (all buy orders, no sells).")
            print(f"   Consider updating config.json price_range to include current price.\n")
        elif current_price < min_price or current_price > max_price:
            print(f"\n⚠️  NOTE: Current price (${current_price:,.2f}) is slightly outside configured range")
            print(f"   Configured range: ${min_price:,.0f} - ${max_price:,.0f}")
            print(f"   Grid will still work but may have asymmetric buy/sell distribution.\n")

        # Calculate expected profits (pass bankroll for accurate return %)
        expected = self.grid.calculate_expected_profit(
            fills_per_day=15,
            bankroll=strategy['bankroll']
        )
        print(f"\nExpected Performance (15 fills/day):")
        print(f"  Gross Profit/Cycle:  ${expected['gross_profit_per_cycle']:.2f}")
        print(f"  Fees/Cycle:          ${expected['fees_per_cycle']:.2f}")
        print(f"  Net Profit/Cycle:    ${expected['net_profit_per_cycle']:.2f}")
        print(f"  Daily Profit:        ${expected['daily_profit']:.2f} ({expected['daily_return_percent']}%)")
        print(f"  Monthly Profit:      ${expected['monthly_profit']:.2f} ({expected['monthly_return_percent']}%)")

        # Log setup
        self.logger.log_grid_setup(
            campaign_name=campaign,
            pair=pair,
            grid_levels=strategy['grid_levels'],
            spacing_percent=strategy['grid_spacing_percent'],
            price_range=strategy['price_range'],
            status='success'
        )
        self._store_call(
            "setup_complete_event",
            lambda: self.store.save_event(
                self.session_id,
                "setup_complete",
                {
                    "campaign_name": campaign,
                    "pair": pair,
                    "grid_levels": strategy['grid_levels'],
                    "grid_spacing_percent": decision.accepted_params.get('grid_spacing_percent', strategy['grid_spacing_percent']),
                    "order_size_percent": decision.accepted_params.get('order_size_percent', strategy['order_size_percent']),
                    "price_range": decision.dynamic_range,
                    "scan_interval_seconds": strategy['scan_interval_seconds'],
                    "stop_loss_bankroll": risk['stop_loss_bankroll'],
                    "current_price": current_price,
                    "expected": expected,
                    "backtest_optimization": self.backtest_optimization,
                },
            ),
        )
        self._persist_known_open_orders()
        self.adaptive_store.save()

        print("\n✓ Setup complete!")
        print("\nNext steps:")
        print("  1. Run dry-run mode: python scripts/agent.py dry-run --config config.json")
        print("  2. Run live mode:    python scripts/agent.py start --config config.json")
        print("\n============================================================\n")

    def dry_run(self, cycles: int = 5):
        """Phase 2: Dry-run simulation (no real orders)"""
        print("\n============================================================")
        print("KRAKEN GRID TRADER - DRY RUN")
        print("============================================================\n")

        if self.grid is None:
            print("ERROR: Run setup first")
            return

        pair = self.config['trading_pair']
        scan_interval = self.config['strategy']['scan_interval_seconds']
        self._ensure_session_started()
        self._store_call(
            "dry_run_started_event",
            lambda: self.store.save_event(
                self.session_id,
                "dry_run_started",
                {"pair": pair, "cycles": cycles, "scan_interval_seconds": scan_interval},
            ),
        )

        print(f"Simulating {cycles} cycles...")
        print(f"Scan interval: {scan_interval}s\n")

        for cycle in range(cycles):
            print(f"--- Cycle {cycle + 1}/{cycles} ---")

            market_snapshot = self._get_market_snapshot(pair)
            current_price = float(market_snapshot['current_price'])
            decision, market_metrics = self._apply_adaptive_grid(
                current_price=current_price,
                market_snapshot=market_snapshot,
                live_risk=self._build_live_risk(current_price, 0.0, float(self.config['strategy']['bankroll'])),
            )
            print(f"Current Price: ${current_price:,.2f}")
            print(f"Adaptive Regime: {decision.regime_tag}")
            print(
                "Accepted Params: "
                f"spacing={decision.accepted_params.get('grid_spacing_percent', self.config['strategy']['grid_spacing_percent'])}% "
                f"order_size={decision.accepted_params.get('order_size_percent', self.config['strategy']['order_size_percent'])}% "
                f"risk={decision.accepted_params.get('risk_multiplier', 1.0):.2f}x"
            )

            required_orders = self.grid.get_required_orders(current_price)
            num_buy_orders = len(required_orders['buy'])
            num_sell_orders = len(required_orders['sell'])

            print(f"Would place {num_buy_orders} buy orders below ${current_price:,.2f}")
            print(f"Would place {num_sell_orders} sell orders above ${current_price:,.2f}")

            # Show next levels
            next_buy = self.grid.get_next_buy_level(current_price)
            next_sell = self.grid.get_next_sell_level(current_price)
            if next_buy:
                print(f"Next buy level:  ${next_buy:,.2f}")
            if next_sell:
                print(f"Next sell level: ${next_sell:,.2f}")
            print(
                f"Dynamic Range:   ${decision.dynamic_range['min']:,.2f} - ${decision.dynamic_range['max']:,.2f}"
            )
            print(
                f"Metrics:         spread={market_metrics['spread_pct']:.4f}% "
                f"atr={market_metrics['atr_pct']:.4f}% "
                f"rolling_vol={market_metrics['rolling_stddev_pct']:.4f}%"
            )
            self.logger.log_metrics_snapshot(
                {
                    'timestamp': datetime.utcnow().isoformat() + 'Z',
                    'pair': pair,
                    'mode': 'dry_run_preview',
                    'cycle_index': cycle + 1,
                    'market_price': round(current_price, 6),
                    'regime_tag': decision.regime_tag,
                    'grid_spacing_percent': decision.accepted_params.get('grid_spacing_percent'),
                    'order_size_percent': decision.accepted_params.get('order_size_percent'),
                    'dynamic_range': decision.dynamic_range,
                    'candidate_score': decision.candidate_score,
                    'baseline_score': decision.baseline_score,
                    'reasons': decision.reasons,
                }
            )

            print()
            time.sleep(2)  # Short delay for readability

        print("✓ Dry run complete!")
        self._store_call(
            "dry_run_completed_event",
            lambda: self.store.save_event(
                self.session_id,
                "dry_run_completed",
                {"pair": pair, "cycles": cycles},
            ),
        )
        print("\nTo run live mode:")
        print("  python scripts/agent.py start --config config.json")
        print("\n============================================================\n")

    def start(self):
        """Phase 3: Start live trading"""
        print("\n============================================================")
        print("KRAKEN GRID TRADER - LIVE MODE")
        print("============================================================\n")

        if self.grid is None:
            print("ERROR: Run setup first")
            return

        pair = self.config['trading_pair']
        scan_interval = self.config['strategy']['scan_interval_seconds']
        stop_loss = self.config['risk_management']['stop_loss_bankroll']
        self._ensure_session_started()

        print(f"Trading Pair:    {pair}")
        print(f"Scan Interval:   {scan_interval}s")
        print(f"Stop Loss:       ${stop_loss:,.2f}")
        print("\nStarting live trading... (Press Ctrl+C to stop)\n")
        self._store_call(
            "live_trading_started_event",
            lambda: self.store.save_event(
                self.session_id,
                "live_trading_started",
                {
                    "pair": pair,
                    "scan_interval_seconds": scan_interval,
                    "stop_loss_bankroll": stop_loss,
                    "runtime_version": LIVE_SAFETY_VERSION,
                },
            ),
        )

        self.running = True

        try:
            while self.running:
                self.run_cycle()
                time.sleep(scan_interval)

        except KeyboardInterrupt:
            print("\n\nReceived stop signal...")
            self.stop()

    def run_cycle(self) -> Dict[str, Any]:
        """Execute exactly one adaptive cycle under the shared runtime lock."""
        with runtime_lock(self._adaptive_lock_path()):
            self._trading_cycle()
            recent_cycles = list(self.adaptive_store.state.get('recent_cycles', []))
            if recent_cycles:
                return dict(recent_cycles[-1])
            return {'status': 'ok', 'message': 'cycle completed without persisted telemetry'}

    def build_review(self) -> Dict[str, Any]:
        """Generate and persist the weekly adaptive review report."""
        with runtime_lock(self._adaptive_lock_path()):
            report = build_review_report(self.adaptive_store)
            report_path = self.adaptive_store.record_review(report)
            self.adaptive_store.save()
            self.logger.log_review_report({**report, 'report_path': str(report_path)})
            self._store_call(
                'adaptive_review_event',
                lambda: self.store.save_event(
                    self.session_id,
                    'adaptive_review_generated',
                    {
                        'report_path': str(report_path),
                        'cycle_count': report['cycle_count'],
                        'rolling_windows': report['rolling_windows'],
                    },
                ),
            )
            return {**report, 'report_path': str(report_path)}

    def run_safety_check(self) -> Dict[str, Any]:
        """Run a one-shot safety evaluation and optionally surface cooldown alerts."""
        with runtime_lock(self._adaptive_lock_path()):
            pair = self.config['trading_pair']
            market_snapshot = self._get_market_snapshot(pair)
            current_price = float(market_snapshot['current_price'])
            if self.is_dry_run:
                base_balance = float(self.tracker.btc_balance if self.tracker is not None else 0.0)
                usd_balance = float(
                    self.tracker.usd_balance
                    if self.tracker is not None
                    else self.config['strategy']['bankroll']
                )
                open_order_count = len(self.active_orders)
            else:
                balance = self.seren.get_balance()
                balance_key = pair_selector.get_balance_key(pair, self.config.get('base_balance_key'))
                base_balance = float(balance['result'].get(balance_key, 0))
                usd_balance = float(balance['result'].get('ZUSD', 0))
                open_orders_response = self.seren.get_open_orders()
                open_order_count = len(open_orders_response['result']['open'])
            live_risk = self._build_live_risk(current_price, base_balance, usd_balance)
            daily_pnl = dict(self.adaptive_store.state.get('daily_pnl', {}))
            cooldown_active = self.adaptive_store.in_cooldown()
            issues = []
            if float(live_risk.get('drawdown_pct', 0.0)) > float(self.config['risk_management'].get('max_live_drawdown_pct', 0.0) or 0.0):
                issues.append('drawdown cap breached')
            daily_loss_cap = float(self.adaptive_settings.get('daily_loss_cap_usd', 0.0))
            if daily_loss_cap > 0 and float(daily_pnl.get('net_change_usd', 0.0)) <= -daily_loss_cap:
                issues.append('daily loss cap breached')
            if cooldown_active:
                issues.append('cooldown active')
            payload = {
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'pair': pair,
                'current_price': current_price,
                'open_order_count': open_order_count,
                'live_risk': live_risk,
                'daily_pnl': daily_pnl,
                'cooldown_active': cooldown_active,
                'failure_state': dict(self.adaptive_store.state.get('failure_state', {})),
                'issues': issues,
            }
            if issues:
                self.adaptive_store.note_incident('safety_check', payload)
                self.logger.log_alert('safety_check', payload)
            self.adaptive_store.save()
            return payload

    def _trading_cycle(self):
        """Execute one trading cycle"""
        pair = self.config['trading_pair']
        stop_loss = self.config['risk_management']['stop_loss_bankroll']

        try:
            self._cycle_deadline_at = time.monotonic() + self._cycle_timeout_seconds()
            market_snapshot = self._get_market_snapshot(pair)
            current_price = float(market_snapshot['current_price'])

            if self.is_dry_run:
                base_balance = float(self.tracker.btc_balance if self.tracker is not None else 0.0)
                usd_balance = float(
                    self.tracker.usd_balance
                    if self.tracker is not None
                    else self.config['strategy']['bankroll']
                )
                if self.tracker is not None:
                    self.tracker.update_balances(base_balance, usd_balance)
                live_risk = self._build_live_risk(current_price, base_balance, usd_balance)
                previous_known_orders: Dict[str, Any] = {}
                self.active_orders = {}
                if self.tracker is not None:
                    self.tracker.open_orders = {}
            else:
                balance = self._call_with_timeout('get_balance', self.seren.get_balance)
                balance_key = pair_selector.get_balance_key(
                    pair, self.config.get('base_balance_key')
                )
                base_balance = float(balance['result'].get(balance_key, 0))
                usd_balance = float(balance['result'].get('ZUSD', 0))
                self.tracker.update_balances(base_balance, usd_balance)
                live_risk = self._enforce_live_risk(current_price, base_balance, usd_balance)

                if self.tracker.should_stop_loss(current_price, stop_loss):
                    print(f"\n⚠ STOP LOSS TRIGGERED at ${self.tracker.get_current_value(current_price):,.2f}")
                    self._store_call(
                        "stop_loss_event",
                        lambda: self.store.save_event(
                            self.session_id,
                            "stop_loss_triggered",
                            {
                                "pair": pair,
                                "current_price": current_price,
                                "portfolio_value": self.tracker.get_current_value(current_price),
                                "stop_loss_bankroll": stop_loss,
                                "live_risk": live_risk,
                            },
                        ),
                    )
                    self.stop()
                    return

                open_orders_response = self._call_with_timeout(
                    'get_open_orders',
                    self.seren.get_open_orders,
                )
                previous_known_orders = self._sync_active_orders(open_orders_response['result']['open'])

            decision, market_metrics = self._apply_adaptive_grid(
                current_price=current_price,
                market_snapshot=market_snapshot,
                live_risk=live_risk,
            )

            if decision.promoted or decision.rolled_back or decision.daily_loss_triggered:
                incident_type = 'adaptive_promotion'
                if decision.rolled_back:
                    incident_type = 'adaptive_rollback'
                elif decision.daily_loss_triggered:
                    incident_type = 'daily_loss_cap'
                self.adaptive_store.note_incident(
                    incident_type,
                    {
                        'pair': pair,
                        'current_price': current_price,
                        'reasons': decision.reasons,
                        'candidate_score': decision.candidate_score,
                        'baseline_score': decision.baseline_score,
                    },
                )
                self.logger.log_alert(
                    incident_type,
                    {
                        'pair': pair,
                        'current_price': current_price,
                        'reasons': decision.reasons,
                    },
                )

            cancelled_for_safety = 0
            if (decision.cooldown_active or decision.daily_loss_triggered) and not self.is_dry_run:
                cancelled_for_safety = self._cancel_open_buy_orders()

            current_open_orders = {
                order_id: {
                    'descr': {
                        'type': details.get('side'),
                        'price': details.get('price'),
                    },
                    'vol': details.get('volume'),
                }
                for order_id, details in self.active_orders.items()
            }
            filled_order_ids = []
            if not self.is_dry_run:
                filled_order_ids = self.grid.find_filled_orders(
                    previous_known_orders,
                    current_open_orders,
                )

            for order_id in filled_order_ids:
                self._process_fill(
                    order_id,
                    current_price,
                    order_details=previous_known_orders.get(order_id),
                )

            current_open_orders = {
                order_id: {
                    'descr': {
                        'type': details.get('side'),
                        'price': details.get('price'),
                    },
                    'vol': details.get('volume'),
                }
                for order_id, details in self.active_orders.items()
            }
            required_orders = self.grid.get_required_orders(current_price)
            placement_summary = self._place_grid_orders(
                required_orders,
                current_open_orders,
                usd_balance,
                base_balance=base_balance,
                skip_new_buys=decision.cooldown_active or decision.daily_loss_triggered,
            )

            total_value_usd = self.tracker.get_current_value(current_price)
            unrealized_pnl = self.tracker.get_unrealized_pnl(current_price)
            recent_cycles = list(self.adaptive_store.state.get('recent_cycles', []))
            previous_equity = float(recent_cycles[-1]['equity_end_usd']) if recent_cycles else float(self.config['strategy']['bankroll'])
            net_pnl_usd = total_value_usd - previous_equity
            fill_count = len(filled_order_ids)
            order_slots = max(len(previous_known_orders), 1)
            fill_rate = fill_count / order_slots
            cancel_rate = cancelled_for_safety / order_slots if order_slots > 0 else 0.0
            cycle_snapshot = {
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'pair': pair,
                'mode': 'dry_run' if self.is_dry_run else 'live',
                'market_price': round(current_price, 6),
                'bid': round(float(market_snapshot.get('bid', 0.0)), 6),
                'ask': round(float(market_snapshot.get('ask', 0.0)), 6),
                'spread_pct': round(float(market_metrics.get('spread_pct', 0.0)), 6),
                'atr_pct': round(float(market_metrics.get('atr_pct', 0.0)), 6),
                'rolling_stddev_pct': round(float(market_metrics.get('rolling_stddev_pct', 0.0)), 6),
                'ema_volatility_pct': round(float(decision.volatility_metrics.get('ema_volatility_pct', 0.0)), 6),
                'regime_tag': decision.regime_tag,
                'grid_spacing_percent': round(float(decision.accepted_params.get('grid_spacing_percent', self.config['strategy']['grid_spacing_percent'])), 6),
                'order_size_percent': round(float(decision.accepted_params.get('order_size_percent', self.config['strategy']['order_size_percent'])), 6),
                'max_open_orders': int(decision.accepted_params.get('max_open_orders', self.config['risk_management']['max_open_orders'])),
                'risk_multiplier': round(float(decision.accepted_params.get('risk_multiplier', 1.0)), 6),
                'dynamic_center_price': round(float(decision.dynamic_center_price), 6),
                'dynamic_range': dict(decision.dynamic_range),
                'equity_end_usd': round(total_value_usd, 6),
                'net_pnl_usd': round(net_pnl_usd, 6),
                'unrealized_pnl_usd': round(unrealized_pnl, 6),
                'realized_pnl_usd': round(self.tracker.get_realized_pnl(), 6),
                'drawdown_pct': round(float(live_risk.get('drawdown_pct', 0.0)), 6),
                'open_orders': len(self.active_orders),
                'fill_count': fill_count,
                'fill_rate': round(fill_rate, 6),
                'cancel_rate': round(cancel_rate, 6),
                'placed_buy_orders': placement_summary['placed_buy'],
                'placed_sell_orders': placement_summary['placed_sell'],
                'skipped_buy_orders': placement_summary['skipped_buy'],
                'skipped_sell_orders': placement_summary['skipped_sell'],
                'candidate_score': round(decision.candidate_score, 6),
                'baseline_score': round(decision.baseline_score, 6),
                'cooldown_active': decision.cooldown_active,
                'daily_loss_triggered': decision.daily_loss_triggered,
                'promoted': decision.promoted,
                'rolled_back': decision.rolled_back,
                'reasons': list(decision.reasons),
            }
            update_cycle_state(store=self.adaptive_store, cycle_snapshot=cycle_snapshot)
            self.adaptive_store.clear_failures()
            self._persist_known_open_orders()
            self.adaptive_store.save()
            rolling_windows = self._rolling_window_metrics()
            self.logger.log_metrics_snapshot(
                {
                    **cycle_snapshot,
                    'rolling_windows': rolling_windows,
                }
            )
            self.logger.log_position_update(
                pair=pair,
                btc_balance=base_balance,
                usd_balance=usd_balance,
                total_value_usd=total_value_usd,
                unrealized_pnl=unrealized_pnl,
                open_orders=len(self.active_orders),
                extra={
                    'regime_tag': decision.regime_tag,
                    'grid_spacing_percent': cycle_snapshot['grid_spacing_percent'],
                    'order_size_percent': cycle_snapshot['order_size_percent'],
                    'risk_multiplier': cycle_snapshot['risk_multiplier'],
                    'rolling_windows': rolling_windows,
                    'fill_count': fill_count,
                    'cancelled_for_safety': cancelled_for_safety,
                },
            )
            self._store_call(
                "position_snapshot",
                lambda: self.store.save_position(
                    session_id=self.session_id,
                    trading_pair=pair,
                    base_balance=base_balance,
                    quote_balance=usd_balance,
                    total_value_usd=total_value_usd,
                    unrealized_pnl=unrealized_pnl,
                    open_orders=len(self.active_orders),
                ),
            )

            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            print(f"[{timestamp}] Price: ${current_price:,.2f} | "
                  f"Regime: {decision.regime_tag} | "
                  f"Open Orders: {len(self.active_orders)} | "
                  f"Fills: {len(self.tracker.filled_orders)} | "
                  f"P&L: ${unrealized_pnl:,.2f}")

        except Exception as e:
            error_msg = str(e)
            self.adaptive_store.register_failure(error_msg)
            self.adaptive_store.save()
            print(f"ERROR in trading cycle: {error_msg}")
            self._halt_live_trading(
                'trading_cycle_error',
                {
                    "error_type": type(e).__name__,
                    "error_message": error_msg,
                    "pair": pair,
                },
            )
        finally:
            self._cycle_deadline_at = None

    def _place_grid_orders(
        self,
        required_orders: Dict,
        current_open_orders: Dict,
        usd_balance: float,
        *,
        base_balance: Optional[float] = None,
        skip_new_buys: bool = False,
    ):
        """Place grid orders that aren't already open"""
        pair = self.config['trading_pair']
        risk = self.config.get('risk_management', {})
        min_quote_reserve = float(risk.get('min_quote_reserve_usd', 0.0))
        max_position_size = float(risk.get('max_position_size', 1.0))
        default_max_open_orders = int(risk.get('max_open_orders', 40))
        accepted = self.current_adaptive_decision.accepted_params if self.current_adaptive_decision else {}
        max_open_orders = int(accepted.get('max_open_orders', default_max_open_orders))
        current_base_balance = float(
            base_balance if base_balance is not None else (self.tracker.btc_balance if self.tracker is not None else 0.0)
        )
        summary = {
            'placed_buy': 0,
            'placed_sell': 0,
            'skipped_buy': 0,
            'skipped_sell': 0,
        }

        open_prices = set()
        committed_buy_notional = 0.0
        committed_buy_volume = 0.0
        committed_sell_volume = 0.0
        for order_data in current_open_orders.values():
            descr = order_data['descr']
            price = float(descr['price'])
            open_prices.add(price)
            if descr.get('type') == 'buy':
                volume = float(order_data.get('vol', 0.0))
                committed_buy_notional += price * volume
                committed_buy_volume += volume
            elif descr.get('type') == 'sell':
                committed_sell_volume += float(order_data.get('vol', 0.0))

        current_open_count = len(current_open_orders)

        for order in required_orders['buy']:
            if order['price'] not in open_prices:
                if skip_new_buys:
                    summary['skipped_buy'] += 1
                    continue
                if current_open_count >= max_open_orders:
                    summary['skipped_buy'] += 1
                    continue
                order_notional = float(order['price']) * float(order['volume'])
                projected_position = current_base_balance + committed_buy_volume + float(order['volume'])
                if projected_position > max_position_size:
                    summary['skipped_buy'] += 1
                    continue
                available_buying_power = usd_balance - committed_buy_notional - min_quote_reserve
                if (not self.is_dry_run) and order_notional > max(available_buying_power, 0.0):
                    print(
                        f"Skipping buy @ ${order['price']:,.2f}: "
                        f"quote reserve ${min_quote_reserve:,.2f} would be breached"
                    )
                    self._store_call(
                        "quote_reserve_skip_event",
                        lambda: self.store.save_event(
                            self.session_id,
                            "quote_reserve_skip",
                            {
                                "pair": pair,
                                "price": order['price'],
                                "volume": order['volume'],
                                "requested_notional_usd": round(order_notional, 2),
                                "available_buying_power_usd": round(max(available_buying_power, 0.0), 2),
                                "min_quote_reserve_usd": round(min_quote_reserve, 2),
                            },
                        ),
                    )
                    summary['skipped_buy'] += 1
                    continue
                if self._place_order(
                    pair=pair,
                    side='buy',
                    price=order['price'],
                    volume=order['volume']
                ):
                    committed_buy_notional += order_notional
                    committed_buy_volume += float(order['volume'])
                    current_open_count += 1
                    summary['placed_buy'] += 1

        for order in required_orders['sell']:
            if order['price'] not in open_prices:
                if current_open_count >= max_open_orders:
                    summary['skipped_sell'] += 1
                    continue
                available_inventory = current_base_balance - committed_sell_volume
                if (not self.is_dry_run) and float(order['volume']) > max(available_inventory, 0.0):
                    summary['skipped_sell'] += 1
                    continue
                if self._place_order(
                    pair=pair,
                    side='sell',
                    price=order['price'],
                    volume=order['volume']
                ):
                    committed_sell_volume += float(order['volume'])
                    current_open_count += 1
                    summary['placed_sell'] += 1

        return summary

    def _place_order(self, pair: str, side: str, price: float, volume: float):
        """Place a single limit order"""
        base = pair_selector.get_base_symbol(pair)
        try:
            if self.is_dry_run:
                print(f"[DRY RUN] Would place {side} order: {volume:.8f} {base} @ ${price:,.2f}")
                return True

            response = self._call_with_timeout(
                'add_order',
                lambda: self.seren.add_order(
                    pair=pair,
                    order_type='limit',
                    side=side,
                    volume=volume,
                    price=price
                ),
            )

            if 'result' in response and 'txid' in response['result']:
                order_id = response['result']['txid'][0]
                self.active_orders[order_id] = {
                    'side': side,
                    'price': price,
                    'volume': volume,
                    'placed_at': datetime.utcnow().isoformat(),
                }
                self.tracker.add_open_order(order_id, {
                    'side': side,
                    'price': price,
                    'volume': volume
                })
                self.logger.log_order(
                    order_id=order_id,
                    order_type='limit',
                    side=side,
                    price=price,
                    volume=volume,
                    status='placed',
                    extra={
                        'pair': pair,
                        'notional_usd': round(price * volume, 6),
                        'risk_multiplier': (
                            self.current_adaptive_decision.accepted_params.get('risk_multiplier', 1.0)
                            if self.current_adaptive_decision
                            else 1.0
                        ),
                    },
                )
                self._store_call(
                    "order_placed",
                    lambda: self.store.save_order(
                        session_id=self.session_id,
                        order_id=order_id,
                        side=side,
                        price=price,
                        volume=volume,
                        status='placed',
                        payload={
                            "pair": pair,
                            "order_type": "limit",
                        },
                    ),
                )
                print(f"✓ Placed {side} order: {volume:.8f} {base} @ ${price:,.2f} (ID: {order_id})")
                return True

        except Exception as e:
            error_msg = str(e)
            print(f"ERROR placing {side} order at ${price:,.2f}: {error_msg}")
            self.logger.log_error(
                operation='place_order',
                error_type=type(e).__name__,
                error_message=error_msg,
                context={'side': side, 'price': price, 'volume': volume}
            )
            self._store_call(
                "order_error_event",
                lambda: self.store.save_event(
                    self.session_id,
                    "order_error",
                    {
                        "pair": pair,
                        "side": side,
                        "price": price,
                        "volume": volume,
                        "error_type": type(e).__name__,
                        "error_message": error_msg,
                    },
                ),
            )
        return False

    def _process_fill(self, order_id: str, current_price: float, order_details: Optional[Dict[str, Any]] = None):
        """Process a filled order"""
        order = order_details or self.active_orders.get(order_id)
        if order is None:
            return

        side = order['side']
        price = order['price']
        volume = order['volume']
        placed_at = order.get('placed_at')

        # Calculate fee (0.16% maker fee)
        cost = price * volume
        fee = cost * 0.0016
        latency_seconds = None
        if placed_at:
            try:
                placed_dt = datetime.fromisoformat(str(placed_at).replace('Z', '+00:00'))
                latency_seconds = max((datetime.utcnow() - placed_dt.replace(tzinfo=None)).total_seconds(), 0.0)
            except ValueError:
                latency_seconds = None

        # Record fill
        self.tracker.record_fill(
            order_id=order_id,
            side=side,
            price=price,
            volume=volume,
            fee=fee,
            cost=cost
        )

        self.logger.log_fill(
            order_id=order_id,
            side=side,
            price=price,
            volume=volume,
            fee=fee,
            cost=cost,
            extra={
                'pair': self.config['trading_pair'],
                'latency_seconds': round(latency_seconds, 6) if latency_seconds is not None else None,
                'current_price': round(current_price, 6),
                'slippage_vs_last_trade': round(current_price - price, 6),
            },
        )
        self._store_call(
            "fill_recorded",
            lambda: self.store.save_fill(
                session_id=self.session_id,
                order_id=order_id,
                side=side,
                price=price,
                volume=volume,
                fee=fee,
                cost=cost,
                payload={"pair": self.config['trading_pair']},
            ),
        )
        self.adaptive_store.append_fill(
            {
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'order_id': order_id,
                'side': side,
                'price': price,
                'quantity': volume,
                'fee_usd': fee,
                'cost_usd': cost,
                'latency_seconds': latency_seconds,
                'current_price': current_price,
            }
        )

        # Remove from active orders
        self.active_orders.pop(order_id, None)

        base = pair_selector.get_base_symbol(self.config['trading_pair'])
        print(f"✓ FILLED {side.upper()}: {volume:.8f} {base} @ ${price:,.2f} (Fee: ${fee:.2f})")

    def status(self):
        """Show current trading status"""
        if self.tracker is None or self.grid is None:
            self.setup(optimize_backtest=False)

        pair = self.config['trading_pair']

        # Get current price
        current_price = self.seren.get_current_price(pair)

        # Print position summary
        print(self.tracker.get_position_summary(current_price))

    def stop(self):
        """Stop trading and cancel all orders"""
        print("\nStopping trading...")

        self.running = False
        self._ensure_session_started()
        self._store_call(
            "stop_requested_event",
            lambda: self.store.save_event(
                self.session_id,
                "stop_requested",
                {
                    "is_dry_run": self.is_dry_run,
                    "active_orders": len(self.active_orders),
                },
            ),
        )

        if not self.is_dry_run:
            try:
                # Cancel all open orders
                print("Cancelling all open orders...")
                self._call_with_timeout('cancel_all_orders', self.seren.cancel_all_orders)
                self.active_orders.clear()
                self.adaptive_store.state['known_open_orders'] = {}
                print("✓ All orders cancelled")

            except Exception as e:
                print(f"ERROR cancelling orders: {e}")
                self._store_call(
                    "cancel_orders_error_event",
                    lambda: self.store.save_event(
                        self.session_id,
                        "cancel_orders_error",
                        {"error_type": type(e).__name__, "error_message": str(e)},
                    ),
                )

        # Print final status
        if self.tracker:
            pair = self.config['trading_pair']
            current_price = self.seren.get_current_price(pair)
            print(self.tracker.get_position_summary(current_price))

            # Export fills to CSV
            output_path = f"fills_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
            self.tracker.export_fills_to_csv(output_path)
            print(f"\n✓ Fills exported to {output_path}")

        print("\n✓ Trading stopped\n")
        self.adaptive_store.save()



# ---------------------------------------------------------------------------
# SerenBucks balance helpers
# ---------------------------------------------------------------------------

from typing import Any


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
                "User-Agent": "seren-kraken-grid-trader/1.0",
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

def _require_live_confirmation(command: str, allow_live: bool) -> None:
    if command == "start" and not allow_live:
        raise SystemExit(
            "Live mode requested but --allow-live was not provided. "
            "Use `python scripts/agent.py start --config config.json --allow-live` "
            "for the startup-only live opt-in."
        )

def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Kraken Grid Trading Bot',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Setup command
    setup_parser = subparsers.add_parser('setup', help='Setup and validate configuration')
    setup_parser.add_argument('--config', required=True, help='Path to config JSON file')

    # Dry-run command
    dryrun_parser = subparsers.add_parser('dry-run', help='Simulate trading without placing real orders')
    dryrun_parser.add_argument('--config', required=True, help='Path to config JSON file')
    dryrun_parser.add_argument('--cycles', type=int, default=5, help='Number of cycles to simulate')

    # Start command
    start_parser = subparsers.add_parser('start', help='Start live trading')
    start_parser.add_argument('--config', required=True, help='Path to config JSON file')
    start_parser.add_argument(
        '--allow-live',
        action='store_true',
        help='Explicit startup-only opt-in for live trading.',
    )

    cycle_parser = subparsers.add_parser('cycle', help='Run exactly one adaptive cycle')
    cycle_parser.add_argument('--config', required=True, help='Path to config JSON file')
    cycle_parser.add_argument(
        '--allow-live',
        action='store_true',
        help='Opt into live order placement for the single cycle; otherwise runs as a dry adaptive preview.',
    )

    review_parser = subparsers.add_parser('review', help='Generate the weekly adaptive review report')
    review_parser.add_argument('--config', required=True, help='Path to config JSON file')

    safety_parser = subparsers.add_parser('safety-check', help='Run the one-shot adaptive safety checks')
    safety_parser.add_argument('--config', required=True, help='Path to config JSON file')

    # Status command
    status_parser = subparsers.add_parser('status', help='Show current trading status')
    status_parser.add_argument('--config', required=True, help='Path to config JSON file')

    # Stop command
    stop_parser = subparsers.add_parser('stop', help='Stop trading and cancel all orders')
    stop_parser.add_argument('--config', required=True, help='Path to config JSON file')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    _require_live_confirmation(args.command, getattr(args, 'allow_live', False))

    dry_run = (args.command == 'dry-run') or (args.command == 'cycle' and not getattr(args, 'allow_live', False))
    agent = KrakenGridTrader(config_path=args.config, dry_run=dry_run)

    # Execute command
    try:
        if args.command == 'setup':
            agent.setup(optimize_backtest=True)
        elif args.command == 'dry-run':
            agent.setup(optimize_backtest=True)
            agent.dry_run(cycles=args.cycles)
        elif args.command == 'start':
            agent.setup(optimize_backtest=False)
            agent.start()
        elif args.command == 'cycle':
            agent.setup(optimize_backtest=False)
            print(json.dumps(agent.run_cycle(), sort_keys=True, indent=2))
        elif args.command == 'review':
            agent.setup(optimize_backtest=False)
            print(json.dumps(agent.build_review(), sort_keys=True, indent=2))
        elif args.command == 'safety-check':
            agent.setup(optimize_backtest=False)
            print(json.dumps(agent.run_safety_check(), sort_keys=True, indent=2))
        elif args.command == 'status':
            agent.status()
        elif args.command == 'stop':
            agent.stop()
    except RuntimeLockError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        raise SystemExit(2) from exc
    finally:
        agent.close()


if __name__ == '__main__':
    main()
