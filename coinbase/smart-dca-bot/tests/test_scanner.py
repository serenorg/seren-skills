from __future__ import annotations

from scanner import OpportunityScanner


def test_scanner_emits_all_signal_types() -> None:
    scanner = OpportunityScanner(
        min_24h_volume_usd=1_000_000,
        max_reallocation_pct=20.0,
        enabled_signals=["oversold_rsi", "volume_spike", "mean_reversion", "new_listing", "learn_earn"],
        min_market_cap_usd=100_000_000,
        require_coinbase_verified=True,
    )

    rows = [
        {
            "asset": "SOL-USD",
            "price": 80.0,
            "sma20": 100.0,
            "volume_24h_usd": 4_500_000,
            "volume_ratio": 3.5,
            "rsi_14": 22.0,
            "price_change_24h_pct": 4.2,
            "price_change_7d_pct": -12.0,
            "new_listing_days": 12,
            "accumulation_score": 0.8,
            "market_cap_usd": 5_000_000_000,
            "coinbase_verified": True,
            "learn_reward_usd": 3.0,
        }
    ]
    signals = scanner.scan(rows, {"BTC-USD": 0.6, "ETH-USD": 0.4})
    kinds = {signal.signal_type for signal in signals}

    assert "volume_spike" in kinds
    assert "mean_reversion" in kinds
    assert "oversold_rsi" in kinds
    assert "new_listing" in kinds
    assert "learn_earn" in kinds


def test_scanner_caps_reallocation_pct() -> None:
    scanner = OpportunityScanner(
        min_24h_volume_usd=1_000_000,
        max_reallocation_pct=10.0,
        enabled_signals=["volume_spike"],
    )
    rows = [
        {
            "asset": "AVAX-USD",
            "price": 100.0,
            "sma20": 110.0,
            "volume_24h_usd": 10_000_000,
            "volume_ratio": 6.0,
            "rsi_14": 55.0,
            "price_change_24h_pct": 8.0,
            "price_change_7d_pct": 12.0,
            "new_listing_days": 100,
            "accumulation_score": 0.2,
            "market_cap_usd": 800_000_000,
            "coinbase_verified": True,
            "learn_reward_usd": 0.0,
        }
    ]
    signals = scanner.scan(rows, {"BTC-USD": 1.0})
    assert signals
    assert max(signal.reallocation_pct for signal in signals) <= 10.0


def test_scanner_respects_coinbase_verified_filter() -> None:
    scanner = OpportunityScanner(
        min_24h_volume_usd=1_000_000,
        max_reallocation_pct=20.0,
        enabled_signals=["learn_earn", "volume_spike"],
        min_market_cap_usd=100_000_000,
        require_coinbase_verified=True,
    )
    rows = [
        {
            "asset": "ALT-USD",
            "price": 10.0,
            "sma20": 12.0,
            "volume_24h_usd": 4_000_000,
            "volume_ratio": 4.0,
            "rsi_14": 25.0,
            "price_change_24h_pct": -7.0,
            "price_change_7d_pct": -14.0,
            "new_listing_days": 20,
            "accumulation_score": 0.7,
            "market_cap_usd": 500_000_000,
            "coinbase_verified": False,
            "learn_reward_usd": 5.0,
        }
    ]
    assert scanner.scan(rows, {"BTC-USD": 1.0}) == []
