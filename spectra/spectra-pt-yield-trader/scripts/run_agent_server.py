#!/usr/bin/env python3
"""HTTP trigger server so seren-cron can run the Spectra planner on schedule."""

from __future__ import annotations

import argparse
import json
import traceback
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from agent import ConfigError, load_config, run_once, run_stop_trading


class SpectraAgentRequestHandler(BaseHTTPRequestHandler):
    config_path = "config.json"
    yes_live = False

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404, "Endpoint not found")
            return

        payload = {
            "status": "ok",
            "service": "spectra-pt-yield-trader",
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "config_path": self.config_path,
        }
        self._send_json(200, payload)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            self.send_error(404, "Endpoint not found")
            return

        try:
            payload = self._read_payload()
            config = load_config(self.config_path)
            yes_live = bool(payload.get("yes_live", self.yes_live)) if isinstance(payload, dict) else self.yes_live
            action = str(payload.get("action", "run")).strip().lower() if isinstance(payload, dict) else "run"
            if action == "stop-trading":
                result = run_stop_trading(config=config)
            else:
                result = run_once(config=config, yes_live=yes_live)
            self._send_json(200, result)
        except ConfigError as exc:
            self._send_json(400, {"status": "error", "error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive surface
            traceback.print_exc()
            self._send_json(500, {"status": "error", "error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        ts = datetime.now(tz=UTC).isoformat()
        print(f"[{ts}] {format % args}")

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ConfigError("Request body must be a JSON object.")
        return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an HTTP trigger server for seren-cron scheduled execution."
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config JSON (default: config.json).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for HTTP server (default: 8080).",
    )
    parser.add_argument(
        "--yes-live",
        action="store_true",
        help="Allow this trigger server to emit live execution handoffs without extra prompts.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    SpectraAgentRequestHandler.config_path = args.config
    SpectraAgentRequestHandler.yes_live = bool(args.yes_live)

    server = HTTPServer(("0.0.0.0", args.port), SpectraAgentRequestHandler)
    print(f"Spectra planner server listening on port {args.port}")
    print(f"Health:  http://localhost:{args.port}/health")
    print(f"Trigger: http://localhost:{args.port}/run")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
