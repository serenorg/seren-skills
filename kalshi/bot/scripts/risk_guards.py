"""Risk guards for Kalshi trading bot.

Provides three protections:
1. Drawdown detection: flag when portfolio drawdown exceeds limit
2. Position aging: flag positions older than max age
3. Cron auto-pause: pause seren-cron job when funds exhausted
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


def check_drawdown(
    positions: List[Dict[str, Any]],
    bankroll: float,
    max_drawdown_pct: float,
) -> Dict[str, Any]:
    """
    Check if portfolio drawdown exceeds the configured limit.

    Args:
        positions: List of position dicts with 'unrealized_pnl' and 'cost_basis'
        bankroll: Starting bankroll in USD
        max_drawdown_pct: Maximum allowed drawdown percentage (e.g. 15.0 for 15%)

    Returns:
        {
            'triggered': bool,
            'current_drawdown_pct': float,
            'max_drawdown_pct': float,
            'total_pnl': float,
            'current_equity': float,
        }
    """
    if max_drawdown_pct <= 0 or bankroll <= 0:
        return {
            'triggered': False,
            'current_drawdown_pct': 0.0,
            'max_drawdown_pct': max_drawdown_pct,
            'total_pnl': 0.0,
            'current_equity': bankroll,
        }

    total_pnl = sum(float(p.get('unrealized_pnl', 0.0)) for p in positions)
    current_equity = bankroll + total_pnl
    drawdown_pct = ((bankroll - current_equity) / bankroll) * 100.0

    return {
        'triggered': drawdown_pct >= max_drawdown_pct,
        'current_drawdown_pct': round(drawdown_pct, 2),
        'max_drawdown_pct': max_drawdown_pct,
        'total_pnl': round(total_pnl, 4),
        'current_equity': round(current_equity, 4),
    }


def check_position_age(
    positions: List[Dict[str, Any]],
    max_hours: float,
) -> List[str]:
    """
    Return tickers of positions exceeding max_hours age.

    Args:
        positions: List of position dicts with 'ticker' and 'opened_at'
        max_hours: Maximum allowed position age in hours

    Returns:
        List of ticker strings for aged positions
    """
    if max_hours <= 0:
        return []

    now = datetime.now(timezone.utc)
    aged: List[str] = []

    for pos in positions:
        opened_str = pos.get('opened_at', '')
        ticker = pos.get('ticker', '')
        if not opened_str or not ticker:
            continue

        try:
            opened = datetime.fromisoformat(opened_str.replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            age_hours = (now - opened).total_seconds() / 3600.0
            if age_hours >= max_hours:
                aged.append(ticker)
        except (ValueError, TypeError):
            continue

    return aged


def auto_pause_cron(
    balance: float,
    min_balance: float,
    seren_client: Any,
    job_id: str = '',
) -> bool:
    """
    Pause seren-cron job if balance is below minimum threshold.

    Args:
        balance: Current SerenBucks balance
        min_balance: Minimum required balance
        seren_client: SerenClient instance with pause_cron_job method
        job_id: Cron job ID to pause

    Returns:
        True if job was paused
    """
    if not job_id:
        return False

    if balance >= min_balance:
        return False

    try:
        print(
            f"AUTO-PAUSE: SerenBucks ${balance:.2f} < min ${min_balance:.2f}. "
            f"Pausing cron job {job_id}."
        )
        seren_client.pause_cron_job(job_id)
        print(f"Cron job {job_id} paused successfully.")
        return True
    except Exception as exc:
        print(f"Failed to pause cron job {job_id}: {exc}")
        return False


def check_near_resolution(
    positions: List[Dict[str, Any]],
    near_resolution_hours: float = 24.0,
) -> List[str]:
    """
    Return tickers of positions nearing market resolution.

    Args:
        positions: List of position dicts with 'ticker' and 'end_date'
        near_resolution_hours: Hours before resolution to flag

    Returns:
        List of ticker strings for positions near resolution
    """
    if near_resolution_hours <= 0:
        return []

    now = datetime.now(timezone.utc)
    near: List[str] = []

    for pos in positions:
        end_str = pos.get('end_date', '')
        ticker = pos.get('ticker', '')
        if not end_str or not ticker:
            continue

        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            hours_remaining = (end_dt - now).total_seconds() / 3600.0
            if 0 <= hours_remaining <= near_resolution_hours:
                near.append(ticker)
        except (ValueError, TypeError):
            continue

    return near
