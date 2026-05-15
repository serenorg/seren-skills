"""Minimal stdio MCP server stub for PlaywrightStealthGateway tests.

Speaks just enough of the JSON-RPC MCP protocol to exercise the gateway's
`initialize` → `notifications/initialized` → `tools/call` round-trips.
Every received message is appended to the log file passed on argv so the
test can assert what the gateway sent.

Not a real Playwright implementation — `tools/call` echoes the requested
arguments back as the result body, which is enough for a transport test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _read_message() -> dict | None:
    header_buf = bytearray()
    while b"\r\n\r\n" not in header_buf:
        ch = sys.stdin.buffer.read(1)
        if not ch:
            return None
        header_buf.extend(ch)
    header_raw, _ = header_buf.split(b"\r\n\r\n", 1)
    content_length = 0
    for line in header_raw.decode("ascii", errors="ignore").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            if key.strip().lower() == "content-length":
                content_length = int(value.strip())
    body = sys.stdin.buffer.read(content_length)
    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def main() -> int:
    log_path = Path(sys.argv[1])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        while True:
            msg = _read_message()
            if msg is None:
                return 0
            log.write(json.dumps(msg) + "\n")
            log.flush()
            method = msg.get("method")
            request_id = msg.get("id")
            if method == "initialize":
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "stub-playwright", "version": "0"},
                        },
                    }
                )
            elif method == "notifications/initialized":
                # No response for notifications.
                continue
            elif method == "tools/call":
                params = msg.get("params") or {}
                args = params.get("arguments") or {}
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "structuredContent": {
                                "body": {"ok": True, **args},
                            },
                        },
                    }
                )
            else:
                if request_id is not None:
                    _write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32601, "message": f"Method not found: {method}"},
                        }
                    )


if __name__ == "__main__":
    sys.exit(main())
