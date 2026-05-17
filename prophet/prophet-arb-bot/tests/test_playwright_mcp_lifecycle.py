"""Issue #647: stale playwright-stealth MCP cleanup before gateway spawn.

These tests pin the contract for the new ``playwright_mcp_lifecycle`` helper
and the auto-cleanup hook in ``PlaywrightStealthGateway.__enter__``. They are
the only tests required for this fix:

- `test_finds_only_stale_playwright_stealth_pids` — the matcher must select
  on the playwright-stealth dist path and skip everything else, including the
  current process tree. This is the trust boundary: a buggy matcher would let
  us kill unrelated user Node processes.
- `test_kill_stale_skips_self_tree` — the killer must never touch a pid that
  belongs to the current process or its descendants, regardless of argv match.
- `test_gateway_enter_invokes_cleanup_before_spawn` — the auto-cleanup must
  run before ``Popen``, otherwise the contention fix doesn't ship.
- `test_timeout_error_embeds_stale_kill_report` — a regression that times out
  anyway must surface the kill report so we don't lose diagnosability.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

from otp_worker import playwright_mcp_gateway as pmg
from otp_worker import playwright_mcp_lifecycle as lifecycle

STUB_SERVER = Path(__file__).parent / "fixtures" / "stub_playwright_mcp_server.py"


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


def test_finds_only_stale_playwright_stealth_pids(monkeypatch):
    """The matcher must select real MCP children and skip everything else.

    This is the safety boundary. False positives here would kill the user's
    IDE (Claude Code / Codex argv carries the path in --mcp-config JSON)
    or unrelated Node processes.
    """
    fake_snapshot = [
        # Real MCP child — bundled bundle path.
        (4001, "node /Applications/SerenDesktop.app/Contents/Resources/"
               "mcp-servers/playwright-stealth/dist/index.js"),
        # Different MCP — must be skipped.
        (4002, "node /opt/seren/mcp-servers/seren-mcp/dist/index.js"),
        # Looks similar but a different basename — must be skipped.
        (4003, "node /tmp/playwright-stealth-spawn-helper.js"),
        # Unrelated Node script — must be skipped.
        (4004, "node /Users/x/projects/my-app/server.js"),
        # Current process — must be skipped via self-tree filter.
        (os.getpid(), "node /Applications/SerenDesktop.app/Contents/Resources/"
                      "mcp-servers/playwright-stealth/dist/index.js"),
        # Forked stealth server with BROWSER_TYPE env in argv.
        (4005, "BROWSER_TYPE=chrome node "
               "/Applications/SerenDesktop.app/Contents/Resources/"
               "mcp-servers/playwright-stealth/dist/index.js"),
        # P0 safety: Claude Code IDE host with the MCP path embedded inside
        # its --mcp-config JSON argv. Killing this would terminate the user
        # session. The token-boundary matcher MUST skip this.
        (4006, '/Users/x/.local/bin/claude --mcp-config '
               '{"mcpServers":{"playwright":{"args":'
               '["/Applications/SerenDesktop.app/Contents/Resources/'
               'mcp-servers/playwright-stealth/dist/index.js"]}}}'),
        # P0 safety: Codex host with same JSON-embedded path.
        (4007, '/opt/homebrew/bin/codex app-server -c '
               'mcp_servers={"playwright"={"args"='
               '["/Applications/SerenDesktop.app/Contents/Resources/'
               'mcp-servers/playwright-stealth/dist/index.js"]}}'),
    ]
    monkeypatch.setattr(lifecycle, "_snapshot_processes", lambda: fake_snapshot)
    monkeypatch.setattr(lifecycle, "_self_tree_pids", lambda: {os.getpid()})

    stale = lifecycle.list_stale_playwright_stealth_pids()

    assert sorted(stale) == [4001, 4005], (
        "matcher must require a whitespace-delimited path token. Real MCP "
        "children match; LLM IDE hosts that carry the path inside JSON "
        "config argv MUST NOT match — killing them would terminate the "
        "user's IDE session."
    )


# ---------------------------------------------------------------------------
# Self-tree safety
# ---------------------------------------------------------------------------


def test_kill_stale_skips_self_tree(monkeypatch):
    """`kill_stale_playwright_mcp_processes` must never signal self-tree pids."""
    fake_snapshot = [
        (4001, "node /Applications/SerenDesktop.app/Contents/Resources/"
               "mcp-servers/playwright-stealth/dist/index.js"),
        (os.getpid(), "node /Applications/SerenDesktop.app/Contents/Resources/"
                      "mcp-servers/playwright-stealth/dist/index.js"),
    ]
    monkeypatch.setattr(lifecycle, "_snapshot_processes", lambda: fake_snapshot)
    monkeypatch.setattr(lifecycle, "_self_tree_pids", lambda: {os.getpid()})

    signaled: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        signaled.append((pid, sig))

    monkeypatch.setattr(lifecycle.os, "kill", fake_kill)
    # Vanish: pretend SIGTERM landed and process exited cleanly so we don't
    # exercise the SIGKILL escalation in this test.
    monkeypatch.setattr(lifecycle, "_wait_for_exit", lambda pid, timeout=2.0: True)

    report = lifecycle.kill_stale_playwright_mcp_processes(grace_seconds=0.0)

    assert os.getpid() not in [pid for pid, _ in signaled], (
        "self-tree pid must not receive any signal — that would terminate the "
        "running agent and is a P0 safety violation"
    )
    assert report.killed == [4001]
    assert report.skipped_self_tree == [os.getpid()]


# ---------------------------------------------------------------------------
# Gateway integration: auto-cleanup before spawn
# ---------------------------------------------------------------------------


def test_gateway_enter_invokes_cleanup_before_spawn(monkeypatch, tmp_path):
    """`__enter__` must call the cleanup helper BEFORE `subprocess.Popen`.

    Uses the existing stub MCP server so the test still exercises the real
    JSON-RPC handshake; the assertion is just about call ordering.
    """
    log_path = tmp_path / "stub_log.jsonl"

    call_order: list[str] = []

    real_popen = pmg.subprocess.Popen

    def tracking_kill(*, grace_seconds: float = 1.0) -> lifecycle.KillReport:
        call_order.append("kill")
        return lifecycle.KillReport(killed=[], skipped_self_tree=[], errors={})

    class TrackingPopen(real_popen):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            call_order.append("popen")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        pmg, "kill_stale_playwright_mcp_processes", tracking_kill
    )
    monkeypatch.setattr(pmg.subprocess, "Popen", TrackingPopen)

    with pmg.PlaywrightStealthGateway(
        command=[sys.executable, str(STUB_SERVER), str(log_path)]
    ) as gateway:
        # exercise one tool call so we know the gateway works post-cleanup
        nav = gateway.playwright_navigate
        nav(url="https://app.prophetmarket.ai")

    assert call_order[:2] == ["kill", "popen"], (
        "cleanup must run BEFORE the new MCP child is spawned; spawning first "
        "races the new child against peer MCPs and reproduces the bug"
    )


# ---------------------------------------------------------------------------
# TimeoutError diagnostic
# ---------------------------------------------------------------------------


def test_timeout_error_embeds_stale_kill_report(monkeypatch, tmp_path):
    """When `initialize` still times out, the error must surface the kill report.

    Without this, an operator who hits a regression sees only `stderr_tail` and
    cannot tell whether contention was already cleared. We use a stub server
    that boots, prints to stderr, but never replies to `initialize`.
    """
    silent_server = tmp_path / "silent_mcp.py"
    silent_server.write_text(
        "import sys, time\n"
        "sys.stderr.write('[playwright-stealth] Stdio transport ready\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(60)\n"
    )

    monkeypatch.setattr(
        pmg,
        "kill_stale_playwright_mcp_processes",
        lambda *, grace_seconds=1.0: lifecycle.KillReport(
            killed=[12345, 67890],
            skipped_self_tree=[os.getpid()],
            errors={},
        ),
    )

    gateway = pmg.PlaywrightStealthGateway(
        command=[sys.executable, str(silent_server)],
        timeout_seconds=0.5,  # keep the test fast
    )
    try:
        with pytest.raises(TimeoutError) as excinfo:
            gateway.__enter__()
    finally:
        # always tear down any spawned process even when the assertion fails
        gateway.__exit__(None, None, None)

    msg = str(excinfo.value)
    assert "stale_killed=" in msg, (
        "timeout error must embed the pre-spawn KillReport so operators can "
        "tell whether contention was cleared before the timeout fired"
    )
    assert "12345" in msg and "67890" in msg
