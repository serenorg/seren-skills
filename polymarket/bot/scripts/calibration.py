"""LLM calibration module for Polymarket bot.

Computes data-driven edge thresholds from historical prediction accuracy.
Non-blocking — runs after the scan/trade pipeline completes.

Components:
1. resolution_sweep()  — match unresolved predictions against Gamma closed markets
2. compute_calibration() — compute MAE from resolved predictions
3. load_calibration() / save_calibration() — local state/calibration.json cache
4. effective_threshold() — returns max(config_threshold, calibrated_threshold)
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# Minimum resolved predictions before calibration takes effect
MIN_RESOLVED_FOR_CALIBRATION = 50

# Added to MAE to account for CLOB spread cost and safety margin
SPREAD_COST = 0.03
SAFETY_MARGIN = 0.02

STATE_DIR = Path(__file__).resolve().parents[1] / "state"
CALIBRATION_FILE = STATE_DIR / "calibration.json"


def load_calibration() -> Optional[Dict[str, Any]]:
    """Load calibration from local cache. Returns None if not available."""
    try:
        if CALIBRATION_FILE.exists():
            return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return None


def save_calibration(cal: Dict[str, Any]) -> None:
    """Write calibration to local cache."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CALIBRATION_FILE.write_text(
        json.dumps(cal, indent=2, default=str), encoding="utf-8"
    )


def effective_threshold(config_threshold: float, calibration: Optional[Dict[str, Any]] = None) -> tuple[float, str]:
    """Return (effective_threshold, reason) based on config and calibration.

    The calibration can only RAISE the threshold above config, never lower it.
    """
    if calibration is None:
        calibration = load_calibration()

    if not calibration:
        return config_threshold, "config (no calibration data)"

    resolved = calibration.get("resolved_count", 0)
    if resolved < MIN_RESOLVED_FOR_CALIBRATION:
        return config_threshold, f"config (calibration: {resolved}/{MIN_RESOLVED_FOR_CALIBRATION} markets resolved)"

    cal_threshold = calibration.get("calibrated_threshold", 0.0)
    if cal_threshold > config_threshold:
        return cal_threshold, (
            f"calibrated from {resolved} resolved markets "
            f"(MAE {calibration.get('median_absolute_error', 0)*100:.1f}% "
            f"+ {SPREAD_COST*100:.0f}% spread + {SAFETY_MARGIN*100:.0f}% safety)"
        )

    return config_threshold, f"config (calibrated {cal_threshold*100:.1f}% is lower)"


def resolution_sweep(polymarket_client: Any, storage: Any) -> int:
    """Sweep recently resolved markets and update prediction outcomes.

    Queries Gamma for closed markets, matches against unresolved predictions
    in SerenDB, and writes resolution outcomes.

    Returns number of predictions resolved.
    """
    if storage is None:
        return 0

    try:
        unresolved = storage.get_unresolved_predictions()
    except Exception:
        return 0

    if not unresolved:
        return 0

    # Build lookup of unresolved market_ids
    # Handle both dict rows and list rows
    unresolved_by_id: Dict[str, Dict] = {}
    for pred in unresolved:
        if isinstance(pred, dict):
            mid = pred.get("market_id", "")
        elif isinstance(pred, (list, tuple)):
            mid = pred[0] if pred else ""
        else:
            continue
        if mid:
            unresolved_by_id[mid] = pred

    if not unresolved_by_id:
        return 0

    # Fetch recently closed markets from Gamma
    try:
        response = polymarket_client.seren.call_publisher(
            publisher="polymarket-data",
            method="GET",
            path="/markets?limit=200&closed=true&active=false",
        )
        closed_markets = response.get("body", [])
        if not closed_markets and "data" in response:
            closed_markets = response.get("data", [])
    except Exception:
        return 0

    resolved_count = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for market in closed_markets:
        condition_id = market.get("conditionId") or market.get("id", "")
        if condition_id not in unresolved_by_id:
            continue

        # Determine resolution outcome from outcomePrices
        # Resolved markets have prices at 1.0/0.0 (or very close)
        raw_prices = market.get("outcomePrices")
        if not raw_prices:
            continue

        try:
            if isinstance(raw_prices, str):
                prices = json.loads(raw_prices)
            else:
                prices = raw_prices
            yes_price = float(str(prices[0]).strip())
        except (json.JSONDecodeError, IndexError, ValueError, TypeError):
            continue

        # Resolved YES = price ~1.0, resolved NO = price ~0.0
        if yes_price > 0.95:
            outcome = "YES"
            actual_prob = 1.0
        elif yes_price < 0.05:
            outcome = "NO"
            actual_prob = 0.0
        else:
            continue  # Not fully resolved yet

        try:
            storage.update_prediction_resolution(
                market_id=condition_id,
                resolution_outcome=outcome,
                resolution_timestamp=now_iso,
                actual_probability=actual_prob,
            )
            resolved_count += 1
        except Exception:
            continue

    return resolved_count


