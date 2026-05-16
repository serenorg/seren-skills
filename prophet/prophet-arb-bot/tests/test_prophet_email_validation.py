from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import agent
from agent import AgentConfig, EXECUTION_MODE_DELTA_NEUTRAL
from arbitrage.intelligence import IntelligenceConfig
from arbitrage.scoring import ScoringConfig
from discovery import AutoDiscoverConfig


def _config(email: str) -> AgentConfig:
    return AgentConfig(
        inputs={"prophet_email": email, "email_provider": "gmail"},
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


def test_placeholder_prophet_email_blocks_before_otp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROPHET_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(agent, "SessionCache", _StaleSessionCache)

    def _fail_if_otp_path_reached(cls: type[Any]) -> list[str]:
        raise AssertionError("placeholder email should block before OTP setup")

    monkeypatch.setattr(
        agent._playwright_mcp_gateway.PlaywrightStealthGateway,
        "_resolve_default_command",
        classmethod(_fail_if_otp_path_reached),
    )

    jwt, viewer_id, reason = agent._acquire_jwt(
        config=_config("you@example.com"),
        gateway=object(),
        transport=object(),
    )

    assert jwt is None
    assert viewer_id is None
    assert reason == "blocked_otp_email_placeholder"


def test_config_example_does_not_ship_executable_placeholder() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config.example.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))

    assert data["inputs"]["prophet_email"] == ""
