#!/usr/bin/env python3
"""HTTP trigger server to run the smart DCA cycle via seren-cron/webhooks."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from agent import run_once
from runtime_paths import activate_runtime

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


class DCARequestHandler(BaseHTTPRequestHandler):
    config_path = "config.json"
    allow_live = False
    accept_risk_disclaimer = False
    webhook_secret = ""
    min_run_interval_seconds = 60
    _last_run_monotonic = 0.0

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404, "Endpoint not found")
            return
        payload = {
            "status": "ok",
            "service": "coinbase-smart-dca-bot",
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "mode": "live" if self.allow_live else "dry_run",
            "min_run_interval_seconds": self.min_run_interval_seconds,
        }
        self._send_json(200, payload)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            self.send_error(404, "Endpoint not found")
            return

        if not self.webhook_secret:
            self._send_json(503, {"status": "error", "error": "server_misconfigured"})
            return

        received = self.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(received, self.webhook_secret):
            self._send_json(401, {"status": "error", "error": "unauthorized"})
            return

        now = time.monotonic()
        last_run = float(type(self)._last_run_monotonic)
        min_interval = max(float(type(self).min_run_interval_seconds), 1.0)
        if now - last_run < min_interval:
            self._send_json(
                429,
                {
                    "status": "error",
                    "error": "rate_limited",
                    "retry_after_seconds": round(
                        min_interval - (now - last_run),
                        2,
                    ),
                },
            )
            return
        type(self)._last_run_monotonic = now

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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--accept-risk-disclaimer", action="store_true")
    parser.add_argument(
        "--min-run-interval-seconds",
        type=int,
        default=0,
        help="Minimum seconds between /run calls. Defaults to runtime.loop_interval_seconds from config.",
    )
    parser.add_argument(
        "--webhook-secret",
        default="",
        help="Shared secret for /run authentication; can also come from DCA_WEBHOOK_SECRET env var.",
    )
    return parser.parse_args()


def _load_loop_interval_seconds(config_path: str, fallback: int = 60) -> int:
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            body = json.load(handle)
        return int(body.get("runtime", {}).get("loop_interval_seconds", fallback))
    except Exception:  # noqa: BLE001
        return fallback


def main() -> int:
    args = parse_args()
    args.config = str(activate_runtime(args.config))
    load_dotenv()
    DCARequestHandler.config_path = args.config
    DCARequestHandler.allow_live = bool(args.allow_live)
    DCARequestHandler.accept_risk_disclaimer = bool(args.accept_risk_disclaimer)
    DCARequestHandler.webhook_secret = args.webhook_secret or os.getenv("DCA_WEBHOOK_SECRET", "")
    if args.min_run_interval_seconds > 0:
        DCARequestHandler.min_run_interval_seconds = int(args.min_run_interval_seconds)
    else:
        DCARequestHandler.min_run_interval_seconds = max(
            1, _load_loop_interval_seconds(args.config, fallback=60)
        )

    if not DCARequestHandler.webhook_secret:
        raise SystemExit("Refusing to start webhook server without DCA_WEBHOOK_SECRET.")

    server = HTTPServer((args.host, args.port), DCARequestHandler)
    print(f"Smart DCA server listening on {args.host}:{args.port}")
    print(f"Health:  http://{args.host}:{args.port}/health")
    print(f"Trigger: http://{args.host}:{args.port}/run")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
