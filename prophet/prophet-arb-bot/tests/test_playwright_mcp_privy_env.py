"""Issue #681 — Privy-compatible env profile for the bundled playwright-stealth MCP.

Three critical tests, no duplicates:

1. ``test_gateway_propagates_env_overrides_to_popen``
   Constructor accepts ``env_overrides`` and merges it onto ``os.environ`` when
   spawning the MCP subprocess. Without this, the env vars Desktop #1957 added
   (``SEREN_PLAYWRIGHT_HEADLESS``/``..._STEALTH_EVASIONS_DISABLE``/etc.) never
   reach the Node child and Privy embedded-wallet provisioning stays broken.

2. ``test_gateway_default_does_not_pass_env_kwarg_to_popen``
   When no overrides are provided (every existing call site today + the OTP
   cold-start path that must keep stealth-on), ``subprocess.Popen`` MUST NOT
   receive an ``env`` kwarg. This pins the regression boundary: child inherits
   parent ``os.environ`` exactly as it did before #681.

3. ``test_warm_create_market_ui_context_passes_privy_compatible_env``
   The cycle-scoped ``_WarmCreateMarketUiContext`` at ``agent.py:2443``
   instantiates ``PlaywrightStealthGateway`` with the canonical Privy profile
   (HEADLESS=0, the two evasions dropped, page-init-patch=1). This is the live
   ``--command run --yes-live`` consumer that #681 is meant to unblock.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# Make the skill's ``scripts`` importable as the agent does.
SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from otp_worker import playwright_mcp_gateway as pmg


# Subset matching the issue's "Recommended profile for prophet-arb-bot /create".
# Pulled by name rather than hard-coded in the assertion so the source-of-truth
# constant in playwright_mcp_gateway.py is the thing under test.
EXPECTED_PRIVY_PROFILE_KEYS = {
    "SEREN_PLAYWRIGHT_HEADLESS": "0",
    "SEREN_PLAYWRIGHT_STEALTH_EVASIONS_DISABLE": (
        "iframe.contentWindow,navigator.permissions"
    ),
    "SEREN_PLAYWRIGHT_DISABLE_PAGE_INIT_PATCH": "1",
}


class _FakePopen:
    """Records the kwargs (including ``env``) that the gateway hands to Popen.

    Uses real OS pipes so the gateway's ``os.read(fd, ...)`` succeeds. The
    initialize response is pre-written into the stdout pipe before the test
    enters ``__enter__``.
    """

    last_init_kwargs: dict[str, Any] = {}
    _open_fds: list[int] = []

    def __init__(self, command: list[str], **kwargs: Any) -> None:
        type(self).last_init_kwargs = dict(kwargs)
        # Real pipes: gateway calls os.read(fd, ...) on stdout.fileno().
        stdout_r, stdout_w = os.pipe()
        stdin_r, stdin_w = os.pipe()
        stderr_r, stderr_w = os.pipe()
        for fd in (stdout_r, stdout_w, stdin_r, stdin_w, stderr_r, stderr_w):
            type(self)._open_fds.append(fd)
        # The gateway sends initialize with id=1 and reads until it sees the
        # matching id. Pre-fill the stdout pipe with a Content-Length-framed
        # JSON-RPC response now. The framing must match the writer side of
        # the gateway (see _write/_read in playwright_mcp_gateway.py).
        init_body = (
            b'{"jsonrpc":"2.0","id":1,"result":'
            b'{"protocolVersion":"2024-11-05"}}'
        )
        init_response = (
            f"Content-Length: {len(init_body)}\r\n\r\n".encode("ascii")
            + init_body
        )
        os.write(stdout_w, init_response)
        self.stdout = _FdReader(stdout_r)
        self.stdin = _FdWriter(stdin_w)
        self.stderr = _FdReader(stderr_r)
        self._terminated = False

    def poll(self) -> int | None:
        return 0 if self._terminated else None

    def terminate(self) -> None:
        self._terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self._terminated = True
        return 0

    def kill(self) -> None:
        self._terminated = True


class _FdReader:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            return b""
        return os.read(self._fd, n)

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


class _FdWriter:
    def __init__(self, fd: int) -> None:
        self._fd = fd
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return os.write(self._fd, data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _stub_subprocess(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Stub ``subprocess.Popen`` and the host process-walker."""
    _FakePopen.last_init_kwargs = {}
    _FakePopen._open_fds = []
    monkeypatch.setattr(pmg.subprocess, "Popen", _FakePopen)
    # Don't walk the host process table.
    monkeypatch.setattr(
        pmg,
        "kill_stale_playwright_mcp_processes",
        lambda *a, **kw: pmg.KillReport(),
    )
    yield
    # Cleanup any pipe fds the fake left open.
    for fd in _FakePopen._open_fds:
        try:
            os.close(fd)
        except OSError:
            pass


