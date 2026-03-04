from __future__ import annotations

from scanner import OpportunityScanner


def test_scanner_emits_all_signal_types() -> None:
    scanner = OpportunityScanner(
        min_24h_volume_usd=1_000_000,
        max_reallocation_pct=20.0,
        enabled_signals=["oversold_rsi", "volume_spike", "mean_reversion", "new_listing"],
    )

    rows = [
        {
            "asset": "SOLUSD",
            "price": 80.0,
            "sma20": 100.0,
            "volume_24h_usd": 4_500_000,
            "volume_ratio": 3.5,
            "rsi_14": 22.0,
            "price_change_24h_pct": 4.2,
            "price_change_7d_pct": -12.0,
            "new_listing_days": 12,
            "accumulation_score": 0.8,
        }
    ]
    signals = scanner.scan(rows, {"XBTUSD": 0.6, "ETHUSD": 0.4})
    kinds = {signal.signal_type for signal in signals}

    assert "volume_spike" in kinds
    assert "mean_reversion" in kinds
    assert "oversold_rsi" in kinds
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
            "price": 100.0,
            "sma20": 110.0,
            "volume_24h_usd": 10_000_000,
            "volume_ratio": 6.0,
            "rsi_14": 55.0,
            "price_change_24h_pct": 8.0,
            "price_change_7d_pct": 12.0,
            "new_listing_days": 100,
            "accumulation_score": 0.2,
        }
    ]
    signals = scanner.scan(rows, {"XBTUSD": 1.0})
    assert signals
    assert max(signal.reallocation_pct for signal in signals) <= 10.0
