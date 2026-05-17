"""Issue #652: the gateway's per-`tools/call` ceiling is no longer the
real failure cap.

The per-entry budget on the `/create` driver
(`test_create_market_via_ui_entry_budget.py`) is the real cap. Per-call
ceilings exist only to detect a dead MCP stdio stream, so the steady-
state ceiling is raised to 180s and the bespoke first-call branch from
#649 is removed.

These are the only tests required to pin the gateway side of #652:

- `test_gateway_default_per_call_floor_is_180_seconds`: the constant
  bump is load-bearing. Without it we'd still fail at 30s on a contended
  host before the per-entry budget ever has a chance to enforce.
- `test_first_tools_call_timeout_still_carries_phase_marker`: the
  `phase=cold_launch` annotation is retained as diagnostic-only
  instrumentation. Without this regression test the marker can be
  silently dropped during refactors.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from otp_worker import playwright_mcp_gateway as pmg
from otp_worker import playwright_mcp_lifecycle as lifecycle
from otp_worker.playwright_mcp_gateway import (
    DEFAULT_TIMEOUT_SECONDS,
    PlaywrightStealthGateway,
)


@pytest.fixture(autouse=True)
def _no_cleanup(monkeypatch):
    """Disable the #647 stale-MCP cleanup so these tests don't kill peer
    Node processes on the test host."""
    monkeypatch.setattr(
        pmg,
        "kill_stale_playwright_mcp_processes",
        lambda *, grace_seconds=1.0: lifecycle.KillReport(),
    )


def test_gateway_default_per_call_floor_is_180_seconds() -> None:
    """#652: per-call ceiling raised to 180s, no first-call carve-out.

    180s is comfortably above any single contended `tools/call` we've
    observed on the heavy-Chrome failure profile that produced #651,
    while still surfacing a truly hung stdio stream within one per-entry
    budget window (default 300s). If this is ever silently dropped back
    to 30s, the per-call ceiling becomes the de-facto cap again and the
    per-entry budget can no longer protect every stage.
    """
    assert DEFAULT_TIMEOUT_SECONDS == 180.0
    # No vestigial first-call constant should still exist after #652.
    assert not hasattr(pmg, "DEFAULT_FIRST_CALL_TIMEOUT_SECONDS"), (
        "DEFAULT_FIRST_CALL_TIMEOUT_SECONDS was removed in #652; if you "
        "see this assertion fail, the first-call carve-out has been "
        "reintroduced and the gateway is back to per-stage policing."
    )


def test_first_tools_call_timeout_still_carries_phase_marker(monkeypatch, tmp_path):
    """`phase=cold_launch` stays as diagnostic-only instrumentation.

    The per-entry budget handles the actual failure path; this marker
    just lets operators tell at-a-glance whether the budget was eaten
    by the cold Chromium launch (the bundled MCP child does this lazily
    on the FIRST `tools/call`) or by later steady-state stages. Removing
    it would not change correctness but would meaningfully degrade
    diagnostics, so we pin it.
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
        "        time.sleep(60)  # exceed test per-call timeout\n"
    )

    with PlaywrightStealthGateway(
        command=[sys.executable, str(hanging_stub)],
        timeout_seconds=0.3,
    ) as gateway:
        with pytest.raises(TimeoutError) as excinfo:
            gateway.playwright_navigate(url="https://app.prophetmarket.ai")

    msg = str(excinfo.value)
    assert "phase=cold_launch" in msg, (
        "first tools/call TimeoutError must embed `phase=cold_launch` so "
        f"operators can disambiguate from steady-state stalls; got: {msg!r}"
    )
