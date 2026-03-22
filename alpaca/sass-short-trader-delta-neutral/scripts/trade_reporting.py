"""Post-cycle trade reporting helpers for SaaS short trader skills."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _format_money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


def _format_qty(value: float | None) -> str:
    return "-" if value is None else f"{value:,.4f}"


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "  (none)"
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    header_line = "  " + " | ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers))
    divider = "  " + "-+-".join("-" * width for width in widths)
    body = [
        "  " + " | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row))
        for row in rows
    ]
    return "\n".join([header_line, divider, *body])


class ShortTradeReportEmitter:
    """Accumulates a single run and emits a structured post-cycle report."""

    def __init__(self, *, skill_slug: str, strategy_name: str, logs_dir: str = "logs") -> None:
        self.skill_slug = skill_slug
        self.strategy_name = strategy_name
        self.logs_dir = Path(logs_dir)
        self.log_path = self.logs_dir / "trade_reports.jsonl"
        self._runs: dict[str, dict[str, Any]] = {}

    def start_run(self, run_id: str, *, mode: str, dry_run: bool, metadata: dict[str, Any] | None = None) -> None:
        self._runs[run_id] = {
            "run_id": run_id,
            "mode": mode,
            "dry_run": dry_run,
            "started_at": _now_iso(),
            "metadata": dict(metadata or {}),
            "order_events": [],
            "marks": {},
            "pnl": None,
        }

    def record_order_events(self, run_id: str, events: list[dict[str, Any]]) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        run["order_events"].extend(json.loads(json.dumps(events, default=str)))

    def record_position_marks(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = _first_text(row.get("ticker"), row.get("symbol"), row.get("position_key"))
            if not ticker:
                continue
            run["marks"][ticker] = json.loads(json.dumps(row, default=str))

    def record_pnl(
        self,
        run_id: str,
        *,
        realized_pnl: float,
        unrealized_pnl: float,
        gross_exposure: float,
        net_exposure: float,
        hit_rate: float,
        max_drawdown: float,
    ) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        net_pnl = float(realized_pnl) + float(unrealized_pnl)
        run["pnl"] = {
            "realized_pnl_usd": float(realized_pnl),
            "unrealized_pnl_usd": float(unrealized_pnl),
            "gross_pnl_usd": net_pnl,
            "net_pnl_usd": net_pnl,
            "equity_end_usd": net_pnl,
            "gross_exposure": float(gross_exposure),
            "net_exposure": float(net_exposure),
            "hit_rate": float(hit_rate),
            "max_drawdown": float(max_drawdown),
        }

    def finish_run(self, run_id: str, *, status: str, metadata_patch: dict[str, Any]) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        report = self._build_report(run, status=status, metadata_patch=metadata_patch)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, default=str, sort_keys=True) + "\n")
        if os.getenv("PYTHONUNBUFFERED") == "1":
            self._print_report(report)

    def _build_report(self, run: dict[str, Any], *, status: str, metadata_patch: dict[str, Any]) -> dict[str, Any]:
        marks = run.get("marks", {})
        pnl = run.get("pnl") or {}
        previous_equity, peak_equity = self._history_stats(mode=str(run.get("mode") or ""))
        equity_end = _float_or_none(pnl.get("equity_end_usd"))
        drawdown_usd = _float_or_none(pnl.get("max_drawdown"))
        drawdown_pct = None
        if drawdown_usd is not None and peak_equity not in (None, 0.0):
            drawdown_pct = drawdown_usd / peak_equity * 100.0
        trades: list[dict[str, Any]] = []
        fill_count = 0
        for event in run.get("order_events", []):
            if not isinstance(event, dict):
                continue
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            ticker = _first_text(event.get("ticker"), event.get("symbol"), event.get("instrument_id"))
            mark = marks.get(ticker or "")
            filled_qty = _float_or_none(event.get("filled_qty"))
            filled_price = _float_or_none(event.get("filled_avg_price"))
            if filled_qty is not None and filled_price is not None:
                fill_count += 1
            trades.append(
                {
                    "order_id": event.get("order_ref"),
                    "market": ticker,
                    "market_id": ticker,
                    "symbol": ticker,
                    "side": event.get("side"),
                    "quantity": _float_or_none(event.get("qty")),
                    "entry_price": _float_or_none(details.get("entry_price")) or _float_or_none(event.get("limit_price")),
                    "exit_price": filled_price if _first_text(details.get("close_reason")) else None,
                    "current_price": _float_or_none(mark.get("mark_price")) if isinstance(mark, dict) else None,
                    "fill_price": filled_price,
                    "realized_pnl_usd": _float_or_none(details.get("realized_pnl")),
                    "unrealized_pnl_usd": _float_or_none(mark.get("unrealized_pnl")) if isinstance(mark, dict) else None,
                    "fee_usd": None,
                    "fill_time": event.get("event_time"),
                    "status": event.get("status"),
                    "metadata": details,
                }
            )

        open_positions = []
        for ticker, mark in marks.items():
            qty = _float_or_none(mark.get("qty"))
            if qty is None or abs(qty) <= 1e-12:
                continue
            open_positions.append(
                {
                    "market": ticker,
                    "market_id": ticker,
                    "symbol": ticker,
                    "side": "SELL" if qty < 0 else "BUY",
                    "quantity": qty,
                    "entry_price": _float_or_none(mark.get("avg_entry_price")),
                    "current_price": _float_or_none(mark.get("mark_price")),
                    "market_value_usd": _float_or_none(mark.get("market_value")),
                    "realized_pnl_usd": _float_or_none(mark.get("realized_pnl")),
                    "unrealized_pnl_usd": _float_or_none(mark.get("unrealized_pnl")),
                    "metadata": {
                        "gross_exposure": _float_or_none(mark.get("gross_exposure")),
                        "net_exposure": _float_or_none(mark.get("net_exposure")),
                    },
                }
            )

        halt_reason = _first_text(
            metadata_patch.get("error"),
            metadata_patch.get("blocked_reason"),
            metadata_patch.get("reason"),
            metadata_patch.get("status"),
        )
        cycle_summary = {
            "realized_pnl_usd": _float_or_none(pnl.get("realized_pnl_usd")),
            "unrealized_pnl_usd": _float_or_none(pnl.get("unrealized_pnl_usd")),
            "fees_usd": None,
            "gross_pnl_usd": _float_or_none(pnl.get("gross_pnl_usd")),
            "net_pnl_usd": _float_or_none(pnl.get("net_pnl_usd")),
            "equity_start_usd": previous_equity,
            "equity_end_usd": equity_end,
            "previous_equity_end_usd": previous_equity,
            "equity_change_vs_previous_cycle_usd": None
            if previous_equity is None or equity_end is None
            else equity_end - previous_equity,
            "drawdown_usd": drawdown_usd,
            "drawdown_pct": drawdown_pct,
            "order_event_count": len(run.get("order_events", [])),
            "fill_count": fill_count,
            "halt_reason": halt_reason if status in {"failed", "blocked", "stopped"} else None,
            "breach_positions": [],
        }
        return {
            "generated_at": _now_iso(),
            "run_id": run.get("run_id"),
            "skill_slug": self.skill_slug,
            "venue": "alpaca",
            "strategy_name": self.strategy_name,
            "mode": run.get("mode"),
            "status": status,
            "dry_run": bool(run.get("dry_run", True)),
            "started_at": run.get("started_at"),
            "completed_at": _now_iso(),
            "summary": metadata_patch,
            "metadata": {
                **dict(run.get("metadata") or {}),
                "max_drawdown": _float_or_none(pnl.get("max_drawdown")),
                "gross_exposure": _float_or_none(pnl.get("gross_exposure")),
                "net_exposure": _float_or_none(pnl.get("net_exposure")),
                "hit_rate": _float_or_none(pnl.get("hit_rate")),
            },
            "cycle_summary": cycle_summary,
            "trades": trades,
            "open_positions": open_positions,
        }

    def _history_stats(self, *, mode: str) -> tuple[float | None, float | None]:
        if not self.log_path.exists():
            return None, None
        previous_equity = None
        peak_equity = None
        with self.log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("skill_slug") != self.skill_slug or entry.get("mode") != mode:
                    continue
                cycle_summary = entry.get("cycle_summary")
                if not isinstance(cycle_summary, dict):
                    continue
                equity = _float_or_none(cycle_summary.get("equity_end_usd"))
                if equity is None:
                    continue
                previous_equity = equity
                peak_equity = equity if peak_equity is None else max(peak_equity, equity)
        return previous_equity, peak_equity

    def _print_report(self, report: dict[str, Any]) -> None:
        cycle_summary = report.get("cycle_summary", {})
        print(
            f"[trade-report] {self.skill_slug} "
            f"{report.get('mode')}/{report.get('status')} run={report.get('run_id')}"
        )
        print(
            "  realized="
            f"{_format_money(_float_or_none(cycle_summary.get('realized_pnl_usd')))} "
            "unrealized="
            f"{_format_money(_float_or_none(cycle_summary.get('unrealized_pnl_usd')))} "
            "net="
            f"{_format_money(_float_or_none(cycle_summary.get('net_pnl_usd')))}"
        )
        print("  Trades:")
        print(
            _render_table(
                ["Market", "Side", "Qty", "Entry", "Current", "Status"],
                [
                    [
                        str(row.get("market") or row.get("symbol") or "-"),
                        str(row.get("side") or "-"),
                        _format_qty(_float_or_none(row.get("quantity"))),
                        _format_money(_float_or_none(row.get("entry_price"))),
                        _format_money(_float_or_none(row.get("current_price"))),
                        str(row.get("status") or "-"),
                    ]
                    for row in report.get("trades", [])
                ],
            )
        )
        print("  Open Positions:")
        print(
            _render_table(
                ["Market", "Side", "Qty", "Entry", "Current", "Unrealized"],
                [
                    [
                        str(row.get("market") or row.get("symbol") or "-"),
                        str(row.get("side") or "-"),
                        _format_qty(_float_or_none(row.get("quantity"))),
                        _format_money(_float_or_none(row.get("entry_price"))),
                        _format_money(_float_or_none(row.get("current_price"))),
                        _format_money(_float_or_none(row.get("unrealized_pnl_usd"))),
                    ]
                    for row in report.get("open_positions", [])
                ],
            )
        )
