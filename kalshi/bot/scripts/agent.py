#!/usr/bin/env python3
"""
Kalshi Trading Agent - Autonomous prediction market trader

Three-stage pipeline per scan cycle:
1. Market Discovery: Fetch active Kalshi markets, filter by liquidity/volume
2. Deep Analysis: Research via Perplexity, estimate fair value via Claude
3. Trade Execution: Kelly sizing, edge gating, order placement via Kalshi REST API

Usage:
    python scripts/agent.py --config config.json --dry-run
    python scripts/agent.py --config config.json --yes-live --once
"""

import argparse
import json
import math
import os
import sys

# --- Force unbuffered stdout so piped/background output is visible immediately ---
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
# --- End unbuffered stdout fix ---

from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Ensure scripts directory is on the path
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from seren_client import SerenClient
from kalshi_client import KalshiClient
from position_tracker import PositionTracker
from logger import TradingLogger
import kelly
from risk_guards import check_drawdown, check_position_age, auto_pause_cron


class TradingAgent:
    """Autonomous Kalshi trading agent"""

    def __init__(self, config_path: str, dry_run: bool = False):
        """
        Initialize trading agent.

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
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.dry_run = dry_run

        # Initialize clients
        print("Initializing Seren client...")
        self.seren = SerenClient()

        print("Initializing Kalshi client...")
        self.kalshi = KalshiClient()

        # Initialize position tracker and logger
        self.positions = PositionTracker(
            positions_file=str(self.logs_dir / "positions.json")
        )
        self.logger = TradingLogger(log_dir=str(self.logs_dir))

        # Trading parameters from config
        self.bankroll = float(self.config['bankroll'])
        self.mispricing_threshold = float(self.config['mispricing_threshold'])
        self.max_kelly_fraction = float(self.config['max_kelly_fraction'])
        self.max_positions = int(self.config['max_positions'])

        # Scan pipeline limits
        self.scan_limit = int(self.config.get('scan_limit', 200))
        self.candidate_limit = int(self.config.get('candidate_limit', 80))
        self.analyze_limit = int(self.config.get('analyze_limit', 30))

        # Market selection gates
        self.min_volume = float(self.config.get('min_volume', 5000.0))
        self.min_open_interest = int(self.config.get('min_open_interest', 100))
        self.max_divergence = float(self.config.get('max_divergence', 0.50))
        self.min_buy_price = float(self.config.get('min_buy_price', 0.02))
        self.min_edge_to_spread_ratio = float(self.config.get('min_edge_to_spread_ratio', 3.0))

        # Execution parameters
        exec_cfg = self.config.get('execution', {})
        self.max_drawdown_pct = float(exec_cfg.get('max_drawdown_pct', 15.0))
        self.max_position_age_hours = float(exec_cfg.get('max_position_age_hours', 72.0))
        self.near_resolution_hours = float(exec_cfg.get('near_resolution_hours', 24.0))
        self.min_serenbucks_balance = float(exec_cfg.get('min_serenbucks_balance', 1.0))
        self.auto_pause_on_exhaustion = bool(exec_cfg.get('auto_pause_on_exhaustion', True))
        self.stop_loss_bankroll = float(self.config.get('stop_loss_bankroll', 0.0))

        # Cron parameters
        cron_cfg = self.config.get('cron', {})
        self.cron_job_id = cron_cfg.get('job_id', os.environ.get('SEREN_CRON_JOB_ID', ''))

        print(f"Agent initialized (Dry-run: {dry_run})")
        print(f"  Bankroll: ${self.bankroll:.2f}")
        print(f"  Mispricing threshold: {self.mispricing_threshold * 100:.1f}%")
        print(f"  Max Kelly fraction: {self.max_kelly_fraction * 100:.1f}%")
        print(f"  Max positions: {self.max_positions}")
        print(f"  Pipeline: fetch={self.scan_limit} -> candidates={self.candidate_limit} -> analyze={self.analyze_limit}")
        print()

    def check_balances(self) -> Dict[str, float]:
        """Check SerenBucks and Kalshi balances."""
        serenbucks = 0.0
        try:
            wallet = self.seren.get_wallet_balance()
            serenbucks = float(wallet.get('balance_usd', 0.0))
        except Exception as e:
            print(f"Warning: Failed to fetch SerenBucks balance: {e}")

        kalshi_balance = 0.0
        if self.kalshi.is_authenticated():
            try:
                kalshi_balance = self.kalshi.get_balance_usd()
            except Exception as e:
                print(f"Warning: Failed to fetch Kalshi balance: {e}")

        return {
            'serenbucks': serenbucks,
            'kalshi': kalshi_balance,
        }

    def scan_markets(self) -> List[Dict]:
        """
        Fetch active Kalshi markets.

        Returns:
            List of market dicts with normalized fields
        """
        print(f"  Fetching up to {self.scan_limit} active markets...")

        try:
            all_markets = []
            cursor = None

            while len(all_markets) < self.scan_limit:
                batch_limit = min(200, self.scan_limit - len(all_markets))
                result = self.kalshi.get_markets(
                    limit=batch_limit,
                    cursor=cursor,
                    status='open',
                )
                markets = result.get('markets', [])
                if not markets:
                    break

                all_markets.extend(markets)
                cursor = result.get('cursor')
                if not cursor:
                    break

            print(f"  Retrieved {len(all_markets)} open markets")
            return all_markets

        except Exception as e:
            print(f"  Market scanning failed: {e}")
            return []

    def rank_candidates(self, markets: List[Dict]) -> List[Dict]:
        """
        Heuristic scoring and filtering to select best candidates for LLM analysis.

        Ranks by volume, open interest, and time to resolution.
        Filters out illiquid, extreme-priced, and far-resolution markets.

        Args:
            markets: Raw markets from Kalshi API

        Returns:
            Top N candidate markets
        """
        now = datetime.now(timezone.utc)
        candidates = []

        for m in markets:
            ticker = m.get('ticker', '')
            title = m.get('title', m.get('subtitle', ''))
            event_ticker = m.get('event_ticker', '')

            # Extract price
            yes_price_cents = m.get('yes_price') or m.get('last_price', 0)
            if not yes_price_cents:
                continue
            yes_price = int(yes_price_cents) / 100.0

            # Filter extreme prices (too certain or too unlikely)
            if yes_price < self.min_buy_price or yes_price > (1.0 - self.min_buy_price):
                continue

            # Extract volume and open interest
            volume = float(m.get('volume', m.get('volume_24h', 0)))
            open_interest = int(m.get('open_interest', 0))

            # Filter illiquid markets
            if volume < self.min_volume:
                continue
            if open_interest < self.min_open_interest:
                continue

            # Parse close time / expiration
            close_time_str = (
                m.get('close_time')
                or m.get('expiration_time')
                or m.get('expected_expiration_time', '')
            )
            days_to_resolution = 0
            end_date = ''
            if close_time_str:
                try:
                    close_dt = datetime.fromisoformat(close_time_str.replace('Z', '+00:00'))
                    if close_dt.tzinfo is None:
                        close_dt = close_dt.replace(tzinfo=timezone.utc)
                    days_to_resolution = max(0, (close_dt - now).days)
                    end_date = close_time_str
                except (ValueError, TypeError):
                    pass

            # Skip markets resolving today or > 180 days out
            if days_to_resolution <= 0 or days_to_resolution > 180:
                continue

            # Score: weight volume highly, bonus for open interest, prefer medium prices
            vol_score = math.log1p(volume)
            oi_score = math.log1p(open_interest)
            price_bonus = 1.5 if 0.15 <= yes_price <= 0.85 else 0.5
            time_bonus = 1.0 if 7 <= days_to_resolution <= 90 else 0.7

            score = (vol_score * 2 + oi_score) * price_bonus * time_bonus

            candidates.append({
                'ticker': ticker,
                'event_ticker': event_ticker,
                'title': title,
                'question': m.get('title', m.get('subtitle', ticker)),
                'yes_price': yes_price,
                'yes_price_cents': int(yes_price_cents),
                'volume': volume,
                'open_interest': open_interest,
                'days_to_resolution': days_to_resolution,
                'end_date': end_date,
                'score': score,
                'raw': m,
            })

        # Sort by score descending
        candidates.sort(key=lambda x: x['score'], reverse=True)

        # Deduplicate by event (max 3 per event)
        MAX_PER_EVENT = 3
        event_counts: Dict[str, int] = {}
        deduped = []
        for c in candidates:
            event = c['event_ticker'] or 'unknown'
            count = event_counts.get(event, 0)
            if count >= MAX_PER_EVENT:
                continue
            event_counts[event] = count + 1
            deduped.append(c)

        result = deduped[:self.candidate_limit]
        print(f"  Ranked {len(markets)} -> filtered {len(candidates)} -> top {len(result)} candidates")
        return result

    def research_opportunity(self, market_question: str) -> str:
        """
        Research a market using Perplexity via Seren publisher.

        Args:
            market_question: Market question to research

        Returns:
            Research summary
        """
        print(f"  Researching: \"{market_question[:80]}\"")
        try:
            research = self.seren.research_market(market_question)
            return research
        except Exception as e:
            print(f"    Research failed: {e}")
            return ""

    def estimate_fair_value(
        self,
        market_question: str,
        current_price: float,
        research: str,
    ) -> tuple[Optional[float], Optional[str]]:
        """
        Estimate fair value using Claude via Seren publisher.

        Args:
            market_question: Market question
            current_price: Current YES price as probability
            research: Research summary

        Returns:
            (fair_value, confidence) or (None, None) if failed
        """
        print(f"  Estimating fair value...")
        try:
            fair_value, confidence = self.seren.estimate_fair_value(
                market_question, current_price, research
            )
            print(f"     Fair value: {fair_value * 100:.1f}% (confidence: {confidence})")
            return fair_value, confidence
        except Exception as e:
            print(f"    Fair value estimation failed: {e}")
            return None, None

    def evaluate_opportunity(
        self,
        candidate: Dict,
        fair_value: float,
        confidence: str,
    ) -> Optional[Dict]:
        """
        Multi-gate evaluation: edge threshold, annualized return,
        exit liquidity, spread.

        Args:
            candidate: Candidate market dict
            fair_value: Estimated fair value probability
            confidence: 'low', 'medium', 'high'

        Returns:
            Trade recommendation dict or None
        """
        current_price = candidate['yes_price']
        ticker = candidate['ticker']
        days = candidate['days_to_resolution']

        # Gate 1: Edge threshold
        edge = kelly.calculate_edge(fair_value, current_price)
        if edge < self.mispricing_threshold:
            print(f"    SKIP: edge {edge*100:.1f}% < threshold {self.mispricing_threshold*100:.1f}%")
            return None

        # Gate 2: Extreme divergence (model might be wrong)
        if edge > self.max_divergence:
            print(f"    SKIP: edge {edge*100:.1f}% > max divergence {self.max_divergence*100:.1f}%")
            return None

        # Gate 3: Annualized return check
        ann_return = kelly.calculate_annualized_return(edge, days)
        if ann_return < 0.25:  # 25% annualized minimum
            print(f"    SKIP: annualized return {ann_return*100:.1f}% < 25%")
            return None

        # Gate 4: Low confidence penalty
        if confidence == 'low':
            print(f"    SKIP: low confidence estimate")
            return None

        # Gate 5: Spread check (if authenticated)
        spread = 0.0
        if self.kalshi.is_authenticated() or True:
            try:
                metrics = self.kalshi.get_book_metrics(ticker)
                spread = metrics.get('spread', 1.0)
                # Edge should be significantly larger than spread
                if spread > 0 and edge / spread < self.min_edge_to_spread_ratio:
                    print(f"    SKIP: edge/spread ratio {edge/spread:.1f} < {self.min_edge_to_spread_ratio}")
                    return None
            except Exception:
                # Can't check spread, proceed with caution
                pass

        # Gate 6: Already have position
        if self.positions.has_position(ticker):
            print(f"    SKIP: already have position in {ticker}")
            return None

        # Gate 7: Max positions
        if len(self.positions.positions) >= self.max_positions:
            print(f"    SKIP: at max positions ({self.max_positions})")
            return None

        # Calculate Kelly position size
        size, side = kelly.calculate_position_size(
            fair_value, current_price, self.bankroll, self.max_kelly_fraction
        )

        if size <= 0:
            print(f"    SKIP: Kelly size is zero")
            return None

        # Convert to contract count
        if side == 'yes':
            cost_per_contract = current_price
        else:
            cost_per_contract = 1.0 - current_price
        count = max(1, int(size / cost_per_contract)) if cost_per_contract > 0 else 0

        if count <= 0:
            return None

        # Price in cents for the order
        if side == 'yes':
            price_cents = kelly.probability_to_cents(current_price)
        else:
            price_cents = kelly.probability_to_cents(1.0 - current_price)

        ev = kelly.calculate_expected_value(fair_value, current_price, size, side)

        recommendation = {
            'ticker': ticker,
            'event_ticker': candidate['event_ticker'],
            'question': candidate['question'],
            'side': side,
            'action': 'buy',
            'count': count,
            'price_cents': price_cents,
            'fair_value': fair_value,
            'market_price': current_price,
            'edge': edge,
            'annualized_return': ann_return,
            'kelly_size_usd': size,
            'expected_value': ev,
            'confidence': confidence,
            'spread': spread,
            'days_to_resolution': days,
            'end_date': candidate['end_date'],
        }

        print(f"    OPPORTUNITY: {side.upper()} {count} contracts @ {price_cents}c")
        print(f"      Edge: {edge*100:.1f}%, EV: ${ev:.2f}, Size: ${size:.2f}")

        return recommendation

    def execute_trade(
        self,
        recommendation: Dict,
    ) -> Optional[Dict]:
        """
        Place a trade via Kalshi REST API (or log dry-run).

        Args:
            recommendation: Trade recommendation from evaluate_opportunity

        Returns:
            Execution result or None
        """
        ticker = recommendation['ticker']
        side = recommendation['side']
        count = recommendation['count']
        price_cents = recommendation['price_cents']

        if self.dry_run:
            print(f"  [DRY-RUN] Would place: BUY {side.upper()} {count}x {ticker} @ {price_cents}c")

            # Log the dry-run trade
            self.logger.log_trade(
                ticker=ticker,
                market_question=recommendation['question'],
                side=side,
                action='buy',
                count=count,
                price_cents=price_cents,
                fair_value=recommendation['fair_value'],
                edge=recommendation['edge'],
                status='dry-run',
            )

            # Track position even in dry-run
            self.positions.add_position({
                'ticker': ticker,
                'event_ticker': recommendation['event_ticker'],
                'side': side,
                'action': 'buy',
                'entry_price': recommendation['market_price'],
                'count': count,
                'market_question': recommendation['question'],
                'end_date': recommendation['end_date'],
            })

            return {
                'status': 'dry-run',
                'ticker': ticker,
                'side': side,
                'count': count,
                'price_cents': price_cents,
            }

        # Live execution
        if not self.kalshi.is_authenticated():
            print(f"  ERROR: Kalshi credentials not configured for live trading")
            return None

        try:
            result = self.kalshi.create_order(
                ticker=ticker,
                action='buy',
                side=side,
                count=count,
                price_cents=price_cents,
                order_type='limit',
            )

            order_id = result.get('order', {}).get('order_id', result.get('order_id', ''))

            self.logger.log_trade(
                ticker=ticker,
                market_question=recommendation['question'],
                side=side,
                action='buy',
                count=count,
                price_cents=price_cents,
                fair_value=recommendation['fair_value'],
                edge=recommendation['edge'],
                status='open',
                order_id=order_id,
            )

            self.positions.add_position({
                'ticker': ticker,
                'event_ticker': recommendation['event_ticker'],
                'side': side,
                'action': 'buy',
                'entry_price': recommendation['market_price'],
                'count': count,
                'market_question': recommendation['question'],
                'end_date': recommendation['end_date'],
            })

            self.logger.notify_trade_executed(
                ticker=ticker,
                side=side,
                count=count,
                price_cents=price_cents,
                edge=recommendation['edge'],
                position_size=recommendation['kelly_size_usd'],
            )

            print(f"  ORDER PLACED: {order_id}")
            return {
                'status': 'executed',
                'order_id': order_id,
                'ticker': ticker,
                'side': side,
                'count': count,
                'price_cents': price_cents,
                'response': result,
            }

        except Exception as e:
            print(f"  ORDER FAILED: {e}")
            self.logger.notify_api_error(f"Order failed for {ticker}: {e}", will_retry=False)
            return None

    def run_scan(self) -> Dict[str, Any]:
        """
        Orchestrate a full scan cycle: discover -> analyze -> trade.

        Returns:
            Scan results dict (JSON-serializable)
        """
        scan_start = datetime.now(timezone.utc)
        errors: List[str] = []

        print("=" * 60)
        print(f"KALSHI BOT SCAN - {scan_start.isoformat()}")
        print(f"Mode: {'DRY-RUN' if self.dry_run else 'LIVE'}")
        print("=" * 60)

        # Check balances
        print("\n--- Balance Check ---")
        balances = self.check_balances()
        print(f"  SerenBucks: ${balances['serenbucks']:.2f}")
        print(f"  Kalshi: ${balances['kalshi']:.2f}")

        # Auto-pause if low SerenBucks
        if self.auto_pause_on_exhaustion:
            auto_pause_cron(
                balance=balances['serenbucks'],
                min_balance=self.min_serenbucks_balance,
                seren_client=self.seren,
                job_id=self.cron_job_id,
            )

        # Risk check on existing positions
        print("\n--- Risk Check ---")
        existing_positions = self.positions.get_positions()
        if existing_positions:
            dd = check_drawdown(existing_positions, self.bankroll, self.max_drawdown_pct)
            if dd['triggered']:
                print(f"  DRAWDOWN ALERT: {dd['current_drawdown_pct']:.1f}% >= {dd['max_drawdown_pct']:.1f}%")
                errors.append(f"Drawdown triggered: {dd['current_drawdown_pct']:.1f}%")

            aged = check_position_age(existing_positions, self.max_position_age_hours)
            if aged:
                print(f"  STALE POSITIONS: {aged}")

            print(f"  Open positions: {len(existing_positions)}")
            print(f"  Unrealized PnL: ${self.positions.get_total_pnl():.2f}")
        else:
            print(f"  No open positions")

        # Stage 1: Market Discovery
        print("\n--- Stage 1: Market Discovery ---")
        raw_markets = self.scan_markets()
        if not raw_markets:
            print("  No markets found. Aborting scan.")
            scan_result = self._build_scan_result(
                scan_start, 0, 0, 0, 0, 0.0, balances, errors
            )
            self._output_result(scan_result)
            return scan_result

        candidates = self.rank_candidates(raw_markets)
        if not candidates:
            print("  No viable candidates after ranking. Aborting scan.")
            scan_result = self._build_scan_result(
                scan_start, len(raw_markets), 0, 0, 0, 0.0, balances, errors
            )
            self._output_result(scan_result)
            return scan_result

        # Stage 2: Deep Analysis
        print("\n--- Stage 2: Deep Analysis ---")
        opportunities = []
        analyzed = 0

        for candidate in candidates[:self.analyze_limit]:
            analyzed += 1
            print(f"\n[{analyzed}/{min(len(candidates), self.analyze_limit)}] {candidate['ticker']}")
            print(f"  {candidate['question'][:80]}")
            print(f"  YES: {candidate['yes_price']*100:.0f}% | Vol: {candidate['volume']:,.0f} | OI: {candidate['open_interest']} | {candidate['days_to_resolution']}d")

            # Research
            research = self.research_opportunity(candidate['question'])
            if not research:
                errors.append(f"Research failed: {candidate['ticker']}")
                continue

            # Estimate fair value
            fair_value, confidence = self.estimate_fair_value(
                candidate['question'],
                candidate['yes_price'],
                research,
            )
            if fair_value is None:
                errors.append(f"Fair value failed: {candidate['ticker']}")
                continue

            # Evaluate
            recommendation = self.evaluate_opportunity(candidate, fair_value, confidence)
            if recommendation:
                opportunities.append(recommendation)

        print(f"\n  Analyzed {analyzed} markets, found {len(opportunities)} opportunities")

        # Stage 3: Trade Execution
        print("\n--- Stage 3: Trade Execution ---")
        trades_executed = 0
        total_deployed = 0.0

        for opp in opportunities:
            result = self.execute_trade(opp)
            if result:
                trades_executed += 1
                total_deployed += opp['kelly_size_usd']

        # Build and output results
        scan_result = self._build_scan_result(
            scan_start,
            len(raw_markets),
            analyzed,
            len(opportunities),
            trades_executed,
            total_deployed,
            balances,
            errors,
            opportunities,
        )

        self.logger.log_scan_result(
            dry_run=self.dry_run,
            markets_scanned=len(raw_markets),
            candidates_analyzed=analyzed,
            opportunities_found=len(opportunities),
            trades_executed=trades_executed,
            capital_deployed=total_deployed,
            serenbucks_balance=balances['serenbucks'],
            kalshi_balance=balances['kalshi'],
            errors=errors,
        )

        self._output_result(scan_result)
        return scan_result

    def _build_scan_result(
        self,
        scan_start: datetime,
        markets_scanned: int,
        candidates_analyzed: int,
        opportunities_found: int,
        trades_executed: int,
        capital_deployed: float,
        balances: Dict[str, float],
        errors: List[str],
        opportunities: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Build the JSON scan result payload."""
        elapsed = (datetime.now(timezone.utc) - scan_start).total_seconds()

        return {
            'scan_timestamp': scan_start.isoformat(),
            'mode': 'dry-run' if self.dry_run else 'live',
            'duration_seconds': round(elapsed, 1),
            'markets_scanned': markets_scanned,
            'candidates_analyzed': candidates_analyzed,
            'opportunities_found': opportunities_found,
            'trades_executed': trades_executed,
            'capital_deployed': round(capital_deployed, 2),
            'balances': balances,
            'positions': self.positions.get_positions(),
            'total_unrealized_pnl': round(self.positions.get_total_pnl(), 4),
            'opportunities': [
                {
                    'ticker': o['ticker'],
                    'question': o['question'],
                    'side': o['side'],
                    'count': o['count'],
                    'price_cents': o['price_cents'],
                    'fair_value': round(o['fair_value'], 4),
                    'market_price': round(o['market_price'], 4),
                    'edge': round(o['edge'], 4),
                    'annualized_return': round(o['annualized_return'], 4),
                    'kelly_size_usd': round(o['kelly_size_usd'], 2),
                    'expected_value': round(o['expected_value'], 2),
                    'confidence': o['confidence'],
                }
                for o in (opportunities or [])
            ],
            'errors': errors,
        }

    def _output_result(self, result: Dict[str, Any]):
        """Print final scan result summary."""
        print("\n" + "=" * 60)
        print("SCAN COMPLETE")
        print("=" * 60)
        print(f"  Markets scanned: {result['markets_scanned']}")
        print(f"  Candidates analyzed: {result['candidates_analyzed']}")
        print(f"  Opportunities found: {result['opportunities_found']}")
        print(f"  Trades executed: {result['trades_executed']}")
        print(f"  Capital deployed: ${result['capital_deployed']:.2f}")
        print(f"  Duration: {result['duration_seconds']:.1f}s")

        if result['opportunities']:
            print(f"\n  Opportunities:")
            for o in result['opportunities']:
                print(f"    {o['side'].upper()} {o['count']}x {o['ticker']} @ {o['price_cents']}c")
                print(f"      Edge: {o['edge']*100:.1f}% | EV: ${o['expected_value']:.2f} | Confidence: {o['confidence']}")

        if result['errors']:
            print(f"\n  Errors ({len(result['errors'])}):")
            for e in result['errors'][:5]:
                print(f"    - {e}")

        # Output JSON to stdout for machine consumption
        print("\n--- JSON OUTPUT ---")
        print(json.dumps(result, indent=2, default=str))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kalshi Trading Agent - Autonomous prediction market trader"
    )
    parser.add_argument(
        "--config", default="config.json",
        help="Path to config.json",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Paper trading mode (default)",
    )
    parser.add_argument(
        "--yes-live", action="store_true",
        help="Enable live trading (overrides --dry-run)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one scan cycle and exit",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    dry_run = True
    if args.yes_live:
        dry_run = False
        print("LIVE TRADING MODE ENABLED")
        print("Real orders will be placed on Kalshi.")
        print()

    agent = TradingAgent(config_path=args.config, dry_run=dry_run)
    result = agent.run_scan()

    # Exit with appropriate code
    if result.get('errors') and not result.get('trades_executed') and not result.get('opportunities_found'):
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
