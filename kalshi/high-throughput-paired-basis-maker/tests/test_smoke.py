"""Smoke tests for Kalshi High-Throughput Paired Basis Maker.

Five critical tests only:
1. test_config_loads - config.example.json parses correctly
2. test_backtest_dry_run - agent.py backtest mode runs end-to-end with synthetic data
3. test_pair_simulation - pair_stateful_replay produces valid results
4. test_risk_guard_drawdown - drawdown detection triggers unwind
5. test_kalshi_auth - RSA signing produces valid headers
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"
KALSHI_CLIENT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "kalshi_client.py"
PAIR_REPLAY_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pair_stateful_replay.py"
RISK_GUARDS_PATH = Path(__file__).resolve().parents[1] / "scripts" / "risk_guards.py"
CONFIG_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "config.example.json"


def _load_module(name: str, path: Path) -> object:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _synthetic_pair_series(
    points: int = 420,
    start_ts: int | None = None,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Generate synthetic price series that produce basis dislocations."""
    start = start_ts or (int(time.time()) - (points * 3600))
    primary: list[tuple[int, float]] = []
    pair: list[tuple[int, float]] = []
    for i in range(points):
        cycle = i % 4
        if cycle == 0:
            p1, p2 = 0.54, 0.46
        elif cycle == 1:
            p1, p2 = 0.53, 0.47
        elif cycle == 2:
            p1, p2 = 0.515, 0.485
        else:
            p1, p2 = 0.505, 0.495
        primary.append((start + (i * 3600), p1))
        pair.append((start + (i * 3600), p2))
    return primary, pair


# ---------------------------------------------------------------------------
# Test 1: Config loads
# ---------------------------------------------------------------------------

