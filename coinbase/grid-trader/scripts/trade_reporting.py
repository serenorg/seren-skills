"""Shared cycle trade reporting for grid-trader runtimes."""

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


class CycleTradeReportEmitter:
    """Collect per-cycle grid events and emit structured reports."""

    def __init__(self, *, skill_slug: str, venue: str, strategy_name: str, logs_dir: str = "logs") -> None:
        self.skill_slug = skill_slug
        self.venue = venue
        self.strategy_name = strategy_name
        self.logs_dir = Path(logs_dir)
        self.log_path = self.logs_dir / "trade_reports.jsonl"
        self._sessions: dict[str, dict[str, Any]] = {}

    def start_session(
        self,
        session_id: str,
        *,
        mode: str,
        dry_run: bool,
        instrument_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._sessions[session_id] = {
            "session_id": session_id,
            "mode": mode,
            "dry_run": dry_run,
            "instrument_id": instrument_id,
            "metadata": dict(metadata or {}),
            "started_at": _now_iso(),
            "cycle_index": 0,
            "last_equity_end_usd": None,
            "peak_equity_end_usd": None,
            "current_position": None,
            "cycle_order_events": [],
            "cycle_fills": [],
            "last_halt_reason": None,
            "breach_positions": [],
        }

    def record_order(
        self,
        session_id: str,
        *,
        order_id: str,
        side: str,
        price: float,
        quantity: float,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session["cycle_order_events"].append(
            {
                "order_id": order_id,
                "side": side,
                "price": float(price),
                "quantity": float(quantity),
                "status": status,
                "metadata": dict(metadata or {}),
            }
        )

    def record_fill(
        self,
        session_id: str,
        *,
        order_id: str,
        side: str,
        price: float,
        quantity: float,
        fee_usd: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session["cycle_fills"].append(
            {
                "order_id": order_id,
                "side": side,
                "fill_price": float(price),
                "quantity": float(quantity),
                "fee_usd": float(fee_usd),
                "fill_time": _now_iso(),
                "metadata": dict(metadata or {}),
            }
        )

    def record_position(
        self,
        session_id: str,
        *,
        instrument_id: str,
        base_balance: float,
        quote_balance: float,
        total_value_usd: float,
        unrealized_pnl_usd: float,
        open_orders: int,
        status: str = "running",
    ) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        current_price = None
        if abs(float(base_balance)) > 1e-12:
            current_price = max((float(total_value_usd) - float(quote_balance)) / float(base_balance), 0.0)
        session["current_position"] = {
            "market": instrument_id,
            "market_id": instrument_id,
            "symbol": instrument_id,
            "side": "LONG" if float(base_balance) > 0 else "FLAT",
            "quantity": float(base_balance),
            "current_price": current_price,
            "quote_balance": float(quote_balance),
            "market_value_usd": float(total_value_usd),
            "unrealized_pnl_usd": float(unrealized_pnl_usd),
            "open_orders": int(open_orders),
        }
        self._emit_report(session_id, status=status)

    def record_event(self, session_id: str, *, event_type: str, payload: dict[str, Any], status: str | None) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session["last_halt_reason"] = _first_text(
            payload.get("error_message"),
            payload.get("blocked_reason"),
            payload.get("reason"),
            event_type,
        )
        instrument = _first_text(payload.get("pair"), payload.get("product_id"))
        if instrument:
            breach = instrument
            if breach not in session["breach_positions"]:
                session["breach_positions"].append(breach)
        if status:
            self._emit_report(session_id, status=status, terminal_event=event_type, event_payload=payload)

    def _emit_report(
        self,
        session_id: str,
        *,
        status: str,
        terminal_event: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        position = session.get("current_position") or {}
        equity_end = _float_or_none(position.get("market_value_usd"))
        previous_equity = _float_or_none(session.get("last_equity_end_usd"))
        peak_equity = _float_or_none(session.get("peak_equity_end_usd"))
        if equity_end is not None:
            peak_equity = equity_end if peak_equity is None else max(peak_equity, equity_end)
        fees_usd = sum(_float_or_none(fill.get("fee_usd")) or 0.0 for fill in session["cycle_fills"])
        cycle_summary = {
            "realized_pnl_usd": None,
            "unrealized_pnl_usd": _float_or_none(position.get("unrealized_pnl_usd")),
            "fees_usd": fees_usd,
            "gross_pnl_usd": _float_or_none(position.get("unrealized_pnl_usd")),
            "net_pnl_usd": None
            if position.get("unrealized_pnl_usd") is None
            else float(position["unrealized_pnl_usd"]) - fees_usd,
            "equity_start_usd": previous_equity,
            "equity_end_usd": equity_end,
            "previous_equity_end_usd": previous_equity,
            "equity_change_vs_previous_cycle_usd": None
            if equity_end is None or previous_equity is None
            else equity_end - previous_equity,
            "drawdown_usd": None
            if peak_equity is None or equity_end is None
            else max(peak_equity - equity_end, 0.0),
            "drawdown_pct": None
            if peak_equity in (None, 0.0) or equity_end is None
            else max(peak_equity - equity_end, 0.0) / peak_equity * 100.0,
            "order_event_count": len(session["cycle_order_events"]),
            "fill_count": len(session["cycle_fills"]),
            "halt_reason": session.get("last_halt_reason"),
            "breach_positions": list(session.get("breach_positions") or []),
        }
        trades = [
            {
                "order_id": fill.get("order_id"),
                "market": position.get("market") or session.get("instrument_id"),
                "market_id": position.get("market_id") or session.get("instrument_id"),
                "symbol": position.get("symbol") or session.get("instrument_id"),
                "side": fill.get("side"),
                "quantity": fill.get("quantity"),
                "entry_price": fill.get("fill_price"),
                "exit_price": None,
                "current_price": position.get("current_price"),
                "fill_price": fill.get("fill_price"),
                "realized_pnl_usd": None,
                "unrealized_pnl_usd": position.get("unrealized_pnl_usd"),
                "fee_usd": fill.get("fee_usd"),
                "fill_time": fill.get("fill_time"),
                "metadata": fill.get("metadata") or {},
            }
            for fill in session["cycle_fills"]
        ]
        open_positions = []
        quantity = _float_or_none(position.get("quantity"))
        if quantity is None or abs(quantity) > 1e-12:
            open_positions.append(
                {
                    "market": position.get("market") or session.get("instrument_id"),
                    "market_id": position.get("market_id") or session.get("instrument_id"),
                    "symbol": position.get("symbol") or session.get("instrument_id"),
                    "side": position.get("side"),
                    "quantity": quantity,
                    "entry_price": None,
                    "current_price": _float_or_none(position.get("current_price")),
                    "market_value_usd": _float_or_none(position.get("market_value_usd")),
                    "unrealized_pnl_usd": _float_or_none(position.get("unrealized_pnl_usd")),
                    "metadata": {
                        "quote_balance": _float_or_none(position.get("quote_balance")),
                        "open_orders": position.get("open_orders"),
                    },
                }
            )

        report = {
            "generated_at": _now_iso(),
            "run_id": session_id,
            "skill_slug": self.skill_slug,
            "venue": self.venue,
            "strategy_name": self.strategy_name,
            "mode": session.get("mode"),
            "status": status,
            "dry_run": bool(session.get("dry_run", True)),
            "started_at": session.get("started_at"),
            "completed_at": _now_iso() if terminal_event else None,
            "summary": {
                "cycle_index": session.get("cycle_index", 0) + 1,
                "terminal_event": terminal_event,
            },
            "metadata": {
                **dict(session.get("metadata") or {}),
                "instrument_id": session.get("instrument_id"),
                "event_payload": dict(event_payload or {}),
            },
            "cycle_summary": cycle_summary,
            "trades": trades,
            "open_positions": open_positions,
        }
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, default=str, sort_keys=True) + "\n")
        if os.getenv("PYTHONUNBUFFERED") == "1":
            self._print_report(report)

        session["cycle_index"] = int(session.get("cycle_index", 0)) + 1
        session["last_equity_end_usd"] = equity_end
        session["peak_equity_end_usd"] = peak_equity
        session["cycle_order_events"] = []
        session["cycle_fills"] = []
        if not terminal_event:
            session["breach_positions"] = []
            session["last_halt_reason"] = None
        if terminal_event:
            self._sessions.pop(session_id, None)

    def _print_report(self, report: dict[str, Any]) -> None:
        cycle_summary = report.get("cycle_summary", {})
        print(
            f"[trade-report] {self.skill_slug} "
            f"{report.get('mode')}/{report.get('status')} run={report.get('run_id')}"
        )
        print(
            "  unrealized="
            f"{_format_money(_float_or_none(cycle_summary.get('unrealized_pnl_usd')))} "
            "fees="
            f"{_format_money(_float_or_none(cycle_summary.get('fees_usd')))} "
            "equity="
            f"{_format_money(_float_or_none(cycle_summary.get('equity_end_usd')))}"
        )
        print("  Trades:")
        print(
            _render_table(
                ["Market", "Side", "Qty", "Fill", "Current", "Order"],
                [
                    [
                        str(row.get("market") or row.get("symbol") or "-"),
                        str(row.get("side") or "-"),
                        _format_qty(_float_or_none(row.get("quantity"))),
                        _format_money(_float_or_none(row.get("fill_price"))),
                        _format_money(_float_or_none(row.get("current_price"))),
                        str(row.get("order_id") or "-"),
                    ]
                    for row in report.get("trades", [])
                ],
            )
        )
        print("  Open Positions:")
        print(
            _render_table(
                ["Market", "Side", "Qty", "Current", "Value", "Unrealized"],
                [
                    [
                        str(row.get("market") or row.get("symbol") or "-"),
                        str(row.get("side") or "-"),
                        _format_qty(_float_or_none(row.get("quantity"))),
                        _format_money(_float_or_none(row.get("current_price"))),
                        _format_money(_float_or_none(row.get("market_value_usd"))),
                        _format_money(_float_or_none(row.get("unrealized_pnl_usd"))),
                    ]
                    for row in report.get("open_positions", [])
                ],
            )
        )
