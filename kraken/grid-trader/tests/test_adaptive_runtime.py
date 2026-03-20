from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
_MODULES_TO_CLEAR = (
    "adaptive_runtime",
    "agent",
    "grid_manager",
    "logger",
    "pair_selector",
    "position_tracker",
    "seren_client",
    "serendb_store",
)


def _load_local_module(module_name: str):
    script_dir = str(_SCRIPT_DIR)
    sys.path[:] = [script_dir, *[path for path in sys.path if path != script_dir]]
    for cached_name in _MODULES_TO_CLEAR:
        sys.modules.pop(cached_name, None)
    spec = importlib.util.spec_from_file_location(
        f"{Path(__file__).stem}_{module_name}",
        _SCRIPT_DIR / f"{module_name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adaptive_runtime = _load_local_module("adaptive_runtime")


def test_shadow_gate_promotes_better_candidate(tmp_path) -> None:
    settings = adaptive_runtime.resolve_adaptive_settings(
        {
            "adaptive": {
                "state_path": str(tmp_path / "state" / "adaptive_state.json"),
                "metrics_log_path": str(tmp_path / "logs" / "metrics.jsonl"),
                "review_log_path": str(tmp_path / "logs" / "reviews.jsonl"),
                "review_output_dir": str(tmp_path / "logs" / "reviews"),
                "alert_log_path": str(tmp_path / "logs" / "alerts.jsonl"),
                "shadow_min_samples": 2,
                "shadow_improvement_threshold_pct": 0.0,
            }
        }
    )
    store = adaptive_runtime.AdaptiveStateStore(settings)
    store.state["recent_cycles"] = [
        {"market_price": 100.0, "fill_rate": 1.0, "net_pnl_usd": 15.0},
        {"market_price": 102.0, "fill_rate": 0.9, "net_pnl_usd": 12.0},
    ]
    store.state["baseline_summary"] = {"scores": [-0.45], "rolling_score": -0.45}
    store.state["candidate_summary"] = {"scores": [-0.06], "rolling_score": -0.06, "candidate_params": {}}

    decision = adaptive_runtime.compute_adaptive_decision(
        store=store,
        config={
            "strategy": {
                "grid_spacing_percent": 1.0,
                "order_size_percent": 10.0,
                "price_range": {"min": 90.0, "max": 110.0},
            },
            "risk_management": {"max_open_orders": 40},
        },
        market_metrics={
            "mid_price": 105.0,
            "spread_pct": 0.08,
            "atr_pct": 10.0,
            "rolling_stddev_pct": 3.2,
            "regime_tag": "trend_up",
        },
        live_risk={"drawdown_pct": 1.0},
        current_price=105.0,
    )

    assert decision.promoted is True
    assert decision.candidate_score > decision.baseline_score
    assert store.state["last_accepted_params"]["grid_spacing_percent"] == decision.candidate_params["grid_spacing_percent"]


def test_review_report_uses_rolling_50_and_200_windows(tmp_path) -> None:
    settings = adaptive_runtime.resolve_adaptive_settings(
        {
            "adaptive": {
                "state_path": str(tmp_path / "state" / "adaptive_state.json"),
                "metrics_log_path": str(tmp_path / "logs" / "metrics.jsonl"),
                "review_log_path": str(tmp_path / "logs" / "reviews.jsonl"),
                "review_output_dir": str(tmp_path / "logs" / "reviews"),
                "alert_log_path": str(tmp_path / "logs" / "alerts.jsonl"),
            }
        }
    )
    store = adaptive_runtime.AdaptiveStateStore(settings)

    for idx in range(60):
        adaptive_runtime.update_cycle_state(
            store=store,
            cycle_snapshot={
                "timestamp": f"2026-03-20T00:{idx:02d}:00Z",
                "market_price": 100.0 + idx,
                "net_pnl_usd": 1.0 if idx % 2 == 0 else -0.5,
                "equity_end_usd": 1000.0 + idx,
                "fill_count": 1,
                "fill_rate": 0.5,
                "drawdown_pct": 0.2,
                "cancel_rate": 0.1,
                "candidate_score": 0.8,
                "regime_tag": "range",
                "grid_spacing_percent": 2.0,
                "order_size_percent": 5.0,
                "max_open_orders": 40,
                "risk_multiplier": 1.0,
                "dynamic_center_price": 100.0 + idx,
            },
        )

    report = adaptive_runtime.build_review_report(store)

    assert report["cycle_count"] == 60
    assert report["rolling_windows"]["last_50"]["count"] == 50
    assert report["rolling_windows"]["last_200"]["count"] == 60
