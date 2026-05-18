"""Issue #676: roll back the privy:pat / privy:id_token contract added by
PR #675 (#674) and instead capture/restore the keys the Privy SDK actually
writes on the live Prophet site.

Manual MCP walk-through against ``https://app.prophetmarket.ai`` after a
fresh OTP login (2026-05-18) enumerated exactly six ``privy:*``
localStorage keys: ``privy:token``, ``privy:refresh_token`` (literal
``"deprecated"`` post-#666), ``privy:connections`` (the embedded wallet
the ``/create`` signing flow uses), ``privy:caid``, the namespaced
``privy:<app_id>:recent-login-method``, and a transient
``privy:sent:<app_id>:<nonce>`` OTP marker. ``privy:pat`` and
``privy:id_token`` were absent in localStorage, sessionStorage,
cookies, and IndexedDB — they are not part of Privy's session contract
on Prophet.

This test pins the corrected three-leg contract end-to-end:

1. ``capture_artifacts`` reads ``privy:connections``, ``privy:caid``,
   and ``privy:<PROPHET_PRIVY_APP_ID>:recent-login-method`` from
   localStorage at OTP-completion time.
2. ``SessionCacheEntry`` carries the new fields on disk so the next
   cycle's restore has them, and the dropped ``privy_pat`` /
   ``privy_id_token`` slots no longer exist on the dataclass.
3. ``_build_init_script`` plants the three new keys when the cache
   carries them, skips empty values, and never references
   ``privy:pat`` or ``privy:id_token`` (rollback assertion).
"""

from __future__ import annotations

import json
from pathlib import Path

from otp_worker.playwright_client import (
    PROPHET_PRIVY_APP_ID,
    PRIVY_CAID_LOCAL_STORAGE_KEY,
    PRIVY_CONNECTIONS_LOCAL_STORAGE_KEY,
    PRIVY_RECENT_LOGIN_METHOD_LOCAL_STORAGE_KEY,
    PRIVY_REFRESH_LOCAL_STORAGE_KEY,
    PRIVY_TOKEN_LOCAL_STORAGE_KEY,
    capture_artifacts,
)
from otp_worker.privy_restore import _build_init_script
from otp_worker.session_cache import SessionCache, SessionCacheEntry


_CONNECTIONS_JSON = (
    '[{"address":"0x8C2D2B60D40dF744235fB4918064955C193bDaEf",'
    '"connectorType":"embedded","walletClientType":"privy"}]'
)
_CAID_VALUE = "a614238d-fe9d-4854-92ee-500c7b77a363"


class _StubSession:
    """Mimics what the Privy SDK leaves in localStorage after a successful OTP."""

    def __init__(
        self,
        *,
        connections: str,
        caid: str,
        recent_login_method: str,
        refresh_token: str,
    ) -> None:
        self._local = {
            PRIVY_REFRESH_LOCAL_STORAGE_KEY: (
                f'"{refresh_token}"' if refresh_token else ""
            ),
            # connections is a JSON ARRAY — the SDK persists it as a
            # JSON-stringified array (no outer surrounding quotes).
            PRIVY_CONNECTIONS_LOCAL_STORAGE_KEY: connections,
            # caid + recent-login-method are JSON-quoted strings.
            PRIVY_CAID_LOCAL_STORAGE_KEY: f'"{caid}"' if caid else "",
            PRIVY_RECENT_LOGIN_METHOD_LOCAL_STORAGE_KEY: (
                f'"{recent_login_method}"' if recent_login_method else ""
            ),
        }

    def get_local_storage(self, key: str) -> str:
        return self._local.get(key, "")

    def get_cookie(self, name: str) -> str:
        return ""


