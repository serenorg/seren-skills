#!/usr/bin/env python3
"""
Simple HTTP server for triggering the Polymarket trading agent via seren-cron.

This server provides a single endpoint that seren-cron can call on a schedule
to trigger autonomous trading scans.

Usage:
    python scripts/run_agent_server.py --config config.json [--port 8080] [--dry-run]
"""

import argparse
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
import traceback

# Import agent
from agent import TradingAgent


class AgentRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler that triggers the trading agent"""

    agent = None  # Will be set by main()
    dry_run = False

    def do_POST(self):
        """Handle POST request to trigger agent scan"""
        if self.path != '/run':
            self.send_error(404, "Endpoint not found")
            return

        try:
            # Run agent scan cycle
            print(f"\n[{self._get_timestamp()}] Cron trigger received - starting scan cycle...")
            self.agent.run_scan_cycle()

            # Send success response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            response = {
                'status': 'success',
                'message': 'Trading scan completed',
                'dry_run': self.dry_run
            }
            self.wfile.write(json.dumps(response).encode())

        except Exception as e:
            print(f"Error running agent: {e}")
            traceback.print_exc()

            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            response = {
                'status': 'error',
                'message': str(e)
            }
            self.wfile.write(json.dumps(response).encode())

    def do_GET(self):
        """Handle GET request for health check"""
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            response = {
                'status': 'healthy',
                'dry_run': self.dry_run
            }
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_error(404, "Endpoint not found")

    def log_message(self, format, *args):
        """Custom log message format"""
        print(f"[{self._get_timestamp()}] {format % args}")

    @staticmethod
    def _get_timestamp():
        """Get current timestamp"""
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Polymarket Trading Agent Server')
    parser.add_argument(
        '--config',
        required=True,
        help='Path to config.json'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=8080,
        help='Port to listen on (default: 8080)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry-run mode (no actual trades)'
    )

    args = parser.parse_args()

    # Check config exists
    if not os.path.exists(args.config):
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)

    # Initialize agent
    try:
        print("Initializing trading agent...")
        agent = TradingAgent(args.config, dry_run=args.dry_run)

        # Set agent on handler class
        AgentRequestHandler.agent = agent
        AgentRequestHandler.dry_run = args.dry_run

        print(f"✓ Agent initialized successfully")
        print()

    except Exception as e:
        print(f"Error initializing agent: {e}")
        traceback.print_exc()
        sys.exit(1)

    # Start HTTP server
    try:
        server = HTTPServer(('0.0.0.0', args.port), AgentRequestHandler)
        print(f"🚀 Agent server running on port {args.port}")
        print(f"   Health check: http://localhost:{args.port}/health")
        print(f"   Trigger endpoint: http://localhost:{args.port}/run")
        print(f"   Dry-run: {args.dry_run}")
        print()
        print("Waiting for seren-cron triggers...")
        print("Press Ctrl+C to stop")
        print()

        server.serve_forever()

    except KeyboardInterrupt:
        print("\n\nServer stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"\nError running server: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
