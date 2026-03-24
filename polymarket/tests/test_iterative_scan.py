"""Tests for the iterative scan loop across all polymarket skills.

Covers the core iteration contract:
- Loop runs up to max_iterations when hurdle not met
- Stops early on success (opportunities found / hurdle met)
- Stops early on low SerenBucks balance
- Progressively relaxes parameters per iteration band
- Backward compatible: absent config defaults to 15 iterations
"""

import copy
import json
import sys
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# polymarket-bot: iterative scan loop
# ---------------------------------------------------------------------------

BOT_SCRIPTS = str(Path(__file__).resolve().parent.parent / "bot" / "scripts")


@pytest.fixture(autouse=True)
def _add_bot_scripts_to_path():
    if BOT_SCRIPTS not in sys.path:
        sys.path.insert(0, BOT_SCRIPTS)
    yield
    if BOT_SCRIPTS in sys.path:
        sys.path.remove(BOT_SCRIPTS)


def _make_bot_config(**overrides):
    base = {
        "bankroll": 100.0,
        "mispricing_threshold": 0.08,
        "max_kelly_fraction": 0.06,
        "scan_interval_minutes": 10,
        "max_positions": 10,
        "stop_loss_bankroll": 0.0,
        "scan_limit": 300,
        "candidate_limit": 80,
        "analyze_limit": 30,
        "min_liquidity": 100.0,
    }
    base.update(overrides)
    return base


