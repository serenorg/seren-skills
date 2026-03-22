from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


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


class FakeAdaptivePersistence:
    def __init__(self, initial_state=None) -> None:
        self.state = dict(initial_state or {})
        self.events = []
        self.locks = {}

    def load_state(self):
        return json.loads(json.dumps(self.state))

    def save_state(self, state):
        self.state = json.loads(json.dumps(state))

    def append_event(self, event_type, payload):
        ref = f"event:{len(self.events) + 1}"
        self.events.append({"type": event_type, "payload": payload, "reference": ref})
        return ref

    def acquire_lock(self, *, lock_key, owner_id, ttl_seconds):
        del ttl_seconds
        existing = self.locks.get(lock_key)
        if existing is not None and existing != owner_id:
            return False
        self.locks[lock_key] = owner_id
        return True

    def release_lock(self, *, lock_key, owner_id):
        if self.locks.get(lock_key) == owner_id:
            self.locks.pop(lock_key, None)


def test_shadow_gate_promotes_better_candidate() -> None:
    settings = adaptive_runtime.resolve_adaptive_settings(
        {
            "adaptive": {
                "shadow_min_samples": 2,
                "shadow_improvement_threshold_pct": 0.0,
            }
        }
    )
    store = adaptive_runtime.AdaptiveStateStore(settings, persistence=FakeAdaptivePersistence())
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


def test_store_persists_state_and_review_via_backend() -> None:
    persistence = FakeAdaptivePersistence()
    store = adaptive_runtime.AdaptiveStateStore(
        adaptive_runtime.resolve_adaptive_settings({"adaptive": {}}),
        persistence=persistence,
    )
    store.state["last_accepted_params"] = {"grid_spacing_percent": 2.25}
    store.save()
    store.record_metric({"timestamp": "2026-03-20T00:00:00Z", "market_price": 101.0})
    reference = store.record_review({"generated_at": "2026-03-20T00:00:00Z", "cycle_count": 1})
    store.save()

    assert persistence.state["last_accepted_params"]["grid_spacing_percent"] == 2.25
    assert [event["type"] for event in persistence.events] == ["metrics", "review"]
    assert store.state["review_reports"][-1]["reference"] == reference


def test_store_persists_alert_events_via_backend() -> None:
    persistence = FakeAdaptivePersistence()
    store = adaptive_runtime.AdaptiveStateStore(
        adaptive_runtime.resolve_adaptive_settings(
            {"adaptive": {"max_failure_count_before_alert": 2}}
        ),
        persistence=persistence,
    )

    store.note_incident("daily_loss_cap", {"cap_usd": 50.0})
    store.register_failure("kraken request failed")
    store.register_failure("kraken request failed again")
    store.save()

    assert persistence.state["risk_incidents"][-1]["incident_type"] == "daily_loss_cap"
    assert [event["type"] for event in persistence.events] == ["alert", "alert"]
    assert persistence.events[0]["payload"]["kind"] == "risk_incident"
    assert persistence.events[1]["payload"]["kind"] == "repeated_failures"


def test_runtime_lock_uses_backend_lease() -> None:
    persistence = FakeAdaptivePersistence()

    with adaptive_runtime.runtime_lock(
        persistence=persistence,
        lock_key="adaptive:Grid_2026:XBTUSD",
        owner_id="owner-a",
        ttl_seconds=120,
    ):
        assert persistence.locks["adaptive:Grid_2026:XBTUSD"] == "owner-a"
        with pytest.raises(adaptive_runtime.RuntimeLockError):
            with adaptive_runtime.runtime_lock(
                persistence=persistence,
                lock_key="adaptive:Grid_2026:XBTUSD",
                owner_id="owner-b",
                ttl_seconds=120,
            ):
                pass

    assert "adaptive:Grid_2026:XBTUSD" not in persistence.locks


def test_review_report_uses_rolling_50_and_200_windows() -> None:
    store = adaptive_runtime.AdaptiveStateStore(
        adaptive_runtime.resolve_adaptive_settings({"adaptive": {}}),
        persistence=FakeAdaptivePersistence(),
    )

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
