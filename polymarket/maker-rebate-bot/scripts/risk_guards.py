"""Risk guards for Polymarket trading skills.

Provides three protections:
1. Drawdown stop-loss: auto-unwind when drawdown exceeds backtest max drawdown
2. Position aging: force-close positions older than max age
3. Cron auto-pause: pause seren-cron job when funds exhausted
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


def check_drawdown_stop_loss(
    *,
    live_risk: dict[str, Any],
    max_drawdown_pct: float,
    unwind_fn: Callable[[], dict[str, Any]],
    log_fn: Callable[[str], None] = print,
) -> dict[str, Any] | None:
    """Trigger unwind if live drawdown exceeds the configured limit.

    Returns unwind result dict if triggered, None otherwise.
    """
    if max_drawdown_pct <= 0:
        return None

    current_dd_pct = float(live_risk.get("drawdown_pct", 0.0))
    if current_dd_pct < max_drawdown_pct:
        return None

    log_fn(
        f"DRAWDOWN STOP-LOSS TRIGGERED: {current_dd_pct:.2f}% >= limit "
        f"{max_drawdown_pct:.2f}%. Equity: "
        f"${live_risk.get('current_equity_usd', 0):.2f}, "
        f"Peak: ${live_risk.get('peak_equity_usd', 0):.2f}. "
        f"Unwinding all positions."
    )
    return unwind_fn()


def check_position_age(
    *,
    position_timestamps: dict[str, str],
    current_exposure: dict[str, float],
    max_age_hours: float,
    now: datetime | None = None,
) -> list[str]:
    """Return token_ids whose positions exceed max_age_hours.

    position_timestamps: {token_id: ISO-8601 string}
    current_exposure:    {token_id: notional_usd}
    """
    if max_age_hours <= 0:
        return []
    if now is None:
        now = datetime.now(timezone.utc)

    aged: list[str] = []
    for token_id, notional in current_exposure.items():
        if notional <= 0:
            continue
        opened_str = position_timestamps.get(token_id)
        if not opened_str:
            continue
        try:
            opened = datetime.fromisoformat(opened_str.replace("Z", "+00:00"))
            if (now - opened).total_seconds() / 3600.0 >= max_age_hours:
                aged.append(token_id)
        except (ValueError, TypeError):
            continue
    return aged


def sync_position_timestamps(
    *,
    position_timestamps: dict[str, str],
    current_exposure: dict[str, float],
    now: datetime | None = None,
) -> dict[str, str]:
    """Keep position_timestamps in sync with live exposure.

    - New positions get the current timestamp.
    - Closed positions are pruned.
    - Existing positions keep their original timestamp.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    return {
        token_id: position_timestamps.get(token_id, now_iso)
        for token_id, notional in current_exposure.items()
        if notional > 0
    }


def auto_pause_cron(
    *,
    serenbucks_balance: float | None,
    trading_balance: float | None,
    min_serenbucks: float = 1.0,
    min_trading_balance: float = 0.0,
    job_id: str | None = None,
    pause_fn: Callable[[str], Any] | None = None,
    log_fn: Callable[[str], None] = print,
) -> bool:
    """Pause the seren-cron job if balances are below thresholds.

    Returns True if the job was paused.
    """
    if not job_id or not pause_fn:
        return False

    reason: str | None = None
    if serenbucks_balance is not None and serenbucks_balance < min_serenbucks:
        reason = (
            f"SerenBucks ${serenbucks_balance:.2f} < min ${min_serenbucks:.2f}"
        )
    elif (
        trading_balance is not None
        and min_trading_balance > 0
        and trading_balance < min_trading_balance
    ):
        reason = (
            f"Trading balance ${trading_balance:.2f} < min "
            f"${min_trading_balance:.2f}"
        )

    if reason is None:
        return False

    log_fn(f"AUTO-PAUSE: {reason}. Pausing cron job {job_id}.")
    try:
        pause_fn(job_id)
        log_fn(f"Cron job {job_id} paused successfully.")
        return True
    except Exception as exc:
        log_fn(f"Failed to pause cron job {job_id}: {exc}")
        return False
