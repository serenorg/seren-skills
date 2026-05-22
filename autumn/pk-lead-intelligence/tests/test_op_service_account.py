"""Unit tests for scripts/auth/op_service_account.py.

Two pure pieces are tested:

- the env-var guard that refuses to shell out if
  OP_SERVICE_ACCOUNT_TOKEN is missing
- the 1Password item-JSON field extractor

The subprocess.run path that actually invokes `op` is not tested
here — it requires the real `op` binary and a live Service Account
token. That path is exercised end-to-end in the Phase 1 dry-run
checkpoint with the operator watching.
"""

from __future__ import annotations

import json

import pytest

from scripts.auth import op_service_account as op_sa


# --------------------------------------------------------------------- #
# SalesforceCredentials dataclass contract                              #
# --------------------------------------------------------------------- #


def test_salesforce_credentials_is_frozen() -> None:
    creds = op_sa.SalesforceCredentials(
        username="user@example.com",
        password="hunter2",
        totp_code="123456",
    )
    assert creds.username == "user@example.com"
    assert creds.password == "hunter2"
    assert creds.totp_code == "123456"
    with pytest.raises(Exception):
        creds.username = "other@example.com"  # type: ignore[misc]


# --------------------------------------------------------------------- #
# Env-var guard                                                         #
# --------------------------------------------------------------------- #


def test_op_raises_when_service_account_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="OP_SERVICE_ACCOUNT_TOKEN"):
        op_sa._op(["vault", "list"])


def test_op_does_not_invoke_subprocess_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard must fire before any subprocess work happens."""

    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)

    called: list[object] = []

    def fake_run(*args: object, **kwargs: object) -> object:  # pragma: no cover
        called.append(args)
        raise AssertionError("subprocess.run must not be called when token missing")

    monkeypatch.setattr(op_sa.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError):
        op_sa._op(["vault", "list"])

    assert called == []


# --------------------------------------------------------------------- #
# JSON field extraction                                                 #
# --------------------------------------------------------------------- #


def _item_payload() -> dict:
    """Representative shape of `op item get ... --format json` output."""

    return {
        "id": "abc123",
        "title": "PK Salesforce",
        "vault": {"id": "v1", "name": "PK Salesforce Skill"},
        "category": "LOGIN",
        "fields": [
            {
                "id": "username",
                "label": "username",
                "value": "ops.user@example.com",
                "type": "STRING",
            },
            {
                "id": "password",
                "label": "password",
                "value": "correct-horse-battery-staple",
                "type": "CONCEALED",
            },
            {
                "id": "totp_xxx",
                "label": "one-time password",
                "type": "OTP",
            },
        ],
    }


def test_extract_field_matches_by_id() -> None:
    item = _item_payload()
    assert (
        op_sa._extract_field(item, "username") == "ops.user@example.com"
    )
    assert (
        op_sa._extract_field(item, "password")
        == "correct-horse-battery-staple"
    )


def test_extract_field_raises_on_missing_field() -> None:
    item = _item_payload()
    with pytest.raises(KeyError, match="not_a_field"):
        op_sa._extract_field(item, "not_a_field")


def test_extract_field_raises_on_blank_value() -> None:
    item = {
        "fields": [
            {"id": "username", "label": "username", "value": ""},
        ],
    }
    with pytest.raises(KeyError, match="username"):
        op_sa._extract_field(item, "username")


def test_extract_field_handles_label_case_insensitive_when_id_missing() -> None:
    item = {
        "fields": [
            {"label": "Username", "value": "ops.user@example.com"},
        ],
    }
    assert (
        op_sa._extract_field(item, "username") == "ops.user@example.com"
    )


# --------------------------------------------------------------------- #
# read_salesforce_credentials integration (subprocess fully faked)      #
# --------------------------------------------------------------------- #


def test_read_salesforce_credentials_assembles_three_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end happy path with `_op` swapped out.

    Guards against the public function dropping a field or mis-ordering
    the two `op` calls. The TOTP call must come after the item get so
    the rolling code is as fresh as possible at use time.
    """

    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "fake-sa-token")

    call_log: list[list[str]] = []

    def fake_op(args: list[str]) -> str:
        call_log.append(args)
        if "--otp" in args:
            return "654321\n"
        return json.dumps(_item_payload())

    monkeypatch.setattr(op_sa, "_op", fake_op)

    creds = op_sa.read_salesforce_credentials(
        vault="PK Salesforce Skill",
        item="PK Salesforce",
    )

    assert creds == op_sa.SalesforceCredentials(
        username="ops.user@example.com",
        password="correct-horse-battery-staple",
        totp_code="654321",
    )

    # Two op calls. Order matters — item get first, OTP second.
    assert len(call_log) == 2
    assert "--otp" not in call_log[0]
    assert "--otp" in call_log[1]

    # Both calls must reference the configured vault + item.
    for call in call_log:
        assert "PK Salesforce" in call
        assert "PK Salesforce Skill" in call


