"""Issue #666: legacy on-disk caches that already contain
``refresh_token == "deprecated"`` must self-heal on the next read.

Without this, the operator's failed cycle leaves a poison-pill cache on
disk (``state/privy_session.json``) and every subsequent ``--command run
--yes-live`` cycle replants the marker, gets rejected by Privy SDK, and
falls through to OTP cold-start. The user has to manually wipe the
cache file — which is exactly the kind of footgun #664 was filed to
get rid of.

The fix is one assignment in ``SessionCache.read``: after JSON-loading
the payload, if ``refresh_token == "deprecated"``, set it to empty.
Downstream code already tolerates empty refresh_token (see
``restore_privy_session`` and ``establish_browser_session_for_create``).

This test pins the contract: reading a poison-pill cache returns an
entry that ``is_fresh()`` AND has ``refresh_token == ""``, so the
warm-context path can immediately enter the cache-fresh branch on a
JWT-only basis instead of forcing a fresh OTP.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from otp_worker.session_cache import SessionCache


def test_session_cache_read_normalizes_legacy_deprecated_marker_to_empty(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PROPHET_ARB_STATE_DIR", str(tmp_path))

    # The exact shape the user's poison-pill cache had on disk: state
    # is "fresh", JWT is well-formed and not expired, but Privy's
    # post-login state writes "deprecated" into privy:refresh_token
    # localStorage which capture_artifacts then captured verbatim.
    poison = {
        "user_email": "operator@example.com",
        "jwt": "eyJ.header.body.sig",
        "jwt_expires_at": "2099-12-31T23:59:59+00:00",
        "refresh_token": "deprecated",
        "privy_session_cookie": "cookieval",
        "last_refreshed_at": "2026-05-18T01:27:50+00:00",
        "state": "fresh",
        "consecutive_refresh_failures": 0,
        "prophet_viewer_id": "vid_abc",
    }
    cache_path = tmp_path / "privy_session.json"
    cache_path.write_text(json.dumps(poison), encoding="utf-8")

    entry = SessionCache().read()

    # The marker MUST be normalized to empty on read so existing
    # operators recover on the next cycle without manually wiping
    # state/privy_session.json. The rest of the entry is unchanged —
    # JWT is still fresh, state is still "fresh", viewer id preserved.
    assert entry.refresh_token == "", (
        f'legacy "deprecated" marker must be normalized to empty on read; '
        f"got {entry.refresh_token!r}"
    )
    assert entry.is_fresh() is True
    assert entry.jwt == "eyJ.header.body.sig"
    assert entry.state == "fresh"
    assert entry.prophet_viewer_id == "vid_abc"
