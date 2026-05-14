"""1Password Service Account credential reader.

Shells out to the `op` CLI to read the Salesforce login item (username,
password, rolling 6-digit TOTP) at runtime. Holds no secrets on disk —
each call to `read_salesforce_credentials` re-reads from the vault and
discards the values when the caller stops using them.

Requires:

- `OP_SERVICE_ACCOUNT_TOKEN` set in the environment.
- The `op` binary on `PATH` (1Password CLI 2.x).
- A Service Account scoped to read the configured vault + item, and
  the item carries `username` + `password` fields plus a TOTP field.

The subprocess path is deliberately not unit-tested — that needs the
real `op` binary and a live token. It is covered by the Phase 1
dry-run checkpoint with the operator watching.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass


_OP_BINARY = "op"


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


def read_salesforce_credentials(
    *,
    vault: str,
    item: str,
) -> SalesforceCredentials:
    """Read username + password + TOTP from one 1Password login item.

    Two `op` calls — `item get --format json` for the static fields,
    then `item get --otp` for the rolling TOTP code. The TOTP call is
    intentionally last so the 6-digit value is as fresh as possible
    when the SSO driver consumes it. Both calls reference the same
    vault + item.
    """

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