def test_gateway_propagates_env_overrides_to_popen() -> None:
    """env_overrides MUST reach Popen merged onto os.environ.

    This is the contract that connects prophet-arb-bot to Desktop #1957.
    Without ``env=`` propagation, the four ``SEREN_PLAYWRIGHT_*`` vars never
    reach the Node child and Privy provisioning stays broken.
    """
    overrides = dict(EXPECTED_PRIVY_PROFILE_KEYS)
    # Confirm parent env is also passed through unmodified — the merge has to
    # be additive, not replacing.
    sentinel_key = "SEREN_TEST_SENTINEL_681"
    sentinel_value = "preserve-me"
    os.environ[sentinel_key] = sentinel_value
    try:
        with pmg.PlaywrightStealthGateway(
            command=["/usr/bin/true"],
            env_overrides=overrides,
            timeout_seconds=5.0,
        ):
            pass
    finally:
        os.environ.pop(sentinel_key, None)

    env_kwarg = _FakePopen.last_init_kwargs.get("env")
    assert env_kwarg is not None, (
        "Expected Popen to receive env=<merged dict> when env_overrides is set; "
        "got no env kwarg, meaning the child still inherits the parent env "
        "and the Privy profile never reaches the Node MCP."
    )
    # Overrides present.
    for k, v in overrides.items():
        assert env_kwarg.get(k) == v, (
            f"Expected {k}={v!r} in merged Popen env; saw {env_kwarg.get(k)!r}"
        )
    # Parent env preserved.
    assert env_kwarg.get(sentinel_key) == sentinel_value, (
        "Override merge must extend os.environ, not replace it."
    )


def test_gateway_default_does_not_pass_env_kwarg_to_popen() -> None:
    """Default behavior MUST be unchanged.

    Existing call sites (including OTP cold-start at agent.py:431) must keep
    inheriting the parent environment exactly as they did before #681. The
    backward-compat boundary is: no env_overrides → no env kwarg to Popen.
    """
    with pmg.PlaywrightStealthGateway(
        command=["/usr/bin/true"],
        timeout_seconds=5.0,
    ):
        pass

    # The contract is "no env kwarg" (or env=None). Either is acceptable;
    # both yield "child inherits parent env" semantics.
    env_kwarg = _FakePopen.last_init_kwargs.get("env", "MISSING")
    assert env_kwarg in (None, "MISSING"), (
        "Default construction must not pass an explicit env to Popen. "
        f"Got env={env_kwarg!r}, which would change inherit semantics."
    )


def test_warm_create_market_ui_context_passes_privy_compatible_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_WarmCreateMarketUiContext._open MUST spawn the gateway with the profile.

    This is the live ``--command run --yes-live`` consumer that #681 unblocks.
    We monkeypatch ``PlaywrightStealthGateway`` in the agent module and
    introspect the kwargs the warm context passes. The Privy profile is the
    canonical subset documented in the issue ("Recommended profile").
    """
    import agent  # noqa: WPS433 — imported lazily to avoid top-level side effects.

    # The constant lives next to the gateway and is the source of truth.
    assert hasattr(pmg, "PRIVY_COMPATIBLE_ENV"), (
        "playwright_mcp_gateway must export PRIVY_COMPATIBLE_ENV as the "
        "canonical /create profile so consumers don't reinvent it."
    )
    profile = pmg.PRIVY_COMPATIBLE_ENV
    # Source-of-truth shape check.
    assert profile == EXPECTED_PRIVY_PROFILE_KEYS, (
        f"PRIVY_COMPATIBLE_ENV drifted: expected {EXPECTED_PRIVY_PROFILE_KEYS}, "
        f"got {profile}"
    )

    captured: dict[str, Any] = {}

    class _SpyGateway:
        """Stand-in that captures constructor kwargs and short-circuits enter."""

        _resolve_default_command = staticmethod(lambda: ["/usr/bin/true"])

        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = dict(kwargs)

        def __enter__(self) -> "_SpyGateway":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        # Methods the warm context calls between entries.
        def reset_for_next_entry(self) -> None:
            return None

        def is_session_healthy(self) -> bool:
            return True

    # Stop the warm context's nested establish_browser_session_for_create from
    # running — we only care that the gateway constructor saw the profile.
    # The wrapper checks ``cache_entry.jwt`` so the fake must expose a
    # truthy value or it will raise SessionEstablishmentFailed before we
    # can read the captured kwargs.
    fake_cache_entry = types.SimpleNamespace(jwt="fake-jwt-for-test")

    def _fake_establish(**kwargs: Any) -> Any:
        return fake_cache_entry

    # Don't let RealBrowserSession (or any later code path) execute either.
    class _SpySession:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "_SpySession":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(agent, "PlaywrightStealthGateway", _SpyGateway)
    monkeypatch.setattr(agent, "RealBrowserSession", _SpySession)
    monkeypatch.setattr(
        agent, "establish_browser_session_for_create", _fake_establish
    )
    # _WarmCreateMarketUiContext does a sanity probe on the module-level
    # gateway's resolver too — make sure that one returns a command.
    monkeypatch.setattr(
        agent._playwright_mcp_gateway.PlaywrightStealthGateway,
        "_resolve_default_command",
        staticmethod(lambda: ["/usr/bin/true"]),
    )

    cfg = types.SimpleNamespace(
        inputs={"prophet_email": "x@example.com", "email_provider": "gmail"}
    )
    sentinel_gateway = object()
    sentinel_transport = object()

    ctx = agent._WarmCreateMarketUiContext(
        config=cfg,
        gateway=sentinel_gateway,
        transport=sentinel_transport,
    )
    with ctx:
        pass

    assert "kwargs" in captured, (
        "_WarmCreateMarketUiContext._open did not instantiate "
        "PlaywrightStealthGateway. Wiring is broken."
    )
    env_overrides = captured["kwargs"].get("env_overrides")
    assert env_overrides == profile, (
        "_WarmCreateMarketUiContext._open must pass "
        "env_overrides=PRIVY_COMPATIBLE_ENV to the gateway so the live "
        "--command run --yes-live /create path actually exercises the "
        "Desktop #1957 fix. "
        f"Expected {profile!r}, got {env_overrides!r}."
    )
