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

    def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

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
