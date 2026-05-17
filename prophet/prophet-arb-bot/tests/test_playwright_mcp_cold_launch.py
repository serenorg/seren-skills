"""Issue #649: elevated first-call timeout for cold Chromium launch.

After #647 cleared peer-MCP contention, the first `tools/call` after
`__enter__` still timed out at 30s because the bundled MCP child does a
lazy `chromium.launch()` on the first tool invocation — and on a host
with a long-running operator Chrome instance, the system-Chrome cold
start can stretch past 30s for Gatekeeper + LaunchServices + stealth
evasion module loads.

These are the only tests required to pin the fix:

- `test_first_tool_call_uses_elevated_cold_launch_timeout`: the first
  `tools/call` after `__enter__` must use ``first_call_timeout_seconds``,
  not the steady-state ``timeout_seconds``. Without this elevation the
  fix is a no-op.
- `test_subsequent_tool_calls_use_steady_state_timeout`: after the first
  successful tool call, subsequent calls revert to the tighter
  ``timeout_seconds``. Without this, every later call would inherit the
  elevated timeout and we'd lose responsiveness for steady-state stalls.
- `test_first_call_timeout_error_carries_phase_marker`: when the first
  call DOES time out, the TimeoutError message must embed
  ``phase=cold_launch`` so operators can disambiguate from
  steady-state stalls in logs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from otp_worker import playwright_mcp_gateway as pmg
from otp_worker import playwright_mcp_lifecycle as lifecycle
from otp_worker.playwright_mcp_gateway import PlaywrightStealthGateway

STUB_SERVER = Path(__file__).parent / "fixtures" / "stub_playwright_mcp_server.py"


@pytest.fixture(autouse=True)
def _no_cleanup(monkeypatch):
    """Disable the #647 stale-MCP cleanup so these tests don't kill peer
    Node processes on the test host."""
    monkeypatch.setattr(
        pmg,
        "kill_stale_playwright_mcp_processes",
        lambda *, grace_seconds=1.0: lifecycle.KillReport(),
    )


def _record_timeouts(monkeypatch) -> list[float]:
    """Patch ``_read_exact`` to capture every timeout the gateway passes
    in, and return the list. The stub server always responds quickly, so
    the function still completes normally; we just observe the timeouts.
    """
    captured: list[float] = []
    real_read_exact = pmg._read_exact

    def wrapped(fd: int, size: int, timeout_seconds: float) -> bytes:
        captured.append(timeout_seconds)
        return real_read_exact(fd, size, timeout_seconds)

    monkeypatch.setattr(pmg, "_read_exact", wrapped)
    return captured


def test_first_tool_call_uses_elevated_cold_launch_timeout(monkeypatch, tmp_path):
    """First `tools/call` after `__enter__` must use first_call_timeout_seconds.

    `__enter__` itself runs `initialize` under the steady-state timeout
    (the handshake is cheap). The cold launch happens inside the FIRST
    `tools/call`, so that one call MUST get the elevated budget.
    """
    log_path = tmp_path / "stub_log.jsonl"
    timeouts = _record_timeouts(monkeypatch)

    with PlaywrightStealthGateway(
        command=[sys.executable, str(STUB_SERVER), str(log_path)],
        timeout_seconds=7.0,
        first_call_timeout_seconds=99.0,
    ) as gateway:
        gateway.playwright_navigate(url="https://app.prophetmarket.ai")

    # __enter__ does `initialize` (one or more _read_exact calls) under
    # the steady-state timeout, then the first tools/call uses the
    # elevated timeout for every _read_exact it issues.
    assert 99.0 in timeouts, (
        "first tools/call after __enter__ must use first_call_timeout_seconds; "
        f"observed timeouts={timeouts!r}"
    )
    assert all(t in (7.0, 99.0) for t in timeouts), (
        "timeouts must be drawn from either steady-state or first-call values; "
        f"observed timeouts={timeouts!r}"
    )


def test_subsequent_tool_calls_use_steady_state_timeout(monkeypatch, tmp_path):
    """After the first tools/call, later calls drop back to timeout_seconds."""
    log_path = tmp_path / "stub_log.jsonl"
    timeouts = _record_timeouts(monkeypatch)

    with PlaywrightStealthGateway(
        command=[sys.executable, str(STUB_SERVER), str(log_path)],
        timeout_seconds=7.0,
        first_call_timeout_seconds=99.0,
    ) as gateway:
        gateway.playwright_navigate(url="https://app.prophetmarket.ai")
        # Reset the captured list so we only see timeouts from the SECOND call.
        timeouts.clear()
        gateway.playwright_navigate(url="https://example.com")

    assert timeouts, "second tools/call must issue at least one read"
    assert 99.0 not in timeouts, (
        "second tools/call must NOT reuse the elevated first-call timeout; "
        f"observed timeouts={timeouts!r}"
    )
    assert all(t == 7.0 for t in timeouts), (
        "after first-call elevation, steady-state calls must use timeout_seconds; "
        f"observed timeouts={timeouts!r}"
    )


def test_first_call_timeout_error_carries_phase_marker(monkeypatch, tmp_path):
    """If the first tools/call itself times out, surface phase=cold_launch.

    We use a stub server that responds to `initialize` but hangs on
    `tools/call`, so the elevated timeout fires while we are inside the
    cold-launch phase. A short first-call timeout keeps the test fast.
    """
    hanging_stub = tmp_path / "hanging_stub.py"
    hanging_stub.write_text(
        '"""Stub MCP server that answers initialize then hangs on tools/call."""\n'
        "import json, sys, time\n"
        "\n"
        "def _read():\n"
        "    header = bytearray()\n"
        "    while b'\\r\\n\\r\\n' not in header:\n"
        "        ch = sys.stdin.buffer.read(1)\n"
        "        if not ch:\n"
        "            return None\n"
        "        header.extend(ch)\n"
        "    h, _ = header.split(b'\\r\\n\\r\\n', 1)\n"
        "    n = 0\n"
        "    for line in h.decode('ascii', errors='ignore').splitlines():\n"
        "        if ':' in line:\n"
        "            k, v = line.split(':', 1)\n"
        "            if k.strip().lower() == 'content-length':\n"
        "                n = int(v.strip())\n"
        "    return json.loads(sys.stdin.buffer.read(n).decode('utf-8'))\n"
        "\n"
        "def _write(payload):\n"
        "    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')\n"
        "    sys.stdout.buffer.write(f'Content-Length: {len(body)}\\r\\n\\r\\n'.encode('ascii'))\n"
        "    sys.stdout.buffer.write(body)\n"
        "    sys.stdout.buffer.flush()\n"
        "\n"
        "while True:\n"
        "    msg = _read()\n"
        "    if msg is None:\n"
        "        sys.exit(0)\n"
        "    if msg.get('method') == 'initialize':\n"
        "        _write({'jsonrpc': '2.0', 'id': msg['id'], 'result': {\n"
        "            'protocolVersion': '2024-11-05',\n"
        "            'capabilities': {'tools': {}},\n"
        "            'serverInfo': {'name': 'hanging-stub', 'version': '0'},\n"
        "        }})\n"
        "    elif msg.get('method') == 'tools/call':\n"
        "        time.sleep(60)  # exceed test first-call timeout\n"
    )

    with PlaywrightStealthGateway(
        command=[sys.executable, str(hanging_stub)],
        timeout_seconds=0.2,
        first_call_timeout_seconds=0.5,
    ) as gateway:
        with pytest.raises(TimeoutError) as excinfo:
            gateway.playwright_navigate(url="https://app.prophetmarket.ai")

    msg = str(excinfo.value)
    assert "phase=cold_launch" in msg, (
        "first-call TimeoutError must embed `phase=cold_launch` so operators "
        f"can disambiguate from steady-state stalls; got: {msg!r}"
    )
