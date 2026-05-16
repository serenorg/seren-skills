"""Issue #580: non-Desktop fallback — `_acquire_jwt` returns a structured
`blocked_otp_browser_unavailable` reason when no Playwright MCP command
can be resolved (no `SEREN_PLAYWRIGHT_MCP_COMMAND` and no bundled
SerenDesktop binary).

Before this fix the same scenario emerged as
`blocked_auth_unexpected:RuntimeError:Playwright MCP connected service is
required for Prophet UI automation...`, which gave the operator no
actionable next step.
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


def test_acquire_jwt_fails_closed_when_no_playwright_mcp_command_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROPHET_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("SEREN_PLAYWRIGHT_MCP_COMMAND", raising=False)
    monkeypatch.setattr(agent, "SessionCache", _StaleSessionCache)

    # Force the gateway resolver to report "no command available" — the
    # subprocess-side equivalent of running outside Seren Desktop on a
    # host that has no playwright-stealth binary and no env override.
    from otp_worker import playwright_mcp_gateway as pmg

    monkeypatch.setattr(pmg.PlaywrightStealthGateway, "_resolve_default_command", classmethod(lambda cls: None))

    def _fail_if_constructed(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "RealBrowserSession must not be constructed when the gateway "
            "cannot be resolved"
        )

    monkeypatch.setattr(agent, "RealBrowserSession", _fail_if_constructed)

    jwt, viewer_id, reason = agent._acquire_jwt(
        config=_config(), gateway=object(), transport=object()
    )

    assert jwt is None
    assert viewer_id is None
    assert reason.startswith("blocked_otp_browser_unavailable"), reason
    assert "PROPHET_SESSION_TOKEN" not in reason, reason
    assert "seed_session_cache" not in reason, reason
    assert "manual" not in reason.lower(), reason
    assert "seren_desktop_playwright" in reason, reason
