"""Issue #580: when a Playwright MCP command resolves, `_acquire_jwt`
constructs `PlaywrightStealthGateway` and passes it (not `HttpGateway`)
to `RealBrowserSession`. This is the inverse of the fallback test in
test_acquire_jwt_blocked_otp_browser_unavailable.py.

Together the two tests pin the new branching: resolver hits → new
gateway; resolver misses → structured blocked envelope. No third path.
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
        inputs={"prophet_email": "jill@volume.finance", "email_provider": "gmail"},
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
    jwt = ""
    prophet_viewer_id = ""
    state = "needs_otp"
    refresh_token = ""

    def is_fresh(self) -> bool:
        return False


class _StaleSessionCache:
    def read(self) -> _StaleCacheEntry:
        return _StaleCacheEntry()


def test_acquire_jwt_constructs_playwright_stealth_gateway_when_command_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROPHET_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(agent, "SessionCache", _StaleSessionCache)

    from otp_worker import playwright_mcp_gateway as pmg

    # Pretend the resolver found a valid command (without actually spawning).
    monkeypatch.setattr(
        pmg.PlaywrightStealthGateway,
        "_resolve_default_command",
        classmethod(lambda cls: ["/usr/bin/true"]),
    )

    constructed: dict[str, Any] = {"count": 0, "command": None}

    class _StubGateway:
        def __init__(self, **kwargs: Any) -> None:
            constructed["count"] += 1
            constructed["command"] = kwargs.get("command")

        def __enter__(self) -> "_StubGateway":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(agent, "PlaywrightStealthGateway", _StubGateway)

    received_session_gateway: dict[str, Any] = {}

    class _CapturingSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            received_session_gateway["gateway"] = kwargs.get("gateway")

        def __enter__(self) -> "_CapturingSession":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(agent, "RealBrowserSession", _CapturingSession)

    class _StubFacade:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def get_fresh_jwt(self, **kwargs: Any) -> Any:
            from otp_worker.auth_facade import FreshJwt

            return FreshJwt(jwt="eyJ.j.w.t", prophet_viewer_id="vid_x", source="otp")

    monkeypatch.setattr(agent, "AuthFacade", _StubFacade)

    jwt, viewer_id, source = agent._acquire_jwt(
        config=_config(), gateway=object(), transport=object()
    )

    assert jwt == "eyJ.j.w.t"
    assert viewer_id == "vid_x"
    assert source == "otp"
    assert constructed["count"] == 1
    # RealBrowserSession received the PlaywrightStealthGateway instance —
    # NOT the publisher-side HttpGateway.
    assert isinstance(received_session_gateway["gateway"], _StubGateway)
