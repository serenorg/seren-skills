#!/usr/bin/env python3
"""DCA scheduling and window lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


@dataclass
class DCAWindow:
    start: datetime
    end: datetime

    @property
    def duration_seconds(self) -> float:
        return max((self.end - self.start).total_seconds(), 1.0)


def floor_to_frequency(ts: datetime, frequency: str) -> datetime:
    ts = ts.astimezone(UTC)
    anchor = ts.replace(minute=0, second=0, microsecond=0)
    if frequency == "daily":
        return anchor.replace(hour=0)
    if frequency == "weekly":
        return (anchor - timedelta(days=anchor.weekday())).replace(hour=0)
    if frequency == "biweekly":
        monday = (anchor - timedelta(days=anchor.weekday())).replace(hour=0)
        epoch = datetime(2024, 1, 1, tzinfo=UTC)
        weeks = int((monday - epoch).days / 7)
        if weeks % 2 == 1:
            monday = monday - timedelta(days=7)
        return monday
    if frequency == "monthly":
        return anchor.replace(day=1, hour=0)
    raise ValueError(f"Unsupported frequency '{frequency}'")


def build_window(*, now: datetime, frequency: str, window_hours: int) -> DCAWindow:
    start = floor_to_frequency(now, frequency)
    end = start + timedelta(hours=window_hours)
    return DCAWindow(start=start, end=end)


def window_progress(window: DCAWindow, now: datetime) -> float:
    elapsed = (now - window.start).total_seconds()
    return max(min(elapsed / window.duration_seconds, 1.0), 0.0)


def should_force_fill(window: DCAWindow, now: datetime) -> bool:
    return now >= window.end
