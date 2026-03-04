#!/usr/bin/env python3
"""Portfolio allocation math for DCA accumulation and drift control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AllocationDrift:
    asset: str
    target_pct: float
    current_pct: float
    drift_pct: float


class PortfolioManager:
    """Builds allocation-aware DCA buy plans."""

    _KNOWN_QUOTES = ("USDT", "USDC", "USD", "EUR", "GBP", "CAD", "JPY", "AUD")

    @classmethod
    def _base_asset_from_pair(cls, pair: str) -> str:
        upper = str(pair).upper().strip().replace("_", "-")
        if "-" in upper:
            return upper.split("-", 1)[0]
        for quote in cls._KNOWN_QUOTES:
            if upper.endswith(quote) and len(upper) > len(quote):
                return upper[: -len(quote)]
        return upper

    @classmethod
    def _balance_candidates(cls, pair: str) -> list[str]:
        base = cls._base_asset_from_pair(pair)
        return [
            base,
            f"{base}-USD",
            f"{base}-USDC",
        ]

    @classmethod
    def _lookup_balance(cls, balances: dict[str, float], pair: str) -> float:
        upper_balances = {str(key).upper(): float(value) for key, value in balances.items()}
        for key in cls._balance_candidates(pair):
            if key in upper_balances:
                return float(upper_balances[key])
        return 0.0

    @staticmethod
    def normalize_allocations(allocations: dict[str, float]) -> dict[str, float]:
        if not allocations:
            raise ValueError("allocations must not be empty")
        total = sum(float(v) for v in allocations.values())
        if total <= 0:
            raise ValueError("allocation total must be > 0")

        normalized: dict[str, float] = {}
        for asset, value in allocations.items():
            text = str(asset).upper().strip().replace("_", "-")
            if not text:
                continue
            if "-" not in text:
                text = f"{text}-USD"
            normalized[text] = float(value) / total
        return normalized

    @staticmethod
    def current_allocations(
        balances: dict[str, float],
        prices: dict[str, float],
        targets: dict[str, float],
    ) -> dict[str, float]:
        values: dict[str, float] = {}
        for asset in targets:
            qty = PortfolioManager._lookup_balance(balances, asset)
            px = float(prices.get(asset, 0.0))
            values[asset] = max(qty * px, 0.0)

        total = sum(values.values())
        if total <= 0:
            n = len(targets)
            return {asset: 1.0 / n for asset in targets}
        return {asset: value / total for asset, value in values.items()}

    @staticmethod
    def detect_drift(
        *,
        targets: dict[str, float],
        current: dict[str, float],
    ) -> list[AllocationDrift]:
        drifts: list[AllocationDrift] = []
        for asset, target in targets.items():
            current_pct = float(current.get(asset, 0.0))
            drift = current_pct - float(target)
            drifts.append(
                AllocationDrift(
                    asset=asset,
                    target_pct=round(target * 100.0, 4),
                    current_pct=round(current_pct * 100.0, 4),
                    drift_pct=round(drift * 100.0, 4),
                )
            )
        return sorted(drifts, key=lambda row: abs(row.drift_pct), reverse=True)

    def build_dca_buy_plan(
        self,
        *,
        total_dca_amount_usd: float,
        targets: dict[str, float],
        current: dict[str, float],
        rebalance_threshold_pct: float,
    ) -> dict[str, Any]:
        targets = self.normalize_allocations(targets)
        current = {asset: float(current.get(asset, 0.0)) for asset in targets}

        drifts = self.detect_drift(targets=targets, current=current)
        underweights = {
            row.asset: max(0.0, (row.target_pct - row.current_pct) / 100.0)
            for row in drifts
            if row.drift_pct <= -abs(rebalance_threshold_pct)
        }

        if underweights:
            weight_total = sum(underweights.values())
            weights = {asset: value / weight_total for asset, value in underweights.items()}
            mode = "drift_rebalance"
        else:
            weights = targets
            mode = "target_weighted"

        orders: list[dict[str, Any]] = []
        for asset, weight in weights.items():
            notional = round(total_dca_amount_usd * weight, 2)
            if notional <= 0:
                continue
            orders.append(
                {
                    "asset": asset,
                    "side": "buy",
                    "notional_usd": notional,
                    "target_weight": round(targets.get(asset, 0.0), 6),
                    "reason": mode,
                }
            )

        return {
            "mode": mode,
            "orders": orders,
            "drift": [row.__dict__ for row in drifts],
            "max_abs_drift_pct": max(abs(row.drift_pct) for row in drifts) if drifts else 0.0,
        }
