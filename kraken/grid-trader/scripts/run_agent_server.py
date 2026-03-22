#!/usr/bin/env python3
"""HTTP trigger server for the adaptive Kraken grid trader."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv

from agent import KrakenGridTrader


UTC = timezone.utc


def _config_scan_interval_seconds(config_path: str, fallback: int = 60) -> int:
    try:
        with open(config_path, 'r', encoding='utf-8') as handle:
            body = json.load(handle)
        return int(body.get('strategy', {}).get('scan_interval_seconds', fallback))
    except Exception:  # noqa: BLE001
        return fallback


def _run_action(*, action: str, config_path: str, allow_live: bool) -> dict:
    dry_run = action == 'cycle' and not allow_live
    trader = KrakenGridTrader(config_path=config_path, dry_run=dry_run)
    try:
        trader.setup(optimize_backtest=False)
        if action == 'cycle':
            return trader.run_cycle()
        if action == 'review':
            return trader.build_review()
        if action == 'safety-check':
            return trader.run_safety_check()
        raise ValueError(f'unsupported action: {action}')
    finally:
        trader.close()


class GridRequestHandler(BaseHTTPRequestHandler):
    config_path = 'config.json'
    allow_live = False
    webhook_secret = ''
    min_run_interval_seconds = 60
    _last_run_monotonic: dict[str, float] = {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path != '/health':
            self.send_error(404, 'Endpoint not found')
            return
        payload = {
            'status': 'ok',
            'service': 'kraken-grid-trader',
            'timestamp': datetime.now(tz=UTC).isoformat(),
            'mode': 'live' if self.allow_live else 'dry_run',
            'min_run_interval_seconds': self.min_run_interval_seconds,
        }
        self._send_json(200, payload)

    def do_POST(self) -> None:  # noqa: N802
        endpoint_map = {
            '/run': 'cycle',
            '/review': 'review',
            '/safety-check': 'safety-check',
        }
        action = endpoint_map.get(self.path)
        if action is None:
            self.send_error(404, 'Endpoint not found')
            return
        if not self._authorize():
            return
        if action == 'cycle' and not self._within_rate_limit(self.path):
            return
        try:
            result = _run_action(
                action=action,
                config_path=self.config_path,
                allow_live=self.allow_live,
            )
            self._send_json(200, {'status': 'ok', 'action': action, 'result': result})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_json(500, {'status': 'error', 'action': action, 'error': str(exc)})

    def _authorize(self) -> bool:
        if not self.webhook_secret:
            self._send_json(503, {'status': 'error', 'error': 'server_misconfigured'})
            return False
        received = self.headers.get('X-Webhook-Secret', '')
        if not hmac.compare_digest(received, self.webhook_secret):
            self._send_json(401, {'status': 'error', 'error': 'unauthorized'})
            return False
        return True

    def _within_rate_limit(self, key: str) -> bool:
        now = time.monotonic()
        last_run = float(type(self)._last_run_monotonic.get(key, 0.0))
        min_interval = max(float(type(self).min_run_interval_seconds), 1.0)
        if now - last_run < min_interval:
            self._send_json(
                429,
                {
                    'status': 'error',
                    'error': 'rate_limited',
                    'retry_after_seconds': round(min_interval - (now - last_run), 2),
                },
            )
            return False
        type(self)._last_run_monotonic[key] = now
        return True

    def log_message(self, fmt: str, *args: object) -> None:
        timestamp = datetime.now(tz=UTC).isoformat()
        print(f'[{timestamp}] {fmt % args}')

    def _send_json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run HTTP trigger server for kraken-grid-trader')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--allow-live', action='store_true')
    parser.add_argument(
        '--min-run-interval-seconds',
        type=int,
        default=0,
        help='Minimum seconds between /run calls. Defaults to strategy.scan_interval_seconds from config.',
    )
    parser.add_argument(
        '--webhook-secret',
        default='',
        help='Shared secret for POST endpoint authentication; can also come from KRAKEN_GRID_WEBHOOK_SECRET.',
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    GridRequestHandler.config_path = args.config
    GridRequestHandler.allow_live = bool(args.allow_live)
    GridRequestHandler.webhook_secret = args.webhook_secret or os.getenv('KRAKEN_GRID_WEBHOOK_SECRET', '')
    if args.min_run_interval_seconds > 0:
        GridRequestHandler.min_run_interval_seconds = int(args.min_run_interval_seconds)
    else:
        GridRequestHandler.min_run_interval_seconds = max(
            1,
            _config_scan_interval_seconds(args.config, fallback=60),
        )
    if not GridRequestHandler.webhook_secret:
        raise SystemExit('Refusing to start webhook server without KRAKEN_GRID_WEBHOOK_SECRET.')

    server = HTTPServer((args.host, args.port), GridRequestHandler)
    print(f'Kraken Grid Trader server listening on {args.host}:{args.port}')
    print(f'Health:  http://{args.host}:{args.port}/health')
    print(f'Cycle:   http://{args.host}:{args.port}/run')
    print(f'Review:  http://{args.host}:{args.port}/review')
    print(f'Safety:  http://{args.host}:{args.port}/safety-check')
    server.serve_forever()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
