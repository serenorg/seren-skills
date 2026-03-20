"""
SerenDB Storage - Database client for Polymarket trading bot

Stores trading data in SerenDB cloud database:
- Positions (open positions tracking)
- Trades (executed trade history)
- Scan logs (bot activity logs)
- Config (bot configuration)
"""

import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from uuid import uuid4
from seren_client import SerenClient


class SerenDBStorage:
    """Client for storing Polymarket bot data in SerenDB"""

    def __init__(
        self,
        seren_client: SerenClient,
        project_name: str = "polymarket-bot"
    ):
        """
        Initialize SerenDB storage

        Args:
            seren_client: SerenClient instance for API calls
            project_name: SerenDB project name (default: polymarket-bot)
        """
        self.seren = seren_client
        self.project_name = project_name
        self.project_id: Optional[str] = None
        self.branch_id: Optional[str] = None
        self.database_name = "trading_db"
        self._normalized_run_id: Optional[str] = None
        self._normalized_dry_run = True
        self._normalized_mode = "paper-sim"

    def setup_database(self) -> bool:
        """
        Create SerenDB project and tables if they don't exist

        Returns:
            True if setup successful, False otherwise
        """
        try:
            # Step 1: Get or create project
            print(f"Setting up SerenDB project '{self.project_name}'...")

            projects = self._list_projects()
            project = next((p for p in projects if p['name'] == self.project_name), None)

            if not project:
                print(f"  Creating new project...")
                project = self._create_project(self.project_name)
                print(f"  ✓ Project created: {project['id']}")
            else:
                print(f"  ✓ Project found: {project['id']}")

            self.project_id = project['id']

            # Step 2: Get main branch
            branches = self._list_branches(self.project_id)
            main_branch = next((b for b in branches if b['name'] == 'main'), None)

            if not main_branch:
                raise Exception("Main branch not found")

            self.branch_id = main_branch['id']
            print(f"  ✓ Using branch: {self.branch_id}")

            # Step 3: Create tables
            print("  Creating tables...")

            # Create positions table
            self._execute_sql("""
                CREATE TABLE IF NOT EXISTS positions (
                    id SERIAL PRIMARY KEY,
                    market_id TEXT UNIQUE NOT NULL,
                    market TEXT NOT NULL,
                    token_id TEXT,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    size REAL NOT NULL,
                    unrealized_pnl REAL DEFAULT 0.0,
                    opened_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
            """)

            # Create trades table
            self._execute_sql("""
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    market_id TEXT NOT NULL,
                    market TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    executed_at TIMESTAMP NOT NULL,
                    tx_hash TEXT
                )
            """)

            # Create scan_logs table
            self._execute_sql("""
                CREATE TABLE IF NOT EXISTS scan_logs (
                    id SERIAL PRIMARY KEY,
                    scan_at TIMESTAMP NOT NULL,
                    markets_scanned INTEGER NOT NULL,
                    opportunities_found INTEGER NOT NULL,
                    trades_executed INTEGER NOT NULL,
                    capital_deployed REAL NOT NULL,
                    api_cost REAL NOT NULL,
                    serenbucks_balance REAL,
                    polymarket_balance REAL
                )
            """)

            # Create config table
            self._execute_sql("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
            """)

            # Create predictions table for tracking AI fair value predictions
            self._execute_sql("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY,
                    market_id TEXT NOT NULL,
                    market_question TEXT NOT NULL,
                    predicted_fair_value REAL NOT NULL,
                    market_price_at_prediction REAL NOT NULL,
                    edge_calculated REAL NOT NULL,
                    prediction_timestamp TIMESTAMP NOT NULL,
                    resolution_outcome TEXT,
                    resolution_timestamp TIMESTAMP,
                    actual_probability REAL,
                    brier_score REAL,
                    traded BOOLEAN DEFAULT FALSE,
                    trade_size REAL,
                    trade_price REAL
                )
            """)

            # Create performance_metrics table for aggregate statistics
            self._execute_sql("""
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id SERIAL PRIMARY KEY,
                    calculated_at TIMESTAMP NOT NULL,
                    total_predictions INTEGER NOT NULL,
                    resolved_predictions INTEGER NOT NULL,
                    avg_brier_score REAL,
                    calibration_slope REAL,
                    calibration_intercept REAL,
                    total_trades INTEGER,
                    winning_trades INTEGER,
                    total_realized_pnl REAL,
                    roi_percentage REAL,
                    kelly_multiplier REAL,
                    edge_threshold REAL
                )
            """)

            # Create resolved_markets table for win/loss tracking
            self._execute_sql("""
                CREATE TABLE IF NOT EXISTS resolved_markets (
                    id SERIAL PRIMARY KEY,
                    market_id TEXT UNIQUE NOT NULL,
                    market_question TEXT NOT NULL,
                    resolution_outcome TEXT NOT NULL,
                    resolution_timestamp TIMESTAMP NOT NULL,
                    final_price REAL,
                    traded BOOLEAN DEFAULT FALSE,
                    entry_price REAL,
                    exit_price REAL,
                    position_size REAL,
                    realized_pnl REAL,
                    roi_percentage REAL
                )
            """)

            self._execute_sql("""
                CREATE SCHEMA IF NOT EXISTS trading;

                CREATE TABLE IF NOT EXISTS trading.strategy_runs (
                    run_id UUID PRIMARY KEY,
                    skill_slug TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    config JSONB NOT NULL DEFAULT '{}'::jsonb,
                    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
                    error_code TEXT,
                    error_message TEXT,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                );

                CREATE INDEX IF NOT EXISTS idx_strategy_runs_skill_mode_started
                    ON trading.strategy_runs (skill_slug, mode, started_at DESC);

                CREATE TABLE IF NOT EXISTS trading.order_events (
                    id BIGSERIAL PRIMARY KEY,
                    run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
                    order_id TEXT,
                    instrument_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    order_type TEXT,
                    event_type TEXT NOT NULL,
                    status TEXT,
                    price NUMERIC(24, 10),
                    quantity NUMERIC(24, 10),
                    notional_usd NUMERIC(24, 10),
                    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                );

                CREATE INDEX IF NOT EXISTS idx_order_events_run_time
                    ON trading.order_events (run_id, event_time DESC);

                CREATE TABLE IF NOT EXISTS trading.fills (
                    id BIGSERIAL PRIMARY KEY,
                    run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
                    order_id TEXT,
                    venue_fill_id TEXT,
                    instrument_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    fill_price NUMERIC(24, 10),
                    fill_quantity NUMERIC(24, 10),
                    fee_usd NUMERIC(24, 10),
                    notional_usd NUMERIC(24, 10),
                    realized_pnl_usd NUMERIC(24, 10),
                    fill_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                );

                CREATE INDEX IF NOT EXISTS idx_fills_run_time
                    ON trading.fills (run_id, fill_time DESC);

                CREATE TABLE IF NOT EXISTS trading.positions (
                    id BIGSERIAL PRIMARY KEY,
                    run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
                    position_key TEXT NOT NULL,
                    instrument_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    quantity NUMERIC(24, 10),
                    entry_price NUMERIC(24, 10),
                    cost_basis_usd NUMERIC(24, 10),
                    market_price NUMERIC(24, 10),
                    market_value_usd NUMERIC(24, 10),
                    unrealized_pnl_usd NUMERIC(24, 10),
                    realized_pnl_usd NUMERIC(24, 10),
                    status TEXT,
                    opened_at TIMESTAMPTZ,
                    closed_at TIMESTAMPTZ,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    UNIQUE (run_id, position_key)
                );

                CREATE INDEX IF NOT EXISTS idx_positions_run_status
                    ON trading.positions (run_id, status);

                CREATE TABLE IF NOT EXISTS trading.position_marks (
                    id BIGSERIAL PRIMARY KEY,
                    run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
                    position_key TEXT NOT NULL,
                    instrument_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    quantity NUMERIC(24, 10),
                    mark_price NUMERIC(24, 10),
                    market_value_usd NUMERIC(24, 10),
                    unrealized_pnl_usd NUMERIC(24, 10),
                    realized_pnl_usd NUMERIC(24, 10),
                    mark_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                );

                CREATE INDEX IF NOT EXISTS idx_position_marks_run_time
                    ON trading.position_marks (run_id, mark_time DESC);

                CREATE TABLE IF NOT EXISTS trading.pnl_periods (
                    id BIGSERIAL PRIMARY KEY,
                    run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
                    period_type TEXT NOT NULL,
                    period_start TIMESTAMPTZ,
                    period_end TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    realized_pnl_usd NUMERIC(24, 10),
                    unrealized_pnl_usd NUMERIC(24, 10),
                    fees_usd NUMERIC(24, 10),
                    gross_pnl_usd NUMERIC(24, 10),
                    net_pnl_usd NUMERIC(24, 10),
                    equity_start_usd NUMERIC(24, 10),
                    equity_end_usd NUMERIC(24, 10),
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                );

                CREATE INDEX IF NOT EXISTS idx_pnl_periods_run_end
                    ON trading.pnl_periods (run_id, period_end DESC);
            """)

            print(f"✅ SerenDB setup complete")
            return True

        except Exception as e:
            print(f"❌ Failed to setup database: {e}")
            import traceback
            traceback.print_exc()
            return False

    def set_run_mode(self, dry_run: bool) -> None:
        """Set the normalized run mode for the current agent process."""
        self._normalized_dry_run = bool(dry_run)
        self._normalized_mode = "paper-sim" if self._normalized_dry_run else "live"

    def _ensure_normalized_run(self, dry_run: Optional[bool] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
        if dry_run is not None:
            self.set_run_mode(dry_run)

        if not self._normalized_run_id:
            self._normalized_run_id = str(uuid4())
            self._execute_sql("""
                INSERT INTO trading.strategy_runs (
                    run_id, skill_slug, venue, strategy_name, mode, status,
                    dry_run, config, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?::jsonb)
            """, (
                self._normalized_run_id,
                "polymarket-bot",
                "polymarket",
                "bot",
                self._normalized_mode,
                "running",
                self._normalized_dry_run,
                json.dumps({
                    "project_name": self.project_name,
                    "database_name": self.database_name,
                }),
                json.dumps(metadata or {}),
            ))
        elif metadata:
            self._execute_sql("""
                UPDATE trading.strategy_runs
                SET mode = ?,
                    dry_run = ?,
                    metadata = COALESCE(metadata, '{}'::jsonb) || ?::jsonb
                WHERE run_id = ?
            """, (
                self._normalized_mode,
                self._normalized_dry_run,
                json.dumps(metadata),
                self._normalized_run_id,
            ))

        return self._normalized_run_id

    def _update_normalized_summary(self, summary_patch: Dict[str, Any]) -> None:
        run_id = self._ensure_normalized_run()
        self._execute_sql("""
            UPDATE trading.strategy_runs
            SET summary = COALESCE(summary, '{}'::jsonb) || ?::jsonb
            WHERE run_id = ?
        """, (
            json.dumps(summary_patch),
            run_id,
        ))

    @staticmethod
    def _position_market_value(position: Dict[str, Any]) -> float:
        size = float(position.get('size', 0) or 0)
        unrealized = float(position.get('unrealized_pnl', 0) or 0)
        return size + unrealized

    # Position methods

    def save_position(self, position: Dict[str, Any]) -> bool:
        """
        Save or update a position

        Args:
            position: Position data dict

        Returns:
            True if successful
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            run_id = self._ensure_normalized_run()
            market_value = self._position_market_value(position)
            instrument_id = position.get('token_id') or position['market_id']
            metadata_json = json.dumps({
                'market': position.get('market', ''),
                'opened_at': position.get('opened_at'),
            })

            # Try to update existing position first
            result = self._execute_sql("""
                UPDATE positions
                SET current_price = ?,
                    unrealized_pnl = ?,
                    updated_at = ?
                WHERE market_id = ?
            """, (
                position['current_price'],
                position['unrealized_pnl'],
                now,
                position['market_id']
            ))

            # If no rows updated, insert new position
            if result.get('changes', 0) == 0:
                self._execute_sql("""
                    INSERT INTO positions (
                        market_id, market, token_id, side,
                        entry_price, current_price, size,
                        unrealized_pnl, opened_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    position['market_id'],
                    position['market'],
                    position.get('token_id', ''),
                    position['side'],
                    position['entry_price'],
                    position['current_price'],
                    position['size'],
                    position['unrealized_pnl'],
                    position['opened_at'],
                    now
                ))

            self._execute_sql("""
                INSERT INTO trading.positions (
                    run_id, position_key, instrument_id, symbol, side, quantity,
                    entry_price, cost_basis_usd, market_price, market_value_usd,
                    unrealized_pnl_usd, realized_pnl_usd, status, opened_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
                ON CONFLICT (run_id, position_key) DO UPDATE
                SET instrument_id = EXCLUDED.instrument_id,
                    symbol = EXCLUDED.symbol,
                    side = EXCLUDED.side,
                    quantity = EXCLUDED.quantity,
                    entry_price = EXCLUDED.entry_price,
                    cost_basis_usd = EXCLUDED.cost_basis_usd,
                    market_price = EXCLUDED.market_price,
                    market_value_usd = EXCLUDED.market_value_usd,
                    unrealized_pnl_usd = EXCLUDED.unrealized_pnl_usd,
                    realized_pnl_usd = EXCLUDED.realized_pnl_usd,
                    status = EXCLUDED.status,
                    opened_at = COALESCE(EXCLUDED.opened_at, trading.positions.opened_at),
                    metadata = EXCLUDED.metadata
            """, (
                run_id,
                position['market_id'],
                instrument_id,
                position['market_id'],
                position['side'],
                position['size'],
                position['entry_price'],
                position['size'],
                position['current_price'],
                market_value,
                position['unrealized_pnl'],
                0.0,
                'open',
                position.get('opened_at', now),
                metadata_json,
            ))
            self._execute_sql("""
                INSERT INTO trading.position_marks (
                    run_id, position_key, instrument_id, symbol, side, quantity,
                    mark_price, market_value_usd, unrealized_pnl_usd, realized_pnl_usd,
                    mark_time, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
            """, (
                run_id,
                position['market_id'],
                instrument_id,
                position['market_id'],
                position['side'],
                position['size'],
                position['current_price'],
                market_value,
                position['unrealized_pnl'],
                0.0,
                now,
                metadata_json,
            ))

            return True

        except Exception as e:
            print(f"Error saving position: {e}")
            return False

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get all open positions

        Returns:
            List of position dicts
        """
        try:
            result = self._execute_sql("SELECT * FROM positions ORDER BY opened_at DESC")
            return result.get('rows', [])
        except Exception as e:
            print(f"Error getting positions: {e}")
            return []

    def get_position(self, market_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific position by market_id

        Args:
            market_id: Market ID

        Returns:
            Position dict or None
        """
        try:
            result = self._execute_sql(
                "SELECT * FROM positions WHERE market_id = ?",
                (market_id,)
            )
            rows = result.get('rows', [])
            return rows[0] if rows else None
        except Exception as e:
            print(f"Error getting position: {e}")
            return None

    def delete_position(self, market_id: str) -> bool:
        """
        Delete a position

        Args:
            market_id: Market ID

        Returns:
            True if successful
        """
        try:
            self._execute_sql("DELETE FROM positions WHERE market_id = ?", (market_id,))
            return True
        except Exception as e:
            print(f"Error deleting position: {e}")
            return False

    # Trade methods

    def save_trade(self, trade: Dict[str, Any]) -> bool:
        """
        Save a trade execution

        Args:
            trade: Trade data dict

        Returns:
            True if successful
        """
        try:
            run_id = self._ensure_normalized_run(dry_run=(trade.get('status') == 'dry_run'))
            executed_at = trade.get('executed_at') or datetime.now(timezone.utc).isoformat()
            order_id = trade.get('tx_hash') or f"{trade['market_id']}:{executed_at}"
            metadata_json = json.dumps({
                'market': trade.get('market', ''),
                'fair_value': trade.get('fair_value'),
                'edge': trade.get('edge'),
                'status': trade.get('status', 'open'),
                'pnl': trade.get('pnl'),
            })
            self._execute_sql("""
                INSERT INTO trades (
                    market_id, market, side, price, size, executed_at, tx_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                trade['market_id'],
                trade['market'],
                trade['side'],
                trade['price'],
                trade['size'],
                executed_at,
                trade.get('tx_hash', '')
            ))
            self._execute_sql("""
                INSERT INTO trading.order_events (
                    run_id, order_id, instrument_id, symbol, side, order_type,
                    event_type, status, price, quantity, notional_usd, event_time, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
            """, (
                run_id,
                order_id,
                trade.get('market_id'),
                trade.get('market_id'),
                trade['side'],
                'market',
                'trade',
                trade.get('status', 'open'),
                trade['price'],
                trade['size'],
                trade['size'],
                executed_at,
                metadata_json,
            ))
            self._execute_sql("""
                INSERT INTO trading.fills (
                    run_id, order_id, venue_fill_id, instrument_id, symbol, side,
                    fill_price, fill_quantity, notional_usd, realized_pnl_usd, fill_time, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
            """, (
                run_id,
                order_id,
                trade.get('tx_hash'),
                trade.get('market_id'),
                trade.get('market_id'),
                trade['side'],
                trade['price'],
                trade['size'],
                trade['size'],
                trade.get('pnl'),
                executed_at,
                metadata_json,
            ))
            return True
        except Exception as e:
            print(f"Error saving trade: {e}")
            return False

    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get recent trades

        Args:
            limit: Maximum number of trades to return

        Returns:
            List of trade dicts
        """
        try:
            result = self._execute_sql(
                "SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?",
                (limit,)
            )
            return result.get('rows', [])
        except Exception as e:
            print(f"Error getting trades: {e}")
            return []

    # Scan log methods

    def save_scan_log(self, log: Dict[str, Any]) -> bool:
        """
        Save a scan cycle log

        Args:
            log: Scan log data dict

        Returns:
            True if successful
        """
        try:
            run_id = self._ensure_normalized_run(dry_run=bool(log.get('dry_run', self._normalized_dry_run)))
            self._execute_sql("""
                INSERT INTO scan_logs (
                    scan_at, markets_scanned, opportunities_found,
                    trades_executed, capital_deployed, api_cost,
                    serenbucks_balance, polymarket_balance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                log['scan_at'],
                log['markets_scanned'],
                log['opportunities_found'],
                log['trades_executed'],
                log['capital_deployed'],
                log['api_cost'],
                log.get('serenbucks_balance'),
                log.get('polymarket_balance')
            ))
            self._execute_sql("""
                INSERT INTO trading.pnl_periods (
                    run_id, period_type, period_end, fees_usd, equity_end_usd, metadata
                ) VALUES (?, ?, ?, ?, ?, ?::jsonb)
            """, (
                run_id,
                'scan_cycle',
                log['scan_at'],
                log['api_cost'],
                log.get('polymarket_balance'),
                json.dumps({
                    'dry_run': bool(log.get('dry_run', self._normalized_dry_run)),
                    'markets_scanned': log['markets_scanned'],
                    'opportunities_found': log['opportunities_found'],
                    'trades_executed': log['trades_executed'],
                    'capital_deployed': log['capital_deployed'],
                    'serenbucks_balance': log.get('serenbucks_balance'),
                    'polymarket_balance': log.get('polymarket_balance'),
                    'errors': log.get('errors', []),
                }),
            ))
            self._update_normalized_summary({
                'last_scan_at': log['scan_at'],
                'markets_scanned': log['markets_scanned'],
                'opportunities_found': log['opportunities_found'],
                'trades_executed': log['trades_executed'],
                'capital_deployed': log['capital_deployed'],
                'api_cost': log['api_cost'],
                'serenbucks_balance': log.get('serenbucks_balance'),
                'polymarket_balance': log.get('polymarket_balance'),
            })
            return True
        except Exception as e:
            print(f"Error saving scan log: {e}")
            return False

    def get_scan_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get recent scan logs

        Args:
            limit: Maximum number of logs to return

        Returns:
            List of scan log dicts
        """
        try:
            result = self._execute_sql(
                "SELECT * FROM scan_logs ORDER BY scan_at DESC LIMIT ?",
                (limit,)
            )
            return result.get('rows', [])
        except Exception as e:
            print(f"Error getting scan logs: {e}")
            return []

    # Config methods

    def save_config(self, key: str, value: Any) -> bool:
        """
        Save a config value

        Args:
            key: Config key
            value: Config value (will be JSON serialized)

        Returns:
            True if successful
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            value_json = json.dumps(value)

            # Try update first
            result = self._execute_sql(
                "UPDATE config SET value = ?, updated_at = ? WHERE key = ?",
                (value_json, now, key)
            )

            # If no rows updated, insert
            if result.get('changes', 0) == 0:
                self._execute_sql(
                    "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value_json, now)
                )

            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Get a config value

        Args:
            key: Config key
            default: Default value if key not found

        Returns:
            Config value (JSON deserialized)
        """
        try:
            result = self._execute_sql(
                "SELECT value FROM config WHERE key = ?",
                (key,)
            )
            rows = result.get('rows', [])
            if rows:
                return json.loads(rows[0]['value'])
            return default
        except Exception as e:
            print(f"Error getting config: {e}")
            return default

    # Prediction tracking methods

    def save_prediction(self, prediction: Dict[str, Any]) -> bool:
        """
        Save a prediction for later performance tracking

        Args:
            prediction: Prediction data dict with keys:
                - market_id: Market ID
                - market_question: Question text
                - predicted_fair_value: AI's fair value estimate (0.0-1.0)
                - market_price_at_prediction: Market price when prediction made
                - edge_calculated: Edge calculated (fair - price)
                - prediction_timestamp: ISO timestamp
                - traded: Whether a trade was placed (optional, default False)
                - trade_size: Size of trade if placed (optional)
                - trade_price: Price of trade if placed (optional)

        Returns:
            True if successful
        """
        try:
            self._execute_sql("""
                INSERT INTO predictions (
                    market_id, market_question, predicted_fair_value,
                    market_price_at_prediction, edge_calculated,
                    prediction_timestamp, traded, trade_size, trade_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                prediction['market_id'],
                prediction['market_question'],
                prediction['predicted_fair_value'],
                prediction['market_price_at_prediction'],
                prediction['edge_calculated'],
                prediction['prediction_timestamp'],
                prediction.get('traded', False),
                prediction.get('trade_size'),
                prediction.get('trade_price')
            ))
            return True
        except Exception as e:
            print(f"Error saving prediction: {e}")
            return False

    def get_unresolved_predictions(self) -> List[Dict[str, Any]]:
        """
        Get all predictions that haven't been resolved yet

        Returns:
            List of prediction dicts
        """
        try:
            result = self._execute_sql(
                "SELECT * FROM predictions WHERE resolution_outcome IS NULL ORDER BY prediction_timestamp DESC"
            )
            return result.get('rows', [])
        except Exception as e:
            print(f"Error getting unresolved predictions: {e}")
            return []

    def update_prediction_resolution(
        self,
        market_id: str,
        resolution_outcome: str,
        resolution_timestamp: str,
        actual_probability: float
    ) -> bool:
        """
        Update a prediction with its resolution outcome

        Args:
            market_id: Market ID
            resolution_outcome: 'YES', 'NO', or 'INVALID'
            resolution_timestamp: ISO timestamp of resolution
            actual_probability: 1.0 for YES, 0.0 for NO, None for INVALID

        Returns:
            True if successful
        """
        try:
            # Get the prediction to calculate Brier score
            result = self._execute_sql(
                "SELECT predicted_fair_value FROM predictions WHERE market_id = ?",
                (market_id,)
            )
            rows = result.get('rows', [])
            if not rows:
                print(f"Prediction not found for market {market_id}")
                return False

            predicted_value = rows[0]['predicted_fair_value']

            # Calculate Brier score if outcome is valid
            brier_score = None
            if actual_probability is not None:
                brier_score = (predicted_value - actual_probability) ** 2

            # Update prediction
            self._execute_sql("""
                UPDATE predictions
                SET resolution_outcome = ?,
                    resolution_timestamp = ?,
                    actual_probability = ?,
                    brier_score = ?
                WHERE market_id = ?
            """, (
                resolution_outcome,
                resolution_timestamp,
                actual_probability,
                brier_score,
                market_id
            ))
            return True
        except Exception as e:
            print(f"Error updating prediction resolution: {e}")
            return False

    def get_resolved_predictions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get resolved predictions for performance analysis

        Args:
            limit: Maximum number to return

        Returns:
            List of resolved prediction dicts
        """
        try:
            result = self._execute_sql(
                "SELECT * FROM predictions WHERE resolution_outcome IS NOT NULL ORDER BY resolution_timestamp DESC LIMIT ?",
                (limit,)
            )
            return result.get('rows', [])
        except Exception as e:
            print(f"Error getting resolved predictions: {e}")
            return []

    # Performance metrics methods

    def save_performance_metrics(self, metrics: Dict[str, Any]) -> bool:
        """
        Save calculated performance metrics

        Args:
            metrics: Metrics data dict with keys:
                - calculated_at: ISO timestamp
                - total_predictions: Total predictions made
                - resolved_predictions: Predictions that have resolved
                - avg_brier_score: Average Brier score
                - calibration_slope: Calibration slope
                - calibration_intercept: Calibration intercept
                - total_trades: Total trades executed
                - winning_trades: Trades that were profitable
                - total_realized_pnl: Total P&L
                - roi_percentage: ROI percentage
                - kelly_multiplier: Current Kelly multiplier
                - edge_threshold: Current edge threshold

        Returns:
            True if successful
        """
        try:
            self._execute_sql("""
                INSERT INTO performance_metrics (
                    calculated_at, total_predictions, resolved_predictions,
                    avg_brier_score, calibration_slope, calibration_intercept,
                    total_trades, winning_trades, total_realized_pnl,
                    roi_percentage, kelly_multiplier, edge_threshold
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metrics['calculated_at'],
                metrics['total_predictions'],
                metrics['resolved_predictions'],
                metrics.get('avg_brier_score'),
                metrics.get('calibration_slope'),
                metrics.get('calibration_intercept'),
                metrics.get('total_trades', 0),
                metrics.get('winning_trades', 0),
                metrics.get('total_realized_pnl', 0.0),
                metrics.get('roi_percentage', 0.0),
                metrics.get('kelly_multiplier'),
                metrics.get('edge_threshold')
            ))
            return True
        except Exception as e:
            print(f"Error saving performance metrics: {e}")
            return False

    def get_latest_metrics(self) -> Optional[Dict[str, Any]]:
        """
        Get the most recent performance metrics

        Returns:
            Metrics dict or None
        """
        try:
            result = self._execute_sql(
                "SELECT * FROM performance_metrics ORDER BY calculated_at DESC LIMIT 1"
            )
            rows = result.get('rows', [])
            return rows[0] if rows else None
        except Exception as e:
            print(f"Error getting latest metrics: {e}")
            return None

    def get_metrics_history(self, limit: int = 30) -> List[Dict[str, Any]]:
        """
        Get historical performance metrics

        Args:
            limit: Maximum number to return

        Returns:
            List of metrics dicts
        """
        try:
            result = self._execute_sql(
                "SELECT * FROM performance_metrics ORDER BY calculated_at DESC LIMIT ?",
                (limit,)
            )
            return result.get('rows', [])
        except Exception as e:
            print(f"Error getting metrics history: {e}")
            return []

    # Resolved markets methods

    def save_resolved_market(self, market: Dict[str, Any]) -> bool:
        """
        Save a resolved market with P&L data

        Args:
            market: Resolved market data dict with keys:
                - market_id: Market ID
                - market_question: Question text
                - resolution_outcome: 'YES', 'NO', or 'INVALID'
                - resolution_timestamp: ISO timestamp
                - final_price: Final market price
                - traded: Whether we traded this market
                - entry_price: Our entry price (if traded)
                - exit_price: Our exit price (if traded)
                - position_size: Size of position (if traded)
                - realized_pnl: Realized P&L (if traded)
                - roi_percentage: ROI percentage (if traded)

        Returns:
            True if successful
        """
        try:
            self._execute_sql("""
                INSERT INTO resolved_markets (
                    market_id, market_question, resolution_outcome,
                    resolution_timestamp, final_price, traded,
                    entry_price, exit_price, position_size,
                    realized_pnl, roi_percentage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market['market_id'],
                market['market_question'],
                market['resolution_outcome'],
                market['resolution_timestamp'],
                market.get('final_price'),
                market.get('traded', False),
                market.get('entry_price'),
                market.get('exit_price'),
                market.get('position_size'),
                market.get('realized_pnl'),
                market.get('roi_percentage')
            ))
            return True
        except Exception as e:
            print(f"Error saving resolved market: {e}")
            return False

    def get_resolved_markets(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get resolved markets

        Args:
            limit: Maximum number to return

        Returns:
            List of resolved market dicts
        """
        try:
            result = self._execute_sql(
                "SELECT * FROM resolved_markets ORDER BY resolution_timestamp DESC LIMIT ?",
                (limit,)
            )
            return result.get('rows', [])
        except Exception as e:
            print(f"Error getting resolved markets: {e}")
            return []

    def get_traded_resolved_markets(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get resolved markets where we had a position

        Args:
            limit: Maximum number to return

        Returns:
            List of resolved market dicts with P&L data
        """
        try:
            result = self._execute_sql(
                "SELECT * FROM resolved_markets WHERE traded = TRUE ORDER BY resolution_timestamp DESC LIMIT ?",
                (limit,)
            )
            return result.get('rows', [])
        except Exception as e:
            print(f"Error getting traded resolved markets: {e}")
            return []

    # Private helper methods

    def _execute_sql(self, query: str, params: tuple = ()) -> Dict[str, Any]:
        """
        Execute SQL query via SerenDB REST API

        Args:
            query: SQL query string
            params: Query parameters (for parameterized queries)

        Returns:
            Query result dict
        """
        if not self.project_id or not self.branch_id:
            raise Exception("Database not initialized. Call setup_database() first.")

        # Format parameterized query
        if params:
            # Convert Python parameterized query to SQL
            # Replace ? with actual values (properly escaped)
            for param in params:
                if isinstance(param, str):
                    # Escape single quotes in strings
                    escaped = param.replace("'", "''")
                    query = query.replace('?', f"'{escaped}'", 1)
                elif param is None:
                    query = query.replace('?', 'NULL', 1)
                else:
                    query = query.replace('?', str(param), 1)

        # Call Seren Gateway database API
        url = f"{self.seren.gateway_url}/projects/{self.project_id}/branches/{self.branch_id}/query"

        response = self.seren.session.post(
            url,
            json={'query': query},
            timeout=30
        )

        response.raise_for_status()
        return self._unwrap_data(response.json())

    def _list_projects(self) -> List[Dict[str, Any]]:
        """List all SerenDB projects"""
        url = f"{self.seren.gateway_url}/projects"
        response = self.seren.session.get(url, timeout=10)
        response.raise_for_status()
        return self._unwrap_data(response.json(), default=[])

    def _create_project(self, name: str) -> Dict[str, Any]:
        """Create a new SerenDB project"""
        url = f"{self.seren.gateway_url}/projects"
        response = self.seren.session.post(
            url,
            json={'name': name, 'region': 'aws-us-east-2'},
            timeout=30
        )
        response.raise_for_status()
        data = self._unwrap_data(response.json())
        # Return full project details
        project_id = data['id']
        return self._get_project(project_id)

    def _get_project(self, project_id: str) -> Dict[str, Any]:
        """Get project details"""
        url = f"{self.seren.gateway_url}/projects/{project_id}"
        response = self.seren.session.get(url, timeout=10)
        response.raise_for_status()
        return self._unwrap_data(response.json(), default={})

    def _list_branches(self, project_id: str) -> List[Dict[str, Any]]:
        """List branches for a project"""
        url = f"{self.seren.gateway_url}/projects/{project_id}/branches"
        response = self.seren.session.get(url, timeout=10)
        response.raise_for_status()
        return self._unwrap_data(response.json(), default=[])

    @staticmethod
    def _unwrap_data(payload: Any, default: Optional[Any] = None) -> Any:
        """Accept either wrapped {'data': ...} or direct payloads from SerenDB."""
        if payload is None:
            return default
        if isinstance(payload, dict) and 'data' in payload:
            return payload['data']
        return payload
