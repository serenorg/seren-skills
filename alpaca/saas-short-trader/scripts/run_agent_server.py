#!/usr/bin/env python3
"""
HTTP runner for seren-cron triggers.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Tuple

from self_learning import ensure_champion, run_full, run_label_update, run_promotion_check, run_retrain
from strategy_engine import DEFAULT_UNIVERSE, StrategyEngine


def json_response(handler: BaseHTTPRequestHandler, code: int, payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def parse_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    engine: StrategyEngine
    dsn: str
    webhook_secret: str

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            json_response(self, 404, {"status": "error", "message": "Not found"})
            return
        json_response(self, 200, {"status": "ok", "service": "saas-short-trader"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            json_response(self, 404, {"status": "error", "message": "Not found"})
            return

        incoming_secret = self.headers.get("x-webhook-secret", "")
        if not hmac.compare_digest(incoming_secret, self.webhook_secret):
            json_response(self, 401, {"status": "error", "message": "Unauthorized"})
            return

        try:
            payload = parse_body(self)
        except json.JSONDecodeError:
            json_response(self, 400, {"status": "error", "message": "Invalid JSON"})
            return

        mode = str(payload.get("mode", "paper-sim"))
        run_type = str(payload.get("run_type", "scan"))
        action = str(payload.get("action", "")).strip().lower()
        strict_feeds = bool(payload.get("strict_required_feeds", True))

        try:
            # Direct learning actions
            if action in {"label-update", "retrain", "promotion-check", "full"}:
                with self.engine.storage.connect() as conn:
                    ensure_champion(conn)
                    if action == "label-update":
                        result = run_label_update(conn, mode=mode)
                    elif action == "retrain":
                        result = run_retrain(conn)
                    elif action == "promotion-check":
                        result = run_promotion_check(conn)
                    else:
                        result = run_full(conn, mode=mode)
                json_response(self, 200, {"status": "ok", "action": action, "result": result})
                return

            # Strategy actions
            self.engine.strict_required_feeds = strict_feeds
            if run_type == "scan":
                result = self.engine.run_scan(
                    mode=mode,
                    run_profile=str(payload.get("run_profile", "continuous")),
                    run_type="scan",
                    universe=list(payload.get("universe", DEFAULT_UNIVERSE)),
                    max_names_scored=int(payload.get("max_names_scored", 30)),
                    max_names_orders=int(payload.get("max_names_orders", 8)),
                    min_conviction=float(payload.get("min_conviction", 65.0)),
                    learning_mode=str(payload.get("learning_mode", "adaptive-paper")),
                    scheduled_window_start=payload.get("scheduled_window_start"),
                )
            elif run_type == "monitor":
                result = self.engine.run_monitor(mode=mode, run_profile=str(payload.get("run_profile", "continuous")))
            elif run_type == "post-close":
                result = self.engine.run_post_close(mode=mode, run_profile=str(payload.get("run_profile", "continuous")))
            else:
                json_response(self, 400, {"status": "error", "message": f"Unsupported run_type: {run_type}"})
                return

            code = 200 if str(result.get("status")) in {"completed", "ok"} else 202
            json_response(self, code, result)
        except Exception as exc:  # pylint: disable=broad-except
            json_response(self, 500, {"status": "error", "message": str(exc)})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[saas-short-trader] {self.address_string()} - {fmt % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run saas-short-trader webhook server")
    parser.add_argument("--dsn", default=os.getenv("SERENDB_DSN", ""), help="SerenDB DSN")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--webhook-secret", default=os.getenv("SAAS_SHORT_TRADER_WEBHOOK_SECRET", ""))
    parser.add_argument("--strict-required-feeds", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.dsn:
        raise SystemExit("SERENDB_DSN is required (--dsn or env)")
    if not args.webhook_secret:
        raise SystemExit("SAAS_SHORT_TRADER_WEBHOOK_SECRET is required (--webhook-secret or env)")

    engine = StrategyEngine(
        dsn=args.dsn,
        api_key=os.getenv("SEREN_API_KEY"),
        strict_required_feeds=bool(args.strict_required_feeds),
    )
    engine.ensure_schema()

    Handler.engine = engine
    Handler.dsn = args.dsn
    Handler.webhook_secret = args.webhook_secret

    server = HTTPServer((args.host, args.port), Handler)
    print(f"Server listening on http://{args.host}:{args.port}")
    print("Health endpoint: GET /health")
    print("Run endpoint:    POST /run")
    server.serve_forever()


if __name__ == "__main__":
    main()
