"""Salesforce credential reader.

Two layered paths, tried in this order on each call:

1. **Env-var path** (issue #795) — `SF_USERNAME` + `SF_PASSWORD` +
   `SF_TOTP_SECRET` set in the environment. The rolling 6-digit code
   is computed locally from the base32 TOTP seed via `pyotp`. Works
   on consumer 1Password plans (or no 1Password at all) because it
   never touches the `op` CLI. The `vault` and `item` arguments are
   accepted but ignored on this path.
2. **1Password Service Account path** (the historical default) —
   shells out to the `op` CLI to read the Salesforce login item from
   a Business/Teams vault. Requires `OP_SERVICE_ACCOUNT_TOKEN` plus
   the `op` binary on `PATH`. Holds no secrets on disk — each call
   re-reads from the vault.

Both paths return the same `SalesforceCredentials` dataclass, so
callers (`agent.py`, `auth/microsoft_sso.py`) never branch on which
source was used.

The `op` subprocess path is deliberately not unit-tested — that
needs the real `op` binary and a live token. It is covered by the
Phase 1 dry-run checkpoint with the operator watching. The env-var
path IS unit-tested because it is pure-Python (no subprocess).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass


_OP_BINARY = "op"

# Env vars consulted in the consumer/no-op-CLI path. All three must
# be present and non-empty to take the env path; partial config is
# rejected with an actionable error rather than silently falling
# through to `op`. Issue #795.
_ENV_VAR_USERNAME = "SF_USERNAME"
_ENV_VAR_PASSWORD = "SF_PASSWORD"
_ENV_VAR_TOTP_SECRET = "SF_TOTP_SECRET"


@dataclass(frozen=True)
class SalesforceCredentials:
    """Credentials read out of 1Password for one Salesforce sign-in.

    `totp_code` is the rolling 6-digit value sampled at credential-read
    time. Callers should consume it within ~30 seconds of receipt; the
    1Password TOTP window is 30 seconds and stale codes will be
    rejected by the IdP.
    """

    username: str
    password: str
    totp_code: str


def _op(args: list[str]) -> str:
    """Run the `op` CLI with the configured Service Account token.

    Guards on `OP_SERVICE_ACCOUNT_TOKEN` first so a misconfigured run
    fails before any subprocess work happens. Returns stdout verbatim
    (whitespace preserved); callers decide whether to `.strip()` or
    `json.loads`.
    """

    env = os.environ.copy()
    if "OP_SERVICE_ACCOUNT_TOKEN" not in env:
        raise RuntimeError(
            "OP_SERVICE_ACCOUNT_TOKEN not set in environment — "
            "cannot read 1Password vault"
        )

    result = subprocess.run(
        [_OP_BINARY, *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _extract_field(item_json: dict, key: str) -> str:
    """Pull a non-empty string value out of an `op item get` payload.

    1Password items expose a `fields` array; each entry has `id`,
    `label`, and `value`. Match on `id` first (stable) and fall back
    to case-insensitive `label` match (more forgiving when the item
    was created in the UI rather than via the CLI).
    """

    key_lower = key.lower()
    for field in item_json.get("fields", []):
        field_id = (field.get("id") or "").lower()
        field_label = (field.get("label") or "").lower()
        if field_id == key_lower or field_label == key_lower:
            value = field.get("value")
            if value:
                return value
            # Field is present but blank — surface as missing rather
            # than handing the SSO driver an empty string.
            raise KeyError(
                f"1Password field {key!r} is present but empty"
            )
    raise KeyError(f"1Password field {key!r} not found in item")


def _read_from_env() -> SalesforceCredentials | None:
    """Try the consumer/no-op-CLI env-var path. Returns creds when all
    three `SF_*` env vars are set and non-empty. Returns `None` to
    signal the caller should fall through to the `op` path.

    Partial config (one or two of three set) is treated as an
    operator misconfiguration and raises rather than silently falling
    through — a half-set env can otherwise route the SSO driver at
    the wrong account when the `op` fallback succeeds for a different
    vault.
    """

    username = os.environ.get(_ENV_VAR_USERNAME)
    password = os.environ.get(_ENV_VAR_PASSWORD)
    totp_secret = os.environ.get(_ENV_VAR_TOTP_SECRET)

    set_count = sum(1 for v in (username, password, totp_secret) if v)
    if set_count == 0:
        return None
    if set_count < 3:
        missing = [
            name
            for name, value in (
                (_ENV_VAR_USERNAME, username),
                (_ENV_VAR_PASSWORD, password),
                (_ENV_VAR_TOTP_SECRET, totp_secret),
            )
            if not value
        ]
        raise RuntimeError(
            f"Salesforce env-var credential path is partially "
            f"configured — missing: {', '.join(missing)}. Set all "
            f"three of {_ENV_VAR_USERNAME}, {_ENV_VAR_PASSWORD}, "
            f"{_ENV_VAR_TOTP_SECRET} to use the consumer path, or "
            f"unset all three to fall back to the 1Password Service "
            f"Account path."
        )

    # All three set — compute the rolling 6-digit code from the
    # base32 TOTP seed. `pyotp.TOTP.now()` matches the same RFC 6238
    # algorithm that 1Password's `op item get --otp` uses.
    try:
        import pyotp  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "pyotp is required for the SF_TOTP_SECRET env path. "
            "Run `pip install -r requirements.txt` and retry."
        ) from exc

    try:
        totp_code = pyotp.TOTP(totp_secret).now()  # type: ignore[arg-type]
    except (ValueError, TypeError, Exception) as exc:
        # pyotp surfaces base32 decode failures as binascii.Error
        # subclasses of ValueError; an operator pasting the rolling
        # 6-digit code into SF_TOTP_SECRET instead of the seed lands
        # here. Re-raise as a RuntimeError naming the encoding so the
        # fix is obvious without reading the traceback.
        raise RuntimeError(
            f"SF_TOTP_SECRET is not a valid base32 TOTP seed: "
            f"{exc}. Paste the base32 secret (long string from "
            f"1Password's one-time-password field, or from "
            f"Salesforce's MFA setup 'Use this secret key' panel) — "
            f"not the rolling 6-digit code."
        ) from exc

    return SalesforceCredentials(
        username=username,  # type: ignore[arg-type]
        password=password,  # type: ignore[arg-type]
        totp_code=totp_code,
    )


def read_salesforce_credentials(
    *,
    vault: str,
    item: str,
) -> SalesforceCredentials:
    """Read username + password + TOTP for one Salesforce sign-in.

    Tries the env-var path first (issue #795). If `SF_USERNAME`,
    `SF_PASSWORD`, and `SF_TOTP_SECRET` are all set, returns those
    creds and ignores `vault` + `item`. Otherwise falls through to
    the 1Password Service Account path, which makes two `op` calls —
    `item get --format json` for the static fields, then
    `item get --otp` for the rolling TOTP code. The TOTP call is
    intentionally last so the 6-digit value is as fresh as possible
    when the SSO driver consumes it. Both `op` calls reference the
    same vault + item.
    """

    env_creds = _read_from_env()
    if env_creds is not None:
        return env_creds

    item_json_raw = _op(
        [
            "item",
            "get",
            item,
            "--vault",
            vault,
            "--format",
            "json",
        ]
    )
    item_json = json.loads(item_json_raw)

    username = _extract_field(item_json, "username")
    password = _extract_field(item_json, "password")

    totp_raw = _op(
        [
            "item",
            "get",
            item,
            "--vault",
            vault,
            "--otp",
        ]
    )
    totp_code = totp_raw.strip()
    if not totp_code:
        raise KeyError(
            "1Password returned an empty TOTP code — "
            "is the item missing a one-time-password field?"
        )

    return SalesforceCredentials(
        username=username,
        password=password,
        totp_code=totp_code,
    )