def test_full_privy_state_round_trips_capture_cache_restore(tmp_path: Path) -> None:
    # The namespaced recent-login-method key embeds Prophet's stable
    # Privy app ID — pin the constant so future renames are caught.
    assert PRIVY_RECENT_LOGIN_METHOD_LOCAL_STORAGE_KEY == (
        f"privy:{PROPHET_PRIVY_APP_ID}:recent-login-method"
    )

    # 1. CAPTURE: the bot reads the three SDK-written Privy keys post-OTP.
    session = _StubSession(
        connections=_CONNECTIONS_JSON,
        caid=_CAID_VALUE,
        recent_login_method="email",
        refresh_token="",  # post-#666 normalized state
    )
    artifacts = capture_artifacts(session, jwt="jwt_value")
    assert artifacts.jwt == "jwt_value"
    assert artifacts.refresh_token == ""  # #666 contract preserved
    assert artifacts.privy_connections == _CONNECTIONS_JSON
    assert artifacts.privy_caid == _CAID_VALUE
    assert artifacts.privy_recent_login_method == "email"

    # The rolled-back fields must not exist on the dataclass anymore.
    assert not hasattr(artifacts, "privy_pat")
    assert not hasattr(artifacts, "privy_id_token")

    # 2. CACHE: write + read round-trip preserves the new fields.
    cache_path = tmp_path / "privy_session.json"
    cache = SessionCache(path=cache_path)
    cache.write(
        SessionCacheEntry(
            jwt="jwt_value",
            refresh_token="",
            privy_connections=_CONNECTIONS_JSON,
            privy_caid=_CAID_VALUE,
            privy_recent_login_method="email",
            state="fresh",
        )
    )
    raw = json.loads(cache_path.read_text())
    assert raw["privy_connections"] == _CONNECTIONS_JSON
    assert raw["privy_caid"] == _CAID_VALUE
    assert raw["privy_recent_login_method"] == "email"
    # The dropped fields must not be persisted.
    assert "privy_pat" not in raw
    assert "privy_id_token" not in raw

    reloaded = cache.read()
    assert reloaded.privy_connections == _CONNECTIONS_JSON
    assert reloaded.privy_caid == _CAID_VALUE
    assert reloaded.privy_recent_login_method == "email"

    # 3. RESTORE: init script plants the three SDK-required keys.
    script = _build_init_script(
        jwt="jwt_value",
        refresh_token="",
        privy_connections=_CONNECTIONS_JSON,
        privy_caid=_CAID_VALUE,
        privy_recent_login_method="email",
    )
    # All three new keys land via setItem on the correct key name.
    assert 'localStorage.setItem("privy:token"' in script
    assert 'localStorage.setItem("privy:connections"' in script
    assert 'localStorage.setItem("privy:caid"' in script
    assert (
        'localStorage.setItem("privy:' + PROPHET_PRIVY_APP_ID
        + ':recent-login-method"'
    ) in script
    # Rollback assertion: the script must NOT reference the dead keys.
    assert "privy:pat" not in script
    assert "privy:id_token" not in script
    # Issue #710: refresh_token IS planted, with the literal
    # "deprecated" sentinel filling in for the empty cache value.
    # See _build_init_script docstring for why skipping the key tore
    # down the planted session.
    assert 'localStorage.setItem("privy:refresh_token", "\\"deprecated\\"")' in script


def test_build_init_script_skips_empty_new_keys() -> None:
    # Legacy operators whose OTP happened pre-#676 carry empty
    # privy_connections / privy_caid / privy_recent_login_method until
    # they re-OTP. Restore must skip those setters rather than plant
    # empty strings (the SDK reads an empty plant as corruption, same
    # failure mode #666 and #674 documented).
    #
    # Issue #710: privy:refresh_token is the exception — Privy plants
    # the literal "deprecated" sentinel itself at login post-migration,
    # so restore plants it too rather than skipping (skipping = SDK
    # reads null = session torn down).
    script = _build_init_script(
        jwt="jwt_value",
        refresh_token="",
        privy_connections="",
        privy_caid="",
        privy_recent_login_method="",
    )
    assert 'localStorage.setItem("privy:token"' in script
    assert "privy:connections" not in script
    assert "privy:caid" not in script
    assert "recent-login-method" not in script
    # Issue #710: refresh_token IS planted with the sentinel value.
    assert 'localStorage.setItem("privy:refresh_token", "\\"deprecated\\"")' in script
    # Rollback: the dropped keys stay absent even in the empty case.
    assert "privy:pat" not in script
    assert "privy:id_token" not in script
