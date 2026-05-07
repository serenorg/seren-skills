"""Critical-only input-validation tests for prophet-bounty-runner.

Reduced from plan §10.3 (12 tests) to the 4 load-bearing assertions:
  - command enum is locked to {setup,run,status} so callers can't smuggle in a
    new mode (e.g. accidental "create" path).
  - run requires prophet_email — without it OTP can't proceed and the run is
    meaningless.
  - status does NOT require prophet_email — read-only earnings query must work
    without an OTP path.
  - dry_run defaults to False per spec inputs schema; the test pins this so an
    accidental schema flip can't silently downgrade live runs to no-ops.

The other 8 quick tests in plan §10.3 (limit bounds, whitespace stripping,
default email_provider) are covered transitively by the spec validator and are
not load-bearing for fail-closed or fraud paths.
"""

from __future__ import annotations

import pytest

from agent import normalize_request  # noqa: E402  (red until phase 5 implements)


def test_command_enum_rejects_unknown_value(base_run_request: dict) -> None:
    request = {**base_run_request, "command": "create"}

    with pytest.raises(ValueError, match="command"):
        normalize_request(request)


def test_run_requires_prophet_email(base_run_request: dict) -> None:
    request = {**base_run_request, "prophet_email": None}

    with pytest.raises(ValueError, match="prophet_email"):
        normalize_request(request)


def test_status_does_not_require_prophet_email() -> None:
    request = {"command": "status", "json_output": True}

    normalized = normalize_request(request)

    assert normalized["command"] == "status"


def test_dry_run_defaults_to_false() -> None:
    request = {"command": "run", "prophet_email": "implementer@example.com"}

    normalized = normalize_request(request)

    assert normalized["dry_run"] is False
