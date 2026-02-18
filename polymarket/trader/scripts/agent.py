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
from typing import Dict, List, Optional
from datetime import datetime

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
        # Load config
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.dry_run = dry_run

        # Initialize clients
        print("Initializing Seren client...")
        self.seren = SerenClient()

        print("Initializing Polymarket client...")
        self.polymarket = PolymarketClient(self.seren)

        # Initialize SerenDB storage
        print("Initializing SerenDB storage...")
        self.storage = SerenDBStorage(self.seren)

        # Setup database (creates tables if they don't exist)
        if not self.storage.setup_database():
            print("⚠️  Warning: SerenDB setup failed, falling back to file storage")
            self.storage = None

        # Initialize position tracker and logger with SerenDB
        self.positions = PositionTracker(serendb_storage=self.storage)
        self.logger = TradingLogger(serendb_storage=self.storage)

        # Trading parameters from config
        self.bankroll = float(self.config['bankroll'])
        self.mispricing_threshold = float(self.config['mispricing_threshold'])
        self.max_kelly_fraction = float(self.config['max_kelly_fraction'])
        self.max_positions = int(self.config['max_positions'])
        self.stop_loss_bankroll = float(self.config.get('stop_loss_bankroll', 0.0))

        # Scan pipeline limits (configurable, with backward-compatible defaults)
        self.scan_limit = int(self.config.get('scan_limit', 100))
        self.candidate_limit = int(self.config.get('candidate_limit', 20))
        self.analyze_limit = int(self.config.get('analyze_limit', self.candidate_limit))
        self.min_liquidity = float(self.config.get('min_liquidity', 100.0))

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
            print(f"     This may indicate polymarket-data publisher is unavailable")
            return []

    def rank_candidates(self, markets: List[Dict], limit: int) -> List[Dict]:
        """
        Cheap heuristic ranking to select the best candidates for LLM analysis.

        No API calls. Ranks by a composite score of:
        - Liquidity (higher = better)
        - Price proximity to 50% (closer to 50% = more uncertain = more edge potential)
        - Volume (if available)

        Args:
            markets: Full list of fetched markets
            limit: Number of candidates to keep

        Returns:
            Top N markets by heuristic score
        """
        def score(m: Dict) -> float:
            liquidity = float(m.get('liquidity', 0))
            price = float(m.get('price', 0.5))
            volume = float(m.get('volume', 0))
            # Proximity to 50% — max uncertainty, most edge potential
            uncertainty = 1.0 - abs(price - 0.5) * 2
            # Normalise liquidity contribution (log scale to avoid domination)
            import math
            liq_score = math.log1p(liquidity)
            vol_score = math.log1p(volume)
            return liq_score + vol_score + uncertainty * 2

        ranked = sorted(markets, key=score, reverse=True)
        selected = ranked[:limit]
        dropped = len(markets) - len(selected)
        print(f"  Ranked {len(markets)} markets → kept top {len(selected)} candidates (dropped {dropped})")
        return selected

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
        try:
            print(f"    📊 Placing {side} order...")

            order = self.polymarket.place_order(
                token_id=market['token_id'],
                side=side,
                size=size,
                price=price
            )

            print(f"    ✓ Order placed: {order.get('orderID', 'unknown')}")

            # Add position to tracker
            self.positions.add_position(
                market=market['question'],
                market_id=market['market_id'],
                token_id=market['token_id'],
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
        print(f"🔍 Polymarket Scan Starting - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print("=" * 60)
        print()

        # Check balances
        balances = self.check_balances()
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
            return

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

    args = parser.parse_args()

    # Check config exists
    if not os.path.exists(args.config):
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)

    # Initialize agent
    try:
        agent = TradingAgent(args.config, dry_run=args.dry_run)
    except Exception as e:
        print(f"Error initializing agent: {e}")
        sys.exit(1)

    # Run scan cycle
    try:
        agent.run_scan_cycle()
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
