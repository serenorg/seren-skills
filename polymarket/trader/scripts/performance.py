"""
Performance Analysis - Calculate prediction accuracy and trading performance metrics

Provides functions for:
- Brier score calculation
- Calibration curve analysis
- Adaptive threshold adjustment
- Market resolution checking
"""

from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime
import math


def calculate_brier_score(predictions: List[Dict[str, Any]]) -> Optional[float]:
    """
    Calculate average Brier score for resolved predictions

    Brier Score = (1/N) * Σ(predicted_probability - actual_outcome)²

    Lower is better (0 = perfect predictions, 1 = worst possible)

    Args:
        predictions: List of prediction dicts with 'predicted_fair_value'
                    and 'actual_probability' keys

    Returns:
        Average Brier score, or None if no valid predictions
    """
    if not predictions:
        return None

    # Filter predictions with valid resolution
    valid_predictions = [
        p for p in predictions
        if p.get('actual_probability') is not None
        and p.get('brier_score') is not None
    ]

    if not valid_predictions:
        return None

    # Average the individual Brier scores
    total_brier = sum(p['brier_score'] for p in valid_predictions)
    return total_brier / len(valid_predictions)


def calculate_calibration_curve(
    predictions: List[Dict[str, Any]],
    n_bins: int = 10
) -> Dict[str, Any]:
    """
    Calculate calibration curve by probability bucket

    Groups predictions into probability bins and compares predicted vs actual rates

    Args:
        predictions: List of prediction dicts
        n_bins: Number of probability bins (default 10 for deciles)

    Returns:
        Dict with:
            - bins: List of bin ranges
            - predicted_probs: Average predicted probability per bin
            - actual_rates: Actual outcome rate per bin
            - counts: Number of predictions per bin
            - slope: Linear regression slope (1.0 = perfect calibration)
            - intercept: Linear regression intercept (0.0 = perfect calibration)
    """
    # Filter valid predictions
    valid_predictions = [
        p for p in predictions
        if p.get('actual_probability') is not None
        and p.get('predicted_fair_value') is not None
    ]

    if not valid_predictions:
        return {
            'bins': [],
            'predicted_probs': [],
            'actual_rates': [],
            'counts': [],
            'slope': None,
            'intercept': None
        }

    # Create bins
    bin_size = 1.0 / n_bins
    bins = []
    predicted_probs = []
    actual_rates = []
    counts = []

    for i in range(n_bins):
        bin_start = i * bin_size
        bin_end = (i + 1) * bin_size

        # Get predictions in this bin
        bin_predictions = [
            p for p in valid_predictions
            if bin_start <= p['predicted_fair_value'] < bin_end
            or (i == n_bins - 1 and p['predicted_fair_value'] == 1.0)  # Include 1.0 in last bin
        ]

        if bin_predictions:
            bins.append((bin_start, bin_end))
            predicted_probs.append(
                sum(p['predicted_fair_value'] for p in bin_predictions) / len(bin_predictions)
            )
            actual_rates.append(
                sum(p['actual_probability'] for p in bin_predictions) / len(bin_predictions)
            )
            counts.append(len(bin_predictions))

    # Calculate linear regression for calibration
    slope, intercept = None, None
    if len(predicted_probs) >= 2:
        slope, intercept = _linear_regression(predicted_probs, actual_rates)

    return {
        'bins': bins,
        'predicted_probs': predicted_probs,
        'actual_rates': actual_rates,
        'counts': counts,
        'slope': slope,
        'intercept': intercept
    }


def _linear_regression(x: List[float], y: List[float]) -> Tuple[float, float]:
    """
    Simple linear regression: y = slope * x + intercept

    Args:
        x: List of x values
        y: List of y values (same length as x)

    Returns:
        (slope, intercept)
    """
    n = len(x)
    if n < 2:
        return 0.0, 0.0

    # Calculate means
    x_mean = sum(x) / n
    y_mean = sum(y) / n

    # Calculate slope
    numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
    denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return 0.0, y_mean

    slope = numerator / denominator
    intercept = y_mean - slope * x_mean

    return slope, intercept