class TestBotIterativeLoop:
    """Test the iterative scan loop in polymarket-bot main()."""

    def test_stops_on_first_success(self, tmp_path):
        """Loop breaks immediately when opportunities are found on iteration 1."""
        config = _make_bot_config(iteration={"max_iterations": 15})
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with patch("agent.TradingAgent") as MockAgent, \
             patch("sys.argv", ["agent.py", "--config", str(config_file), "--dry-run"]):
            mock_instance = MagicMock()
            mock_instance.config = config
            mock_instance.mispricing_threshold = 0.08
            mock_instance.scan_limit = 300
            mock_instance.min_annualized_return = 0.25
            mock_instance._last_serenbucks_balance = 20.0
            mock_instance.run_scan_cycle.return_value = 3  # found opportunities
            MockAgent.return_value = mock_instance

            from agent import main
            main()

            assert mock_instance.run_scan_cycle.call_count == 1

    def test_iterates_on_zero_opportunities(self, tmp_path):
        """Loop continues when no opportunities found, up to max_iterations."""
        config = _make_bot_config(iteration={"max_iterations": 3})
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with patch("agent.TradingAgent") as MockAgent, \
             patch("sys.argv", ["agent.py", "--config", str(config_file), "--dry-run"]):
            mock_instance = MagicMock()
            mock_instance.config = config
            mock_instance.mispricing_threshold = 0.08
            mock_instance.scan_limit = 300
            mock_instance.min_annualized_return = 0.25
            mock_instance._last_serenbucks_balance = 20.0
            mock_instance.run_scan_cycle.return_value = 0
            MockAgent.return_value = mock_instance

            from agent import main
            main()

            assert mock_instance.run_scan_cycle.call_count == 3

    def test_stops_on_low_balance(self, tmp_path):
        """Loop breaks when SerenBucks balance drops below threshold."""
        config = _make_bot_config(iteration={"max_iterations": 15, "low_balance_threshold": 5.0})
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with patch("agent.TradingAgent") as MockAgent, \
             patch("sys.argv", ["agent.py", "--config", str(config_file), "--dry-run"]):
            mock_instance = MagicMock()
            mock_instance.config = config
            mock_instance.mispricing_threshold = 0.08
            mock_instance.scan_limit = 300
            mock_instance.min_annualized_return = 0.25
            mock_instance._last_serenbucks_balance = 2.0  # below threshold
            mock_instance.run_scan_cycle.return_value = 0
            MockAgent.return_value = mock_instance

            from agent import main
            main()

            # Should stop after first iteration due to low balance
            assert mock_instance.run_scan_cycle.call_count == 1

    def test_relaxes_mispricing_threshold_in_band_1(self, tmp_path):
        """Iterations 1-5 lower mispricing_threshold."""
        config = _make_bot_config(iteration={"max_iterations": 3, "threshold_step": 0.01})
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with patch("agent.TradingAgent") as MockAgent, \
             patch("sys.argv", ["agent.py", "--config", str(config_file), "--dry-run"]):
            mock_instance = MagicMock()
            mock_instance.config = config
            mock_instance.mispricing_threshold = 0.08
            mock_instance.scan_limit = 300
            mock_instance.min_annualized_return = 0.25
            mock_instance._last_serenbucks_balance = 20.0
            mock_instance.run_scan_cycle.return_value = 0
            MockAgent.return_value = mock_instance

            from agent import main
            main()

            # After 3 iterations (all in band 1-5), threshold relaxed after each:
            # 0.08 - 3*0.01 = 0.05
            assert mock_instance.mispricing_threshold == pytest.approx(0.05, abs=0.001)

    def test_run_scan_cycle_returns_opportunity_count(self, tmp_path):
        """run_scan_cycle() returns int count of opportunities."""
        # This tests the return value contract, not the full scan
        from agent import TradingAgent

        config = _make_bot_config()
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with patch.object(TradingAgent, "__init__", lambda self, *a, **kw: None):
            agent = TradingAgent.__new__(TradingAgent)
            agent.dry_run = True
            agent.config = config
            agent.bankroll = 100.0
            agent.mispricing_threshold = 0.08
            agent.max_kelly_fraction = 0.06
            agent.max_positions = 10
            agent.stop_loss_bankroll = 0.0
            agent.min_annualized_return = 0.25
            agent.max_resolution_days = 180
            agent.min_exit_bid_depth_ratio = 0.5
            agent.scan_limit = 300
            agent.candidate_limit = 80
            agent.analyze_limit = 30
            agent.min_liquidity = 100.0
            agent.stale_price_demotion = 0.1

            # Mock dependencies
            agent.seren = MagicMock()
            agent.polymarket = MagicMock()
            agent.positions = MagicMock()
            agent.positions.sync_with_polymarket.return_value = {"added": 0, "removed": 0, "updated": 0}
            agent.positions.get_all_positions.return_value = []
            agent.logger = MagicMock()
            agent.storage = None

            # Mock scan_markets to return empty
            agent.scan_markets = MagicMock(return_value=[])

            # Mock check_balances
            agent.check_balances = MagicMock(return_value={"serenbucks": 20.0, "polymarket": 100.0})

            result = agent.run_scan_cycle()
            assert isinstance(result, int)
            assert result == 0


# ---------------------------------------------------------------------------
# liquidity-paired-basis-maker: PR #150 optimization loop (pre-existing)
# ---------------------------------------------------------------------------


class TestLPBMOptimizationExists:
    """Verify PR #150 auto-tune optimization loop is present in LPBM."""

    def test_config_example_has_optimization_block(self):
        config_path = Path(__file__).resolve().parent.parent / "liquidity-paired-basis-maker" / "config.example.json"
        config = json.loads(config_path.read_text())
        backtest = config.get("backtest", {})
        optimization = backtest.get("optimization", {})
        assert "optimization" in backtest, "PR #150 optimization block missing from config"
        assert optimization.get("target_return_pct") == 25.0


# ---------------------------------------------------------------------------
# maker-rebate-bot: PR #150 optimization loop (pre-existing)
# ---------------------------------------------------------------------------


class TestMRBOptimizationExists:
    """Verify PR #150 auto-tune optimization loop is present in MRB."""

    def test_config_example_has_optimization_block(self):
        config_path = Path(__file__).resolve().parent.parent / "maker-rebate-bot" / "config.example.json"
        config = json.loads(config_path.read_text())
        backtest = config.get("backtest", {})
        optimization = backtest.get("optimization", {})
        assert "optimization" in backtest, "PR #150 optimization block missing from config"
        assert optimization.get("target_return_pct") == 25.0
