#!/usr/bin/env python3
"""
Coinbase Grid Trading Bot - Automated grid trading on Coinbase Exchange via Seren Gateway

Usage:
    python scripts/agent.py setup   --config config.json
    python scripts/agent.py dry-run --config config.json
    python scripts/agent.py start   --config config.json
    python scripts/agent.py status  --config config.json
    python scripts/agent.py stop    --config config.json
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from dotenv import load_dotenv

from seren_client import SerenClient
from grid_manager import GridManager
from position_tracker import PositionTracker
from logger import GridTraderLogger
from serendb_store import SerenDBStore
import pair_selector


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


class CoinbaseGridTrader:
    """Coinbase Exchange Grid Trading Bot"""

    MAKER_FEE_RATE = 0.0040  # 0.40% for < $10K 30-day volume

    @staticmethod
    def _is_truthy(value: str) -> bool:
        return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}

    def __init__(self, config_path: str, dry_run: bool = False):
        """
        Initialize grid trader

        Args:
            config_path: Path to config JSON file
            dry_run: If True, simulate trades without placing real orders
        """
        load_dotenv()

        self.config = self._load_config(config_path)
        self.is_dry_run = dry_run

        seren_key = os.getenv('SEREN_API_KEY')
        cb_key = os.getenv('CB_ACCESS_KEY')
        cb_secret = os.getenv('CB_ACCESS_SECRET')
        cb_passphrase = os.getenv('CB_ACCESS_PASSPHRASE')

        if not seren_key:
            raise ValueError("SEREN_API_KEY environment variable is required")
        publisher_authenticated_env = os.getenv('SEREN_DESKTOP_PUBLISHER_AUTH')
        publisher_authenticated = (
            self._is_truthy(publisher_authenticated_env)
            if publisher_authenticated_env is not None
            else None
        )

        self.seren = SerenClient(
            seren_api_key=seren_key,
            cb_access_key=cb_key,
            cb_secret=cb_secret,
            cb_passphrase=cb_passphrase,
            publisher_authenticated=publisher_authenticated,
        )
        self.logger = GridTraderLogger(logs_dir='logs')
        self.store: Optional[SerenDBStore] = None
        self.session_id = str(uuid.uuid4())
        self._session_started = False

        self.grid: GridManager = None
        self.tracker: PositionTracker = None
        self.running = False
        self.active_orders: Dict[str, Dict] = {}  # order_id -> {side, price, size}

        try:
            self.store = _build_store_from_env()
            self.store.ensure_schema()
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: SerenDB persistence unavailable: {exc}", file=sys.stderr)
            self.store = None

    def close(self):
        """Close any external resources."""
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
        """Create a persistence session."""
        if self.store is None or self._session_started:
            return

        campaign_name = str(self.config.get("campaign_name", "coinbase-grid-trader"))
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
                },
            ),
        )
        self._session_started = True

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load and validate configuration"""
        with open(config_path, 'r') as f:
            config = json.load(f)

        for field in ['campaign_name', 'trading_pair', 'strategy', 'risk_management']:
            if field not in config:
                raise ValueError(f"Missing required config field: {field}")

        return config

    def _init_grid(self):
        """Initialize GridManager and PositionTracker from config"""
        strategy = self.config['strategy']
        order_size_usd = strategy['bankroll'] * (strategy['order_size_percent'] / 100)

        self.grid = GridManager(
            min_price=strategy['price_range']['min'],
            max_price=strategy['price_range']['max'],
            grid_levels=strategy['grid_levels'],
            spacing_percent=strategy['grid_spacing_percent'],
            order_size_usd=order_size_usd
        )
        self.tracker = PositionTracker(
            initial_bankroll=strategy['bankroll'],
            product_id=self.config['trading_pair']
        )

    def setup(self):
        """Validate configuration and show profit projections"""
        print("\n============================================================")
        print("COINBASE GRID TRADER - SETUP")
        print("============================================================\n")

        product_id = self.config['trading_pair']
        strategy = self.config['strategy']
        risk = self.config['risk_management']

        print(f"Campaign:        {self.config['campaign_name']}")
        print(f"Trading Pair:    {product_id}")
        print(f"Auth Mode:       {self.seren.auth_mode}")
        print(f"Bankroll:        ${strategy['bankroll']:,.2f}")
        print(f"Grid Levels:     {strategy['grid_levels']}")
        print(f"Grid Spacing:    {strategy['grid_spacing_percent']}%")
        print(f"Order Size:      {strategy['order_size_percent']}% of bankroll")
        print(f"Price Range:     ${strategy['price_range']['min']:,.0f} - ${strategy['price_range']['max']:,.0f}")
        print(f"Scan Interval:   {strategy['scan_interval_seconds']}s")
        print(f"Stop Loss:       ${risk['stop_loss_bankroll']:,.2f}")

        # Validate pair exists on Coinbase Exchange
        print("\nValidating trading pair...")
        if not pair_selector.validate_pair(self.seren, product_id):
            print(f"\n✗ ERROR: '{product_id}' is not an active product on Coinbase Exchange")
            print("\nActive USD pairs:")
            for p in pair_selector.get_usd_pairs(self.seren):
                print(f"  {p}")
            sys.exit(1)
        print(f"✓ {product_id} is active on Coinbase Exchange")

        # Initialize grid
        self._init_grid()
        self._ensure_session_started()

        reference_price = self.grid.get_reference_price()
        print(f"\nReference Price: ${reference_price:,.2f} (midpoint of price range)")
        print("  Note: Update price_range in config.json to center the grid on market price.")
        print("  Live ticker requires a publisher update to add GET /products/{id}/ticker.\n")

        # Validate price range coverage
        min_p = strategy['price_range']['min']
        max_p = strategy['price_range']['max']
        tolerance = 0.05
        if reference_price < min_p * (1 - tolerance):
            print(f"⚠️  WARNING: Reference price below configured range → all sells, no buys")
        elif reference_price > max_p * (1 + tolerance):
            print(f"⚠️  WARNING: Reference price above configured range → all buys, no sells")

        # Profit projection
        expected = self.grid.calculate_expected_profit(
            fills_per_day=15,
            bankroll=strategy['bankroll']
        )
        print("Expected Performance (15 fills/day, 0.40% maker fees):")
        print(f"  Avg Grid Spacing:    ${expected['avg_spacing_usd']:,.2f}")
        print(f"  Gross Profit/Cycle:  ${expected['gross_profit_per_cycle']:.4f}")
        print(f"  Fees/Cycle:          ${expected['fees_per_cycle']:.4f}")
        print(f"  Net Profit/Cycle:    ${expected['net_profit_per_cycle']:.4f}")
        print(f"  Daily Profit:        ${expected['daily_profit']:.2f} ({expected['daily_return_percent']}%)")
        print(f"  Monthly Profit:      ${expected['monthly_profit']:.2f} ({expected['monthly_return_percent']}%)")

        self.logger.log_grid_setup(
            campaign_name=self.config['campaign_name'],
            product_id=product_id,
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
                    "campaign_name": self.config['campaign_name'],
                    "product_id": product_id,
                    "grid_levels": strategy['grid_levels'],
                    "grid_spacing_percent": strategy['grid_spacing_percent'],
                    "order_size_percent": strategy['order_size_percent'],
                    "price_range": strategy['price_range'],
                    "scan_interval_seconds": strategy['scan_interval_seconds'],
                    "stop_loss_bankroll": risk['stop_loss_bankroll'],
                    "reference_price": reference_price,
                    "expected": expected,
                },
            ),
        )

        print("\n✓ Setup complete!")
        print("\nNext steps:")
        print("  1. Dry run:  python scripts/agent.py dry-run --config config.json")
        print("  2. Live run: python scripts/agent.py start   --config config.json")
        print("============================================================\n")

    def dry_run(self, cycles: int = 5):
        """Simulate trading cycles without placing real orders"""
        print("\n============================================================")
        print("COINBASE GRID TRADER - DRY RUN")
        print("============================================================\n")

        if self.grid is None:
            print("ERROR: Run setup first")
            return

        product_id = self.config['trading_pair']
        scan_interval = self.config['strategy']['scan_interval_seconds']
        reference_price = self.grid.get_reference_price()
        self._ensure_session_started()
        self._store_call(
            "dry_run_started_event",
            lambda: self.store.save_event(
                self.session_id,
                "dry_run_started",
                {"product_id": product_id, "cycles": cycles, "scan_interval_seconds": scan_interval},
            ),
        )

        print(f"Simulating {cycles} cycles (reference price: ${reference_price:,.2f})")
        print(f"Scan interval: {scan_interval}s\n")

        for cycle in range(cycles):
            print(f"--- Cycle {cycle + 1}/{cycles} ---")
            required = self.grid.get_required_orders(reference_price)
            print(f"Reference Price: ${reference_price:,.2f}")
            print(f"Would place {len(required['buy'])} buy orders below ${reference_price:,.2f}")
            print(f"Would place {len(required['sell'])} sell orders above ${reference_price:,.2f}")

            next_buy = self.grid.get_next_buy_level(reference_price)
            next_sell = self.grid.get_next_sell_level(reference_price)
            if next_buy:
                print(f"Next buy level:  ${next_buy:,.2f}")
            if next_sell:
                print(f"Next sell level: ${next_sell:,.2f}")
            print()
            time.sleep(2)

        print("✓ Dry run complete!")
        self._store_call(
            "dry_run_completed_event",
            lambda: self.store.save_event(
                self.session_id,
                "dry_run_completed",
                {"product_id": product_id, "cycles": cycles},
            ),
        )
        print("\nTo run live mode:")
        print("  python scripts/agent.py start --config config.json")
        print("============================================================\n")

    def start(self):
        """Start live trading"""
        print("\n============================================================")
        print("COINBASE GRID TRADER - LIVE MODE")
        print("============================================================\n")

        if self.grid is None:
            print("ERROR: Run setup first")
            return

        product_id = self.config['trading_pair']
        scan_interval = self.config['strategy']['scan_interval_seconds']
        stop_loss = self.config['risk_management']['stop_loss_bankroll']
        self._ensure_session_started()

        print(f"Trading Pair:    {product_id}")
        print(f"Scan Interval:   {scan_interval}s")
        print(f"Stop Loss:       ${stop_loss:,.2f}")
        print("\nStarting live trading... (Press Ctrl+C to stop)\n")
        self._store_call(
            "live_trading_started_event",
            lambda: self.store.save_event(
                self.session_id,
                "live_trading_started",
                {
                    "product_id": product_id,
                    "scan_interval_seconds": scan_interval,
                    "stop_loss_bankroll": stop_loss,
                },
            ),
        )

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
        product_id = self.config['trading_pair']
        stop_loss = self.config['risk_management']['stop_loss_bankroll']
        base_currency = pair_selector.get_base_currency(product_id)

        try:
            # 1. Fetch open orders from Coinbase
            open_orders_list = self.seren.get_open_orders(product_id)
            current_open = {o['id']: o for o in open_orders_list}

            # 2. Detect fills
            filled_ids = self.grid.find_filled_orders(self.active_orders, current_open)
            for order_id in filled_ids:
                self._process_fill(order_id)

            # 3. Update balances and check stop-loss
            base_bal = self.seren.get_account_balance(base_currency)
            usd_bal = self.seren.get_account_balance('USD')
            self.tracker.update_balances(base_bal, usd_bal)

            reference_price = self.grid.get_reference_price()
            if self.tracker.should_stop_loss(reference_price, stop_loss):
                portfolio_value = self.tracker.get_current_value(reference_price)
                print(f"\n⚠ STOP LOSS TRIGGERED at ${portfolio_value:,.2f}")
                self._store_call(
                    "stop_loss_event",
                    lambda: self.store.save_event(
                        self.session_id,
                        "stop_loss_triggered",
                        {
                            "product_id": product_id,
                            "reference_price": reference_price,
                            "portfolio_value": portfolio_value,
                            "stop_loss_bankroll": stop_loss,
                        },
                    ),
                )
                self.stop()
                return

            # 4. Place missing grid orders
            required = self.grid.get_required_orders(reference_price)
            open_prices = {float(o['price']) for o in current_open.values()}
            self._place_grid_orders(required, open_prices, product_id)

            # 5. Log position snapshot
            self.logger.log_position_update(
                product_id=product_id,
                base_balance=base_bal,
                quote_balance=usd_bal,
                total_value_usd=self.tracker.get_current_value(reference_price),
                unrealized_pnl=self.tracker.get_unrealized_pnl(reference_price),
                open_orders=len(self.active_orders)
            )
            self._store_call(
                "position_snapshot",
                lambda: self.store.save_position(
                    session_id=self.session_id,
                    trading_pair=product_id,
                    base_balance=base_bal,
                    quote_balance=usd_bal,
                    total_value_usd=self.tracker.get_current_value(reference_price),
                    unrealized_pnl=self.tracker.get_unrealized_pnl(reference_price),
                    open_orders=len(self.active_orders),
                ),
            )

            # 6. Print status line
            ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            print(
                f"[{ts}] Open Orders: {len(self.active_orders)} | "
                f"Fills: {len(self.tracker.filled_orders)} | "
                f"P&L: ${self.tracker.get_unrealized_pnl(reference_price):,.2f}"
            )

        except Exception as exc:
            err = str(exc)
            print(f"ERROR in trading cycle: {err}")
            self.logger.log_error(
                operation='trading_cycle',
                error_type=type(exc).__name__,
                error_message=err
            )
            self._store_call(
                "trading_cycle_error_event",
                lambda: self.store.save_event(
                    self.session_id,
                    "trading_cycle_error",
                    {
                        "error_type": type(exc).__name__,
                        "error_message": err,
                    },
                ),
            )

    def _place_grid_orders(self, required: Dict, open_prices: set, product_id: str):
        """Place buy and sell orders not already open"""
        for side in ('buy', 'sell'):
            for order in required[side]:
                if order['price'] not in open_prices:
                    self._place_order(
                        product_id=product_id,
                        side=side,
                        price=order['price'],
                        size=order['size']
                    )

    def _place_order(self, product_id: str, side: str, price: float, size: float):
        """Place a single limit order"""
        base = pair_selector.get_base_currency(product_id)
        if self.is_dry_run:
            print(f"[DRY RUN] Would place {side} order: {size:.8f} {base} @ ${price:,.2f}")
            return

        try:
            response = self.seren.place_limit_order(
                side=side,
                product_id=product_id,
                price=price,
                size=size,
                post_only=True
            )
            order_id = response['id']
            order_details = {'side': side, 'price': price, 'size': size}
            self.active_orders[order_id] = order_details
            self.tracker.add_open_order(order_id, order_details)
            self.logger.log_order(
                order_id=order_id,
                side=side,
                price=price,
                size=size,
                status='placed'
            )
            self._store_call(
                "order_placed",
                lambda: self.store.save_order(
                    session_id=self.session_id,
                    order_id=order_id,
                    side=side,
                    price=price,
                    size=size,
                    status='placed',
                    payload={
                        "product_id": product_id,
                        "post_only": True,
                    },
                ),
            )
            print(f"✓ Placed {side} order: {size:.8f} {base} @ ${price:,.2f} (ID: {order_id})")

        except Exception as exc:
            err = str(exc)
            print(f"ERROR placing {side} order at ${price:,.2f}: {err}")
            self.logger.log_error(
                operation='place_order',
                error_type=type(exc).__name__,
                error_message=err,
                context={'side': side, 'price': price, 'size': size}
            )
            self._store_call(
                "order_error_event",
                lambda: self.store.save_event(
                    self.session_id,
                    "order_error",
                    {
                        "product_id": product_id,
                        "side": side,
                        "price": price,
                        "size": size,
                        "error_type": type(exc).__name__,
                        "error_message": err,
                    },
                ),
            )

    def _process_fill(self, order_id: str):
        """Record a filled order"""
        if order_id not in self.active_orders:
            return

        order = self.active_orders.pop(order_id)
        side = order['side']
        price = order['price']
        size = order['size']
        cost = price * size
        fee = cost * self.MAKER_FEE_RATE

        self.tracker.record_fill(
            order_id=order_id,
            side=side,
            price=price,
            size=size,
            fee=fee,
            cost=cost
        )
        self.logger.log_fill(
            order_id=order_id,
            side=side,
            price=price,
            size=size,
            fee=fee,
            cost=cost
        )
        self._store_call(
            "fill_recorded",
            lambda: self.store.save_fill(
                session_id=self.session_id,
                order_id=order_id,
                side=side,
                price=price,
                size=size,
                fee=fee,
                cost=cost,
                payload={"product_id": self.config['trading_pair']},
            ),
        )
        print(f"✓ FILLED {side.upper()}: {size:.8f} @ ${price:,.2f} (Fee: ${fee:.4f})")

    def status(self):
        """Show current trading status"""
        if self.tracker is None:
            print("ERROR: No active trading session")
            return
        reference_price = self.grid.get_reference_price()
        print(self.tracker.get_position_summary(reference_price))

    def stop(self):
        """Stop trading and cancel all open orders"""
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

        product_id = self.config['trading_pair']

        if not self.is_dry_run:
            try:
                print("Cancelling all open orders...")
                cancelled = self.seren.cancel_all_orders(product_id)
                print(f"✓ Cancelled {cancelled} orders")
            except Exception as exc:
                print(f"ERROR cancelling orders: {exc}")
                self._store_call(
                    "cancel_orders_error_event",
                    lambda: self.store.save_event(
                        self.session_id,
                        "cancel_orders_error",
                        {"error_type": type(exc).__name__, "error_message": str(exc)},
                    ),
                )

        if self.tracker:
            reference_price = self.grid.get_reference_price()
            print(self.tracker.get_position_summary(reference_price))

            output_path = f"fills_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
            self.tracker.export_fills_to_csv(output_path)
            print(f"\n✓ Fills exported to {output_path}")

        print("\n✓ Trading stopped\n")


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Coinbase Grid Trading Bot',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command')

    for cmd, help_text in [
        ('setup',   'Validate config and show profit projections'),
        ('dry-run', 'Simulate trading without placing real orders'),
        ('start',   'Start live trading'),
        ('status',  'Show current trading status'),
        ('stop',    'Stop trading and cancel all orders'),
    ]:
        sp = subparsers.add_parser(cmd, help=help_text)
        sp.add_argument('--config', required=True, help='Path to config JSON file')
        if cmd == 'dry-run':
            sp.add_argument('--cycles', type=int, default=5, help='Cycles to simulate')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dry_run = (args.command == 'dry-run')
    agent = CoinbaseGridTrader(config_path=args.config, dry_run=dry_run)

    try:
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
    finally:
        agent.close()


if __name__ == '__main__':
    main()
