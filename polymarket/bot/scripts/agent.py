#!/usr/bin/env python3
"""
Polymarket Trading Agent - Autonomous prediction market trader

This agent:
1. Scans Polymarket for active markets
2. Researches opportunities using Perplexity
3. Estimates fair value with Claude
4. Identifies mispriced markets
5. Executes trades using Kelly Criterion
6. Monitors positions and reports P&L

Usage:
    python scripts/agent.py --config config.json [--dry-run]
"""

import argparse
import json
import os
import re
import sys
from copy import deepcopy

# --- Force unbuffered stdout so piped/background output is visible immediately ---
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
# --- End unbuffered stdout fix ---

from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

# Import our modules
from seren_client import SerenClient
from polymarket_client import PolymarketClient
from position_tracker import PositionTracker
from logger import TradingLogger
from serendb_storage import SerenDBStorage
import kelly
import calibration
from risk_guards import (
    auto_pause_cron,
    check_drawdown_stop_loss,
    check_position_age,
    sync_position_timestamps,
)


class TradingAgent:
    """Autonomous Polymarket trading agent"""

    def __init__(self, config_path: str, dry_run: bool = False):
        """
        Initialize trading agent

        Args:
            config_path: Path to config.json
            dry_run: If True, don't place actual trades
        """
        load_dotenv()

        # Load config
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        self.config_path = Path(config_path).resolve()
        self.logs_dir = self.config_path.parent / "logs"
        self.state_file = self.logs_dir / "runtime_state.json"
        self.runtime_state = self._load_runtime_state()
        self._open_order_exposure = {
            "market_ids": set(),
            "token_ids": set(),
            "event_counts": {},
        }

        self.dry_run = dry_run

        # Initialize clients
        print("Initializing Seren client...")
        self.seren = SerenClient()

        print("Initializing Polymarket client...")
        self.polymarket = PolymarketClient(
            self.seren,
            dry_run=dry_run,
        )

        # Initialize SerenDB storage
        print("Initializing SerenDB storage...")
        self.storage = SerenDBStorage(self.seren)

        # Setup database (creates tables if they don't exist)
        if not self.storage.setup_database():
            print("⚠️  Warning: SerenDB setup failed, falling back to file storage")
            self.storage = None
        else:
            self.storage.set_run_mode(self.dry_run)

        # Initialize position tracker and logger with SerenDB
        self.positions = PositionTracker(serendb_storage=self.storage)
        self.logger = TradingLogger(serendb_storage=self.storage)

        # Trading parameters from config
        self.bankroll = float(self.config['bankroll'])
        self.mispricing_threshold = float(self.config['mispricing_threshold'])
        self.max_kelly_fraction = float(self.config['max_kelly_fraction'])
        self.max_positions = int(self.config['max_positions'])
        self.stop_loss_bankroll = float(self.config.get('stop_loss_bankroll', 0.0))
        self.max_drawdown_pct = float(self.config.get("execution", {}).get("max_drawdown_pct", self.config.get("max_drawdown_pct", 15.0)))
        self.max_position_age_hours = float(self.config.get("execution", {}).get("max_position_age_hours", 72.0))
        self.min_serenbucks_balance = float(self.config.get("execution", {}).get("min_serenbucks_balance", 1.0))
        self.stale_order_max_age_seconds = int(self.config.get("execution", {}).get("stale_order_max_age_seconds", 1800))
        self.take_profit_pct = float(self.config.get("execution", {}).get("take_profit_pct", 0.10))
        self.position_stop_loss_pct = float(self.config.get("execution", {}).get("position_stop_loss_pct", 0.10))
        self.alert_move_pct = float(self.config.get("execution", {}).get("alert_move_pct", 0.08))
        self.near_resolution_hours = float(self.config.get("execution", {}).get("near_resolution_hours", 24.0))
        self.max_positions_per_event = int(
            self.config.get("execution", {}).get(
                "max_positions_per_event",
                self.config.get("max_per_category", 1),
            )
        )
        self.cron_job_id = self.config.get("cron", {}).get("job_id", os.environ.get("SEREN_CRON_JOB_ID", ""))

        # Safety guards (configurable, with backward-compatible defaults)
        self.min_annualized_return = float(self.config.get('min_annualized_return', 0.25))
        self.max_resolution_days = int(self.config.get('max_resolution_days', 180))
        self.min_exit_bid_depth_ratio = float(self.config.get('min_exit_bid_depth_ratio', 0.5))

        # Scan pipeline limits (configurable, with backward-compatible defaults)
        self.scan_limit = int(self.config.get('scan_limit', 100))
        self.candidate_limit = int(self.config.get('candidate_limit', 20))
        self.analyze_limit = int(self.config.get('analyze_limit', self.candidate_limit))
        self.min_liquidity = float(self.config.get('min_liquidity', 10000.0))
        self.stale_price_demotion = float(self.config.get('stale_price_demotion', 0.1))

        # Market selection sanity gates
        self.max_divergence = float(self.config.get('max_divergence', 0.50))
        self.min_buy_price = float(self.config.get('min_buy_price', 0.02))
        self.min_volume = float(self.config.get('min_volume', 5000.0))

        # Execution quality gates
        self.min_edge_to_spread_ratio = float(self.config.get('min_edge_to_spread_ratio', 3.0))
        self.max_depth_fraction = float(self.config.get('max_depth_fraction', 0.25))

        # Calibration-driven threshold override
        self._calibration = calibration.load_calibration()
        self.effective_mispricing_threshold, self._threshold_reason = (
            calibration.effective_threshold(self.mispricing_threshold, self._calibration)
        )

        print(f"✓ Agent initialized (Dry-run: {dry_run})")
        print(f"  Bankroll: ${self.bankroll:.2f}")
        print(f"  Mispricing threshold: {self.effective_mispricing_threshold * 100:.1f}% ({self._threshold_reason})")
        print(f"  Max Kelly fraction: {self.max_kelly_fraction * 100:.1f}%")
        print(f"  Max positions: {self.max_positions}")
        print(f"  Scan pipeline: fetch={self.scan_limit} → candidates={self.candidate_limit} → analyze={self.analyze_limit}")
        print()

        # Sync positions on startup
        print("Syncing positions with Polymarket...")
        try:
            sync_result = self.positions.sync_with_polymarket(self.polymarket)
            print(f"✓ Position sync complete:")
            print(f"  Added: {sync_result['added']}")
            print(f"  Updated: {sync_result['updated']}")
            print(f"  Removed: {sync_result['removed']}")
            print(f"  Total positions: {len(self.positions.get_all_positions())}")
        except Exception as e:
            print(f"⚠️  Position sync failed: {e}")
        print()

    def _load_runtime_state(self) -> Dict[str, Any]:
        """Load cron-maintained local runtime state."""
        if not self.state_file.exists():
            return {
                "order_timestamps": {},
                "position_alerts": {},
                "pending_exit_markets": {},
            }
        try:
            with open(self.state_file, "r") as handle:
                payload = json.load(handle)
        except Exception:
            return {
                "order_timestamps": {},
                "position_alerts": {},
                "pending_exit_markets": {},
            }
        if not isinstance(payload, dict):
            return {
                "order_timestamps": {},
                "position_alerts": {},
                "pending_exit_markets": {},
            }
        payload.setdefault("order_timestamps", {})
        payload.setdefault("position_alerts", {})
        payload.setdefault("pending_exit_markets", {})
        return payload

    def _save_runtime_state(self) -> None:
        """Persist cron-maintained local runtime state."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as handle:
            json.dump(self.runtime_state, handle, indent=2, sort_keys=True)

    @staticmethod
    def _iso_to_datetime(raw_value: str) -> Optional[datetime]:
        if not raw_value:
            return None
        try:
            dt = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed != parsed:
            return default
        return parsed

    @staticmethod
    def _rows_from_payload(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
        return []

    def _extract_order_timestamps(
        self,
        raw_orders: Any,
        prior_order_timestamps: Dict[str, str],
    ) -> Dict[str, str]:
        """Build a best-effort order id -> first seen timestamp map."""
        now_iso = datetime.now(timezone.utc).isoformat()
        extracted: Dict[str, str] = {}
        for row in self._rows_from_payload(raw_orders):
            order_id = ""
            for key in ("id", "orderID", "order_id"):
                value = str(row.get(key, "")).strip()
                if value:
                    order_id = value
                    break
            if not order_id:
                continue
            timestamp = ""
            for key in ("createdAt", "created_at", "placed_at", "updatedAt", "updated_at", "timestamp"):
                value = str(row.get(key, "")).strip()
                if value:
                    timestamp = value
                    break
            extracted[order_id] = prior_order_timestamps.get(order_id) or timestamp or now_iso
        return extracted

    def _extract_open_order_exposure(self, raw_orders: Any) -> Dict[str, Any]:
        """Build a best-effort snapshot of live resting-order exposure."""
        market_ids: set[str] = set()
        token_ids: set[str] = set()
        event_counts: Dict[str, int] = {}

        for row in self._rows_from_payload(raw_orders):
            market_id = PositionTracker._first_text(
                row.get("market_id"),
                row.get("conditionId"),
                row.get("market"),
                row.get("market_slug"),
            )
            token_id = PositionTracker._first_text(
                row.get("token_id"),
                row.get("tokenId"),
                row.get("asset_id"),
                row.get("assetId"),
                row.get("clobTokenId"),
            )
            event_id = PositionTracker._first_text(
                row.get("event_id"),
                row.get("seriesSlug"),
                row.get("category"),
            )
            if market_id:
                market_ids.add(market_id)
            if token_id:
                token_ids.add(token_id)
            if event_id:
                event_counts[event_id] = event_counts.get(event_id, 0) + 1

        return {
            "market_ids": market_ids,
            "token_ids": token_ids,
            "event_counts": event_counts,
        }

    def _has_open_order_exposure(self, market: Dict[str, Any]) -> bool:
        exposure = getattr(self, "_open_order_exposure", None) or {}
        market_ids = exposure.get("market_ids", set())
        token_ids = exposure.get("token_ids", set())
        if market.get("market_id") in market_ids:
            return True
        for key in ("token_id", "no_token_id"):
            token = str(market.get(key, "")).strip()
            if token and token in token_ids:
                return True
        return False

    def _pending_exit_is_fresh(self, raw_value: str) -> bool:
        pending_at = self._iso_to_datetime(raw_value)
        if pending_at is None:
            return False
        return (datetime.now(timezone.utc) - pending_at).total_seconds() < self.stale_order_max_age_seconds

    def _resolve_position_roi(self, position) -> float:
        if position.size <= 0:
            return 0.0
        return position.unrealized_pnl / position.size

    def _pending_exit_for_market(self, market_id: str) -> bool:
        pending = self.runtime_state.get("pending_exit_markets", {})
        raw = str(pending.get(market_id, "")).strip()
        if not raw:
            return False
        return self._pending_exit_is_fresh(raw)

    def _clear_pending_exit(self, market_id: str) -> None:
        self.runtime_state.get("pending_exit_markets", {}).pop(market_id, None)

    def _prune_pending_exit_markets(self, open_market_ids: set[str]) -> None:
        pending = self.runtime_state.setdefault("pending_exit_markets", {})
        stale_market_ids = [
            market_id
            for market_id, pending_at in pending.items()
            if market_id not in open_market_ids or not self._pending_exit_is_fresh(str(pending_at))
        ]
        for market_id in stale_market_ids:
            pending.pop(market_id, None)

    def _record_position_alert(self, market_id: str, key: str) -> bool:
        """Return True if this alert should be emitted once."""
        alerts = self.runtime_state.setdefault("position_alerts", {})
        market_alerts = alerts.setdefault(market_id, {})
        if market_alerts.get(key):
            return False
        market_alerts[key] = datetime.now(timezone.utc).isoformat()
        return True

    def check_balances(self) -> Dict[str, float]:
        """
        Check SerenBucks and Polymarket balances

        Returns:
            Dict with 'serenbucks' and 'polymarket' balances
        """
        try:
            wallet_status = self.seren.get_wallet_balance()
            # API returns balance_usd (float) and balance_atomic (int)
            serenbucks = float(wallet_status.get('balance_usd', 0.0))
        except Exception as e:
            print(f"Warning: Failed to fetch SerenBucks balance: {e}")
            serenbucks = 0.0

        try:
            polymarket = self.polymarket.get_balance()
        except Exception as e:
            print(f"Warning: Failed to fetch Polymarket balance: {e}")
            polymarket = 0.0

        return {
            'serenbucks': serenbucks,
            'polymarket': polymarket
        }

    def _close_position_via_guard(
        self,
        position,
        reason: str,
        roi_pct: float,
        *,
        open_orders: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Submit a marketable SELL to close a held YES/NO token position."""
        from polymarket_live import build_marketable_sell_order

        trader = self.polymarket._require_trader()
        cancelled_order_ids: List[str] = []
        raw_orders = open_orders if open_orders is not None else self.polymarket.get_open_orders()
        for row in self._rows_from_payload(raw_orders):
            order_id = PositionTracker._first_text(
                row.get("id"),
                row.get("orderID"),
                row.get("order_id"),
            )
            order_market_id = PositionTracker._first_text(
                row.get("market_id"),
                row.get("conditionId"),
                row.get("market"),
                row.get("market_slug"),
            )
            order_token_id = PositionTracker._first_text(
                row.get("token_id"),
                row.get("tokenId"),
                row.get("asset_id"),
                row.get("assetId"),
                row.get("clobTokenId"),
            )
            if not order_id:
                continue
            if order_token_id == position.token_id or order_market_id == position.market_id:
                try:
                    self.polymarket.cancel_order(order_id)
                    cancelled_order_ids.append(order_id)
                except Exception:
                    continue

        sell_plan = build_marketable_sell_order(position.token_id, position.quantity)
        response = trader.create_order(
            token_id=position.token_id,
            side="SELL",
            price=sell_plan["price"],
            size=position.quantity,
            tick_size=sell_plan["tick_size"],
            neg_risk=sell_plan["neg_risk"],
            fee_rate_bps=sell_plan["fee_rate_bps"],
        )

        self.runtime_state.setdefault("pending_exit_markets", {})[position.market_id] = datetime.now(timezone.utc).isoformat()
        self.logger.log_trade(
            market=position.market,
            market_id=position.market_id,
            side=position.thesis_side,
            size=position.size,
            price=position.current_price,
            fair_value=position.current_price,
            edge=0.0,
            status="closing",
            pnl=round(position.unrealized_pnl, 2),
        )
        self.logger.log_notification(
            level="warning" if roi_pct < 0 else "info",
            title=f"Guard Exit: {reason}",
            message=(
                f"Submitted marketable close for \"{position.market}\".\n"
                f"  • Held side: {position.side}\n"
                f"  • ROI: {roi_pct * 100:+.1f}%\n"
                f"  • Quantity: {position.quantity:.6f}\n"
                f"  • Cancelled resting orders: {len(cancelled_order_ids)}\n"
                f"  • Estimated unfilled size: {sell_plan['estimated_unfilled_size']:.6f}"
            ),
            data={
                "market_id": position.market_id,
                "token_id": position.token_id,
                "reason": reason,
                "roi_pct": roi_pct,
                "cancelled_order_ids": cancelled_order_ids,
                "estimated_unfilled_size": sell_plan["estimated_unfilled_size"],
                "response": response,
            },
        )
        return {
            "market_id": position.market_id,
            "reason": reason,
            "roi_pct": roi_pct,
            "cancelled_order_ids": cancelled_order_ids,
            "response": response,
            "sell_plan": sell_plan,
        }

    def monitor_existing_risk(self) -> Dict[str, Any]:
        """Use seren-cron runs to reconcile live orders/positions and act on guardrails."""
        summary: Dict[str, Any] = {
            "stale_orders_cancelled": 0,
            "guard_exits": [],
            "alerts": 0,
        }
        if self.dry_run:
            return summary

        from polymarket_live import cancel_stale_orders

        prior_order_timestamps = deepcopy(self.runtime_state.get("order_timestamps", {}))
        open_orders = []
        try:
            open_orders = self.polymarket.get_open_orders()
        except Exception as exc:
            self.logger.notify_api_error(f"Failed to load open orders for monitoring: {exc}")

        current_order_timestamps = self._extract_order_timestamps(open_orders, prior_order_timestamps)
        self._open_order_exposure = self._extract_open_order_exposure(open_orders)

        if current_order_timestamps:
            stale_cleanup = cancel_stale_orders(
                trader=self.polymarket._require_trader(),
                prior_order_timestamps=current_order_timestamps,
                stale_order_max_age_seconds=self.stale_order_max_age_seconds,
            )
            summary["stale_orders_cancelled"] = int(stale_cleanup.get("stale_count", 0) or 0)
            if summary["stale_orders_cancelled"] > 0:
                self.logger.log_notification(
                    level="warning",
                    title="Stale Orders Cancelled",
                    message=f"Cancelled {summary['stale_orders_cancelled']} stale open order(s) during cron monitoring.",
                    data=stale_cleanup,
                )
                summary["alerts"] += 1
                try:
                    open_orders = self.polymarket.get_open_orders()
                except Exception:
                    open_orders = []

        self.runtime_state["order_timestamps"] = self._extract_order_timestamps(open_orders, current_order_timestamps)
        self._open_order_exposure = self._extract_open_order_exposure(open_orders)

        try:
            self.positions.sync_with_polymarket(self.polymarket)
        except Exception as exc:
            self.logger.notify_api_error(f"Position monitoring sync failed: {exc}")
            return summary

        open_market_ids = {position.market_id for position in self.positions.get_all_positions()}
        self._prune_pending_exit_markets(open_market_ids)

        now = datetime.now(timezone.utc)
        for position in self.positions.get_all_positions():
            if position.quantity <= 0:
                continue

            roi_pct = self._resolve_position_roi(position)

            if abs(roi_pct) >= self.alert_move_pct:
                direction = "gain" if roi_pct >= 0 else "loss"
                alert_key = f"{direction}:{round(abs(roi_pct), 2)}"
                if self._record_position_alert(position.market_id, alert_key):
                    self.logger.log_notification(
                        level="info" if roi_pct >= 0 else "warning",
                        title=f"Position {direction.title()} Alert",
                        message=(
                            f"\"{position.market}\" moved {roi_pct * 100:+.1f}% since entry.\n"
                            f"  • Held side: {position.side}\n"
                            f"  • Entry: {position.entry_price:.4f}\n"
                            f"  • Current: {position.current_price:.4f}"
                        ),
                        data={
                            "market_id": position.market_id,
                            "token_id": position.token_id,
                            "roi_pct": roi_pct,
                            "position_side": position.side,
                        },
                    )
                    summary["alerts"] += 1

            exit_reason = ""
            if self.take_profit_pct > 0 and roi_pct >= self.take_profit_pct:
                exit_reason = "take-profit"
            elif self.position_stop_loss_pct > 0 and roi_pct <= -self.position_stop_loss_pct:
                exit_reason = "stop-loss"
            elif self.max_position_age_hours > 0:
                opened = self._iso_to_datetime(position.opened_at)
                if opened and ((now - opened).total_seconds() / 3600.0) >= self.max_position_age_hours:
                    exit_reason = "max-age"
            if not exit_reason and self.near_resolution_hours > 0 and position.end_date:
                end_dt = self._iso_to_datetime(position.end_date)
                if end_dt is not None:
                    hours_to_resolution = (end_dt - now).total_seconds() / 3600.0
                    if 0 <= hours_to_resolution <= self.near_resolution_hours:
                        exit_reason = "near-resolution"

            if exit_reason and not self._pending_exit_for_market(position.market_id):
                try:
                    summary["guard_exits"].append(
                        self._close_position_via_guard(
                            position,
                            exit_reason,
                            roi_pct,
                            open_orders=open_orders,
                        )
                    )
                except Exception as exc:
                    self.logger.notify_api_error(
                        f"Failed to close \"{position.market}\" via {exit_reason}: {exc}",
                        will_retry=False,
                    )

        self._save_runtime_state()
        return summary

    def scan_markets(self, limit: int = 100) -> List[Dict]:
        """
        Scan Polymarket for active markets with server-side end_date filtering.

        Does NOT rely on Gamma's sort order (tested unreliable — returns
        zero-volume seeded markets above actually-traded ones). Instead fetches
        a large batch and lets rank_candidates do client-side scoring.

        Args:
            limit: Max markets to fetch

        Returns:
            List of market dicts
        """
        try:
            # Server-side date filter: only fetch markets resolving within our window
            from datetime import timedelta
            end_date_min = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            max_end = datetime.now(timezone.utc) + timedelta(days=self.max_resolution_days)
            end_date_max = max_end.strftime("%Y-%m-%dT%H:%M:%SZ")

            print(f"  Fetching up to {limit} active markets (resolving within {self.max_resolution_days}d)...")
            markets = self.polymarket.get_markets(
                limit=limit,
                active=True,
                sort_by="",
                end_date_min=end_date_min,
                end_date_max=end_date_max,
            )
            print(f"  ✓ Retrieved {len(markets)} markets with sufficient liquidity")
            return markets
        except Exception as e:
            print(f"  ⚠️  Market scanning failed: {e}")
            print(f"     Check polymarket-data publisher availability")
            return []

    def rank_candidates(self, markets: List[Dict], limit: int) -> List[Dict]:
        """
        Cheap heuristic ranking to select the best candidates for LLM analysis.

        Ranks by liquidity + volume (Gamma API prices are stale 0.50 seeds).
        After ranking, enriches top candidates with live CLOB midpoint prices.

        Args:
            markets: Full list of fetched markets
            limit: Number of candidates to keep

        Returns:
            Top N markets by heuristic score, enriched with live prices
        """
        import math

        stale_demotion = self.stale_price_demotion

        def _parse_best_price(m):
            """Return YES-outcome price as float in [0,1], or None."""
            op = m.get('outcomePrices', '')
            if not op:
                return None
            try:
                parts = [float(p.strip()) for p in op.split(',')]
                if parts:
                    return parts[0]
            except (ValueError, TypeError):
                pass
            return None

        def _parse_price_asymmetry(m: Dict) -> float:
            """Return abs(p1 - p2) from outcomePrices string, or -1 if unparseable."""
            raw = m.get('outcomePrices', '')
            if not raw:
                return -1.0
            try:
                parts = raw.split(',')
                p1, p2 = float(parts[0]), float(parts[1])
                return abs(p1 - p2)
            except (IndexError, ValueError, TypeError):
                return -1.0

        def _is_stale_gamma(m: Dict) -> bool:
            """True if outcomePrices is the Gamma 0.5/0.5 default seed."""
            asymmetry = _parse_price_asymmetry(m)
            return 0 <= asymmetry < 0.02

        def score(m: Dict) -> float:
            liquidity = float(m.get('liquidity', 0))
            volume = float(m.get('volume', 0))
            liq_score = math.log1p(liquidity)
            vol_score = math.log1p(volume)
            base = liq_score + vol_score * 2

            # Demote markets whose outcomePrices are still at the Gamma 0.5/0.5 default
            if _is_stale_gamma(m):
                return base * stale_demotion

            price = _parse_best_price(m)
            if price is not None:
                if price < 0.05 or price > 0.95:
                    base *= 0.3
                elif 0.15 <= price <= 0.85:
                    base *= 1.5
            return base

        # Hard-filter stale 50/50 Gamma markets before ranking — they waste LLM budget
        stale_gamma_filtered = [m for m in markets if not _is_stale_gamma(m)]
        stale_gamma_pre_filter = len(markets) - len(stale_gamma_filtered)
        if stale_gamma_pre_filter:
            print(f"  Filtered {stale_gamma_pre_filter} stale 50/50 Gamma-seeded markets")

        # Hard-filter zero/low-volume markets — seeded but never traded
        min_vol = self.min_volume
        volume_filtered = [m for m in stale_gamma_filtered if float(m.get('volume', 0)) >= min_vol]
        low_vol_count = len(stale_gamma_filtered) - len(volume_filtered)
        if low_vol_count:
            print(f"  Filtered {low_vol_count} markets with volume < ${min_vol:,.0f}")

        ranked = sorted(volume_filtered, key=score, reverse=True)

        # Filter out markets resolving too far in the future
        now = datetime.now(timezone.utc)
        time_filtered = []
        for m in ranked:
            end_date_str = m.get('end_date', '')
            if not end_date_str:
                continue

            try:
                end_dt = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            days_to_resolution = (end_dt - now).days
            if days_to_resolution <= 0:
                continue

            m['days_to_resolution'] = days_to_resolution
            if days_to_resolution > self.max_resolution_days:
                continue  # skip far-out markets
            time_filtered.append(m)

        if len(ranked) - len(time_filtered) > 0:
            print(f"  Filtered {len(ranked) - len(time_filtered)} markets resolving >{self.max_resolution_days} days out")

        # --- Slug-group deduplication: max 3 markets per slug prefix ---
        MAX_PER_SLUG_GROUP = 3
        slug_group_counts = {}
        deduped = []
        slug_skips = 0
        for m in time_filtered:
            slug = m.get('market_slug', m.get('question', ''))
            parts = slug.lower().replace(' ', '-').split('-')
            # Check both head-4 and tail-4 to catch prefix- and suffix-similar slugs
            head_key = 'h:' + ('-'.join(parts[:4]) if len(parts) >= 4 else slug.lower())
            tail_key = 't:' + ('-'.join(parts[-4:]) if len(parts) >= 4 else slug.lower())
            head_count = slug_group_counts.get(head_key, 0)
            tail_count = slug_group_counts.get(tail_key, 0)
            if head_count >= MAX_PER_SLUG_GROUP or tail_count >= MAX_PER_SLUG_GROUP:
                slug_skips += 1
                continue
            slug_group_counts[head_key] = head_count + 1
            slug_group_counts[tail_key] = tail_count + 1
            deduped.append(m)

        if slug_skips > 0:
            print(f"  Deduped {slug_skips} markets exceeding {MAX_PER_SLUG_GROUP}/slug-group cap")

        # --- Event-group deduplication: max 5 markets per sporting/political event ---
        # Polymarket templates one market per team/candidate for multi-outcome events
        # (e.g. 30 "Will X win the NBA Finals?" markets). The slug dedup catches some
        # but misses variants with different slug prefixes. This extracts the event
        # name and caps per event.
        MAX_PER_EVENT_GROUP = 5
        EVENT_PATTERNS = [
            # "Will X win the 2026 NBA Finals?" → "2026 nba finals"
            re.compile(r'win (?:the )?((?:\d{4}[\s\-])?(?:nba|nhl|mlb|nfl|fifa|uefa|f1|afl|mls|wnba|ncaa)[\w\s\-]+(?:finals?|cup|championship|trophy|series|prix|bowl|league|tournament|primary|election|season))', re.IGNORECASE),
            # "X: Points O/U 12.5" → extract sport event from context
            re.compile(r'((?:nba|nhl|mlb|nfl|fifa|uefa|f1|eurovision|oscar|emmy|grammy)[\w\s\-]*(?:finals?|cup|championship|trophy|series|prix|bowl|league|tournament|award|contest|song))', re.IGNORECASE),
            # "Will X be the {party} nominee for {office}" → "{party} nominee {office}"
            re.compile(r'((?:republican|democratic|democrat) nominee (?:for )?[\w\s]+(?:senate|governor|house|president))', re.IGNORECASE),
        ]

        def _extract_event(question: str) -> str:
            """Extract event name from market question, or empty string."""
            for pattern in EVENT_PATTERNS:
                match = pattern.search(question)
                if match:
                    # Normalize: lowercase, collapse whitespace
                    return re.sub(r'\s+', ' ', match.group(1).strip().lower())
            return ''

        event_group_counts: Dict[str, int] = {}
        event_deduped = []
        event_skips = 0
        for m in deduped:
            event = _extract_event(m.get('question', ''))
            if not event:
                event_deduped.append(m)
                continue
            count = event_group_counts.get(event, 0)
            if count >= MAX_PER_EVENT_GROUP:
                event_skips += 1
                continue
            event_group_counts[event] = count + 1
            event_deduped.append(m)

        if event_skips > 0:
            top_events = sorted(event_group_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            print(f"  Deduped {event_skips} template markets exceeding {MAX_PER_EVENT_GROUP}/event cap")
            for ev, ct in top_events:
                print(f"    {ev}: {ct} kept, rest deduped")

        pre_selected = event_deduped[:limit]

        # Enrich with live CLOB midpoint prices. If CLOB is unavailable, keep only
        # markets that already have a non-fallback Gamma price.
        # Also reject markets where Gamma outcomePrices is the stale 0.5/0.5 default
        # unless the CLOB provides a real midpoint.
        enriched = []
        stale_price_skips = 0
        stale_gamma_skips = 0
        for m in pre_selected:
            # Detect stale Gamma 50/50 default prices from outcomePrices field
            stale_gamma_price = False
            outcome_prices_str = m.get('outcomePrices', '')
            if outcome_prices_str:
                try:
                    parts = [float(p.strip()) for p in outcome_prices_str.split(',')]
                    if len(parts) == 2 and all(abs(p - 0.5) <= 0.01 for p in parts):
                        stale_gamma_price = True
                except (ValueError, TypeError):
                    pass

            live_mid = None
            try:
                live_mid = self.polymarket.get_midpoint(m['token_id'])
            except Exception:
                live_mid = None

            if live_mid and 0.01 < live_mid < 0.99:
                # If Gamma seeded this market at 50/50 and the CLOB also returns
                # ~50%, the midpoint is likely derived from a thin/symmetric book
                # on a market that has never traded.  Reject it.
                if stale_gamma_price and abs(live_mid - 0.5) <= 0.03:
                    stale_gamma_skips += 1
                    question = m.get('question', '')[:60]
                    print(f"  Skipping stale 50/50 market (CLOB mid ≈ Gamma seed): {question}")
                    continue
                m['price'] = live_mid
                m['price_source'] = 'clob_midpoint'
                enriched.append(m)
                continue

            # CLOB enrichment failed — check if Gamma price is trustworthy
            if stale_gamma_price:
                stale_gamma_skips += 1
                question = m.get('question', '')[:60]
                print(f"  Skipping stale 50/50 Gamma market (no CLOB): {question}")
                continue

            if m.get('price_source') in ('gamma', 'clob_last_trade', 'clob_book_mid'):
                enriched.append(m)
                continue

            # stale_gamma price_source means all CLOB fallbacks also failed
            if m.get('price_source') == 'stale_gamma':
                stale_gamma_skips += 1
                question = m.get('question', '')[:60]
                print(f"  Skipping stale 50/50 market (all CLOB fallbacks failed): {question}")
                continue

            stale_price_skips += 1
            question = m.get('question', '')[:60]
            print(f"  Skipping stale-priced market: {question}")

        dropped = len(markets) - len(enriched)
        if stale_gamma_skips:
            print(f"  Skipped {stale_gamma_skips} markets with stale 50/50 Gamma prices and no valid CLOB midpoint")
        if stale_price_skips:
            print(f"  Skipped {stale_price_skips} markets with fallback 50% prices and no valid CLOB midpoint")
        print(f"  Ranked {len(markets)} markets → kept top {len(enriched)} candidates (dropped {dropped})")
        return enriched

    def research_opportunity(self, market_question: str) -> str:
        """
        Research a market using Perplexity

        Args:
            market_question: Market question to research

        Returns:
            Research summary
        """
        print(f"  🧠 Researching: \"{market_question}\"")

        try:
            research = self.seren.research_market(market_question)
            return research
        except Exception as e:
            print(f"    ⚠️  Research failed: {e}")
            return ""

    def estimate_fair_value(
        self,
        market_question: str,
        current_price: float,
        research: str
    ) -> tuple[Optional[float], Optional[str]]:
        """
        Estimate fair value using Claude

        Args:
            market_question: Market question
            current_price: Current market price (0.0-1.0)
            research: Research summary

        Returns:
            (fair_value, confidence) or (None, None) if failed
        """
        print(f"  💡 Estimating fair value...")

        try:
            fair_value, confidence = self.seren.estimate_fair_value(
                market_question,
                current_price,
                research
            )

            print(f"     Fair value: {fair_value * 100:.1f}% (confidence: {confidence})")
            return fair_value, confidence

        except Exception as e:
            print(f"    ⚠️  Fair value estimation failed: {e}")
            return None, None

    def evaluate_opportunity(
        self,
        market: Dict,
        research: str,
        fair_value: float,
        confidence: str
    ) -> Optional[Dict]:
        """
        Evaluate if a market presents a trading opportunity

        Args:
            market: Market data dict
            research: Research summary
            fair_value: Estimated fair value (0.0-1.0)
            confidence: Confidence level ('low'|'medium'|'high')

        Returns:
            Trade recommendation dict or None if no opportunity
        """
        current_price = market['price']

        # --- Fix 3: Min buy price floor ---
        # Markets priced below min_buy_price should not generate BUY signals.
        # At these prices the CLOB book is typically one-sided or empty, slippage
        # consumes most of the theoretical edge, and the market is likely resolved,
        # abandoned, or data-broken.
        if current_price < self.min_buy_price and fair_value > current_price:
            print(f"    ✗ BLOCKED: market price {current_price*100:.1f}% below {self.min_buy_price*100:.0f}% buy floor")
            return None

        # --- Fix 2: Extreme-divergence sanity gate ---
        # A >max_divergence gap between AI estimate and market price almost always
        # means the AI misunderstood the question, the market data is stale/broken,
        # or the market is too illiquid for the price to be meaningful.
        divergence = abs(fair_value - current_price)
        if divergence > self.max_divergence:
            print(f"    ✗ BLOCKED: {divergence*100:.0f}pp divergence exceeds {self.max_divergence*100:.0f}pp sanity limit")
            print(f"      AI fair value: {fair_value*100:.1f}% vs market: {current_price*100:.1f}%")
            print(f"      This likely indicates a data issue or model misunderstanding, not real edge")
            return None

        # Calculate edge
        edge = kelly.calculate_edge(fair_value, current_price)

        # Check if edge exceeds threshold (uses calibrated threshold if available)
        if edge < self.effective_mispricing_threshold:
            print(f"    ✗ Edge {edge * 100:.1f}% below threshold {self.effective_mispricing_threshold * 100:.1f}%")
            return None

        # Annualized return gate: edge must justify the lockup period
        days_to_resolution = market.get('days_to_resolution', 0)
        if days_to_resolution <= 0:
            print(f"    ✗ Missing or invalid resolution date; cannot annualize return")
            return None

        years_to_resolution = days_to_resolution / 365.0
        annualized_return = kelly.calculate_annualized_return(edge, years_to_resolution)
        if annualized_return < self.min_annualized_return:
            print(f"    ✗ Annualized return {annualized_return * 100:.1f}% below {self.min_annualized_return * 100:.0f}% hurdle ({days_to_resolution}d to resolution)")
            return None

        # --- Execution quality gates: spread, depth, and exit liquidity ---
        book_metrics = None
        try:
            token_to_check = market.get('token_id')
            if token_to_check:
                book_metrics = self.polymarket.get_book_metrics(token_to_check)

                # Exit liquidity: must have bids
                if book_metrics['best_bid'] <= 0:
                    print(f"    ✗ No exit liquidity: zero bids on order book")
                    return None

                # Edge-to-spread ratio gate: edge must exceed spread cost by min_edge_to_spread_ratio
                spread = book_metrics['spread']
                if spread > 0:
                    edge_to_spread = edge / spread
                    if edge_to_spread < self.min_edge_to_spread_ratio:
                        print(f"    ✗ BLOCKED: edge/spread ratio {edge_to_spread:.1f}x below {self.min_edge_to_spread_ratio:.1f}x minimum")
                        print(f"      Edge: {edge*100:.1f}%, spread: {spread*100:.1f}% — spread eats the edge")
                        return None
        except Exception:
            print(f"    ✗ Could not verify exit liquidity or book metrics")
            return None

        # Reject low confidence estimates
        if confidence == 'low':
            print(f"    ✗ Confidence too low: {confidence}")
            return None

        # Check if we already have a position
        if self.positions.has_exposure(
            market['market_id'],
            token_id=market.get('token_id', ''),
            no_token_id=market.get('no_token_id', ''),
        ):
            print(f"    ✗ Already have position in this market")
            return None

        if self._has_open_order_exposure(market):
            print(f"    ✗ Already have live resting-order exposure in this market")
            return None

        if self.max_positions_per_event > 0 and market.get('event_id'):
            existing_in_event = [
                pos for pos in self.positions.get_all_positions()
                if getattr(pos, 'event_id', '') == market['event_id']
            ]
            open_order_event_count = int(
                (getattr(self, "_open_order_exposure", {}) or {})
                .get("event_counts", {})
                .get(market['event_id'], 0)
            )
            if len(existing_in_event) + open_order_event_count >= self.max_positions_per_event:
                print(
                    f"    ✗ Event exposure cap reached "
                    f"({len(existing_in_event) + open_order_event_count}/{self.max_positions_per_event})"
                )
                return None

        # Check if we're at max positions
        if len(self.positions.get_all_positions()) >= self.max_positions:
            print(f"    ✗ At max positions ({self.max_positions})")
            return None

        # Calculate current bankroll
        current_bankroll = self._safe_float(
            self.positions.get_current_bankroll(self.bankroll),
            self.bankroll,
        )

        # Check stop loss
        if current_bankroll <= self.stop_loss_bankroll:
            print(f"    ✗ Bankroll below stop loss (${current_bankroll:.2f} <= ${self.stop_loss_bankroll:.2f})")
            return None

        # Calculate position size
        available = self.positions.get_available_capital(self.bankroll)
        position_size, side = kelly.calculate_position_size(
            fair_value,
            current_price,
            available,
            self.max_kelly_fraction
        )

        if position_size == 0:
            print(f"    ✗ Position size too small")
            return None

        # Depth-constrained sizing: cap position at max_depth_fraction of visible book
        if book_metrics:
            relevant_depth = book_metrics['ask_depth_usd'] if side == 'BUY' else book_metrics['bid_depth_usd']
            if relevant_depth > 0:
                max_from_depth = relevant_depth * self.max_depth_fraction
                if position_size > max_from_depth:
                    print(f"    Depth cap: ${position_size:.2f} → ${max_from_depth:.2f} "
                          f"({self.max_depth_fraction*100:.0f}% of ${relevant_depth:.0f} visible depth)")
                    position_size = max_from_depth
                    if position_size < 1.0:
                        print(f"    ✗ Position too small after depth cap")
                        return None

        # Calculate expected value
        ev = kelly.calculate_expected_value(fair_value, current_price, position_size, side)

        print(f"    ✓ Opportunity found!")
        print(f"      Edge: {edge * 100:.1f}%")
        print(f"      Side: {side}")
        print(f"      Size: ${position_size:.2f} ({(position_size / available) * 100:.1f}% of available)")
        print(f"      Expected value: ${ev:+.2f}")

        return {
            'market': market,
            'fair_value': fair_value,
            'confidence': confidence,
            'edge': edge,
            'side': side,
            'position_size': position_size,
            'expected_value': ev
        }

    def execute_trade(self, opportunity: Dict) -> bool:
        """
        Execute a trade

        Args:
            opportunity: Trade opportunity dict

        Returns:
            True if trade executed successfully
        """
        market = opportunity['market']
        side = opportunity['side']
        size = opportunity['position_size']
        price = market['price']

        if self.dry_run:
            print(f"    [DRY-RUN] Would place {side} order:")
            print(f"      Market: \"{market['question']}\"")
            print(f"      Size: ${size:.2f}")
            print(f"      Price: {price * 100:.1f}%")
            print(f"      Expected value: ${opportunity['expected_value']:+.2f}")
            print()

            # Log the trade
            self.logger.log_trade(
                market=market['question'],
                market_id=market['market_id'],
                side=side,
                size=size,
                price=price,
                fair_value=opportunity['fair_value'],
                edge=opportunity['edge'],
                status='dry_run'
            )

            return True

        # Execute actual trade
        # On Polymarket CLOB, "SELL" means betting against the outcome.
        # This is done by BUYing the NO token at the live ask price.
        if side == 'SELL' and market.get('no_token_id'):
            exec_token_id = market['no_token_id']
            exec_side = 'BUY'
            position_side = 'NO'
            try:
                no_ask_price = self.polymarket.get_price(exec_token_id, 'BUY')
                exec_price = no_ask_price if no_ask_price and no_ask_price > 0 else 1.0 - price
            except Exception:
                exec_price = 1.0 - price
            print(f"    📊 Placing BUY NO order @ {exec_price:.4f} (betting against YES @ {price*100:.1f}%)...")
        else:
            exec_token_id = market['token_id']
            exec_side = side
            exec_price = price
            position_side = 'YES'
            print(f"    📊 Placing {side} order @ {exec_price:.4f}...")

        try:
            order = self.polymarket.place_order(
                token_id=exec_token_id,
                side=exec_side,
                size=size,
                price=exec_price
            )

            order_id = str(order.get('orderID') or order.get('id') or order.get('order_id') or 'unknown')
            print(f"    ✓ Order placed: {order_id}")
            if order_id != 'unknown':
                self.runtime_state.setdefault("order_timestamps", {})[order_id] = datetime.now(timezone.utc).isoformat()
                self._save_runtime_state()

            # Add position to tracker
            self.positions.add_position(
                market=market['question'],
                market_id=market['market_id'],
                token_id=exec_token_id,
                side=position_side,
                thesis_side=side,
                entry_price=exec_price,
                size=size,
                quantity=(size / exec_price) if exec_price > 0 else None,
                event_id=market.get('event_id', ''),
                end_date=market.get('end_date', ''),
            )

            # Log the trade
            self.logger.log_trade(
                market=market['question'],
                market_id=market['market_id'],
                side=side,
                size=size,
                price=price,
                fair_value=opportunity['fair_value'],
                edge=opportunity['edge'],
                status='open'
            )

            return True

        except Exception as e:
            print(f"    ✗ Trade failed: {e}")
            self.logger.notify_api_error(str(e))
            return False

    def run_scan_cycle(self):
        """Run a single scan cycle"""
        print("=" * 60)
        print(f"🔍 Polymarket Scan Starting - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print("=" * 60)
        print()

        # Check balances
        balances = self.check_balances()
        self._last_serenbucks_balance = balances['serenbucks']
        print(f"Balances:")
        print(f"  SerenBucks: ${balances['serenbucks']:.2f}")
        print(f"  Polymarket: ${balances['polymarket']:.2f}")
        print()

        print("Monitoring live positions and orders...")
        monitor_summary = self.monitor_existing_risk()
        if (
            monitor_summary.get("stale_orders_cancelled")
            or monitor_summary.get("guard_exits")
            or monitor_summary.get("alerts")
        ):
            print(
                f"  Cancelled stale orders: {monitor_summary.get('stale_orders_cancelled', 0)} | "
                f"Guard exits: {len(monitor_summary.get('guard_exits', []))} | "
                f"Alerts: {monitor_summary.get('alerts', 0)}"
            )
        else:
            print("  No stale orders, guard exits, or alerts")
        print()

        # Sync positions with Polymarket API
        print("Syncing positions...")
        try:
            sync_result = self.positions.sync_with_polymarket(self.polymarket)
            if sync_result['added'] > 0 or sync_result['removed'] > 0 or sync_result['updated'] > 0:
                print(f"  Added: {sync_result['added']}, Updated: {sync_result['updated']}, Removed: {sync_result['removed']}")
            else:
                print(f"  All positions in sync ({len(self.positions.get_all_positions())} open)")
        except Exception as e:
            print(f"  ⚠️  Sync failed: {e}")
        print()

        # ── risk guards ──────────────────────────────────────────
        current_bankroll = self._safe_float(
            self.positions.get_current_bankroll(self.bankroll),
            self.bankroll,
        )
        peak = getattr(self, "_peak_equity", self.bankroll)
        if current_bankroll > peak:
            peak = current_bankroll
        self._peak_equity = peak
        dd_pct = ((peak - current_bankroll) / peak * 100.0) if peak > 0 else 0.0

        live_risk = {"drawdown_pct": dd_pct, "current_equity_usd": current_bankroll, "peak_equity_usd": peak}
        unwind_result = check_drawdown_stop_loss(
            live_risk=live_risk,
            max_drawdown_pct=getattr(self, "max_drawdown_pct", 0.0),
            unwind_fn=lambda: {"action": "unwind_triggered", "positions_closed": len(self.positions.get_all_positions())},
        )
        if unwind_result:
            if getattr(self, "cron_job_id", ""):
                try:
                    from setup_cron import pause_job
                    auto_pause_cron(
                        serenbucks_balance=0.0,
                        trading_balance=0.0,
                        min_serenbucks=1.0,
                        job_id=getattr(self, "cron_job_id", ""),
                        pause_fn=pause_job,
                    )
                except Exception:
                    pass
            return 0

        # check position age
        all_positions = self.positions.get_all_positions()
        for pos in all_positions:
            if not hasattr(pos, "opened_at") or not pos.opened_at:
                continue
            try:
                opened = datetime.fromisoformat(pos.opened_at.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600.0
                max_age_hours = getattr(self, "max_position_age_hours", 0.0)
                if age_hours >= max_age_hours and max_age_hours > 0:
                    print(f"    POSITION AGE LIMIT: {pos.market} open {age_hours:.1f}h >= {max_age_hours}h limit. Flagged for close.")
            except (ValueError, TypeError):
                continue

        # Check for low balances
        if balances['serenbucks'] < 5.0:
            self.logger.notify_low_balance('serenbucks', balances['serenbucks'], 20.0)

        # Stage 1: Broad fetch
        print("Scanning markets...")
        markets = self.scan_markets(limit=self.scan_limit)
        print(f"  Fetched: {len(markets)} markets")
        print()

        if not markets:
            print("⚠️  No markets found - check polymarket-data publisher availability")
            print()
            self.logger.log_scan_result(
                dry_run=self.dry_run,
                markets_scanned=0,
                opportunities_found=0,
                trades_executed=0,
                capital_deployed=0.0,
                api_cost=0.0,
                serenbucks_balance=balances['serenbucks'],
                polymarket_balance=balances['polymarket'],
                errors=['No markets returned from polymarket-data']
            )
            return 0

        # Stage 2: Cheap heuristic ranking — no LLM
        print("Ranking candidates (no LLM)...")
        candidates = self.rank_candidates(markets, limit=self.candidate_limit)
        analyze_batch = candidates[:self.analyze_limit]
        print(f"  Candidates: {len(candidates)}, will analyze: {len(analyze_batch)}")
        print()

        # Stage 3: Deep LLM analysis
        opportunities = []
        for market in analyze_batch:
            print(f"Evaluating: \"{market['question']}\"")
            print(f"  Current price: {market['price'] * 100:.1f}%")
            print(f"  Liquidity: ${market['liquidity']:.2f}")

            research = self.research_opportunity(market['question'])
            if not research:
                continue

            fair_value, confidence = self.estimate_fair_value(
                market['question'],
                market['price'],
                research
            )
            if not fair_value:
                continue

            # Save prediction for calibration tracking
            if self.storage:
                try:
                    self.storage.save_prediction({
                        'market_id': market['market_id'],
                        'market_question': market['question'],
                        'predicted_fair_value': fair_value,
                        'market_price_at_prediction': market['price'],
                        'edge_calculated': abs(fair_value - market['price']),
                        'prediction_timestamp': datetime.now(timezone.utc).isoformat(),
                        'confidence': confidence,
                    })
                except Exception:
                    pass  # Non-blocking

            opp = self.evaluate_opportunity(market, research, fair_value, confidence)
            if opp:
                opportunities.append(opp)

            print()

        print(f"📊 Found {len(opportunities)} opportunities")
        print()

        # Execute trades
        trades_executed = 0
        capital_deployed = 0.0

        for opp in opportunities:
            if self.execute_trade(opp):
                trades_executed += 1
                capital_deployed += opp['position_size']

        api_cost = len(analyze_batch) * 0.05  # ~$0.05 per market (research + estimate)
        self.logger.log_scan_result(
            dry_run=self.dry_run,
            markets_scanned=len(markets),
            opportunities_found=len(opportunities),
            trades_executed=trades_executed,
            capital_deployed=capital_deployed,
            api_cost=api_cost,
            serenbucks_balance=balances['serenbucks'],
            polymarket_balance=balances['polymarket']
        )

        print("=" * 60)
        print("Scan complete!")
        print(f"  Fetched:    {len(markets)} markets")
        print(f"  Candidates: {len(candidates)} (after heuristic ranking)")
        print(f"  Analyzed:   {len(analyze_batch)} (LLM research + fair value)")
        print(f"  Opportunities: {len(opportunities)}")
        print(f"  Trades executed: {trades_executed}")
        print(f"  Capital deployed: ${capital_deployed:.2f}")
        print(f"  Estimated API cost: ~${api_cost:.2f} SerenBucks")
        print("=" * 60)

        # Structured trade summary for LLM agents (issue #296)
        print_trade_summary(opportunities, capital_deployed=capital_deployed)

        # Non-blocking post-scan calibration
        try:
            cal = calibration.run_post_scan_calibration(
                self.polymarket, self.storage, self.mispricing_threshold
            )
            if cal:
                self._calibration = cal
                self.effective_mispricing_threshold, self._threshold_reason = (
                    calibration.effective_threshold(self.mispricing_threshold, cal)
                )
        except Exception as e:
            print(f"  Calibration error (non-blocking): {e}")

        print()

        return len(opportunities)

    def run_monitor_cycle(self) -> Dict[str, Any]:
        """Run only the cron-backed monitoring pass."""
        print("=" * 60)
        print(f"👁️  Polymarket Monitor Starting - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print("=" * 60)
        print()

        balances = self.check_balances()
        self._last_serenbucks_balance = balances['serenbucks']
        print("Balances:")
        print(f"  SerenBucks: ${balances['serenbucks']:.2f}")
        print(f"  Polymarket: ${balances['polymarket']:.2f}")
        print()

        summary = self.monitor_existing_risk()
        print(
            "Monitor complete:"
            f" stale_orders_cancelled={summary.get('stale_orders_cancelled', 0)}"
            f" guard_exits={len(summary.get('guard_exits', []))}"
            f" alerts={summary.get('alerts', 0)}"
        )
        return summary


def print_trade_summary(opportunities, *, capital_deployed=0.0, file=None):
    """Print a structured, grep-able trade summary block.

    Emits a contiguous block delimited by marker lines so any LLM agent
    can extract the complete trade table without partial-read errors.
    """
    if not opportunities:
        return

    import sys as _sys
    out = file or _sys.stdout

    total_ev = sum(o['expected_value'] for o in opportunities)

    print("=== DRY-RUN TRADE SUMMARY ===", file=out)
    print("| # | Market | Side | Price | FV | Edge | Size | EV |", file=out)
    print("|---|--------|------|-------|----|------|------|----|", file=out)
    for i, opp in enumerate(opportunities, 1):
        m = opp['market']
        print(
            f"| {i} "
            f"| {m['question']} "
            f"| {opp['side']} "
            f"| {m['price'] * 100:.1f}% "
            f"| {opp['fair_value'] * 100:.1f}% "
            f"| {opp['edge'] * 100:.1f}% "
            f"| ${opp['position_size']:.2f} "
            f"| {'+' if opp['expected_value'] >= 0 else '-'}${abs(opp['expected_value']):.2f} |",
            file=out,
        )
    print(
        f"TOTAL_DEPLOYED: ${capital_deployed:.2f} | TOTAL_EV: {'+' if total_ev >= 0 else '-'}${abs(total_ev):.2f}",
        file=out,
    )
    print("=== END TRADE SUMMARY ===", file=out)


def _bootstrap_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.exists():
        return path

    example_path = path.with_name("config.example.json")
    if example_path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")

    return path


def main():
    """Main entry point"""
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed != parsed:
            return default
        return parsed

    parser = argparse.ArgumentParser(description='Polymarket Trading Agent')
    parser.add_argument(
        '--config',
        required=True,
        help='Path to config.json'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry-run mode (no actual trades)'
    )
    parser.add_argument(
        '--yes-live',
        action='store_true',
        help='Explicit startup-only opt-in for live trading.'
    )
    parser.add_argument(
        '--run-type',
        choices=('scan', 'monitor'),
        default='scan',
        help='Cron execution mode: full scan or monitor-only pass.',
    )

    args = parser.parse_args()

    config_path = _bootstrap_config_path(args.config)

    # Check config exists
    if not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)

    if not args.dry_run and not args.yes_live:
        print(
            "Error: live trading requires --yes-live. "
            "Use --dry-run for paper mode or pass --yes-live for a startup-only live opt-in."
        )
        sys.exit(1)

    # Initialize agent
    try:
        agent = TradingAgent(str(config_path), dry_run=args.dry_run)
    except Exception as e:
        print(f"Error initializing agent: {e}")
        sys.exit(1)

    # Run iterative scan cycle
    try:
        iter_cfg = agent.config.get("iteration", {})
        max_iterations = int(iter_cfg.get("max_iterations", 15))
        threshold_step = float(iter_cfg.get("threshold_step", 0.01))
        min_threshold_floor = float(iter_cfg.get("min_threshold_floor", 0.02))
        annualized_return_step = float(iter_cfg.get("annualized_return_step", 0.05))
        annualized_return_floor = float(iter_cfg.get("annualized_return_floor", 0.0))
        low_balance_threshold = float(iter_cfg.get("low_balance_threshold", 1.50))

        # Save original parameters so we can report cumulative deltas
        original_mispricing_threshold = agent.mispricing_threshold
        original_scan_limit = agent.scan_limit
        original_min_annualized_return = agent.min_annualized_return

        total_opportunities = 0
        effective_threshold = _coerce_float(
            getattr(
                agent,
                "effective_mispricing_threshold",
                getattr(agent, "mispricing_threshold", 0.0),
            ),
            _coerce_float(getattr(agent, "mispricing_threshold", 0.0), 0.0),
        )

        for iteration in range(1, max_iterations + 1):
            print(f"\n>>> Iteration {iteration}/{max_iterations}  "
                  f"effective_threshold={effective_threshold:.4f}  "
                  f"scan_limit={agent.scan_limit}  "
                  f"min_annualized_return={agent.min_annualized_return:.4f}")

            if args.run_type == 'monitor':
                monitor_result = agent.run_monitor_cycle()
                opportunities_found = len(monitor_result.get("guard_exits", []))
            else:
                opportunities_found = agent.run_scan_cycle()
            total_opportunities += (opportunities_found or 0)

            print(f"<<< Iteration {iteration} result: {opportunities_found or 0} opportunities found")

            # Early-exit: stop iterating once we find opportunities
            if args.run_type == 'scan' and (opportunities_found or 0) > 0:
                print(f"    Found {opportunities_found} opportunities — stopping iteration loop.")
                break
            if args.run_type == 'monitor':
                break

            # Check SerenBucks balance from the last scan cycle
            serenbucks_balance = getattr(agent, '_last_serenbucks_balance', None)
            if serenbucks_balance is not None and serenbucks_balance < low_balance_threshold:
                print(f"    SerenBucks balance ${serenbucks_balance:.2f} < ${low_balance_threshold:.2f} — stopping iteration loop.")
                break

            # Progressively relax parameters based on iteration band
            if iteration <= 5:
                new_threshold = agent.mispricing_threshold - threshold_step
                agent.mispricing_threshold = max(new_threshold, min_threshold_floor)
                print(f"    Relaxed mispricing_threshold → {agent.mispricing_threshold:.4f}")
            elif iteration <= 10:
                agent.scan_limit += 100
                print(f"    Expanded scan_limit → {agent.scan_limit}")
            else:
                new_annualized = agent.min_annualized_return - annualized_return_step
                agent.min_annualized_return = max(new_annualized, annualized_return_floor)
                print(f"    Relaxed min_annualized_return → {agent.min_annualized_return:.4f}")

        # Cumulative summary
        print()
        print("=" * 60)
        print("Iterative Scan Summary")
        print("=" * 60)
        print(f"  Iterations run:           {iteration}")
        print(f"  Total opportunities:      {total_opportunities}")
        print(f"  mispricing_threshold:     {original_mispricing_threshold:.4f} → {agent.mispricing_threshold:.4f}")
        print(f"  scan_limit:               {original_scan_limit} → {agent.scan_limit}")
        print(f"  min_annualized_return:    {original_min_annualized_return:.4f} → {agent.min_annualized_return:.4f}")
        print("=" * 60)

        # auto-pause cron if funds exhausted
        if hasattr(agent, "cron_job_id") and agent.cron_job_id:
            try:
                from setup_cron import pause_job
                auto_pause_cron(
                    serenbucks_balance=getattr(agent, "_last_serenbucks_balance", None),
                    trading_balance=agent.positions.get_current_bankroll(agent.bankroll) if hasattr(agent, "positions") else None,
                    min_serenbucks=agent.min_serenbucks_balance,
                    job_id=agent.cron_job_id,
                    pause_fn=pause_job,
                )
            except Exception:
                pass

    except KeyboardInterrupt:
        print("\n\nScan interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\nError during scan: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
