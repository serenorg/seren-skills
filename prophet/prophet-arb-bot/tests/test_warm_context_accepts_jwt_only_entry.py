"""Issue #670: warm-context establish must accept JWT-only cache entries.

#666 retired Privy's localStorage refresh-token mechanism: it's now
treated as deprecated server-side, the cache normalizes the
``"deprecated"`` sentinel to empty on read, and
``establish_browser_session_for_create`` no longer requires a
non-empty ``refresh_token`` to enter the cache-fresh branch.

But ``_WarmCreateMarketUiContext._open`` carried a duplicate
refresh-token check that #666 missed. After #666 + #668 land, the
inner ``establish_browser_session_for_create`` returns a valid
JWT-only cache entry, and the wrapper then immediately rejects it
because ``cache_entry.refresh_token == ""`` — raising bare
``SessionEstablishmentFailed("prophet_session_unavailable")`` with no
diagnostics attached.

The fix is mechanical: drop the ``refresh_token`` clause from the
wrapper guard, mirroring #666's drop in establish_session.py:122.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

import agent  # noqa: E402  (provided via PYTHONPATH=scripts)
from otp_worker.establish_session import SessionEstablishmentFailed


@dataclass
class _JwtOnlyCacheEntry:
    jwt: str = "eyJ.fake.jwt"
    refresh_token: str = ""


class _NoopMcpContext:
    def __enter__(self) -> "_NoopMcpContext":
        return self

    def __exit__(self, *_a: Any) -> None:
        return None


def test_warm_context_open_accepts_jwt_only_cache_entry(monkeypatch):
    """JWT-only cache entries must clear the wrapper without raising."""

    # Force the MCP-available branch — the wrapper guards on whether a
    # playwright-stealth command is resolvable before doing anything else.
    monkeypatch.setattr(
        agent._playwright_mcp_gateway.PlaywrightStealthGateway,
        "_resolve_default_command",
        classmethod(lambda cls: ["node", "/fake/playwright-stealth/index.js"]),
    )
    # The actual gateway + browser session don't need to do anything; the
    # bug is the post-establish predicate over the returned cache entry,
    # not the MCP plumbing.
    # Issue #681: ``_open()`` now passes ``env_overrides=PRIVY_COMPATIBLE_ENV``
    # to the gateway. Accept and ignore any kwargs — these tests cover the
    # JWT-only acceptance predicate, not the env-propagation contract.
    monkeypatch.setattr(agent, "PlaywrightStealthGateway", lambda **_: _NoopMcpContext())
    monkeypatch.setattr(
        agent, "RealBrowserSession", lambda *, gateway: _NoopMcpContext()
    )
    captured = {"called_with_email": None}

    def fake_establish(*, session, email, provider, **_kwargs):
        captured["called_with_email"] = email
        return _JwtOnlyCacheEntry()

    monkeypatch.setattr(agent, "establish_browser_session_for_create", fake_establish)

    config = SimpleNamespace(
        inputs={"prophet_email": "op@example.com", "email_provider": "gmail"}
    )

    ctx = agent._WarmCreateMarketUiContext(
        config=config, gateway=object(), transport=object()
    )

    # Pre-#670 this raises SessionEstablishmentFailed("prophet_session_unavailable")
    # because the wrapper rejects refresh_token="". Post-fix it must
    # land the entry and leave the context usable.
    ctx._open()

    assert ctx.cache_entry is not None
    assert ctx.cache_entry.jwt == "eyJ.fake.jwt"
    assert ctx.cache_entry.refresh_token == ""
    assert captured["called_with_email"] == "op@example.com"


def test_warm_context_open_still_rejects_missing_jwt(monkeypatch):
    """Empty JWT is still a hard failure — the wrapper isn't permissive."""

    monkeypatch.setattr(
        agent._playwright_mcp_gateway.PlaywrightStealthGateway,
        "_resolve_default_command",
        classmethod(lambda cls: ["node", "/fake/playwright-stealth/index.js"]),
    )
    # Issue #681: ``_open()`` now passes ``env_overrides=PRIVY_COMPATIBLE_ENV``
    # to the gateway. Accept and ignore any kwargs — these tests cover the
    # JWT-only acceptance predicate, not the env-propagation contract.
    monkeypatch.setattr(agent, "PlaywrightStealthGateway", lambda **_: _NoopMcpContext())
    monkeypatch.setattr(
        agent, "RealBrowserSession", lambda *, gateway: _NoopMcpContext()
    )
    monkeypatch.setattr(
        agent,
        "establish_browser_session_for_create",
        lambda **_kw: _JwtOnlyCacheEntry(jwt="", refresh_token=""),
    )

    config = SimpleNamespace(
        inputs={"prophet_email": "op@example.com", "email_provider": "gmail"}
    )
    ctx = agent._WarmCreateMarketUiContext(
        config=config, gateway=object(), transport=object()
    )

    with pytest.raises(SessionEstablishmentFailed) as exc:
        ctx._open()
    assert "prophet_session_unavailable" in str(exc.value)
