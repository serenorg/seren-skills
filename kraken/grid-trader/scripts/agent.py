#!/usr/bin/env python3
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
import sys
import time
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
from dotenv import load_dotenv

from seren_client import SerenClient
from grid_manager import GridManager
from position_tracker import PositionTracker
from logger import GridTraderLogger
import pair_selector


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

        # Load config
        self.config = self._load_config(config_path)
        self.is_dry_run = dry_run

        # Initialize clients
        api_key = os.getenv('SEREN_API_KEY')
        if not api_key:
            raise ValueError("SEREN_API_KEY environment variable is required")

        self.seren = SerenClient(api_key=api_key)
        self.logger = GridTraderLogger(logs_dir='logs')

        # Initialize components
        self.grid = None
        self.tracker = None
        self.running = False
        self.active_orders = {}  # order_id -> order_details

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from JSON file"""
        with open(config_path, 'r') as f:
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

        return config

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

    def setup(self):
        """Phase 1: Setup and validate configuration"""
        print("\n============================================================")
        print("KRAKEN GRID TRADER - SETUP")
        print("============================================================\n")

        # Auto-select the best pair from the candidate list (if configured)
        self._select_trading_pair()

        campaign = self.config['campaign_name']
        pair = self.config['trading_pair']
        strategy = self.config['strategy']
        risk = self.config['risk_management']

        print(f"Campaign:        {campaign}")
        print(f"Trading Pair:    {pair}")
        print(f"Bankroll:        ${strategy['bankroll']:,.2f}")
        print(f"Grid Levels:     {strategy['grid_levels']}")
        print(f"Grid Spacing:    {strategy['grid_spacing_percent']}%")
        print(f"Order Size:      {strategy['order_size_percent']}% of bankroll")
        print(f"Price Range:     ${strategy['price_range']['min']:,.0f} - ${strategy['price_range']['max']:,.0f}")
        print(f"Scan Interval:   {strategy['scan_interval_seconds']}s")
        print(f"Stop Loss:       ${risk['stop_loss_bankroll']:,.2f}")

        # Initialize grid manager
        order_size_usd = strategy['bankroll'] * (strategy['order_size_percent'] / 100)
        self.grid = GridManager(
            min_price=strategy['price_range']['min'],
            max_price=strategy['price_range']['max'],
            grid_levels=strategy['grid_levels'],
            spacing_percent=strategy['grid_spacing_percent'],
            order_size_usd=order_size_usd
        )

        # Initialize position tracker
        self.tracker = PositionTracker(initial_bankroll=strategy['bankroll'])

        # Get current price
        print("\nFetching current market data...")
        current_price = self.seren.get_current_price(pair)  # Last trade price

        print(f"Current Price:   ${current_price:,.2f}")

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

        print(f"Simulating {cycles} cycles...")
        print(f"Scan interval: {scan_interval}s\n")

        for cycle in range(cycles):
            print(f"--- Cycle {cycle + 1}/{cycles} ---")

            # Get current price
            current_price = self.seren.get_current_price(pair)
            print(f"Current Price: ${current_price:,.2f}")

            # Get required orders
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

            print()
            time.sleep(2)  # Short delay for readability

        print("✓ Dry run complete!")
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

        print(f"Trading Pair:    {pair}")
        print(f"Scan Interval:   {scan_interval}s")
        print(f"Stop Loss:       ${stop_loss:,.2f}")
        print("\nStarting live trading... (Press Ctrl+C to stop)\n")

        self.running = True

        try:
            while self.running:
                self._trading_cycle()
                time.sleep(scan_interval)

        except KeyboardInterrupt:
            print("\n\nReceived stop signal...")
            self.stop()

    def _trading_cycle(self):
        """Execute one trading cycle"""
        pair = self.config['trading_pair']
        stop_loss = self.config['risk_management']['stop_loss_bankroll']

        try:
            # 1. Get current price
            current_price = self.seren.get_current_price(pair)

            # 2. Update balances
            balance = self.seren.get_balance()
            balance_key = pair_selector.get_balance_key(
                pair, self.config.get('base_balance_key')
            )
            base_balance = float(balance['result'].get(balance_key, 0))
            usd_balance = float(balance['result'].get('ZUSD', 0))
            self.tracker.update_balances(base_balance, usd_balance)

            # 3. Check stop loss
            if self.tracker.should_stop_loss(current_price, stop_loss):
                print(f"\n⚠ STOP LOSS TRIGGERED at ${self.tracker.get_current_value(current_price):,.2f}")
                self.stop()
                return

            # 4. Get open orders from Kraken
            open_orders_response = self.seren.get_open_orders()
            current_open_orders = open_orders_response['result']['open']

            # 5. Find filled orders
            filled_order_ids = self.grid.find_filled_orders(
                self.active_orders,
                current_open_orders
            )

            # 6. Process fills
            for order_id in filled_order_ids:
                self._process_fill(order_id, current_price)

            # 7. Get required orders for current price
            required_orders = self.grid.get_required_orders(current_price)

            # 8. Place new orders
            self._place_grid_orders(required_orders, current_open_orders)

            # 9. Log position update
            self.logger.log_position_update(
                pair=pair,
                btc_balance=base_balance,
                usd_balance=usd_balance,
                total_value_usd=self.tracker.get_current_value(current_price),
                unrealized_pnl=self.tracker.get_unrealized_pnl(current_price),
                open_orders=len(self.active_orders)
            )

            # 10. Print status
            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            print(f"[{timestamp}] Price: ${current_price:,.2f} | "
                  f"Open Orders: {len(self.active_orders)} | "
                  f"Fills: {len(self.tracker.filled_orders)} | "
                  f"P&L: ${self.tracker.get_unrealized_pnl(current_price):,.2f}")

        except Exception as e:
            error_msg = str(e)
            print(f"ERROR in trading cycle: {error_msg}")
            self.logger.log_error(
                operation='trading_cycle',
                error_type=type(e).__name__,
                error_message=error_msg
            )

    def _place_grid_orders(self, required_orders: Dict, current_open_orders: Dict):
        """Place grid orders that aren't already open"""
        pair = self.config['trading_pair']

        # Get currently open order prices
        open_prices = set()
        for order_data in current_open_orders.values():
            descr = order_data['descr']
            price = float(descr['price'])
            open_prices.add(price)

        # Place buy orders
        for order in required_orders['buy']:
            if order['price'] not in open_prices:
                self._place_order(
                    pair=pair,
                    side='buy',
                    price=order['price'],
                    volume=order['volume']
                )

        # Place sell orders
        for order in required_orders['sell']:
            if order['price'] not in open_prices:
                self._place_order(
                    pair=pair,
                    side='sell',
                    price=order['price'],
                    volume=order['volume']
                )

    def _place_order(self, pair: str, side: str, price: float, volume: float):
        """Place a single limit order"""
        base = pair_selector.get_base_symbol(pair)
        try:
            if self.is_dry_run:
                print(f"[DRY RUN] Would place {side} order: {volume:.8f} {base} @ ${price:,.2f}")
                return

            response = self.seren.add_order(
                pair=pair,
                order_type='limit',
                side=side,
                volume=volume,
                price=price
            )

            if 'result' in response and 'txid' in response['result']:
                order_id = response['result']['txid'][0]
                self.active_orders[order_id] = {
                    'side': side,
                    'price': price,
                    'volume': volume
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
                    status='placed'
                )
                print(f"✓ Placed {side} order: {volume:.8f} {base} @ ${price:,.2f} (ID: {order_id})")

        except Exception as e:
            error_msg = str(e)
            print(f"ERROR placing {side} order at ${price:,.2f}: {error_msg}")
            self.logger.log_error(
                operation='place_order',
                error_type=type(e).__name__,
                error_message=error_msg,
                context={'side': side, 'price': price, 'volume': volume}
            )

    def _process_fill(self, order_id: str, current_price: float):
        """Process a filled order"""
        if order_id not in self.active_orders:
            return

        order = self.active_orders[order_id]
        side = order['side']
        price = order['price']
        volume = order['volume']

        # Calculate fee (0.16% maker fee)
        cost = price * volume
        fee = cost * 0.0016

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
            cost=cost
        )

        # Remove from active orders
        del self.active_orders[order_id]

        base = pair_selector.get_base_symbol(self.config['trading_pair'])
        print(f"✓ FILLED {side.upper()}: {volume:.8f} {base} @ ${price:,.2f} (Fee: ${fee:.2f})")

    def status(self):
        """Show current trading status"""
        if self.tracker is None:
            print("ERROR: No active trading session")
            return

        pair = self.config['trading_pair']

        # Get current price
        current_price = self.seren.get_current_price(pair)

        # Print position summary
        print(self.tracker.get_position_summary(current_price))

    def stop(self):
        """Stop trading and cancel all orders"""
        print("\nStopping trading...")

        self.running = False

        if not self.is_dry_run:
            try:
                # Cancel all open orders
                print("Cancelling all open orders...")
                self.seren.cancel_all_orders()
                print("✓ All orders cancelled")

            except Exception as e:
                print(f"ERROR cancelling orders: {e}")

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

    # Initialize agent
    dry_run = (args.command == 'dry-run')
    agent = KrakenGridTrader(config_path=args.config, dry_run=dry_run)

    # Execute command
    if args.command == 'setup':
        agent.setup()
    elif args.command == 'dry-run':
        agent.setup()
        agent.dry_run(cycles=args.cycles)
    elif args.command == 'start':
        agent.setup()
        agent.start()
    elif args.command == 'status':
        agent.status()
    elif args.command == 'stop':
        agent.stop()


if __name__ == '__main__':
    main()
