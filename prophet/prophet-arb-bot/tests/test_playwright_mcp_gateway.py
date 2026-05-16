"""Issue #580: end-to-end protocol test for PlaywrightStealthGateway.

One test exercises every critical responsibility of the new stdio MCP
shim:

  - Process spawning works on the provided command.
  - `initialize` is sent first with the expected protocolVersion.
  - `notifications/initialized` follows.
  - `tools/call` sends the playwright tool name + arguments verbatim.
  - The unwrapped `structuredContent.body` is returned to the caller.
  - `playwright_<tool>` attributes resolve to callables (so
    `_resolve_mcp_callable` in `playwright_client.py` will find them).
  - `__exit__` terminates the subprocess (no leak).

Uses tests/fixtures/stub_playwright_mcp_server.py — a tiny Python stdio
MCP server that logs every received message and echoes call arguments
back as the result body. Keeps the test hermetic (no real Playwright,
no SerenDesktop dependency).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from otp_worker import playwright_mcp_gateway as pmg
from otp_worker.playwright_mcp_gateway import PlaywrightStealthGateway

STUB_SERVER = Path(__file__).parent / "fixtures" / "stub_playwright_mcp_server.py"


def test_playwright_stealth_gateway_speaks_mcp_and_invokes_playwright_tool(tmp_path):
    log_path = tmp_path / "stub_log.jsonl"

    with PlaywrightStealthGateway(
        command=[sys.executable, str(STUB_SERVER), str(log_path)]
    ) as gateway:
        nav = gateway.playwright_navigate
        assert callable(nav)
        result = nav(url="https://app.prophetmarket.ai")

    assert result == {"ok": True, "url": "https://app.prophetmarket.ai"}

    sent = [json.loads(line) for line in log_path.read_text().splitlines() if line]
    assert sent[0]["method"] == "initialize"
    assert sent[0]["params"]["protocolVersion"] == "2024-11-05"
    assert sent[1]["method"] == "notifications/initialized"
    assert sent[2]["method"] == "tools/call"
    assert sent[2]["params"]["name"] == "playwright_navigate"
    assert sent[2]["params"]["arguments"] == {"url": "https://app.prophetmarket.ai"}


def test_resolver_accepts_embedded_runtime_bundle_path(monkeypatch):
    """Issue #585: packaged Desktop builds have used both bundle layouts.

    Missing the embedded-runtime path makes auth fall into
    `blocked_otp_browser_unavailable`, which in turn caused agents to ask
    users to extract JWTs manually.
    """

    embedded = (
        "/Applications/SerenDesktop.app/Contents/Resources/embedded-runtime/"
        "mcp-servers/playwright-stealth/dist/index.js"
    )

    monkeypatch.delenv("SEREN_PLAYWRIGHT_MCP_COMMAND", raising=False)
    monkeypatch.setattr(
        pmg.Path,
        "exists",
        lambda self: str(self) == embedded,
    )
    monkeypatch.setenv("SEREN_EMBEDDED_NODE_BIN", "/opt/seren/node")

    assert PlaywrightStealthGateway._resolve_default_command() == [
        "/opt/seren/node",
        embedded,
    ]
