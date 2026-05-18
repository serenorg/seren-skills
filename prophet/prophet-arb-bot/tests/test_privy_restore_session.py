"""Issue #638: `restore_privy_session` plants the cached JWT + refresh token
into the caller's browser via `playwright_add_init_script`, then navigates
to the Prophet origin exactly once.

The Privy SDK serializes both values via `JSON.stringify`, so each token
must land in `localStorage` wrapped in balanced double-quotes for the SDK
to unwrap correctly via `localStorage.getItem(k).slice(1, -1)`. The init
script runs at `document_start` on every navigation, so the SDK observes
the planted state on its first read — the old "navigate, write via
evaluate, re-navigate to force SDK re-boot" workaround is gone.
"""

from __future__ import annotations

from typing import Any

from otp_worker.playwright_client import (
    PRIVY_REFRESH_LOCAL_STORAGE_KEY,
    PRIVY_TOKEN_LOCAL_STORAGE_KEY,
    PROPHET_APP_URL,
)
from otp_worker.privy_restore import restore_privy_session


class _StubSession:
    def __init__(self) -> None:
        self.init_scripts: list[str] = []
        self.navigations: list[str] = []
        self.evaluations: list[str] = []
        self.cookies_added: list[list[dict[str, Any]]] = []

    def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        # Issue #705: capture the cookie payload + a snapshot of the
        # init-script count and navigation count at the moment of the
        # add_cookies call. The cookie must land BEFORE the navigate
        # so Prophet's middleware sees it on the first /create request.
        self.cookies_added.append(
            {
                "cookies": list(cookies),
                "init_scripts_at_call": len(self.init_scripts),
                "navigations_at_call": len(self.navigations),
            }
        )

    def navigate(self, url: str) -> None:
        self.navigations.append(url)

    def evaluate(self, script: str) -> Any:
        # The new restore path must not need `evaluate` to plant state.
        self.evaluations.append(script)
        return True


def test_privy_restore_registers_init_script_before_navigate() -> None:
    session = _StubSession()

    restore_privy_session(
        session, jwt="eyJ.j.w.t", refresh_token="rt_abc123"
    )

    # (a) Exactly one init script registered, exactly one navigate, and
    # the init script registration must precede the navigate so it fires
    # at document_start on the first load of the Prophet origin.
    assert len(session.init_scripts) == 1, session.init_scripts
    assert session.navigations == [PROPHET_APP_URL], session.navigations

    # (b) No `evaluate()` calls — the restore path is entirely driven by
    # the document_start init script now. Any evaluate call would mean
    # we're racing the Privy SDK's boot read again.
    assert session.evaluations == [], session.evaluations


def test_privy_restore_init_script_wraps_tokens_in_balanced_quotes() -> None:
    session = _StubSession()

    restore_privy_session(
        session, jwt="eyJ.j.w.t", refresh_token="rt_abc123"
    )

    script = session.init_scripts[0]

    # (a) Both localStorage keys referenced.
    assert PRIVY_TOKEN_LOCAL_STORAGE_KEY in script, script
    assert PRIVY_REFRESH_LOCAL_STORAGE_KEY in script, script

    # (b) Each value is wrapped in literal balanced double-quotes inside
    # the JS string. The JSON-quoted form of `eyJ.j.w.t` is `"eyJ.j.w.t"`;
    # embedding that as a JS string literal escapes the surrounding
    # quotes as `\"`. The SDK then strips that wrapping via
    # `localStorage.getItem(k).slice(1, -1)`.
    assert '"\\"eyJ.j.w.t\\""' in script, script
    assert '"\\"rt_abc123\\""' in script, script

    # (c) Origin guard present so the planted state cannot leak to
    # about:blank or any subframe of a non-Prophet origin.
    assert "app.prophetmarket.ai" in script, script


def test_privy_restore_plants_deprecated_sentinel_when_refresh_token_empty() -> None:
    """Issue #710: when the cached ``refresh_token`` is empty (the post-#666
    cache shape — Privy SDK writes the literal ``"deprecated"`` sentinel
    into ``privy:refresh_token`` post-OTP, and capture/cache normalize it
    to empty for on-disk hygiene), the restore init script MUST plant the
    sentinel back into localStorage rather than skip the key entirely.

    Skipping the key was the pre-#710 behavior: the boot-time Privy SDK
    then read ``null`` from ``privy:refresh_token``, treated the session
    as corrupt, called ``destroyLocalState``, and tore down the JWT and
    connections we'd just planted. Prophet middleware then redirected
    ``/create`` to ``/?returnTo=%2Fcreate`` on every restored cycle.

    Pin the contract: empty refresh_token in → the literal
    JSON-stringified ``"deprecated"`` planted out. The SDK strips the
    JSON wrapping via ``localStorage.getItem(k).slice(1, -1)`` and reads
    the bare string ``deprecated``.
    """
    session = _StubSession()

    restore_privy_session(session, jwt="eyJ.j.w.t", refresh_token="")

    assert len(session.init_scripts) == 1, session.init_scripts
    script = session.init_scripts[0]

    # The refresh-token localStorage key MUST be referenced — not skipped.
    assert PRIVY_REFRESH_LOCAL_STORAGE_KEY in script, script

    # The planted value MUST be the JSON-stringified `"deprecated"`
    # sentinel, embedded as a JS string literal. JSON-stringifying the
    # Python string `deprecated` gives `"deprecated"`; embedding that
    # as a JS string literal escapes the surrounding quotes as `\"`.
    assert '"\\"deprecated\\""' in script, script


