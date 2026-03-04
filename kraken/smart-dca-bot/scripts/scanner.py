#!/usr/bin/env python3
"""Opportunity scanner for DCA allocation shifts."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


@dataclass
class ScannerSignal:
    signal_id: str
    signal_type: str
    asset: str
    confidence_pct: float
    trigger_data: dict[str, Any]
    suggestion: str
    reallocation_pct: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = datetime.now(tz=UTC).isoformat()
        return payload


class OpportunityScanner:
    """Scans market rows and produces reallocation suggestions."""

    def __init__(
        self,
        *,
        min_24h_volume_usd: float,
        max_reallocation_pct: float,
        enabled_signals: list[str],
    ) -> None:
        self.min_24h_volume_usd = float(min_24h_volume_usd)
        self.max_reallocation_pct = float(max_reallocation_pct)
        self.enabled_signals = set(enabled_signals)

    def _signal_id(self, signal_type: str, asset: str) -> str:
        raw = f"{signal_type}:{asset}:{datetime.now(tz=UTC).date().isoformat()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _cap(self, pct: float) -> float:
        return round(max(min(pct, self.max_reallocation_pct), 0.0), 2)

    def scan(
        self,
        market_rows: list[dict[str, Any]],
        base_allocations: dict[str, float],
    ) -> list[ScannerSignal]:
        signals: list[ScannerSignal] = []
        if not market_rows:
            return signals

        base_anchor = max(base_allocations.items(), key=lambda item: item[1])[0] if base_allocations else "XBTUSD"

        for row in market_rows:
            asset = str(row.get("asset") or row.get("pair") or "").upper()
            if not asset:
                continue

            volume = float(row.get("volume_24h_usd", 0.0))
            if volume < self.min_24h_volume_usd:
                continue

            volume_ratio = float(row.get("volume_ratio", 1.0))
            rsi_14 = float(row.get("rsi_14", 50.0))
            price = float(row.get("price", 0.0))
            sma20 = float(row.get("sma20", 0.0))
            new_listing_days = int(row.get("new_listing_days", 999))
            accumulation_score = float(row.get("accumulation_score", 0.0))

            if "oversold_rsi" in self.enabled_signals and rsi_14 < 30.0:
                rsi_gap = max(30.0 - rsi_14, 0.0)
                realloc = self._cap(8.0 + rsi_gap * 0.45)
                signals.append(
                    ScannerSignal(
                        signal_id=self._signal_id("oversold_rsi", asset),
                        signal_type="oversold_rsi",
                        asset=asset,
                        confidence_pct=round(min(62.0 + rsi_gap * 1.2, 90.0), 2),
                        trigger_data={"rsi_14": rsi_14},
                        suggestion=(
                            f"Oversold RSI setup detected. Shift up to {realloc:.2f}% into {asset}"
                        ),
                        reallocation_pct=realloc,
                    )
                )

            if "volume_spike" in self.enabled_signals and volume_ratio >= 3.0:
                realloc = self._cap(12.0 + (volume_ratio - 3.0) * 2.0)
                signals.append(
                    ScannerSignal(
                        signal_id=self._signal_id("volume_spike", asset),
                        signal_type="volume_spike",
                        asset=asset,
                        confidence_pct=round(min(70.0 + volume_ratio * 4.0, 95.0), 2),
                        trigger_data={"volume_ratio": volume_ratio, "volume_24h_usd": volume},
                        suggestion=(
                            f"Shift up to {realloc:.2f}% from {base_anchor} into {asset} due to 24h volume spike"
                        ),
                        reallocation_pct=realloc,
                    )
                )

            if "mean_reversion" in self.enabled_signals and sma20 > 0 and price > 0:
                deviation_pct = ((price - sma20) / sma20) * 100.0
                if deviation_pct <= -10.0:
                    realloc = self._cap(10.0 + abs(deviation_pct + 10.0) * 0.4)
                    signals.append(
                        ScannerSignal(
                            signal_id=self._signal_id("mean_reversion", asset),
                            signal_type="mean_reversion",
                            asset=asset,
                            confidence_pct=round(min(68.0 + abs(deviation_pct), 90.0), 2),
                            trigger_data={
                                "price": price,
                                "sma20": sma20,
                                "deviation_pct": round(deviation_pct, 4),
                            },
                            suggestion=(
                                f"Mean-reversion candidate detected. Shift up to {realloc:.2f}% into {asset}"
                            ),
                            reallocation_pct=realloc,
                        )
                    )

            if "new_listing" in self.enabled_signals and new_listing_days <= 30 and accumulation_score >= 0.6:
                realloc = self._cap(6.0 + accumulation_score * 10.0)
                signals.append(
                    ScannerSignal(
                        signal_id=self._signal_id("new_listing", asset),
                        signal_type="new_listing",
                        asset=asset,
                        confidence_pct=round(min(60.0 + accumulation_score * 40.0, 88.0), 2),
                        trigger_data={
                            "new_listing_days": new_listing_days,
                            "accumulation_score": accumulation_score,
                        },
                        suggestion=(
                            f"New listing accumulation pattern. Shift up to {realloc:.2f}% into {asset}"
                        ),
                        reallocation_pct=realloc,
                    )
                )

        # Keep strongest unique signal per (type, asset) and rank by confidence.
        by_key: dict[tuple[str, str], ScannerSignal] = {}
        for signal in signals:
            key = (signal.signal_type, signal.asset)
            prev = by_key.get(key)
            if prev is None or signal.confidence_pct > prev.confidence_pct:
                by_key[key] = signal

        return sorted(by_key.values(), key=lambda s: s.confidence_pct, reverse=True)
