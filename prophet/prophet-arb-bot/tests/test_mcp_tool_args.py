"""Pin the BrowserSession body → upgraded-MCP arg translation.

Issue #638: the upgraded SerenDesktop Playwright MCP exposes
`playwright_wait_for_selector(selector, state?, timeout?)`. Before the
upgrade `RealBrowserSession.wait_for` polled via `evaluate`, so
`_mcp_tool_args` was never exercised on the wait path. Now that the
dedicated tool exists, the translation MUST send `timeout` (not
`timeout_ms`) or the MCP rejects the call.

`add_init_script` / `add_cookies` pass through unchanged — the body
keys already match the MCP schema.
"""

from __future__ import annotations

from otp_worker.playwright_client import _mcp_tool_args


def test_wait_for_selector_translates_timeout_ms_to_timeout() -> None:
    out = _mcp_tool_args(
        "wait_for_selector",
        {"selector": "#email-input", "timeout_ms": 15_000},
    )
    assert out == {"selector": "#email-input", "timeout": 15_000}


def test_wait_for_selector_accepts_timeout_directly() -> None:
    """Callers that already use the MCP-native key must not lose precision."""
    out = _mcp_tool_args(
        "wait_for_selector",
        {"selector": "#email-input", "timeout": 5_000},
    )
    assert out == {"selector": "#email-input", "timeout": 5_000}


def test_wait_for_selector_default_timeout_30s() -> None:
    out = _mcp_tool_args("wait_for_selector", {"selector": "#sentinel"})
    assert out == {"selector": "#sentinel", "timeout": 30_000}


def test_add_init_script_passes_through() -> None:
    """The init-script body shape (`{script}`) already matches the MCP."""
    body = {"script": "window.localStorage.setItem('k', 'v');"}
    assert _mcp_tool_args("add_init_script", body) == body


def test_add_cookies_passes_through() -> None:
    """The add-cookies body shape (`{cookies: [...]}`) already matches the MCP."""
    body = {
        "cookies": [
            {
                "name": "privy-session",
                "value": "abc",
                "domain": "app.prophetmarket.ai",
                "path": "/",
                "httpOnly": True,
                "secure": True,
            }
        ]
    }
    assert _mcp_tool_args("add_cookies", body) == body
