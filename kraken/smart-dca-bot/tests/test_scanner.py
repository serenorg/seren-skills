from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from scanner import OpportunityScanner


def test_scanner_emits_all_signal_types() -> None:
    scanner = OpportunityScanner(
        min_24h_volume_usd=1_000_000,
        max_reallocation_pct=20.0,
        enabled_signals=["volume_spike", "mean_reversion", "momentum_breakout", "new_listing"],
    )

    rows = [
        {
            "asset": "SOLUSD",
            "volume_24h_usd": 4_500_000,
            "volume_ratio": 3.5,
            "rsi_14": 22.0,
            "price_change_24h_pct": 4.2,
            "price_change_7d_pct": -12.0,
            "ma50_breakout": True,
            "new_listing_days": 12,
            "accumulation_score": 0.8,
        }
    ]
    signals = scanner.scan(rows, {"XBTUSD": 0.6, "ETHUSD": 0.4})
    kinds = {signal.signal_type for signal in signals}

    assert "volume_spike" in kinds
    assert "mean_reversion" in kinds
    assert "momentum_breakout" in kinds
    assert "new_listing" in kinds


def test_scanner_caps_reallocation_pct() -> None:
    scanner = OpportunityScanner(
        min_24h_volume_usd=1_000_000,
        max_reallocation_pct=10.0,
        enabled_signals=["volume_spike"],
    )
    rows = [
        {
            "asset": "AVAXUSD",
            "volume_24h_usd": 10_000_000,
            "volume_ratio": 6.0,
            "rsi_14": 55.0,
            "price_change_24h_pct": 8.0,
            "price_change_7d_pct": 12.0,
            "ma50_breakout": True,
            "new_listing_days": 100,
            "accumulation_score": 0.2,
        }
    ]
    signals = scanner.scan(rows, {"XBTUSD": 1.0})
    assert signals
    assert max(signal.reallocation_pct for signal in signals) <= 10.0