def compute_calibration(storage: Any) -> Optional[Dict[str, Any]]:
    """Compute calibration metrics from resolved predictions.

    Returns calibration dict if enough data, None otherwise.
    """
    if storage is None:
        return None

    try:
        resolved = storage.get_resolved_predictions(limit=5000)
    except Exception:
        return None

    if not resolved or len(resolved) < MIN_RESOLVED_FOR_CALIBRATION:
        return None

    # Compute MAE per confidence bucket
    errors_all: List[float] = []
    errors_by_confidence: Dict[str, List[float]] = {"low": [], "medium": [], "high": []}

    for pred in resolved:
        if isinstance(pred, dict):
            fv = pred.get("predicted_fair_value")
            actual = pred.get("actual_probability")
            confidence = pred.get("confidence", "medium")
        elif isinstance(pred, (list, tuple)):
            # Fallback for list-style rows — positions depend on schema
            continue
        else:
            continue

        if fv is None or actual is None:
            continue

        try:
            error = abs(float(fv) - float(actual))
        except (ValueError, TypeError):
            continue

        errors_all.append(error)
        bucket = str(confidence).lower() if confidence else "medium"
        if bucket in errors_by_confidence:
            errors_by_confidence[bucket].append(error)

    if len(errors_all) < MIN_RESOLVED_FOR_CALIBRATION:
        return None

    mae = statistics.median(errors_all)
    calibrated_threshold = mae + SPREAD_COST + SAFETY_MARGIN

    cal = {
        "resolved_count": len(errors_all),
        "median_absolute_error": round(mae, 4),
        "calibrated_threshold": round(calibrated_threshold, 4),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Per-confidence MAE (only if bucket has data)
    for bucket, errors in errors_by_confidence.items():
        if errors:
            cal[f"{bucket}_confidence_mae"] = round(statistics.median(errors), 4)
            cal[f"{bucket}_confidence_count"] = len(errors)

    # Save to local cache
    save_calibration(cal)

    # Save to SerenDB
    try:
        storage.save_performance_metrics({
            "calculated_at": cal["computed_at"],
            "total_predictions": len(errors_all),
            "resolved_predictions": len(errors_all),
            "avg_brier_score": None,
            "calibration_slope": None,
            "calibration_intercept": None,
            "edge_threshold": calibrated_threshold,
        })
    except Exception:
        pass  # Local cache is sufficient

    return cal


def print_calibration_report(cal: Optional[Dict[str, Any]], config_threshold: float) -> None:
    """Print calibration summary after scan."""
    if not cal:
        unresolved_count = cal.get("resolved_count", 0) if cal else 0
        print(f"Calibration: {unresolved_count}/{MIN_RESOLVED_FOR_CALIBRATION} markets resolved "
              f"(need {MIN_RESOLVED_FOR_CALIBRATION - unresolved_count} more for data-driven threshold)")
        return

    resolved = cal.get("resolved_count", 0)
    if resolved < MIN_RESOLVED_FOR_CALIBRATION:
        print(f"Calibration: {resolved}/{MIN_RESOLVED_FOR_CALIBRATION} markets resolved "
              f"(need {MIN_RESOLVED_FOR_CALIBRATION - resolved} more for data-driven threshold)")
        return

    mae = cal.get("median_absolute_error", 0)
    cal_thresh = cal.get("calibrated_threshold", 0)
    eff, _ = effective_threshold(config_threshold, cal)

    print(f"LLM Calibration (N={resolved} resolved):")
    print(f"  Overall MAE: {mae*100:.1f}%")

    for bucket in ("high", "medium", "low"):
        bucket_mae = cal.get(f"{bucket}_confidence_mae")
        bucket_n = cal.get(f"{bucket}_confidence_count", 0)
        if bucket_mae is not None:
            print(f"  {bucket.capitalize()}-confidence MAE: {bucket_mae*100:.1f}% (N={bucket_n})")

    print(f"  Calibrated threshold: {cal_thresh*100:.1f}% "
          f"(MAE {mae*100:.1f}% + {SPREAD_COST*100:.0f}% spread + {SAFETY_MARGIN*100:.0f}% safety)")
    print(f"  Config threshold: {config_threshold*100:.1f}%")
    print(f"  Effective threshold: {eff*100:.1f}% ({'calibrated' if eff > config_threshold else 'config'})")


def run_post_scan_calibration(
    polymarket_client: Any,
    storage: Any,
    config_threshold: float,
) -> Optional[Dict[str, Any]]:
    """Non-blocking post-scan calibration pipeline.

    Runs resolution sweep + calibration computation + report.
    Returns calibration dict if computed, None otherwise.
    """
    print()
    print("--- Post-scan calibration ---")

    # Step 1: Resolution sweep
    try:
        resolved_count = resolution_sweep(polymarket_client, storage)
        if resolved_count > 0:
            print(f"  Resolved {resolved_count} predictions from closed markets")
    except Exception as e:
        print(f"  Resolution sweep failed: {e}")

    # Step 2: Compute calibration
    cal = None
    try:
        cal = compute_calibration(storage)
    except Exception as e:
        print(f"  Calibration computation failed: {e}")

    # Step 3: Print report
    if cal is None:
        cal = load_calibration()  # Try local cache
    print_calibration_report(cal, config_threshold)

    return cal
