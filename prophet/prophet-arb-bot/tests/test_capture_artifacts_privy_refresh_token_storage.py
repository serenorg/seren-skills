"""Issue #583: Privy migrated the refresh token from an HttpOnly cookie to
`window.localStorage["privy:refresh_token"]`. `capture_artifacts` must read
the new storage location, JSON-unwrap the value (Privy's SDK JSON-stringifies
localStorage values — same pattern as `privy:token`), and fall back to the
legacy cookie only when localStorage is empty so operators on older Privy
installs do not regress.

Two assertions:

1. localStorage hit: returns the unwrapped refresh token from localStorage
   and never consults the cookie jar.
2. localStorage miss → cookie fallback: returns the legacy cookie value.

The unwrap behavior matches the existing `_unwrap_jwt` seam at
playwright_client.py:95-110 and is exercised by sending the
JSON-stringified form Privy actually writes (`'"abc..."'`).
"""

from __future__ import annotations

from typing import Any

from otp_worker.playwright_client import (
    PRIVY_REFRESH_COOKIE,
    PRIVY_REFRESH_LOCAL_STORAGE_KEY,
    capture_artifacts,
)


class _StubSession:
    """Minimal `BrowserSession` stand-in that records lookups."""

    def __init__(
        self,
        *,
        local_storage: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self._local_storage = local_storage or {}
        self._cookies = cookies or {}
        self.cookie_reads: list[str] = []
        self.local_storage_reads: list[str] = []

    def get_local_storage(self, key: str) -> str | None:
        self.local_storage_reads.append(key)
        return self._local_storage.get(key)

    def get_cookie(self, name: str) -> str | None:
        self.cookie_reads.append(name)
        return self._cookies.get(name)

    # Protocol surface methods that capture_artifacts never calls.
    def navigate(self, url: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None: ...
    def get_url(self) -> str:
        return ""

    def is_checked(self, selector: str) -> bool:
        return False


def test_capture_artifacts_reads_refresh_token_from_localstorage_and_unwraps_json_quotes() -> None:
    session = _StubSession(
        local_storage={PRIVY_REFRESH_LOCAL_STORAGE_KEY: '"rt_abc123"'},
        cookies={PRIVY_REFRESH_COOKIE: "stale_legacy_value"},
    )

    artifacts = capture_artifacts(session, jwt="eyJ.j.w.t")

    assert artifacts.jwt == "eyJ.j.w.t"
    assert artifacts.refresh_token == "rt_abc123"
    assert PRIVY_REFRESH_LOCAL_STORAGE_KEY in session.local_storage_reads
    assert PRIVY_REFRESH_COOKIE not in session.cookie_reads


def test_capture_artifacts_treats_privy_deprecated_marker_as_empty_refresh_token() -> None:
    """Issue #666: Privy now writes the literal string ``"deprecated"`` into
    ``privy:refresh_token`` as a migration sentinel — they retired the
    cookie/localStorage refresh mechanism server-side and a single
    long-lived JWT is now the session. Our old code captured that marker
    verbatim and replanted it on restore; the SDK then rejected the
    session because the planted refresh_token was not a real Privy token.

    The fix: normalize the marker to empty at capture time so we never
    write a poison-pill cache. Downstream code already tolerates an
    empty refresh_token (see ``restore_privy_session`` and the
    cache-fresh guard in ``establish_browser_session_for_create``).

    The unwrap order is preserved: the marker stays JSON-stringified in
    localStorage as ``'"deprecated"'``, so the wrapped form must also
    be recognized — not just the bare string.
    """
    session = _StubSession(
        # Both the bare form and the JSON-wrapped form Privy actually
        # writes must be recognized as the deprecation sentinel.
        local_storage={PRIVY_REFRESH_LOCAL_STORAGE_KEY: '"deprecated"'},
        cookies={PRIVY_REFRESH_COOKIE: "stale_legacy_cookie_value"},
    )

    artifacts = capture_artifacts(session, jwt="eyJ.j.w.t")

    # The marker MUST be normalized to empty — not the literal
    # "deprecated" string, not the JSON-quoted form. An empty
    # refresh_token signals "JWT-only session" to every downstream
    # consumer (cache, restore, establish), which is what Privy's
    # actual server-side state is now.
    assert artifacts.refresh_token == "", (
        f'capture must normalize "deprecated" marker to empty refresh_token; '
        f"got {artifacts.refresh_token!r}"
    )
    # The cookie fallback must NOT fire just because the marker was
    # rejected — the cookie value is from a prior Privy era and is
    # equally stale. Capturing it would just resurrect the poison pill
    # under a different name.
    assert PRIVY_REFRESH_COOKIE not in session.cookie_reads, (
        "cookie fallback must not fire on deprecation marker — "
        f"reads={session.cookie_reads}"
    )


def test_capture_artifacts_falls_back_to_cookie_when_localstorage_is_empty() -> None:
    session = _StubSession(
        local_storage={},
        cookies={PRIVY_REFRESH_COOKIE: "rt_from_legacy_cookie"},
    )

    artifacts = capture_artifacts(session, jwt="eyJ.j.w.t")

    assert artifacts.refresh_token == "rt_from_legacy_cookie"
    assert PRIVY_REFRESH_LOCAL_STORAGE_KEY in session.local_storage_reads
    assert PRIVY_REFRESH_COOKIE in session.cookie_reads
