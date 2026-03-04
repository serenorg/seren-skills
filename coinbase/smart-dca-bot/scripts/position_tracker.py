#!/usr/bin/env python3
"""Cost-basis lot tracking for tax/reporting downstream skills."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


@dataclass
class CostBasisLot:
    lot_id: str
    asset: str
    quantity: float
    cost_basis_usd: float
    acquisition_date: str
    source: str
    execution_id: str
    disposed: bool = False
    disposed_at: str | None = None


class PositionTracker:
    """Tracks open lots and weighted-average cost by asset."""

    def __init__(self, path: str = "state/cost_basis_lots.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lots: list[CostBasisLot] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self.lots = []
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.lots = [CostBasisLot(**item) for item in raw]

    def _save(self) -> None:
        payload = [asdict(lot) for lot in self.lots]
        self.path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    def add_buy_lot(
        self,
        *,
        asset: str,
        quantity: float,
        cost_basis_usd: float,
        execution_id: str,
        source: str = "dca",
    ) -> CostBasisLot:
        lot = CostBasisLot(
            lot_id=str(uuid.uuid4()),
            asset=asset.upper(),
            quantity=float(quantity),
            cost_basis_usd=float(cost_basis_usd),
            acquisition_date=datetime.now(tz=UTC).isoformat(),
            source=source,
            execution_id=execution_id,
        )
        self.lots.append(lot)
        self._save()
        return lot

    def open_lots(self, asset: str) -> list[CostBasisLot]:
        code = asset.upper()
        return [lot for lot in self.lots if lot.asset == code and not lot.disposed]

    def weighted_avg_entry(self, asset: str) -> float:
        active = self.open_lots(asset)
        total_qty = sum(lot.quantity for lot in active)
        if total_qty <= 0:
            return 0.0
        total_cost = sum(lot.cost_basis_usd for lot in active)
        return total_cost / total_qty

    def export_for_serendb(self) -> list[dict[str, object]]:
        return [asdict(lot) for lot in self.lots]
