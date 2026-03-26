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
import sys

# --- Force unbuffered stdout so piped/background output is visible immediately ---
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
# --- End unbuffered stdout fix ---

from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

# Import our modules
from seren_client import SerenClient
from polymarket_client import PolymarketClient
from position_tracker import PositionTracker
from logger import TradingLogger
from serendb_storage import SerenDBStorage
import kelly


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

        # Safety guards (configurable, with backward-compatible defaults)
        self.min_annualized_return = float(self.config.get('min_annualized_return', 0.25))
        self.max_resolution_days = int(self.config.get('max_resolution_days', 180))
        self.min_exit_bid_depth_ratio = float(self.config.get('min_exit_bid_depth_ratio', 0.5))

        # Scan pipeline limits (configurable, with backward-compatible defaults)
        self.scan_limit = int(self.config.get('scan_limit', 100))
        self.candidate_limit = int(self.config.get('candidate_limit', 20))
        self.analyze_limit = int(self.config.get('analyze_limit', self.candidate_limit))
        self.min_liquidity = float(self.config.get('min_liquidity', 100.0))
        self.stale_price_demotion = float(self.config.get('stale_price_demotion', 0.1))

        print(f"✓ Agent initialized (Dry-run: {dry_run})")
        print(f"  Bankroll: ${self.bankroll:.2f}")
        print(f"  Mispricing threshold: {self.mispricing_threshold * 100:.1f}%")
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

    def scan_markets(self, limit: int = 100) -> List[Dict]:
        """
        Scan Polymarket for active markets

        Args:
            limit: Max markets to fetch

        Returns:
            List of market dicts
        """
        try:
            print(f"  Fetching up to {limit} active markets from Polymarket...")
            markets = self.polymarket.get_markets(limit=limit, active=True)
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

        ranked = sorted(stale_gamma_filtered, key=score, reverse=True)

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

        pre_selected = deduped[:limit]

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

        # Calculate edge
        edge = kelly.calculate_edge(fair_value, current_price)

        # Check if edge exceeds threshold
        if edge < self.mispricing_threshold:
            print(f"    ✗ Edge {edge * 100:.1f}% below threshold {self.mispricing_threshold * 100:.1f}%")
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

        # Exit liquidity check: ensure we can sell what we buy
        try:
            token_to_check = market.get('no_token_id') or market.get('token_id')
            if token_to_check:
                bid_price = self.polymarket.get_price(token_to_check, 'SELL')
                if bid_price <= 0:
                    print(f"    ✗ No exit liquidity: zero bids on order book")
                    return None
        except Exception:
            print(f"    ✗ Could not verify exit liquidity")
            return None

        # Reject low confidence estimates
        if confidence == 'low':
            print(f"    ✗ Confidence too low: {confidence}")
            return None

        # Check if we already have a position
        if self.positions.has_position(market['market_id']):
            print(f"    ✗ Already have position in this market")
            return None

        # Check if we're at max positions
        if len(self.positions.get_all_positions()) >= self.max_positions:
            print(f"    ✗ At max positions ({self.max_positions})")
            return None

        # Calculate current bankroll
        current_bankroll = self.positions.get_current_bankroll(self.bankroll)

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
            print(f"    📊 Placing {side} order @ {exec_price:.4f}...")

        try:
            order = self.polymarket.place_order(
                token_id=exec_token_id,
                side=exec_side,
                size=size,
                price=exec_price
            )

            print(f"    ✓ Order placed: {order.get('orderID', 'unknown')}")

            # Add position to tracker
            self.positions.add_position(
                market=market['question'],
                market_id=market['market_id'],
                token_id=exec_token_id,
                side=side,
                entry_price=price,
                size=size
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
        print()

        return len(opportunities)


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
        max_iterations = min(int(iter_cfg.get("max_iterations", 15)), 2)
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

        for iteration in range(1, max_iterations + 1):
            print(f"\n>>> Iteration {iteration}/{max_iterations}  "
                  f"mispricing_threshold={agent.mispricing_threshold:.4f}  "
                  f"scan_limit={agent.scan_limit}  "
                  f"min_annualized_return={agent.min_annualized_return:.4f}")

            opportunities_found = agent.run_scan_cycle()
            total_opportunities += (opportunities_found or 0)

            print(f"<<< Iteration {iteration} result: {opportunities_found or 0} opportunities found")

            # Early-exit: stop iterating once we find opportunities
            if (opportunities_found or 0) > 0:
                print(f"    Found {opportunities_found} opportunities — stopping iteration loop.")
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
