"""Issue #571: cold-start auth defects in `_acquire_jwt`.

Two regressions pinned here:

1. `_acquire_jwt` must instantiate `RealBrowserSession` with the live
   `gateway`. The constructor requires `gateway` as a keyword-only arg,
   so a bare `RealBrowserSession()` call raises `TypeError` on the very
   first cold-start (no cached JWT, no `PROPHET_SESSION_TOKEN`). Jill
   hit this as a first-run user; tests stubbed the browser so it stayed
   invisible.

2. When something unexpected fires in the auth path, the returned
   `reason` must surface the exception message (truncated) — not just
   the type name. The old envelope was `blocked_auth_unexpected:TypeError`
   with no detail, leaving the operator nothing to debug from.
"""

from __future__ import annotations

from typing import Any

import pytest

import agent
from agent import AgentConfig, EXECUTION_MODE_DELTA_NEUTRAL
from arbitrage.intelligence import IntelligenceConfig
from arbitrage.scoring import ScoringConfig
from discovery import AutoDiscoverConfig


def _config() -> AgentConfig:
    return AgentConfig(
        inputs={"prophet_email": "jill@example.com", "email_provider": "gmail"},
        project_name="prophet",
        database_name="prophet",
        scoring=ScoringConfig(),
        intelligence=IntelligenceConfig(),
        auto_discover=AutoDiscoverConfig(enabled=True, initial_bet_usdc=1.0),
        live_mode=False,
        max_orders_per_run=5,
        execution_mode=EXECUTION_MODE_DELTA_NEUTRAL,
        max_hedge_slippage_bps=200.0,
    )


class _StaleCacheEntry:
    """Cache entry that forces the cold-start (RealBrowserSession) path."""

    jwt = ""
    prophet_viewer_id = ""
    state = "needs_otp"
    refresh_token = ""

    def is_fresh(self) -> bool:
        return False


class _StaleSessionCache:
    def read(self) -> _StaleCacheEntry:
        return _StaleCacheEntry()


def _force_cold_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROPHET_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(agent, "SessionCache", _StaleSessionCache)


def test_acquire_jwt_passes_gateway_to_real_browser_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The root-cause regression test for issue #571.

    `RealBrowserSession.__init__(self, *, gateway, headless=True)` makes
    `gateway` a required keyword argument. Calling `RealBrowserSession()`
    without it raises `TypeError`. The test captures the constructor
    kwargs from a real cold-start dispatch and asserts the live gateway
    was forwarded.
    """
    _force_cold_start(monkeypatch)

    captured: dict[str, Any] = {}

    class _CapturingSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

        def __enter__(self) -> "_CapturingSession":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    monkeypatch.setattr(agent, "RealBrowserSession", _CapturingSession)

    # AuthFacade is exercised separately; here we just need any return
    # so `_acquire_jwt` exits cleanly after `with RealBrowserSession()`.
    class _StubFacade:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def get_fresh_jwt(self, **kwargs: Any) -> Any:
            from otp_worker.auth_facade import FreshJwt

            return FreshJwt(jwt="eyJ.j.w.t", prophet_viewer_id="vid_x", source="otp")

    monkeypatch.setattr(agent, "AuthFacade", _StubFacade)

    sentinel_gateway = object()
    jwt, viewer_id, source = agent._acquire_jwt(
        config=_config(), gateway=sentinel_gateway, transport=object()
    )

    assert jwt == "eyJ.j.w.t"
    assert viewer_id == "vid_x"
    assert source == "otp"
    # The args list must be empty (the bug was a positional/no-arg call);
    # `gateway` must arrive as a keyword.
    assert captured["args"] == ()
    assert captured["kwargs"].get("gateway") is sentinel_gateway


def test_acquire_jwt_surfaces_exception_message_on_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an unexpected exception fires in the auth path, the reason
    string must include the exception message — not just the type name.
    Without the message the operator has no way to tell `TypeError` from
    `ValueError` from a deserialization fault.
    """
    _force_cold_start(monkeypatch)

    class _ExplodingSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "_ExplodingSession":
            raise ValueError("custom-msg-12345")

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    monkeypatch.setattr(agent, "RealBrowserSession", _ExplodingSession)

    jwt, viewer_id, reason = agent._acquire_jwt(
        config=_config(), gateway=object(), transport=object()
    )

    assert jwt is None
    assert viewer_id is None
    assert reason.startswith("blocked_auth_unexpected:ValueError:"), reason
    assert "custom-msg-12345" in reason


def test_real_browser_session_uses_mcp_gateway_when_available() -> None:
    """Issue #576: direct MCP dispatch must win over Playwright publisher calls.

    Seren Desktop exposes Playwright as connected MCP tools, not a paid
    publisher. If the runtime gateway supplies MCP callables,
    RealBrowserSession must call those methods and must not call
    gateway.call("playwright", ...), which fails with "Publisher
    'playwright' not found".
    """

    class _McpGateway:
        def __init__(self) -> None:
            self.navigate_calls: list[dict[str, str]] = []
            self.publisher_calls: list[tuple[Any, ...]] = []

        def mcp_playwright_navigate(self, *, url: str) -> dict[str, bool]:
            self.navigate_calls.append({"url": url})
            return {"ok": True}

        def call(self, *args: Any, **kwargs: Any) -> Any:
            self.publisher_calls.append(args)
            raise AssertionError("Playwright publisher must not be called")

    from otp_worker.playwright_client import RealBrowserSession

    gateway = _McpGateway()
    session = RealBrowserSession(gateway=gateway)

    session.navigate("https://app.prophetmarket.ai")

    assert gateway.navigate_calls == [{"url": "https://app.prophetmarket.ai"}]
    assert gateway.publisher_calls == []


def test_real_browser_session_reports_mcp_requirement_without_gateway_method() -> None:
    """Issue #576: no silent fallback to the missing Playwright publisher."""

    class _PublisherOnlyGateway:
        def call(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Playwright publisher must not be called")

    from otp_worker.playwright_client import RealBrowserSession

    session = RealBrowserSession(gateway=_PublisherOnlyGateway())

    with pytest.raises(RuntimeError, match="Playwright MCP"):
        session.navigate("https://app.prophetmarket.ai")
