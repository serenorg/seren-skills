"""Issue #568: MCP-first setup + .env auto-load + duplicate-account guard.

Two critical pins:

  1. `agent.py main()` must call `maybe_load_dotenv()` before any other
     work. Without this, `.env` files Jill creates are never read by
     `--command setup` and the auth check at `db.py:184` raises
     `RuntimeError` even when the operator has a perfectly valid key
     on disk.

  2. `SKILL.md` must carry:
     - the duplicate-account warning that sibling skills carry,
     - an MCP-first language anchor so Claude probes auth via MCP
       before subprocessing the Python runner,
     - no POSIX-only shell snippets (`export SEREN_API_KEY=`,
       `cp .env.example`) in the API-Key-Setup / Minimal-Run flow.
"""

from __future__ import annotations

import re
from pathlib import Path


ARB_BOT_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = ARB_BOT_ROOT / "SKILL.md"
AGENT_PY = ARB_BOT_ROOT / "scripts" / "agent.py"
LOCAL_PULL_RUNNER_PY = ARB_BOT_ROOT / "scripts" / "run_local_pull_runner.py"


def test_main_calls_maybe_load_dotenv() -> None:
    """The setup-path auth check must see `.env` contents.

    Static check on agent.py: somewhere inside `main()` the source must
    invoke `maybe_load_dotenv(`. We don't care exactly how the helper is
    imported, only that it runs before argparse/auth dispatch.
    """
    source = AGENT_PY.read_text(encoding="utf-8")

    # Slice from the `def main(` declaration to end of file. We only
    # check the body of main, not module-level imports — the call has to
    # happen *at runtime* before auth.
    main_idx = source.find("def main(")
    assert main_idx >= 0, "agent.py no longer defines main(); update this test"
    main_body = source[main_idx:]

    assert "maybe_load_dotenv(" in main_body, (
        "agent.py main() must call maybe_load_dotenv() before auth checks "
        "so that .env files in the skill root are honored. See issue #568."
    )


def test_skill_md_carries_duplicate_account_warning() -> None:
    """Sibling skills carry this warning; the arb-bot did not.

    Issue #567 transcript shows Sonnet 4.6 creating a $0-balance
    `claude@example.com` account because the warning was missing.
    """
    body = SKILL_MD.read_text(encoding="utf-8")
    # Match the canonical sibling-skill wording (case-insensitive).
    pattern = re.compile(
        r"do not create a new account if a key already exists",
        re.IGNORECASE,
    )
    assert pattern.search(body), (
        "SKILL.md must carry the duplicate-account warning shipped by "
        "sibling skills (1099-da-tax-reconciler, smart-dca-bot, etc.). "
        "See issue #568."
    )


def test_skill_md_leads_with_mcp_first_auth_probe() -> None:
    """SKILL.md must instruct the agent to probe auth via MCP first.

    The whole point of the rewrite: don't force a subprocess that may
    not have API_KEY in its env. Probe MCP first; that's what works on
    Seren Desktop even when subprocess env-injection is broken.
    """
    body = SKILL_MD.read_text(encoding="utf-8")
    assert "mcp__seren-mcp__list_projects" in body, (
        "SKILL.md must mention `mcp__seren-mcp__list_projects` as the "
        "first-line auth probe. See issue #568."
    )