def adjust_kelly_multiplier(
    current_multiplier: float,
    metrics: Dict[str, Any],
    min_multiplier: float = 0.1,
    max_multiplier: float = 0.5,
    default_multiplier: float = 0.25
) -> float:
    """
    Adjust Kelly multiplier based on prediction performance

    Logic:
    - If Brier score is good (<= 0.1), increase multiplier (more aggressive)
    - If Brier score is poor (>= 0.2), decrease multiplier (more conservative)
    - If calibration slope < 1.0 (overconfident), decrease multiplier
    - If ROI is negative, decrease multiplier

    Args:
        current_multiplier: Current Kelly multiplier
        metrics: Performance metrics dict
        min_multiplier: Minimum allowed multiplier
        max_multiplier: Maximum allowed multiplier
        default_multiplier: Default to use if no data

    Returns:
        Adjusted Kelly multiplier
    """
    # Need at least some resolved predictions to adjust
    resolved_predictions = metrics.get('resolved_predictions', 0)
    if resolved_predictions < 10:
        return default_multiplier

    brier_score = metrics.get('avg_brier_score')
    calibration_slope = metrics.get('calibration_slope')
    roi_percentage = metrics.get('roi_percentage', 0.0)

    # Start with current multiplier
    new_multiplier = current_multiplier

    # Adjust based on Brier score
    if brier_score is not None:
        if brier_score <= 0.1:
            # Excellent predictions - increase aggression
            new_multiplier *= 1.1
        elif brier_score >= 0.2:
            # Poor predictions - decrease aggression
            new_multiplier *= 0.9

    # Adjust based on calibration
    if calibration_slope is not None:
        if calibration_slope < 0.9:
            # Overconfident - be more conservative
            new_multiplier *= 0.9
        elif calibration_slope > 1.1:
            # Underconfident - can be more aggressive
            new_multiplier *= 1.05

    # Adjust based on ROI
    if roi_percentage < -5.0:
        # Losing money - be more conservative
        new_multiplier *= 0.85
    elif roi_percentage > 10.0:
        # Making good money - can be more aggressive
        new_multiplier *= 1.05

    # Clamp to min/max
    new_multiplier = max(min_multiplier, min(max_multiplier, new_multiplier))

    return round(new_multiplier, 3)


def adjust_edge_threshold(
    current_threshold: float,
    metrics: Dict[str, Any],
    min_threshold: float = 0.02,
    max_threshold: float = 0.20,
    default_threshold: float = 0.05
) -> float:
    """
    Adjust edge threshold based on prediction accuracy

    Logic:
    - If Brier score is poor, increase threshold (be more selective)
    - If calibration is off, increase threshold
    - If losing money, increase threshold

    Args:
        current_threshold: Current edge threshold
        metrics: Performance metrics dict
        min_threshold: Minimum allowed threshold (2%)
        max_threshold: Maximum allowed threshold (20%)
        default_threshold: Default to use if no data (5%)

    Returns:
        Adjusted edge threshold
    """
    # Need at least some resolved predictions to adjust
    resolved_predictions = metrics.get('resolved_predictions', 0)
    if resolved_predictions < 10:
        return default_threshold

    brier_score = metrics.get('avg_brier_score')
    calibration_slope = metrics.get('calibration_slope')
    roi_percentage = metrics.get('roi_percentage', 0.0)

    # Start with current threshold
    new_threshold = current_threshold

    # Adjust based on Brier score
    if brier_score is not None:
        if brier_score <= 0.1:
            # Excellent predictions - can lower threshold
            new_threshold *= 0.95
        elif brier_score >= 0.2:
            # Poor predictions - increase threshold
            new_threshold *= 1.1

    # Adjust based on calibration
    if calibration_slope is not None:
        if abs(calibration_slope - 1.0) > 0.2:
            # Poor calibration - increase threshold
            new_threshold *= 1.05

    # Adjust based on ROI
    if roi_percentage < -5.0:
        # Losing money - increase threshold
        new_threshold *= 1.15
    elif roi_percentage > 15.0:
        # Making good money - can lower threshold
        new_threshold *= 0.95

    # Clamp to min/max
    new_threshold = max(min_threshold, min(max_threshold, new_threshold))

    return round(new_threshold, 4)