def test_privy_restore_plants_real_refresh_token_when_cache_has_one() -> None:
    """Issue #710 forward-compat: if the cache somehow carries a real
    (non-empty, non-sentinel) refresh_token — for example an operator
    on an older Privy install that still issues refresh tokens, or a
    future Privy migration that brings them back — restore plants the
    real value, not the sentinel.
    """
    session = _StubSession()

    restore_privy_session(session, jwt="eyJ.j.w.t", refresh_token="rt_real")

    script = session.init_scripts[0]

    assert '"\\"rt_real\\""' in script, script
    # And specifically NOT the sentinel value — pre-empts a future
    # refactor that accidentally always plants "deprecated".
    assert '"\\"deprecated\\""' not in script, script


def test_privy_restore_plants_session_cookie_before_navigate() -> None:
    """Issue #705: when the cache carries a `privy_session_cookie`,
    `restore_privy_session` MUST plant it via `session.add_cookies(...)`
    BEFORE the navigate so Prophet's server-side middleware sees the
    HttpOnly cookie on its first `/create` request. Without this, every
    cycle lands on `/?returnTo=%2Fcreate` (the unauth redirect target).

    Pin the exact cookie attributes (name, value, domain, security
    flags) — any future refactor that ships an unguarded cookie payload
    must explicitly update this contract.
    """
    session = _StubSession()

    restore_privy_session(
        session,
        jwt="eyJ.j.w.t",
        refresh_token="rt_abc123",
        privy_session_cookie="sess_xyz_789",
    )

    # add_cookies fires exactly once with one cookie.
    assert len(session.cookies_added) == 1, session.cookies_added
    call = session.cookies_added[0]
    assert len(call["cookies"]) == 1
    cookie = call["cookies"][0]

    assert cookie["name"] == "privy-session"
    assert cookie["value"] == "sess_xyz_789"
    assert cookie["domain"] == "app.prophetmarket.ai"
    assert cookie["path"] == "/"
    # Privy sets the cookie HttpOnly+Secure at login; the restored
    # cookie MUST match so middleware accepts it.
    assert cookie["httpOnly"] is True
    assert cookie["secure"] is True
    assert cookie["sameSite"] == "Lax"

    # Ordering: cookie planting MUST precede the navigate. The
    # snapshots prove the add_cookies call happened when zero
    # navigations had been issued yet.
    assert call["navigations_at_call"] == 0, (
        "add_cookies must precede navigate so Prophet's middleware sees "
        "the cookie on the very first request to /create"
    )


def test_privy_restore_plants_both_cookies_when_both_present() -> None:
    """Issue #707: Privy SDK writes BOTH `privy-token` and `privy-session`
    at login. Prophet middleware checks both. #706 restored only
    `privy-session` and /create still redirected — this test pins that
    both cookies survive into the single add_cookies call when both are
    in the cache, with the JWT-bearing token cookie carrying the same
    HttpOnly+Secure+Lax attributes Privy set at login.
    """
    session = _StubSession()

    restore_privy_session(
        session,
        jwt="eyJ.j.w.t",
        refresh_token="",
        privy_session_cookie="sess_xyz_789",
        privy_token_cookie="tok_abc_123",
    )

    assert len(session.cookies_added) == 1, session.cookies_added
    cookies = session.cookies_added[0]["cookies"]

    by_name = {c["name"]: c for c in cookies}
    assert set(by_name.keys()) == {"privy-session", "privy-token"}

    tok = by_name["privy-token"]
    assert tok["value"] == "tok_abc_123"
    assert tok["domain"] == "app.prophetmarket.ai"
    assert tok["path"] == "/"
    assert tok["httpOnly"] is True
    assert tok["secure"] is True
    assert tok["sameSite"] == "Lax"

    # Both cookies must land BEFORE the navigate so middleware sees them
    # on the very first /create request.
    assert session.cookies_added[0]["navigations_at_call"] == 0


def test_privy_restore_skips_cookie_when_cache_has_no_value() -> None:
    """Issue #705: an empty `privy_session_cookie` (legacy cache, or a
    capture path that hasn't been wired yet) MUST NOT trigger an
    add_cookies call with an empty value — that would clear the cookie
    domain-wide and make the situation worse, not better.
    """
    session = _StubSession()

    restore_privy_session(
        session,
        jwt="eyJ.j.w.t",
        refresh_token="rt_abc123",
        # privy_session_cookie omitted — exercises the empty default
    )

    assert session.cookies_added == [], (
        "no cookie payload should be planted when the cache field is empty"
    )
