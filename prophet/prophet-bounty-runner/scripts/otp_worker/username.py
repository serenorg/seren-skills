"""Auto-generate a Prophet username from an email address.

Prophet's `/onboarding` form requires a username. To keep onboarding
zero-touch, the skill derives one from `prophet_email`:

  email = "taariq@serendb.com"  ->  base_username = "taariq"
  on Prophet-side collision      ->  collision_fallback = "taariq_5fa1"

The collision suffix is a deterministic 4-char prefix of
`sha256(email)`, so re-running after a 409 is idempotent on a single
account. The username is the user's permanent public Prophet handle;
this is acceptable per operator direction (Prophet team approved
auto-fill on 2026-05-08).
"""

from __future__ import annotations

import hashlib
import re

_ALLOWED = re.compile(r"[^a-z0-9_]+")
_MAX_LEN = 30


def base_username_from_email(email: str) -> str:
    """Return the sanitized local-part of the email as a username.

    Strips characters outside `[a-z0-9_]` and lower-cases. Falls back to
    the literal `"user"` if the cleaned local-part is empty (e.g., an
    email like `....@example.com`).
    """
    local = (email or "").split("@", 1)[0].lower()
    cleaned = _ALLOWED.sub("", local) or "user"
    return cleaned[:_MAX_LEN]


def collision_fallback(email: str) -> str:
    """Return base + `_` + 4-char sha256(email) suffix, capped at the max.

    Called by the onboarding helper when Prophet returns a username-
    taken error on the base form. Deterministic so multiple cold-starts
    against the same email converge on the same fallback.
    """
    digest = hashlib.sha256((email or "").encode("utf-8")).hexdigest()[:4]
    base = base_username_from_email(email)
    return (base + "_" + digest)[:_MAX_LEN]
