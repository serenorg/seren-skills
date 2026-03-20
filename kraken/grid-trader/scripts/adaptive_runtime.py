"""Adaptive runtime helpers for kraken/grid-trader."""

from __future__ import annotations

import fcntl
import json
import math
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterator


UTC = timezone.utc
ROUND_TRIP_FEE_PCT = 0.32

DEFAULT_ADAPTIVE_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "state_path": "state/adaptive_state.json",
    "lock_path": "state/runtime.lock",
    "slippage_buffer_pct": 0.2,
    "min_spacing_percent": 0.0,
    "max_spacing_multiplier": 2.5,
    "min_order_size_multiplier": 0.25,
    "max_order_size_multiplier": 1.0,
    "min_risk_multiplier": 0.35,
    "max_risk_multiplier": 1.0,
    "cooldown_after_consecutive_losses": 3,
    "cooldown_minutes": 60,
    "daily_loss_cap_usd": 0.0,
    "shadow_min_samples": 5,
    "shadow_improvement_threshold_pct": 5.0,
    "shadow_rollback_degradation_pct": 10.0,
    "metrics_log_path": "logs/metrics.jsonl",
    "review_log_path": "logs/weekly_reviews.jsonl",
    "review_output_dir": "logs/reviews",
    "alert_log_path": "logs/alerts.jsonl",
    "max_failure_count_before_alert": 3,
}


class RuntimeLockError(RuntimeError):
    """Raised when another adaptive runtime already owns the mutation lock."""


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_append(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def resolve_adaptive_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("adaptive", {})
    settings = dict(DEFAULT_ADAPTIVE_SETTINGS)
    if isinstance(raw, dict):
        settings.update(raw)
    settings["enabled"] = bool(settings.get("enabled", True))
    settings["state_path"] = str(settings.get("state_path", DEFAULT_ADAPTIVE_SETTINGS["state_path"]))
    settings["lock_path"] = str(settings.get("lock_path", DEFAULT_ADAPTIVE_SETTINGS["lock_path"]))
    settings["metrics_log_path"] = str(settings.get("metrics_log_path", DEFAULT_ADAPTIVE_SETTINGS["metrics_log_path"]))
    settings["review_log_path"] = str(settings.get("review_log_path", DEFAULT_ADAPTIVE_SETTINGS["review_log_path"]))
    settings["review_output_dir"] = str(settings.get("review_output_dir", DEFAULT_ADAPTIVE_SETTINGS["review_output_dir"]))
    settings["alert_log_path"] = str(settings.get("alert_log_path", DEFAULT_ADAPTIVE_SETTINGS["alert_log_path"]))
    settings["slippage_buffer_pct"] = _safe_float(settings.get("slippage_buffer_pct"), 0.2)
    settings["min_spacing_percent"] = _safe_float(settings.get("min_spacing_percent"), 0.0)
    settings["max_spacing_multiplier"] = max(_safe_float(settings.get("max_spacing_multiplier"), 2.5), 1.0)
    settings["min_order_size_multiplier"] = _clamp(
        _safe_float(settings.get("min_order_size_multiplier"), 0.25), 0.05, 1.0
    )
    settings["max_order_size_multiplier"] = _clamp(
        _safe_float(settings.get("max_order_size_multiplier"), 1.0), settings["min_order_size_multiplier"], 1.0
    )
    settings["min_risk_multiplier"] = _clamp(_safe_float(settings.get("min_risk_multiplier"), 0.35), 0.05, 1.0)
    settings["max_risk_multiplier"] = _clamp(
        _safe_float(settings.get("max_risk_multiplier"), 1.0),
        settings["min_risk_multiplier"],
        1.0,
    )
    settings["cooldown_after_consecutive_losses"] = max(
        int(_safe_float(settings.get("cooldown_after_consecutive_losses"), 3)),
        1,
    )
    settings["cooldown_minutes"] = max(int(_safe_float(settings.get("cooldown_minutes"), 60)), 1)
    settings["daily_loss_cap_usd"] = max(_safe_float(settings.get("daily_loss_cap_usd"), 0.0), 0.0)
    settings["shadow_min_samples"] = max(int(_safe_float(settings.get("shadow_min_samples"), 5)), 2)
    settings["shadow_improvement_threshold_pct"] = max(
        _safe_float(settings.get("shadow_improvement_threshold_pct"), 5.0), 0.0
    )
    settings["shadow_rollback_degradation_pct"] = max(
        _safe_float(settings.get("shadow_rollback_degradation_pct"), 10.0), 0.0
    )
    settings["max_failure_count_before_alert"] = max(
        int(_safe_float(settings.get("max_failure_count_before_alert"), 3)), 1
    )
    return settings


@contextmanager
def runtime_lock(lock_path: str | Path) -> Iterator[None]:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeLockError(f"another kraken-grid-trader adaptive run is already active ({path})") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "acquired_at": _now_iso()}, sort_keys=True))
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "ema_volatility_pct": 0.0,
        "current_risk_multiplier": 1.0,
        "win_streak": 0,
        "loss_streak": 0,
        "cooldown_until": None,
        "latest_regime_tag": "range",
        "last_accepted_params": {},
        "baseline_summary": {"scores": [], "rolling_score": 0.0},
        "candidate_summary": {"scores": [], "rolling_score": 0.0, "candidate_params": {}},
        "recent_cycles": [],
        "recent_fills": [],
        "known_open_orders": {},
        "parameter_history": [],
        "promotion_history": [],
        "risk_incidents": [],
        "daily_pnl": {"date": None, "equity_start_usd": None, "equity_end_usd": None, "net_change_usd": 0.0},
        "failure_state": {"count": 0, "last_error": "", "last_failure_at": None},
        "review_reports": [],
    }


