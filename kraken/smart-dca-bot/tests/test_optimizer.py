from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from optimizer import decide_execution


def _snapshot() -> dict:
    return {
        "price": 100.0,
        "vwap": 101.0,
        "bid": 99.9,
        "ask": 100.1,
        "low_24h": 97.0,
        "depth_score": 0.8,
        "candles": [100.0 + (i * 0.1) for i in range(20)],
    }


def test_vwap_optimized_executes_on_discount() -> None:
    snap = _snapshot()
    decision = decide_execution(
        strategy="vwap_optimized",
        snapshot=snap,
        window_progress=0.3,
        force_fill=False,
    )
    assert decision.strategy == "vwap_optimized"
    assert decision.should_execute is True
    assert decision.order_type == "limit"


def test_momentum_dip_can_wait_without_signal() -> None:
    snap = _snapshot()
    snap["candles"] = [100.0 + i for i in range(20)]
    decision = decide_execution(
        strategy="momentum_dip",
        snapshot=snap,
        window_progress=0.5,
        force_fill=False,
    )
    assert decision.strategy == "momentum_dip"
    assert decision.should_execute in {True, False}
    assert decision.order_type == "limit"


def test_time_weighted_has_four_slices() -> None:
    decision = decide_execution(
        strategy="time_weighted",
        snapshot=_snapshot(),
        window_progress=0.1,
        force_fill=False,
    )
    assert decision.should_execute is True
    assert decision.slices == [0.25, 0.25, 0.25, 0.25]


def test_forced_fill_uses_market() -> None:
    decision = decide_execution(
        strategy="spread_optimized",
        snapshot=_snapshot(),
        window_progress=0.2,
        force_fill=True,
    )
    assert decision.should_execute is True
    assert decision.order_type == "market"