def test_config_loads() -> None:
    """config.example.json parses and contains required sections."""
    assert CONFIG_EXAMPLE_PATH.exists(), f"Missing {CONFIG_EXAMPLE_PATH}"
    config = json.loads(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    assert "execution" in config
    assert "backtest" in config
    assert "strategy" in config
    assert config["execution"]["dry_run"] is True
    assert config["strategy"]["bankroll"] == 1000.0
    assert config["backtest"]["days"] == 270
    assert config["backtest"]["days_min"] == 90
    assert config["backtest"]["days_max"] == 540
    assert config["strategy"]["basis_entry_bps"] == 35
    assert config["strategy"]["pairs_max"] == 10


# ---------------------------------------------------------------------------
# Test 2: Backtest dry run
# ---------------------------------------------------------------------------

def test_backtest_dry_run(monkeypatch) -> None:
    """agent.py backtest mode runs end-to-end with mocked Kalshi data."""
    module = _load_module("kalshi_basis_agent_test", SCRIPT_PATH)

    config = json.loads(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"))
    config["backtest"]["min_events"] = 1

    primary, pair = _synthetic_pair_series()
    synthetic_markets = [
        {
            "market_id": f"KALSHI-M{idx}",
            "pair_market_id": f"KALSHI-P{idx}",
            "question": f"Test market {idx}?",
            "pair_question": f"Test pair {idx}?",
            "event_ticker": f"EVENT-{idx}",
            "end_ts": int(time.time()) + (5 * 24 * 3600),
            "history": primary,
            "pair_history": pair,
        }
        for idx in range(max(config["strategy"]["pairs_max"], 8))
    ]

    monkeypatch.setattr(
        module,
        "_load_backtest_markets",
        lambda p, bt, start_ts, end_ts: (synthetic_markets, "synthetic"),
    )

    output = module.run_backtest(config, None)
    assert output["status"] == "ok"
    assert output["mode"] == "backtest"
    assert output["results"]["starting_bankroll_usd"] == 100
    assert output["results"]["fill_events"] > 0
    assert output["backtest_summary"]["quoted_points"] > 0
    assert output["results"]["return_pct"] >= -100.0
    assert output["results"]["pair_count"] == len(synthetic_markets)
    assert len(output["pairs"]) > 0


# ---------------------------------------------------------------------------
# Test 3: Pair simulation
# ---------------------------------------------------------------------------

def test_pair_simulation() -> None:
    """pair_stateful_replay produces valid results with synthetic data."""
    replay_module = _load_module("kalshi_pair_replay_test", PAIR_REPLAY_PATH)

    primary, pair = _synthetic_pair_series(points=200)
    params = replay_module.PairReplayParams(
        bankroll_usd=100.0,
        basis_entry_bps=35.0,
        basis_exit_bps=10.0,
        min_edge_bps=2.0,
        expected_unwind_cost_bps=2.0,
        expected_convergence_ratio=0.35,
        base_pair_notional_usd=600.0,
        max_notional_per_pair_usd=850.0,
        max_total_notional_usd=2000.0,
        max_leg_notional_usd=900.0,
        participation_rate=0.95,
        min_history_points=30,
        volatility_window_points=24,
    )

    # Build synthetic orderbooks
    books, mode = replay_module.normalize_orderbook_snapshots([], primary, params)
    pair_books, pair_mode = replay_module.normalize_orderbook_snapshots([], pair, params)

    market = {
        "market_id": "TEST-PRIMARY",
        "pair_market_id": "TEST-PAIR",
        "history": primary,
        "pair_history": pair,
        "orderbooks": books,
        "pair_orderbooks": pair_books,
        "orderbook_mode": "synthetic",
        "end_ts": int(time.time()) + 86400,
    }

    result = replay_module.simulate_pair_backtest(market, params, allocated_capital=100.0)

    assert result["market_id"] == "TEST-PRIMARY"
    assert result["pair_market_id"] == "TEST-PAIR"
    assert result["considered_points"] > 0
    assert isinstance(result["equity_curve"], list)
    assert len(result["equity_curve"]) > 1
    assert result["fill_events"] >= 0
    assert isinstance(result["pnl_usd"], float)
    assert result["orderbook_mode"] == "synthetic"


# ---------------------------------------------------------------------------
# Test 4: Risk guard drawdown
# ---------------------------------------------------------------------------

def test_risk_guard_drawdown() -> None:
    """Drawdown detection triggers unwind when threshold exceeded."""
    guards = _load_module("kalshi_risk_guards_test", RISK_GUARDS_PATH)

    unwind_called = False
    unwind_result = {"cancelled_orders": [], "sold_positions": ["TICKER-1"]}

    def mock_unwind():
        nonlocal unwind_called
        unwind_called = True
        return unwind_result

    # Should NOT trigger when drawdown is below threshold
    result = guards.check_drawdown_stop_loss(
        live_risk={"drawdown_pct": 5.0, "current_equity_usd": 950, "peak_equity_usd": 1000},
        max_drawdown_pct=15.0,
        unwind_fn=mock_unwind,
    )
    assert result is None
    assert not unwind_called

    # SHOULD trigger when drawdown exceeds threshold
    result = guards.check_drawdown_stop_loss(
        live_risk={"drawdown_pct": 20.0, "current_equity_usd": 800, "peak_equity_usd": 1000},
        max_drawdown_pct=15.0,
        unwind_fn=mock_unwind,
    )
    assert result is not None
    assert unwind_called
    assert result["sold_positions"] == ["TICKER-1"]

    # Test position age detection
    now = datetime(2026, 4, 8, 12, 0, 0, tzinfo=timezone.utc)
    aged = guards.check_position_age(
        position_timestamps={
            "TICKER-A": "2026-04-05T00:00:00+00:00",  # 84 hours old
            "TICKER-B": "2026-04-08T10:00:00+00:00",  # 2 hours old
        },
        current_exposure={"TICKER-A": 500.0, "TICKER-B": 300.0},
        max_age_hours=72,
        now=now,
    )
    assert "TICKER-A" in aged
    assert "TICKER-B" not in aged


# ---------------------------------------------------------------------------
# Test 5: Kalshi auth signing
# ---------------------------------------------------------------------------

def test_kalshi_auth() -> None:
    """RSA signing headers are produced correctly with a test key."""
    client_module = _load_module("kalshi_client_test", KALSHI_CLIENT_PATH)

    # Test with no key (should produce empty signature)
    client = client_module.KalshiClient(
        api_key="test-api-key-123",
        private_key_pem=None,
        private_key_path=None,
    )
    assert client.api_key == "test-api-key-123"
    assert not client.is_authenticated  # No private key loaded

    # Test signing method returns empty when no key
    sig = client._sign_request("GET", "/markets", 1234567890000)
    assert sig == ""

    # Test auth headers contain required Kalshi fields
    headers = client._auth_headers("GET", "/markets")
    assert "KALSHI-ACCESS-KEY" in headers
    assert headers["KALSHI-ACCESS-KEY"] == "test-api-key-123"
    assert "KALSHI-ACCESS-SIGNATURE" in headers
    assert "KALSHI-ACCESS-TIMESTAMP" in headers
    assert headers["Content-Type"] == "application/json"

    # Verify timestamp is recent (within 5 seconds)
    ts_ms = int(headers["KALSHI-ACCESS-TIMESTAMP"])
    now_ms = int(time.time() * 1000)
    assert abs(now_ms - ts_ms) < 5000

    # Test with actual RSA key if cryptography is available
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        client_with_key = client_module.KalshiClient(
            api_key="test-key",
            private_key_pem=pem,
        )
        assert client_with_key.is_authenticated
        sig = client_with_key._sign_request("GET", "/markets", 1234567890000)
        assert len(sig) > 0  # Should produce a non-empty base64 signature
    except ImportError:
        pass  # cryptography not installed, skip RSA test portion
