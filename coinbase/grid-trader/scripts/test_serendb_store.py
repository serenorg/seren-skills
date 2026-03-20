import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from serendb_store import SerenDBStore


def _make_store():
    store = object.__new__(SerenDBStore)
    queries = []
    store._execute_sql = queries.append
    store._sql_text = SerenDBStore._sql_text
    store._sql_bool = SerenDBStore._sql_bool
    store._sql_json = SerenDBStore._sql_json
    return store, queries


class TestSerenDBStoreNormalizedWrites:
    def test_create_session_dual_writes_normalized_strategy_run(self):
        store, queries = _make_store()

        SerenDBStore.create_session(
            store,
            "00000000-0000-0000-0000-000000000001",
            "coinbase-grid",
            "BTC-USD",
            False,
        )

        assert len(queries) == 1
        assert "INSERT INTO coinbase_grid_sessions" in queries[0]
        assert "INSERT INTO trading.strategy_runs" in queries[0]
        assert "'coinbase-grid-trader'" in queries[0]

    def test_save_position_dual_writes_normalized_position_mark_and_pnl(self):
        store, queries = _make_store()

        SerenDBStore.save_position(
            store,
            session_id="00000000-0000-0000-0000-000000000001",
            trading_pair="BTC-USD",
            base_balance=0.25,
            quote_balance=1250.0,
            total_value_usd=25000.0,
            unrealized_pnl=320.5,
            open_orders=6,
        )

        assert len(queries) == 1
        assert "INSERT INTO coinbase_grid_positions" in queries[0]
        assert "INSERT INTO trading.positions" in queries[0]
        assert "INSERT INTO trading.position_marks" in queries[0]
        assert "INSERT INTO trading.pnl_periods" in queries[0]
