"""Issue #636: `restore_privy_session` warm-starts a Python-owned browser
into an authenticated Prophet origin by writing the cached JWT + refresh
token into `window.localStorage`. The Privy SDK serializes both values
via `JSON.stringify`, so each value must land wrapped in balanced
double-quotes for the SDK to read it back via `localStorage.getItem(k).slice(1,-1)`.

`window.localStorage` is partitioned by origin, so the function MUST
navigate to `https://app.prophetmarket.ai` BEFORE writing — otherwise
the writes land on `about:blank` and never reach the Prophet origin.
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
        self.calls: list[tuple[str, Any]] = []

    def navigate(self, url: str) -> None:
        self.calls.append(("navigate", url))

    def evaluate(self, script: str) -> Any:
        self.calls.append(("evaluate", script))
        return True


def test_privy_restore_writes_localstorage_after_origin_navigate() -> None:
    session = _StubSession()

    restore_privy_session(
        session, jwt="eyJ.j.w.t", refresh_token="rt_abc123"
    )

    # (a) navigate to the Prophet origin precedes every evaluate call.
    nav_indexes = [i for i, (op, _) in enumerate(session.calls) if op == "navigate"]
    evaluate_indexes = [
        i for i, (op, _) in enumerate(session.calls) if op == "evaluate"
    ]
    assert nav_indexes, "expected a navigate() call before any evaluate()"
    assert evaluate_indexes, "expected at least one evaluate() call"
    assert nav_indexes[0] < min(evaluate_indexes)
    # The navigate target must be the Prophet origin so localStorage writes
    # land in the right partition.
    assert session.calls[nav_indexes[0]][1] == PROPHET_APP_URL

    # (b) the privy:token write wraps the JWT in balanced double-quotes.
    scripts = [arg for op, arg in session.calls if op == "evaluate"]
    token_scripts = [s for s in scripts if PRIVY_TOKEN_LOCAL_STORAGE_KEY in s]
    assert token_scripts, (
        f"no evaluate() script referenced {PRIVY_TOKEN_LOCAL_STORAGE_KEY}"
    )
    # The JSON-quoted form of `eyJ.j.w.t` is `"eyJ.j.w.t"` — the script
    # must embed that balanced-quote literal so the Privy SDK can unwrap.
    assert '"\\"eyJ.j.w.t\\""' in token_scripts[0]

    # (c) same for privy:refresh_token.
    refresh_scripts = [
        s for s in scripts if PRIVY_REFRESH_LOCAL_STORAGE_KEY in s
    ]
    assert refresh_scripts, (
        f"no evaluate() script referenced {PRIVY_REFRESH_LOCAL_STORAGE_KEY}"
    )
    assert '"\\"rt_abc123\\""' in refresh_scripts[0]