# --------------------------------------------------------------------- #
# Consumer-1Password env-var fallback (issue #795)                      #
# --------------------------------------------------------------------- #

# Canonical RFC 6238 test seed. Public, by design — picked so the test
# is reproducible without leaking a real secret. Tests must never use a
# customer's actual TOTP seed.
_RFC6238_TEST_SEED = "JBSWY3DPEHPK3PXP"


def test_read_salesforce_credentials_uses_env_vars_when_all_three_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #795 — consumer 1Password users have no Service Account.
    When SF_USERNAME + SF_PASSWORD + SF_TOTP_SECRET are all set, the
    reader must return creds from env and must NOT invoke `op`. The
    rolling 6-digit code is computed locally from the base32 seed.

    Pin: any future refactor that re-enters the `op` subprocess when
    env vars are set re-locks consumer users out and is a P0
    regression for issue #795's audience.
    """

    import pyotp  # type: ignore[import-not-found]

    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)
    monkeypatch.setenv("SF_USERNAME", "jill@example.com")
    monkeypatch.setenv("SF_PASSWORD", "consumer-pw")
    monkeypatch.setenv("SF_TOTP_SECRET", _RFC6238_TEST_SEED)

    def fake_op_must_not_be_called(args: list[str]) -> str:
        raise AssertionError(
            f"`op` was invoked despite SF_* env vars being set: args={args}"
        )

    monkeypatch.setattr(op_sa, "_op", fake_op_must_not_be_called)

    creds = op_sa.read_salesforce_credentials(
        vault="ignored-in-env-path",
        item="ignored-in-env-path",
    )

    assert creds.username == "jill@example.com"
    assert creds.password == "consumer-pw"
    # Rolling code matches independent pyotp computation in the same
    # 30-second window. Strict equality is safe because both calls
    # happen in the same test tick.
    assert creds.totp_code == pyotp.TOTP(_RFC6238_TEST_SEED).now()
    # Sanity-check shape; 1Password's `op item get --otp` returns the
    # same 6-digit format.
    assert len(creds.totp_code) == 6
    assert creds.totp_code.isdigit()


def test_read_salesforce_credentials_env_path_rejects_partial_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two of three env vars set is a configuration error, not a hint
    to fall back to `op`. A partial config would silently mis-route
    credentials (e.g. an operator who set SF_USERNAME + SF_PASSWORD
    but forgot SF_TOTP_SECRET would end up with the wrong account if
    `op` returned a different vault's creds). Raise clearly.

    Pin: the error message must name SF_TOTP_SECRET so the operator
    knows which field is missing without spelunking the source.
    """

    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)
    monkeypatch.setenv("SF_USERNAME", "jill@example.com")
    monkeypatch.setenv("SF_PASSWORD", "consumer-pw")
    monkeypatch.delenv("SF_TOTP_SECRET", raising=False)

    def fake_op_must_not_be_called(args: list[str]) -> str:
        raise AssertionError(
            "`op` was invoked despite partial SF_* env vars — the "
            "reader must not silently fall back when the env path is "
            "partially configured."
        )

    monkeypatch.setattr(op_sa, "_op", fake_op_must_not_be_called)

    with pytest.raises(RuntimeError, match="SF_TOTP_SECRET"):
        op_sa.read_salesforce_credentials(
            vault="ignored-in-env-path",
            item="ignored-in-env-path",
        )


def test_read_salesforce_credentials_env_path_rejects_invalid_totp_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The TOTP seed must be valid base32. An operator who pastes the
    rolling 6-digit *code* into SF_TOTP_SECRET instead of the base32
    seed is the most likely failure mode in the field. Fail loudly
    rather than producing a deterministic-but-wrong code that
    Salesforce will silently reject every 30 seconds.

    Pin: the error message must hint that the value is the base32
    secret, not the 6-digit code.
    """

    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)
    monkeypatch.setenv("SF_USERNAME", "jill@example.com")
    monkeypatch.setenv("SF_PASSWORD", "consumer-pw")
    # "123456" is what an operator types when they confuse the rolling
    # 6-digit code with the underlying seed. Not valid base32 length.
    monkeypatch.setenv("SF_TOTP_SECRET", "123456")

    with pytest.raises(RuntimeError, match="base32"):
        op_sa.read_salesforce_credentials(
            vault="ignored-in-env-path",
            item="ignored-in-env-path",
        )
