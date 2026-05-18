"""Issue #674: capture and restore the SDK's full ``privy:*`` localStorage state.

A manual diagnostic probe with a ``localStorage.removeItem`` hook on the
Prophet origin showed that the Privy SDK calls ``Dh.destroyLocalState``
within ~550ms of page boot when only ``privy:token`` is present —
wiping ``privy:token``, ``privy:refresh_token``, ``privy:pat`` (Privy
access token), and ``privy:id_token`` (identity token) together. The
SDK treats a partial keyset as a corrupted login and forces a clean
sign-in.

This test pins the three-leg fix:

1. ``capture_artifacts`` reads ``privy:pat`` and ``privy:id_token``
   alongside ``privy:token`` and ``privy:refresh_token`` from
   localStorage at OTP-completion time.
2. ``SessionCacheEntry`` carries the two new fields on disk so the
   next cycle's restore has them.
3. ``_build_init_script`` plants all three required keys when the
   cache carries them, and correctly skips empty values (per #666's
   "no empty/sentinel writes" contract that triggered the same SDK
   wipe).
"""

from __future__ import annotations

import json
from pathlib import Path

from otp_worker.playwright_client import (
    PRIVY_ID_TOKEN_LOCAL_STORAGE_KEY,
    PRIVY_PAT_LOCAL_STORAGE_KEY,
    PRIVY_REFRESH_LOCAL_STORAGE_KEY,
    PRIVY_TOKEN_LOCAL_STORAGE_KEY,
    capture_artifacts,
)
from otp_worker.privy_restore import _build_init_script
from otp_worker.session_cache import SessionCache, SessionCacheEntry


class _StubSession:
    """Mimics what the Privy SDK leaves in localStorage after a successful OTP."""

    def __init__(self, *, pat: str, id_token: str, refresh_token: str) -> None:
        self._local = {
            PRIVY_REFRESH_LOCAL_STORAGE_KEY: f'"{refresh_token}"' if refresh_token else "",
            PRIVY_PAT_LOCAL_STORAGE_KEY: f'"{pat}"' if pat else "",
            PRIVY_ID_TOKEN_LOCAL_STORAGE_KEY: f'"{id_token}"' if id_token else "",
        }

    def get_local_storage(self, key: str) -> str:
        return self._local.get(key, "")

    def get_cookie(self, name: str) -> str:
        return ""


def test_full_privy_state_round_trips_capture_cache_restore(tmp_path: Path) -> None:
    # 1. CAPTURE: the bot reads all three required Privy keys post-OTP.
    session = _StubSession(
        pat="pat_token_value",
        id_token="id_token_value",
        refresh_token="",  # post-#666 normalized state
    )
    artifacts = capture_artifacts(session, jwt="jwt_value")
    assert artifacts.jwt == "jwt_value"
    assert artifacts.refresh_token == ""  # #666 contract preserved
    assert artifacts.privy_pat == "pat_token_value"
    assert artifacts.privy_id_token == "id_token_value"

    # 2. CACHE: write + read round-trip preserves the new fields.
    cache_path = tmp_path / "privy_session.json"
    cache = SessionCache(path=cache_path)
    cache.write(
        SessionCacheEntry(
            jwt="jwt_value",
            refresh_token="",
            privy_pat="pat_token_value",
            privy_id_token="id_token_value",
            state="fresh",
        )
    )
    raw = json.loads(cache_path.read_text())
    assert raw["privy_pat"] == "pat_token_value"
    assert raw["privy_id_token"] == "id_token_value"
    reloaded = cache.read()
    assert reloaded.privy_pat == "pat_token_value"
    assert reloaded.privy_id_token == "id_token_value"

    # 3. RESTORE: init script plants all three required keys.
    script = _build_init_script(
        jwt="jwt_value",
        refresh_token="",  # post-#666: empty → no setter
        privy_pat="pat_token_value",
        privy_id_token="id_token_value",
    )
    # All three required keys land via setItem.
    assert 'localStorage.setItem("privy:token"' in script
    assert 'localStorage.setItem("privy:pat"' in script
    assert 'localStorage.setItem("privy:id_token"' in script
    # And refresh_token does NOT — empty values are skipped per #666 (planting
    # an empty value also triggers Dh.destroyLocalState, same root cause).
    assert "privy:refresh_token" not in script


def test_build_init_script_skips_empty_pat_and_id_token() -> None:
    # Legacy operators whose OTP happened pre-#674 carry empty privy_pat /
    # privy_id_token until they re-OTP. Restore must skip both setters
    # rather than plant empty strings (the SDK reads an empty plant as
    # corruption, exactly the failure mode #674 documents).
    script = _build_init_script(
        jwt="jwt_value",
        refresh_token="",
        privy_pat="",
        privy_id_token="",
    )
    assert 'localStorage.setItem("privy:token"' in script
    assert "privy:pat" not in script
    assert "privy:id_token" not in script
    assert "privy:refresh_token" not in script