class AdaptiveStateStore:
    """Persist adaptive trading state across grid-trader restarts."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.path = Path(settings["state_path"])
        self.metrics_log_path = Path(settings["metrics_log_path"])
        self.review_log_path = Path(settings["review_log_path"])
        self.review_output_dir = Path(settings["review_output_dir"])
        self.alert_log_path = Path(settings["alert_log_path"])
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = {}
        except json.JSONDecodeError:
            payload = {}
        state = _default_state()
        if isinstance(payload, dict):
            state.update(payload)
        state.setdefault("recent_cycles", [])
        state.setdefault("recent_fills", [])
        state.setdefault("known_open_orders", {})
        state.setdefault("parameter_history", [])
        state.setdefault("promotion_history", [])
        state.setdefault("risk_incidents", [])
        state.setdefault("review_reports", [])
        state.setdefault("failure_state", {"count": 0, "last_error": "", "last_failure_at": None})
        state.setdefault("daily_pnl", {"date": None, "equity_start_usd": None, "equity_end_usd": None, "net_change_usd": 0.0})
        state.setdefault("baseline_summary", {"scores": [], "rolling_score": 0.0})
        state.setdefault("candidate_summary", {"scores": [], "rolling_score": 0.0, "candidate_params": {}})
        return state

    def save(self) -> None:
        self.state["updated_at"] = _now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, sort_keys=True, indent=2), encoding="utf-8")

    def accepted_params(self, fallback: dict[str, Any]) -> dict[str, Any]:
        params = self.state.get("last_accepted_params")
        if not isinstance(params, dict) or not params:
            params = dict(fallback)
            self.state["last_accepted_params"] = dict(params)
        return dict(params)

    def append_fill(self, fill_event: dict[str, Any]) -> None:
        recent_fills = list(self.state.get("recent_fills", []))
        recent_fills.append(fill_event)
        self.state["recent_fills"] = recent_fills[-200:]

    def append_cycle(self, cycle_snapshot: dict[str, Any]) -> None:
        recent_cycles = list(self.state.get("recent_cycles", []))
        recent_cycles.append(cycle_snapshot)
        self.state["recent_cycles"] = recent_cycles[-200:]
        _json_append(self.metrics_log_path, cycle_snapshot)

    def note_incident(self, incident_type: str, payload: dict[str, Any]) -> None:
        incidents = list(self.state.get("risk_incidents", []))
        incidents.append(
            {
                "incident_type": incident_type,
                "payload": payload,
                "recorded_at": _now_iso(),
            }
        )
        self.state["risk_incidents"] = incidents[-100:]
        _json_append(
            self.alert_log_path,
            {
                "recorded_at": _now_iso(),
                "kind": "risk_incident",
                "incident_type": incident_type,
                "payload": payload,
            },
        )

    def register_failure(self, error_message: str) -> None:
        failure_state = dict(self.state.get("failure_state", {}))
        count = int(failure_state.get("count", 0)) + 1
        failure_state.update(
            {
                "count": count,
                "last_error": error_message,
                "last_failure_at": _now_iso(),
            }
        )
        self.state["failure_state"] = failure_state
        if count >= int(self.settings["max_failure_count_before_alert"]):
            _json_append(
                self.alert_log_path,
                {
                    "recorded_at": _now_iso(),
                    "kind": "repeated_failures",
                    "count": count,
                    "last_error": error_message,
                },
            )

    def clear_failures(self) -> None:
        self.state["failure_state"] = {"count": 0, "last_error": "", "last_failure_at": None}

    def update_daily_pnl(self, *, equity_end_usd: float) -> dict[str, Any]:
        daily = dict(self.state.get("daily_pnl", {}))
        today = _now().date().isoformat()
        start_equity = daily.get("equity_start_usd")
        if daily.get("date") != today or start_equity is None:
            daily = {
                "date": today,
                "equity_start_usd": float(equity_end_usd),
                "equity_end_usd": float(equity_end_usd),
                "net_change_usd": 0.0,
            }
        else:
            daily["equity_end_usd"] = float(equity_end_usd)
            daily["net_change_usd"] = float(equity_end_usd) - float(start_equity)
        self.state["daily_pnl"] = daily
        return daily

    def in_cooldown(self, now: datetime | None = None) -> bool:
        until = _parse_iso(self.state.get("cooldown_until"))
        if until is None:
            return False
        reference = now or _now()
        return reference < until

    def set_cooldown(self, minutes: int) -> None:
        self.state["cooldown_until"] = (_now() + timedelta(minutes=minutes)).isoformat()

    def clear_cooldown(self) -> None:
        self.state["cooldown_until"] = None

    def record_review(self, report: dict[str, Any]) -> Path:
        self.review_output_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.review_output_dir / f"weekly_review_{_now().strftime('%Y%m%dT%H%M%SZ')}.json"
        report_path.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
        _json_append(self.review_log_path, report)
        review_reports = list(self.state.get("review_reports", []))
        review_reports.append({"generated_at": report["generated_at"], "path": str(report_path)})
        self.state["review_reports"] = review_reports[-20:]
        return report_path


@dataclass
class AdaptiveDecision:
    accepted_params: dict[str, Any]
    candidate_params: dict[str, Any]
    dynamic_center_price: float
    dynamic_range: dict[str, float]
    volatility_metrics: dict[str, float]
    regime_tag: str
    reasons: list[str]
    cooldown_active: bool
    baseline_score: float
    candidate_score: float
    promoted: bool
    rolled_back: bool
    daily_loss_triggered: bool


def _window(items: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    return items[-size:]


def _rolling_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return mean(values)


def _rolling_stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _rolling_mean(values)
    variance = sum((value - avg) ** 2 for value in values) / len(values)
    return math.sqrt(max(variance, 0.0))


def _compute_regime(price_history: list[float], rolling_stddev_pct: float) -> str:
    if len(price_history) < 5:
        return "range"
    short = _rolling_mean(price_history[-5:])
    long = _rolling_mean(price_history[-20:] if len(price_history) >= 20 else price_history)
    if long <= 0:
        return "range"
    trend_pct = (short - long) / long * 100.0
    threshold = max(rolling_stddev_pct * 0.35, 0.35)
    if trend_pct >= threshold:
        return "trend_up"
    if trend_pct <= -threshold:
        return "trend_down"
    return "range"


def compute_market_metrics(
    *,
    recent_cycles: list[dict[str, Any]],
    current_price: float,
    bid: float,
    ask: float,
    high: float,
    low: float,
) -> dict[str, float | str]:
    price_history = [_safe_float(item.get("market_price")) for item in recent_cycles if item.get("market_price") is not None]
    price_history.append(float(current_price))
    atr_pct = 0.0
    if current_price > 0:
        atr_pct = max((float(high) - float(low)) / float(current_price) * 100.0, 0.0)
    returns = []
    for previous, current in zip(price_history[:-1], price_history[1:]):
        if previous > 0:
            returns.append((current - previous) / previous * 100.0)
    rolling_stddev_pct = _rolling_stddev(returns)
    regime_tag = _compute_regime(price_history, rolling_stddev_pct)
    mid_price = (float(bid) + float(ask)) / 2.0 if float(ask) > 0 else float(current_price)
    spread_pct = ((float(ask) - float(bid)) / mid_price * 100.0) if mid_price > 0 else 0.0
    return {
        "mid_price": round(mid_price, 6),
        "spread_pct": round(max(spread_pct, 0.0), 6),
        "atr_pct": round(atr_pct, 6),
        "rolling_stddev_pct": round(max(rolling_stddev_pct, 0.0), 6),
        "regime_tag": regime_tag,
    }


def _fee_floor_percent(settings: dict[str, Any]) -> float:
    configured_min = max(_safe_float(settings.get("min_spacing_percent")), 0.0)
    fee_floor = (2.0 * ROUND_TRIP_FEE_PCT) + _safe_float(settings.get("slippage_buffer_pct"), 0.2)
    return max(configured_min, fee_floor)


def _vwap_from_recent_fills(recent_fills: list[dict[str, Any]]) -> float | None:
    samples = recent_fills[-20:]
    total_notional = 0.0
    total_qty = 0.0
    for fill in samples:
        price = _safe_float(fill.get("price"))
        quantity = _safe_float(fill.get("quantity"))
        if price <= 0 or quantity <= 0:
            continue
        total_notional += price * quantity
        total_qty += quantity
    if total_qty <= 0:
        return None
    return total_notional / total_qty


def compute_adaptive_decision(
    *,
    store: AdaptiveStateStore,
    config: dict[str, Any],
    market_metrics: dict[str, float | str],
    live_risk: dict[str, Any],
    current_price: float,
) -> AdaptiveDecision:
    strategy = config.get("strategy", {})
    risk_management = config.get("risk_management", {})
    settings = store.settings
    base_spacing = _safe_float(strategy.get("grid_spacing_percent"), 2.0)
    base_order_size = _safe_float(strategy.get("order_size_percent"), 5.0)
    base_max_open_orders = max(int(_safe_float(risk_management.get("max_open_orders"), 40)), 1)
    fee_floor = _fee_floor_percent(settings)
    rolling_stddev_pct = _safe_float(market_metrics.get("rolling_stddev_pct"))
    atr_pct = _safe_float(market_metrics.get("atr_pct"))
    regime_tag = str(market_metrics.get("regime_tag") or "range")
    drawdown_pct = _safe_float(live_risk.get("drawdown_pct"))
    loss_streak = int(store.state.get("loss_streak", 0))
    win_streak = int(store.state.get("win_streak", 0))

    risk_multiplier = 1.0
    reasons: list[str] = []

    if atr_pct >= 8.0 or rolling_stddev_pct >= 3.0:
        risk_multiplier *= 0.7
        reasons.append("high volatility widened spacing and reduced risk")
    elif atr_pct <= 3.0 and rolling_stddev_pct <= 1.0:
        risk_multiplier *= 1.0
        reasons.append("low volatility allowed tighter spacing")

    if regime_tag != "range":
        risk_multiplier *= 0.85
        reasons.append(f"{regime_tag} regime reduced aggressiveness")

    if drawdown_pct >= 10.0:
        risk_multiplier *= 0.6
        reasons.append("drawdown exceeded 10% so risk was cut sharply")
    elif drawdown_pct >= 5.0:
        risk_multiplier *= 0.8
        reasons.append("drawdown exceeded 5% so risk was reduced")

    if loss_streak > 0:
        risk_multiplier *= 0.85 ** loss_streak
        reasons.append("losing streak applied additional cooldown pressure")
    elif win_streak >= 3 and regime_tag == "range":
        reasons.append("winning streak retained base risk but did not exceed configured caps")

    risk_multiplier = _clamp(
        risk_multiplier,
        _safe_float(settings["min_risk_multiplier"]),
        _safe_float(settings["max_risk_multiplier"]),
    )

    spacing_multiplier = 1.0 + min(max(atr_pct / 10.0, 0.0), 1.5)
    if regime_tag == "range" and atr_pct <= 4.0:
        spacing_multiplier *= 0.9
    if regime_tag != "range":
        spacing_multiplier *= 1.1

    candidate_spacing = _clamp(
        max(base_spacing * spacing_multiplier, fee_floor),
        fee_floor,
        max(base_spacing * _safe_float(settings["max_spacing_multiplier"], 2.5), fee_floor),
    )
    candidate_order_size = _clamp(
        base_order_size * risk_multiplier,
        base_order_size * _safe_float(settings["min_order_size_multiplier"], 0.25),
        base_order_size * _safe_float(settings["max_order_size_multiplier"], 1.0),
    )
    candidate_max_open_orders = max(
        2,
        min(base_max_open_orders, int(round(base_max_open_orders * max(risk_multiplier, 0.5)))),
    )

    recent_fills = list(store.state.get("recent_fills", []))
    dynamic_center_price = _vwap_from_recent_fills(recent_fills) or float(current_price)
    base_min = _safe_float(strategy.get("price_range", {}).get("min"), current_price * 0.9)
    base_max = _safe_float(strategy.get("price_range", {}).get("max"), current_price * 1.1)
    base_half_width = max((base_max - base_min) / 2.0, float(current_price) * 0.05)
    width_multiplier = _clamp(1.0 + (atr_pct / 20.0), 0.8, 1.8)
    dynamic_half_width = max(base_half_width * width_multiplier, float(current_price) * 0.05)
    dynamic_range = {
        "min": round(max(dynamic_center_price - dynamic_half_width, 0.01), 2),
        "max": round(dynamic_center_price + dynamic_half_width, 2),
    }

    baseline_params = store.accepted_params(
        {
            "grid_spacing_percent": base_spacing,
            "order_size_percent": base_order_size,
            "max_open_orders": base_max_open_orders,
            "risk_multiplier": 1.0,
            "dynamic_range": dynamic_range,
        }
    )
    candidate_params = {
        "grid_spacing_percent": round(candidate_spacing, 4),
        "order_size_percent": round(candidate_order_size, 4),
        "max_open_orders": candidate_max_open_orders,
        "risk_multiplier": round(risk_multiplier, 6),
        "dynamic_range": dynamic_range,
    }

    baseline_score = _shadow_score(
        market_metrics=market_metrics,
        drawdown_pct=drawdown_pct,
        params=baseline_params,
        recent_cycles=list(store.state.get("recent_cycles", [])),
    )
    candidate_score = _shadow_score(
        market_metrics=market_metrics,
        drawdown_pct=drawdown_pct,
        params=candidate_params,
        recent_cycles=list(store.state.get("recent_cycles", [])),
    )

    promoted = False
    rolled_back = False
    candidate_summary = dict(store.state.get("candidate_summary", {}))
    baseline_summary = dict(store.state.get("baseline_summary", {}))
    baseline_scores = list(baseline_summary.get("scores", []))
    candidate_scores = list(candidate_summary.get("scores", []))
    baseline_scores.append(round(baseline_score, 6))
    candidate_scores.append(round(candidate_score, 6))
    baseline_scores = baseline_scores[-200:]
    candidate_scores = candidate_scores[-200:]
    baseline_summary["scores"] = baseline_scores
    candidate_summary["scores"] = candidate_scores
    baseline_summary["rolling_score"] = round(_rolling_mean(baseline_scores[-50:]), 6)
    candidate_summary["rolling_score"] = round(_rolling_mean(candidate_scores[-50:]), 6)
    candidate_summary["candidate_params"] = candidate_params

    improvement_threshold = 1.0 + (_safe_float(settings["shadow_improvement_threshold_pct"]) / 100.0)
    rollback_threshold = 1.0 - (_safe_float(settings["shadow_rollback_degradation_pct"]) / 100.0)
    minimum_samples = int(settings["shadow_min_samples"])

    accepted_params = dict(baseline_params)
    if len(candidate_scores) >= minimum_samples:
        baseline_mean = _rolling_mean(baseline_scores[-minimum_samples:])
        candidate_mean = _rolling_mean(candidate_scores[-minimum_samples:])
        if candidate_mean > baseline_mean * improvement_threshold:
            accepted_params = dict(candidate_params)
            promoted = True
            store.state["promotion_history"] = (
                list(store.state.get("promotion_history", []))
                + [
                    {
                        "promoted_at": _now_iso(),
                        "baseline_mean": round(baseline_mean, 6),
                        "candidate_mean": round(candidate_mean, 6),
                        "accepted_params": accepted_params,
                    }
                ]
            )[-50:]
            baseline_summary = {"scores": candidate_scores[-200:], "rolling_score": round(candidate_mean, 6)}
            candidate_summary = {"scores": [], "rolling_score": 0.0, "candidate_params": accepted_params}
            reasons.append("shadow evaluation promoted the adaptive candidate")

    if store.state.get("promotion_history"):
        recent_promotion = store.state["promotion_history"][-1]
        promoted_mean = _safe_float(recent_promotion.get("candidate_mean"), 0.0)
        if promoted_mean > 0 and baseline_summary.get("rolling_score", 0.0) < promoted_mean * rollback_threshold:
            accepted_params = dict(baseline_params)
            rolled_back = True
            reasons.append("candidate rollback triggered after degradation")
            store.state["promotion_history"] = (
                list(store.state.get("promotion_history", []))
                + [
                    {
                        "rolled_back": True,
                        "rolled_back_at": _now_iso(),
                        "promoted_mean": round(promoted_mean, 6),
                        "baseline_rolling_score": round(_safe_float(baseline_summary.get("rolling_score")), 6),
                    }
                ]
            )[-50:]

    daily = store.state.get("daily_pnl", {})
    daily_loss_triggered = False
    daily_loss_cap = _safe_float(settings["daily_loss_cap_usd"])
    if daily_loss_cap > 0 and abs(_safe_float(daily.get("net_change_usd"))) >= daily_loss_cap and _safe_float(daily.get("net_change_usd")) < 0:
        daily_loss_triggered = True
        reasons.append("daily loss cap triggered a trading pause")

    cooldown_active = store.in_cooldown()
    if loss_streak >= int(settings["cooldown_after_consecutive_losses"]):
        store.set_cooldown(int(settings["cooldown_minutes"]))
        cooldown_active = True
        reasons.append("loss streak triggered cooldown")

    store.state["ema_volatility_pct"] = round(
        (_safe_float(store.state.get("ema_volatility_pct")) * 0.7) + (rolling_stddev_pct * 0.3),
        6,
    )
    store.state["current_risk_multiplier"] = round(_safe_float(accepted_params.get("risk_multiplier", risk_multiplier)), 6)
    store.state["latest_regime_tag"] = regime_tag
    store.state["baseline_summary"] = baseline_summary
    store.state["candidate_summary"] = candidate_summary
    store.state["last_accepted_params"] = accepted_params

    return AdaptiveDecision(
        accepted_params=accepted_params,
        candidate_params=candidate_params,
        dynamic_center_price=round(dynamic_center_price, 6),
        dynamic_range=dynamic_range,
        volatility_metrics={
            "atr_pct": round(atr_pct, 6),
            "rolling_stddev_pct": round(rolling_stddev_pct, 6),
            "ema_volatility_pct": _safe_float(store.state.get("ema_volatility_pct")),
        },
        regime_tag=regime_tag,
        reasons=reasons or ["adaptive engine retained prior parameters"],
        cooldown_active=cooldown_active,
        baseline_score=round(baseline_score, 6),
        candidate_score=round(candidate_score, 6),
        promoted=promoted,
        rolled_back=rolled_back,
        daily_loss_triggered=daily_loss_triggered,
    )


def _shadow_score(
    *,
    market_metrics: dict[str, float | str],
    drawdown_pct: float,
    params: dict[str, Any],
    recent_cycles: list[dict[str, Any]],
) -> float:
    spacing = _safe_float(params.get("grid_spacing_percent"))
    risk_multiplier = _safe_float(params.get("risk_multiplier"), 1.0)
    fill_rate = _rolling_mean([_safe_float(item.get("fill_rate")) for item in recent_cycles[-20:] if item.get("fill_rate") is not None])
    recent_net = _rolling_mean([_safe_float(item.get("net_pnl_usd")) for item in recent_cycles[-20:] if item.get("net_pnl_usd") is not None])
    volatility = _safe_float(market_metrics.get("rolling_stddev_pct"))
    regime = str(market_metrics.get("regime_tag") or "range")
    ideal_spacing = max(_safe_float(market_metrics.get("atr_pct")) * 0.35, 0.75)
    spacing_penalty = abs(spacing - ideal_spacing)
    regime_bonus = 1.0 if regime == "range" else -0.5
    return (recent_net * 0.1) + (fill_rate * 5.0) + regime_bonus - (drawdown_pct * 0.35) - spacing_penalty - ((1.0 - risk_multiplier) * 2.0) - volatility


def update_cycle_state(
    *,
    store: AdaptiveStateStore,
    cycle_snapshot: dict[str, Any],
) -> None:
    net_pnl = _safe_float(cycle_snapshot.get("net_pnl_usd"))
    if net_pnl > 0:
        store.state["win_streak"] = int(store.state.get("win_streak", 0)) + 1
        store.state["loss_streak"] = 0
        store.clear_cooldown()
    elif net_pnl < 0:
        store.state["loss_streak"] = int(store.state.get("loss_streak", 0)) + 1
        store.state["win_streak"] = 0
    store.update_daily_pnl(equity_end_usd=_safe_float(cycle_snapshot.get("equity_end_usd")))
    store.append_cycle(cycle_snapshot)
    parameter_history = list(store.state.get("parameter_history", []))
    parameter_history.append(
        {
            "recorded_at": cycle_snapshot.get("timestamp"),
            "regime_tag": cycle_snapshot.get("regime_tag"),
            "grid_spacing_percent": cycle_snapshot.get("grid_spacing_percent"),
            "order_size_percent": cycle_snapshot.get("order_size_percent"),
            "max_open_orders": cycle_snapshot.get("max_open_orders"),
            "risk_multiplier": cycle_snapshot.get("risk_multiplier"),
            "dynamic_center_price": cycle_snapshot.get("dynamic_center_price"),
        }
    )
    store.state["parameter_history"] = parameter_history[-200:]


def build_review_report(store: AdaptiveStateStore) -> dict[str, Any]:
    recent_cycles = list(store.state.get("recent_cycles", []))
    recent_params = list(store.state.get("parameter_history", []))
    recent_incidents = list(store.state.get("risk_incidents", []))
    cycle_count = len(recent_cycles)
    last_50 = _window(recent_cycles, 50)
    report = {
        "generated_at": _now_iso(),
        "cycle_count": cycle_count,
        "rolling_windows": {
            "last_50": summarize_window(last_50),
            "last_200": summarize_window(_window(recent_cycles, 200)),
        },
        "latest_regime_tag": store.state.get("latest_regime_tag", "range"),
        "current_risk_multiplier": store.state.get("current_risk_multiplier", 1.0),
        "accepted_params": dict(store.state.get("last_accepted_params", {})),
        "candidate_summary": dict(store.state.get("candidate_summary", {})),
        "baseline_summary": dict(store.state.get("baseline_summary", {})),
        "recent_parameter_changes": recent_params[-20:],
        "risk_incidents": recent_incidents[-20:],
        "promotion_history": list(store.state.get("promotion_history", []))[-20:],
        "rollback_actions": [
            item
            for item in list(store.state.get("promotion_history", []))[-20:]
            if item.get("rolled_back")
        ],
    }
    return report


def summarize_window(window: list[dict[str, Any]]) -> dict[str, Any]:
    if not window:
        return {
            "count": 0,
            "net_pnl_per_fill": 0.0,
            "fill_rate": 0.0,
            "max_drawdown": 0.0,
            "cancel_rate": 0.0,
            "regime_specific_score": 0.0,
        }
    fill_counts = [_safe_float(item.get("fill_count")) for item in window]
    fill_rates = [_safe_float(item.get("fill_rate")) for item in window]
    net_pnls = [_safe_float(item.get("net_pnl_usd")) for item in window]
    drawdowns = [_safe_float(item.get("drawdown_pct")) for item in window]
    cancel_rates = [_safe_float(item.get("cancel_rate")) for item in window]
    scores = [_safe_float(item.get("candidate_score")) for item in window]
    total_fills = sum(fill_counts)
    return {
        "count": len(window),
        "net_pnl_per_fill": round(sum(net_pnls) / total_fills, 6) if total_fills > 0 else 0.0,
        "fill_rate": round(_rolling_mean(fill_rates), 6),
        "max_drawdown": round(max(drawdowns), 6),
        "cancel_rate": round(_rolling_mean(cancel_rates), 6),
        "regime_specific_score": round(_rolling_mean(scores), 6),
    }
