"""Issue #647: reclaim stale ``playwright-stealth`` MCP processes before spawn.

The arb-bot's ``PlaywrightStealthGateway`` spawns a fresh
``playwright-stealth`` MCP Node child for every cycle. When other Claude Code
or Codex sessions on the same host have also spawned ``playwright-stealth``
children (one per session), they all share the same ``playwright-core``
registry walk and stealth-evasion module loads under
``~/Library/Caches/ms-playwright`` and ``node_modules``. With ~10 peer
children idling, registry/lock contention pushes the new child's synchronous
``getActiveBrowserType()`` past the Python gateway's 30s ``select.select``
timeout — every ``initialize`` request hangs, every ``--command run --yes-live``
returns ``status=blocked, reason=create_market_via_ui_unexpected``.

Idle ``playwright-stealth`` children are by definition not serving an active
caller — the protocol is request/response over stdio with a single client per
process. Reclaiming them before spawn is the right thing to do; it does not
disrupt active work on other sessions because their MCP child is single-tenant
and only does real work while their LLM has a tool call in flight.

Two safety boundaries:

- The pid matcher selects ONLY on ``playwright-stealth/dist/index.js`` argv.
- The killer NEVER targets pids in the current process tree.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field

PLAYWRIGHT_STEALTH_DIST_FRAGMENT = "playwright-stealth/dist/index.js"

# Issue #647 audit: the argv match must select ONLY real MCP child
# processes. The fragment also appears inside JSON-embedded
# ``--mcp-config '{..."args":["...playwright-stealth/dist/index.js"]...}'``
# argv strings carried by Claude Code, Codex, and other LLM hosts that
# spawn the MCP. Killing those would terminate the entire IDE session.
#
# Token rule: when argv is split on whitespace, at least one resulting
# token must END with ``/playwright-stealth/dist/index.js``. That is true
# for the real MCP child (``node /.../playwright-stealth/dist/index.js``)
# and false for JSON-embedded forms (which end with ``]}``/``]}}``/``"``
# after the fragment, never on a clean ``.js`` boundary).
_REQUIRED_TOKEN_SUFFIX = "/" + PLAYWRIGHT_STEALTH_DIST_FRAGMENT


@dataclass
class KillReport:
    """Result of a single cleanup pass."""

    killed: list[int] = field(default_factory=list)
    skipped_self_tree: list[int] = field(default_factory=list)
    errors: dict[int, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "killed": list(self.killed),
            "skipped_self_tree": list(self.skipped_self_tree),
            "errors": {str(k): v for k, v in self.errors.items()},
        }

    def is_empty(self) -> bool:
        return not (self.killed or self.skipped_self_tree or self.errors)


# ---------------------------------------------------------------------------
# Process discovery
# ---------------------------------------------------------------------------


def _snapshot_processes() -> list[tuple[int, str]]:
    """Return ``[(pid, argv_string), ...]`` for every visible process.

    Uses ``ps -A -o pid=,command=`` on Unix. We deliberately avoid ``psutil``
    so the skill keeps zero extra runtime deps. Each line of ``ps`` output
    looks like ``  4001 node /Applications/.../dist/index.js`` — leading
    whitespace and a single space between pid and command, with the command
    column carrying argv (joined by spaces).
    """
    ps_path = shutil.which("ps") or "/bin/ps"
    try:
        result = subprocess.run(
            [ps_path, "-A", "-o", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    out: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        head, _, tail = line.partition(" ")
        try:
            pid = int(head)
        except ValueError:
            continue
        out.append((pid, tail.strip()))
    return out


def _self_tree_pids() -> set[int]:
    """Return the current process's pid plus all descendants.

    We walk ``ps -A -o pid=,ppid=`` once and traverse the parent map. Avoids
    psutil for the same zero-dep reason as ``_snapshot_processes``.
    """
    ps_path = shutil.which("ps") or "/bin/ps"
    try:
        result = subprocess.run(
            [ps_path, "-A", "-o", "pid=,ppid="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return {os.getpid()}
    parent_of: dict[int, int] = {}
    children_of: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        parent_of[pid] = ppid
        children_of.setdefault(ppid, []).append(pid)

    self_pid = os.getpid()
    tree: set[int] = {self_pid}
    # Walk ancestors so we never kill a parent that owns us.
    cur = self_pid
    while cur in parent_of and parent_of[cur] != 0 and parent_of[cur] not in tree:
        cur = parent_of[cur]
        tree.add(cur)
    # Walk descendants iteratively.
    frontier = [self_pid]
    while frontier:
        node = frontier.pop()
        for child in children_of.get(node, ()):
            if child not in tree:
                tree.add(child)
                frontier.append(child)
    return tree


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _argv_matches_playwright_stealth(argv: str) -> bool:
    """Return True if an argv string is a ``playwright-stealth`` MCP child.

    The match requires that at least one whitespace-delimited argv token
    ends with ``/playwright-stealth/dist/index.js`` — i.e. the path is
    being executed as a script, not embedded inside a JSON config string.

    Covers:

    - ``node .../playwright-stealth/dist/index.js``
    - ``BROWSER_TYPE=chrome node .../playwright-stealth/dist/index.js``
    - ``node .../playwright-stealth/dist/index.js --verbose`` (the index.js
      token is itself a clean match; the trailing ``--verbose`` is a
      separate token).

    Explicitly excludes:

    - Claude Code / Codex hosts whose ``--mcp-config '{..."args":["...
      playwright-stealth/dist/index.js"] ...}'`` argv has the path inside
      a JSON-quoted string. The JSON-embedded token ends with ``]}``,
      ``]}}``, ``"``, etc. — never on a clean ``.js`` boundary. Killing
      those would terminate the IDE session.
    """
    for token in argv.split():
        if token.endswith(_REQUIRED_TOKEN_SUFFIX):
            return True
    return False


def list_stale_playwright_stealth_pids() -> list[int]:
    """Return pids of ``playwright-stealth`` MCP children that are not us.

    Self-tree pids are filtered. The killer uses an internal unfiltered match
    so it can record which self-tree pids it saw-and-skipped on its report.
    """
    self_tree = _self_tree_pids()
    return [
        pid
        for pid, argv in _snapshot_processes()
        if _argv_matches_playwright_stealth(argv) and pid not in self_tree
    ]


def _wait_for_exit(pid: int, timeout: float = 2.0) -> bool:
    """Poll ``kill(pid, 0)`` until the pid is gone or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            # Process still exists; we just lack permission to signal it.
            return False
        time.sleep(0.05)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def kill_stale_playwright_mcp_processes(
    *, grace_seconds: float = 1.0
) -> KillReport:
    """Send SIGTERM (then SIGKILL after grace) to every stale stealth MCP pid.

    Returns a :class:`KillReport` even if no pids matched — callers persist
    the report alongside their TimeoutError diagnostics.
    """
    report = KillReport()
    self_tree = _self_tree_pids()
    # Walk the unfiltered match set so self-tree pids that happen to match
    # the playwright-stealth argv (e.g. an in-process descendant) are
    # surfaced on the report instead of silently filtered.
    matched = [
        (pid, argv)
        for pid, argv in _snapshot_processes()
        if _argv_matches_playwright_stealth(argv)
    ]

    for pid, _argv in matched:
        if pid in self_tree:
            # Defense-in-depth: never signal a self-tree pid. Record it so
            # the operator can see the matcher saw it and skipped it.
            report.skipped_self_tree.append(pid)
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            # Already gone; treat as killed for the purpose of the report.
            report.killed.append(pid)
            continue
        except (PermissionError, OSError) as exc:
            report.errors[pid] = f"{type(exc).__name__}:{exc}"
            continue

        if grace_seconds > 0 and _wait_for_exit(pid, timeout=grace_seconds):
            report.killed.append(pid)
            continue

        # Escalate: SIGKILL.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            report.killed.append(pid)
            continue
        except (PermissionError, OSError) as exc:
            report.errors[pid] = f"sigkill:{type(exc).__name__}:{exc}"
            continue

        # Best-effort wait — don't block forever if we can't reap.
        _wait_for_exit(pid, timeout=1.0)
        report.killed.append(pid)

    return report
