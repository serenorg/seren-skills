"""JWT + refresh-token cache for the Privy session.

A single JSON file at
~/.config/seren/skills/prophet-arb-bot/state/privy_session.json
with permissions 0600. Atomic writes (write-then-rename). Never logged,
never persisted to SerenDB. The path can be overridden with the
`PROPHET_ARB_STATE_DIR` environment variable.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

CacheState = Literal["fresh", "needs_refresh", "needs_otp"]


@dataclass
class SessionCacheEntry:
    user_email: str = ""
    jwt: str = ""
    jwt_expires_at: str = ""
    refresh_token: str = ""
    privy_session_cookie: str = ""
    last_refreshed_at: str = ""
    state: CacheState = "needs_otp"
    consecutive_refresh_failures: int = 0
    prophet_viewer_id: str = ""

    def is_fresh(self, *, leeway_seconds: int = 60) -> bool:
        """JWT is usable now and not within `leeway_seconds` of expiry."""
        if self.state != "fresh" or not self.jwt or not self.jwt_expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.jwt_expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        now = datetime.now(timezone.utc)
        return (exp - now).total_seconds() > leeway_seconds


def default_cache_path() -> Path:
    override = os.environ.get("PROPHET_ARB_STATE_DIR") or ""
    base = Path(override).expanduser() if override else None
    if base is None:
        base = Path.home() / ".config" / "seren" / "skills" / "prophet-arb-bot" / "state"
    return base / "privy_session.json"


class SessionCache:
    """File-backed cache for Privy auth artifacts.

    The cache is opened lazily; missing files are treated as `needs_otp`
    so the first run cleanly falls through to TokenAcquirer. A corrupt
    file is also treated as `needs_otp` (plan §11.6) — never crash.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_cache_path()

    def read(self) -> SessionCacheEntry:
        if not self.path.exists():
            return SessionCacheEntry(state="needs_otp")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return SessionCacheEntry(state="needs_otp")
        # Drop unknown keys so old/forward-compat schemas don't crash construction.
        known = {f.name for f in SessionCacheEntry.__dataclass_fields__.values()}
        clean = {k: v for k, v in payload.items() if k in known}
        try:
            return SessionCacheEntry(**clean)
        except TypeError:
            return SessionCacheEntry(state="needs_otp")

    def write(self, entry: SessionCacheEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: write to temp file in same dir, fsync, chmod 0600, rename.
        # Same-dir is required for atomicity on POSIX rename().
        fd, tmp_path = tempfile.mkstemp(
            prefix=".privy_session.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(entry), f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
