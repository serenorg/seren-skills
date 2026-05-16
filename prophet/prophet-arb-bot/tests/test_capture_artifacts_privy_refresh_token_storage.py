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


def test_capture_artifacts_falls_back_to_cookie_when_localstorage_is_empty() -> None:
    session = _StubSession(
        local_storage={},
        cookies={PRIVY_REFRESH_COOKIE: "rt_from_legacy_cookie"},
    )

    artifacts = capture_artifacts(session, jwt="eyJ.j.w.t")

    assert artifacts.refresh_token == "rt_from_legacy_cookie"
    assert PRIVY_REFRESH_LOCAL_STORAGE_KEY in session.local_storage_reads
    assert PRIVY_REFRESH_COOKIE in session.cookie_reads