def calculate_win_rate(resolved_markets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate win/loss statistics from resolved markets

    Args:
        resolved_markets: List of resolved market dicts with P&L data

    Returns:
        Dict with:
            - total_trades: Number of traded markets
            - winning_trades: Number of profitable trades
            - losing_trades: Number of unprofitable trades
            - win_rate: Percentage of winning trades
            - total_pnl: Sum of all realized P&L
            - avg_win: Average profit on winning trades
            - avg_loss: Average loss on losing trades
            - profit_factor: Ratio of gross wins to gross losses
    """
    # Filter to only traded markets
    traded_markets = [m for m in resolved_markets if m.get('traded', False)]

    if not traded_markets:
        return {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'profit_factor': 0.0
        }

    winning_trades = [m for m in traded_markets if (m.get('realized_pnl') or 0.0) > 0]
    losing_trades = [m for m in traded_markets if (m.get('realized_pnl') or 0.0) < 0]

    total_pnl = sum(m.get('realized_pnl', 0.0) for m in traded_markets)
    total_wins = sum(m.get('realized_pnl', 0.0) for m in winning_trades)
    total_losses = abs(sum(m.get('realized_pnl', 0.0) for m in losing_trades))

    win_rate = len(winning_trades) / len(traded_markets) * 100 if traded_markets else 0.0
    avg_win = total_wins / len(winning_trades) if winning_trades else 0.0
    avg_loss = total_losses / len(losing_trades) if losing_trades else 0.0
    profit_factor = total_wins / total_losses if total_losses > 0 else 0.0

    return {
        'total_trades': len(traded_markets),
        'winning_trades': len(winning_trades),
        'losing_trades': len(losing_trades),
        'win_rate': round(win_rate, 2),
        'total_pnl': round(total_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(profit_factor, 2)
    }


def summarize_performance(
    predictions: List[Dict[str, Any]],
    resolved_markets: List[Dict[str, Any]],
    current_kelly: float = 0.25,
    current_threshold: float = 0.05
) -> Dict[str, Any]:
    """
    Generate comprehensive performance summary with recommendations

    Args:
        predictions: List of all predictions (resolved and unresolved)
        resolved_markets: List of resolved market outcomes
        current_kelly: Current Kelly multiplier
        current_threshold: Current edge threshold

    Returns:
        Dict with all metrics plus recommended adjustments
    """
    resolved_predictions = [p for p in predictions if p.get('resolution_outcome') is not None]

    # Calculate metrics
    brier_score = calculate_brier_score(resolved_predictions)
    calibration = calculate_calibration_curve(resolved_predictions)
    win_stats = calculate_win_rate(resolved_markets)

    # Build metrics dict
    metrics = {
        'total_predictions': len(predictions),
        'resolved_predictions': len(resolved_predictions),
        'avg_brier_score': brier_score,
        'calibration_slope': calibration.get('slope'),
        'calibration_intercept': calibration.get('intercept'),
        'total_trades': win_stats['total_trades'],
        'winning_trades': win_stats['winning_trades'],
        'total_realized_pnl': win_stats['total_pnl'],
        'roi_percentage': (win_stats['total_pnl'] / win_stats['total_trades'] * 100
                          if win_stats['total_trades'] > 0 else 0.0),
        'kelly_multiplier': current_kelly,
        'edge_threshold': current_threshold,
        'calculated_at': datetime.utcnow().isoformat() + 'Z'
    }

    # Calculate recommended adjustments
    recommended_kelly = adjust_kelly_multiplier(current_kelly, metrics)
    recommended_threshold = adjust_edge_threshold(current_threshold, metrics)

    # Add recommendations
    metrics['recommended_kelly_multiplier'] = recommended_kelly
    metrics['recommended_edge_threshold'] = recommended_threshold
    metrics['kelly_adjustment'] = recommended_kelly - current_kelly
    metrics['threshold_adjustment'] = recommended_threshold - current_threshold

    # Add full calibration data and win stats
    metrics['calibration'] = calibration
    metrics['win_stats'] = win_stats

    return metrics
