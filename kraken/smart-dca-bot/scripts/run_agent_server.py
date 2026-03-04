#!/usr/bin/env python3
"""HTTP trigger server to run the smart DCA cycle via seren-cron/webhooks."""

from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from agent import run_once


class DCARequestHandler(BaseHTTPRequestHandler):
    config_path = "config.json"
    allow_live = False
    accept_risk_disclaimer = False
    webhook_secret = ""

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404, "Endpoint not found")
            return
        payload = {
            "status": "ok",
            "service": "kraken-smart-dca-bot",
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "config_path": self.config_path,
            "allow_live": self.allow_live,
        }
        self._send_json(200, payload)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            self.send_error(404, "Endpoint not found")
            return

        if self.webhook_secret:
            received = self.headers.get("X-Webhook-Secret", "")
            if received != self.webhook_secret:
                self._send_json(401, {"status": "error", "error": "unauthorized"})
                return

        try:
            result = run_once(
                config_path=self.config_path,
                allow_live=self.allow_live,
                accept_risk_disclaimer=self.accept_risk_disclaimer,
            )
            code = 200 if result.get("status") == "ok" else 400
            self._send_json(code, result)
        except Exception as exc:  # pragma: no cover
            traceback.print_exc()
            self._send_json(500, {"status": "error", "error": str(exc)})

    def log_message(self, fmt: str, *args: object) -> None:
        ts = datetime.now(tz=UTC).isoformat()
        print(f"[{ts}] {fmt % args}")

    def _send_json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run HTTP trigger server for smart-dca-bot")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--accept-risk-disclaimer", action="store_true")
    parser.add_argument(
        "--webhook-secret",
        default="",
        help="Optional shared secret; can also come from DCA_WEBHOOK_SECRET env var.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    DCARequestHandler.config_path = args.config
    DCARequestHandler.allow_live = bool(args.allow_live)
    DCARequestHandler.accept_risk_disclaimer = bool(args.accept_risk_disclaimer)
    DCARequestHandler.webhook_secret = args.webhook_secret or os.getenv("DCA_WEBHOOK_SECRET", "")

    server = HTTPServer(("0.0.0.0", args.port), DCARequestHandler)
    print(f"Smart DCA server listening on port {args.port}")
    print(f"Health:  http://localhost:{args.port}/health")
    print(f"Trigger: http://localhost:{args.port}/run")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
