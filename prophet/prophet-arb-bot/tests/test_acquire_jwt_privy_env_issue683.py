"""Issue #683 — OTP cold-start gateway needs PRIVY_COMPATIBLE_ENV.

#682 wired the Privy-compatible env profile into the two ``/create`` gateway
spawn sites (``_default_browser_session_factory`` and
``_WarmCreateMarketUiContext._open``) but deliberately left the OTP cold-start
gateway at ``scripts/agent.py:432`` on the bundled MCP's stealth-on defaults.
A live cycle on ``main`` at ``68a1ce7`` (post-#682) still blocks at
``blocked_otp:OtpEmailTimeout:privy:connections did not appear …`` because
Privy provisions the embedded wallet **during** the OTP login redirect, not
after — so the OTP gateway needs the same profile until
``serenorg/seren-desktop#1958`` flips the bundled MCP's defaults.

One critical test, no duplicates: spawn-kwarg contract on the OTP path.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import agent
from otp_worker import playwright_mcp_gateway as pmg


def test_acquire_jwt_otp_cold_start_passes_privy_compatible_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The OTP cold-start gateway MUST be constructed with PRIVY_COMPATIBLE_ENV.

    Without this, a fresh-cache cycle blocks at OtpEmailTimeout because the
    bundled MCP's headless+full-stealth defaults prevent Privy's embedded
    wallet from provisioning during OTP login. Verified by E2E on main at
    ``68a1ce7`` (post-#682); the OTP MCP child carries only BROWSER_TYPE +
    SEREN_PLAYWRIGHT_MCP_COMMAND in its env, with none of the four #1957
    Privy-compatible vars.
    """
    # Point the session cache at an empty tmp dir so ``entry.is_fresh()``
    # returns False and OTP cold-start path is forced.
    monkeypatch.setenv("PROPHET_ARB_STATE_DIR", str(tmp_path))
    # Make sure the env-jwt escape hatch doesn't short-circuit the cold-start.
    monkeypatch.delenv("PROPHET_SESSION_TOKEN", raising=False)

    # The path also guards on _resolve_default_command — return a synthetic
    # command so the agent doesn't bail with OTP_BROWSER_UNAVAILABLE_REASON.
    monkeypatch.setattr(
        agent._playwright_mcp_gateway.PlaywrightStealthGateway,
        "_resolve_default_command",
        classmethod(lambda cls: ["/usr/bin/true"]),
    )

    captured: dict[str, Any] = {}

    class _SpyGateway:
        """Records constructor kwargs; short-circuits enter/exit."""

        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = dict(kwargs)

        def __enter__(self) -> "_SpyGateway":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    class _SpySession:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "_SpySession":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    sentinel_jwt = "eyJ.fake-jwt.for-test"
    sentinel_viewer = "vid_test"

    class _FakeFreshSession:
        jwt = sentinel_jwt
        prophet_viewer_id = sentinel_viewer
        source = "otp"

    class _FakeAuthFacade:
        def __init__(self, *, cache: Any) -> None:
            pass

        def get_fresh_jwt(self, **kwargs: Any) -> Any:
            # By the time this runs, _SpyGateway has already been
            # constructed, so the assertion below will see the kwargs.
            return _FakeFreshSession()

    monkeypatch.setattr(agent, "PlaywrightStealthGateway", _SpyGateway)
    monkeypatch.setattr(agent, "RealBrowserSession", _SpySession)
    monkeypatch.setattr(agent, "AuthFacade", _FakeAuthFacade)

    # example.com is in the reserved-example placeholder list; use a real-looking
    # domain so _prophet_email_block_reason returns None.
    config = types.SimpleNamespace(
        inputs={"prophet_email": "op@serendb.com", "email_provider": "gmail"}
    )
    jwt, viewer, source = agent._acquire_jwt(
        config=config, gateway=object(), transport=object()
    )

    assert jwt == sentinel_jwt, "OTP cold-start path did not run to completion"
    assert source == "otp"

    assert "kwargs" in captured, (
        "_acquire_jwt did not instantiate PlaywrightStealthGateway. "
        "The OTP cold-start spawn site at agent.py:432 must reach the "
        "gateway constructor."
    )
    env_overrides = captured["kwargs"].get("env_overrides")
    assert env_overrides == pmg.PRIVY_COMPATIBLE_ENV, (
        "OTP cold-start gateway must be constructed with "
        "env_overrides=PRIVY_COMPATIBLE_ENV so the bundled playwright-stealth "
        "MCP child gets the Privy-compatible launch profile (HEADLESS=0, "
        "two stealth evasions dropped, page-init patch off). Without this, "
        "Privy's embedded wallet never provisions during OTP login and the "
        "cycle blocks at OtpEmailTimeout. "
        f"Expected {pmg.PRIVY_COMPATIBLE_ENV!r}, got {env_overrides!r}."
    )
