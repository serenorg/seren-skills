"""Critical-only tests for prophet.schema_probe.

The probe is the audit-of-record path. These tests pin two contracts:

- main(argv) — accepts an explicit argv list, so agent.py can hand in
  `[]` instead of letting sys.argv leak through (issue #479).
- agent.py wires --command probe-schema to call probe.main([]) — so the
  parent's --config / --command flags do not get re-parsed.

Issue #493: removed tests for `fetch_schema_authenticates_with_authorization_bearer`,
`fetch_schema_prefers_privy_jwt_when_provided`,
`fetch_schema_uses_certified_ssl_context`,
`fetch_schema_unwraps_seren_gateway_envelope`,
`fetch_schema_passes_unwrapped_response_through` — those exercised the
prophet-ai publisher proxy path that no longer exists. The same
contracts are now covered by
`tests/test_prophet_transport.py::test_post_graphql_uses_authorization_bearer_against_prophet`
and the surrounding transport tests, which exercise the canonical
direct-to-Prophet HTTP path that the probe now uses.
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

import agent
from prophet import schema_probe


def test_schema_probe_main_accepts_argv_and_does_not_read_sys_argv(
    monkeypatch, tmp_path
) -> None:
    """schema_probe.main must accept an argv list and not fall back to sys.argv.

    Before the fix, parser.parse_args() defaulted to sys.argv[1:]. When
    agent.py invoked probe_main() from within `--command probe-schema`,
    the parent's argv leaked into the child's argparse and crashed it
    with `unrecognized arguments`.
    """
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent.py", "--config", "config.json", "--command", "probe-schema"],
    )
    monkeypatch.delenv("PROPHET_SESSION_TOKEN", raising=False)
    out_file = tmp_path / "schema.json"

    with mock.patch.object(
        schema_probe, "fetch_schema", return_value={"data": {"__schema": {"types": []}}}
    ):
        rc = schema_probe.main(["--output", str(out_file)])

    assert rc == 0
    assert out_file.exists()


def test_agent_probe_schema_command_does_not_leak_argv(monkeypatch) -> None:
    """agent.main(--command probe-schema) must isolate its child argv.

    Pin the contract that the parent's args.command/args.config never
    reach prophet.schema_probe.main, regardless of sys.argv.
    """
    captured: dict[str, list[str] | None] = {"argv": "<not-called>"}  # type: ignore[dict-item]

    def fake_probe_main(argv=None):
        captured["argv"] = argv
        return 0

    monkeypatch.setenv("SEREN_API_KEY", "test-key")
    with mock.patch("prophet.schema_probe.main", side_effect=fake_probe_main):
        rc = agent.main(
            ["--config", "config.json", "--command", "probe-schema"]
        )
    assert rc == 0
    leaked = captured["argv"] or []
    assert "--config" not in leaked
    assert "--command" not in leaked
    assert "probe-schema" not in leaked


def test_schema_probe_main_rejects_bogus_argv(monkeypatch) -> None:
    """Argparse errors must come from the argv we pass, not sys.argv."""
    monkeypatch.setattr(sys, "argv", ["agent.py"])  # clean parent
    with pytest.raises(SystemExit) as exc:
        schema_probe.main(["--unknown-flag"])
    assert exc.value.code == 2


def test_introspection_query_captures_mutation_args() -> None:
    """The probe must capture `args` on fields so future drift in input
    types (PlaceOrderInput, CancelOrderInput, OrdersInput) is visible
    in the saved fixture, not just each input object's own field list.
    """
    assert "args" in schema_probe.INTROSPECTION_QUERY
