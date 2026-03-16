from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_local_module(module_name: str):
    script_dir = str(_SCRIPT_DIR)
    sys.path[:] = [script_dir, *[path for path in sys.path if path != script_dir]]
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        f"{Path(__file__).stem}_{module_name}",
        _SCRIPT_DIR / f"{module_name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OpportunityScanner = _load_local_module("scanner").OpportunityScanner


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