def test_skill_md_drops_posix_only_setup_commands() -> None:
    """Drop `export SEREN_API_KEY=` and `cp .env.example` snippets.

    Those are bash-only. On Windows cmd.exe they fail silently and
    Sonnet 4.6 ends up creating dummy accounts (issue #567).
    """
    body = SKILL_MD.read_text(encoding="utf-8")

    # `export SEREN_API_KEY=` was the actual offender in the Minimal Run
    # block. Allow `SEREN_API_KEY` to appear in prose (env-var name) but
    # not as a shell `export` statement.
    assert "export SEREN_API_KEY=" not in body, (
        "Drop `export SEREN_API_KEY=...` — it's POSIX-only and fails on "
        "Windows. Use neutral language like 'ensure SEREN_API_KEY is in "
        "<skill-root>/.env'. See issue #568."
    )

    # `cp .env.example .env` is the other offender. The arb-bot does not
    # need an .env.example copy step anyway, because Issue 568's
    # MCP-first flow doesn't require local credential plumbing for
    # setup. If `cp .env.example` is documented anywhere, it should be
    # behind a "fallback" heading.
    cp_matches = re.findall(r"cp\s+\.env\.example", body)
    assert len(cp_matches) == 0, (
        f"Drop `cp .env.example .env` snippets ({len(cp_matches)} found) — "
        "they're POSIX-only and the MCP-first flow doesn't need them. "
        "See issue #568."
    )


def test_skill_md_routes_playwright_to_desktop_mcp_not_publisher() -> None:
    """Issue #576: UI automation must use Playwright MCP, not publisher lookup.

    Opus 4.5 regressed by searching/querying for a Playwright publisher
    during the Prophet `/create` flow. In Seren Desktop, Playwright is a
    connected MCP service exposed as the `mcp__playwright__...` tool
    namespace, so the runbook must anchor that exact route.
    """
    body = SKILL_MD.read_text(encoding="utf-8")

    assert "mcp__playwright__playwright_navigate" in body, (
        "SKILL.md must name the Playwright MCP tool namespace so agents "
        "drive Prophet's UI with Seren Desktop's connected MCP service. "
        "See issue #576."
    )
    assert re.search(r"Playwright\s+is\s+.*MCP\s+connected\s+service", body, re.IGNORECASE | re.DOTALL), (
        "SKILL.md must explicitly say Playwright is an MCP connected "
        "service in Seren Desktop. See issue #576."
    )
    assert re.search(r"not\s+.*Playwright\s+publisher", body, re.IGNORECASE | re.DOTALL), (
        "SKILL.md must explicitly prohibit Playwright publisher routing. "
        "See issue #576."
    )


def test_skill_md_uses_seren_desktop_bundled_python_on_windows() -> None:
    """Issue #644: Windows Desktop already ships `python3.exe`.

    Agents should use the documented `python3` commands from Desktop instead
    of sending Windows users to install system Python or translating to
    `python`.
    """
    body = SKILL_MD.read_text(encoding="utf-8")

    assert "Seren Desktop bundles Python on Windows" in body, (
        "SKILL.md must state that Seren Desktop bundles Python on Windows. "
        "See issue #644."
    )
    assert "bundled `python3.exe`" in body, (
        "SKILL.md must name the bundled `python3.exe` path behavior. "
        "See issue #644."
    )
    assert "Do not translate `python3` to `python`" in body, (
        "SKILL.md must prohibit `python3` -> `python` translation inside "
        "Seren Desktop. See issue #644."
    )
    assert "do not ask the user to install system Python" in body, (
        "SKILL.md must not ask Desktop Windows users to install system Python. "
        "See issue #644."
    )


def test_local_pull_runner_reuses_active_python_interpreter() -> None:
    """Issue #644: scheduled child runs must inherit the active interpreter.

    On Windows Seren Desktop, the active interpreter is the bundled CPython
    runtime. Reusing `sys.executable` keeps cron-triggered `agent.py` runs on
    that interpreter instead of depending on system PATH discovery.
    """
    source = LOCAL_PULL_RUNNER_PY.read_text(encoding="utf-8")
    build_command_idx = source.find("def build_agent_command(")
    assert build_command_idx >= 0, (
        "run_local_pull_runner.py no longer defines build_agent_command(); "
        "update the Windows bundled Python guard. See issue #644."
    )

    build_command_body = source[build_command_idx:]
    assert "sys.executable" in build_command_body, (
        "run_local_pull_runner.py must invoke agent.py through sys.executable "
        "so Seren Desktop's bundled Python is reused on Windows. See issue #644."
    )
