"""Stateful stdio MCP shim for the bundled ``playwright-stealth`` server.

Issue #580: PR #578 rewired ``RealBrowserSession._call`` to look for
MCP-style attributes on the gateway (``playwright_<tool>``) instead of
calling a retired ``playwright`` publisher slug. But the production
``HttpGateway`` only does publisher REST — it carries none of those
attributes. The cold-start OTP path raised ``RuntimeError`` on the
first ``navigate()`` call.

This module finishes the migration. ``PlaywrightStealthGateway`` spawns
SerenDesktop's bundled ``playwright-stealth`` MCP server (or any
operator-supplied command) as a long-lived stdio subprocess, speaks the
JSON-RPC MCP wire protocol (``initialize`` →
``notifications/initialized`` → ``tools/call`` * N), and exposes each
``playwright_<tool>`` as a Python callable so ``_resolve_mcp_callable``
in ``playwright_client.py`` finds the gateway attribute directly.

Mirrors ``scripts/polymarket_live.py:_call_seren_mcp_tool`` (the existing
``seren-mcp`` shim) but keeps the subprocess alive across calls because
Playwright needs a single browser context for the full OTP dance.
"""

from __future__ import annotations

import json
import os
import select
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from otp_worker.playwright_mcp_lifecycle import (
    KillReport,
    kill_stale_playwright_mcp_processes,
)


DEFAULT_BUNDLED_PATHS = (
    # Current packaged app bundle.
    "/Applications/SerenDesktop.app/Contents/Resources/"
    "mcp-servers/playwright-stealth/dist/index.js",
    # Older packaged app bundle layout still present in some Desktop builds.
    "/Applications/SerenDesktop.app/Contents/Resources/embedded-runtime/"
    "mcp-servers/playwright-stealth/dist/index.js",
)
# Issue #652: per-`tools/call` ceiling is no longer the real failure cap;
# the per-entry budget on the `/create` driver
# (_run_create_market_via_ui_inner) is. Per-call ceilings exist only to
# detect a dead MCP stdio stream. 180s is comfortably above any single
# contended `tools/call` we have observed on the heavy-Chrome failure
# profile that produced #651, while still surfacing a truly hung stream
# within one per-entry budget window. Supersedes the #649 first-call
# carve-out: the new floor covers cold-Chromium-launch latency without a
# dedicated branch in `_call_tool`. The `phase=cold_launch` marker is
# retained as diagnostic-only instrumentation.
DEFAULT_TIMEOUT_SECONDS = 180.0
MCP_PROTOCOL_VERSION = "2024-11-05"
PROPHET_STABLE_URL = "https://app.prophetmarket.ai/markets"

# Issue #681: Privy-compatible env profile for the bundled `playwright-stealth`
# MCP. Honored by Desktop #1957 (commit 34e6621e); older Desktop builds
# silently ignore these vars and the child launches headless + full-stealth.
# The skill still fails closed downstream (`prophet_session_unavailable`) on
# old Desktop, so propagating the profile is safe even when unrecognized.
#
# Profile rationale (see issue body for the full audit):
#   STEALTH_EVASIONS_DISABLE=iframe.contentWindow,navigator.permissions
#       The two surfaces Privy probes. Drop these two evasions, keep the
#       rest so Prophet's anti-bot surface still tolerates us.
#   DISABLE_PAGE_INIT_PATCH=1
#       The MCP's hand-rolled addInitScript double-patches
#       navigator.permissions.query on top of the stealth plugin's own
#       evasion. Off for /create.
#   BROWSER_TYPE=chrome (issue #685)
#       After #684 wired the three vars above into every spawn site, live
#       cycles still blocked at OtpEmailTimeout: privy:connections never
#       landed in localStorage. Side-by-side ps evidence showed the
#       connected MCP launching Google Chrome while the bundled MCP fell
#       through to Playwright's bundled Chromium. Privy's embedded-wallet
#       provisioning needs Chrome-specific surfaces (Widevine CDM, GAIA
#       identity, WebAuthn platform authenticator) that vanilla Chromium
#       does not ship. The bundled MCP's browser.ts:309 already reads
#       BROWSER_TYPE and routes "chrome" to the installed-browser
#       registry's executablePath (or Playwright's channel="chrome"
#       fallback) — same path the connected MCP uses.
#
# NOTE: SEREN_PLAYWRIGHT_HEADLESS=0 was previously set here on the strength
# of Desktop #1957's README warning about headless Chromium iframe
# regressions (microsoft/playwright#31896, #33674). After #685/#686 routed
# the bundled MCP to real Google Chrome, side-by-side ps -o command=
# evidence (issue #687, 2026-05-18) showed the connected MCP that does
# provision Privy in ~5s is itself running headless Chrome, while our
# headed Chrome times out at 30s. The iframe regression is Chromium-
# specific; real Chrome handles headless fine. The Desktop README
# guidance was right for Chromium and wrong for Chrome. We rely on the
# bundled MCP's default — shouldLaunchHeadless() returns true when the
# var is unset (browser.ts:362) — so omission yields headless.
PRIVY_COMPATIBLE_ENV: dict[str, str] = {
    "SEREN_PLAYWRIGHT_STEALTH_EVASIONS_DISABLE": (
        "iframe.contentWindow,navigator.permissions"
    ),
    "SEREN_PLAYWRIGHT_DISABLE_PAGE_INIT_PATCH": "1",
    "BROWSER_TYPE": "chrome",
}
_RESET_ENTRY_CAPTURE_SCRIPT = """
(() => {
  try {
    if (window.__seren_original_fetch__) {
      window.fetch = window.__seren_original_fetch__;
    }
    delete window.__seren_original_fetch__;
    delete window.__seren_capture__;
    delete window.__seren_capture_installed__;
  } catch (e) { /* best effort */ }
  return true;
})()
"""
_CURRENT_URL_SCRIPT = "(() => window.location.href)()"
_PRIVY_STATE_SCRIPT = """
(() => {
  try {
    return window.localStorage.getItem("privy:token")
      || window.localStorage.getItem("privy:user")
      || "";
  } catch (e) {
    return "";
  }
})()
"""


class PlaywrightMcpUnavailable(RuntimeError):
    """Raised when no ``playwright-stealth`` command can be resolved."""


class PlaywrightStealthGateway:
    """Long-lived stdio MCP client for the bundled playwright-stealth server.

    Use as a context manager — ``__enter__`` spawns and initializes,
    ``__exit__`` terminates. Every ``playwright_<tool>`` attribute
    resolves to a callable that issues a ``tools/call`` against the
    subprocess and returns the unwrapped body.
    """

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        env_overrides: Mapping[str, str] | None = None,
    ) -> None:
        resolved = command if command is not None else self._resolve_default_command()
        if not resolved:
            raise PlaywrightMcpUnavailable(
                "No playwright-stealth MCP command resolvable. Set "
                "SEREN_PLAYWRIGHT_MCP_COMMAND or run inside Seren Desktop."
            )
        self._command = resolved
        self._timeout_seconds = timeout_seconds
        # Issue #681: when set, the Node child receives os.environ extended
        # with these overrides. When None (default), Popen is called without
        # an ``env`` kwarg so the child inherits the parent env exactly as it
        # has since #580 — preserving the OTP cold-start path's stealth-on
        # profile and avoiding behavior drift for every other gateway caller.
        self._env_overrides: dict[str, str] | None = (
            dict(env_overrides) if env_overrides else None
        )
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_request_id = 1
        # Issue #647: KillReport from the pre-spawn cleanup pass. Surfaced in
        # TimeoutError messages and via `--command reset-playwright-mcp`.
        self._last_kill_report: KillReport = KillReport()
        # Issue #652: diagnostic-only. The per-entry budget on the /create
        # driver is the real failure cap; per-call ceilings here exist only
        # to detect a dead MCP stdio stream. We still track whether the
        # in-flight call is the first `tools/call` after `__enter__` so a
        # TimeoutError fired during it can be annotated with
        # `phase=cold_launch`. Marker has no effect on dispatch.
        self._first_tool_call_pending: bool = True
        self._in_cold_launch_call: bool = False

    # -- Lifecycle ----------------------------------------------------------

    @classmethod
    def _resolve_default_command(cls) -> list[str] | None:
        """Pick a spawn command. None means "no path available — fail closed"."""
        override = (os.environ.get("SEREN_PLAYWRIGHT_MCP_COMMAND") or "").strip()
        if override:
            parts = shlex.split(override)
            return parts or None
        node_bin = (os.environ.get("SEREN_EMBEDDED_NODE_BIN") or "").strip() or "node"
        home = Path.home()
        candidates = [
            *(Path(p) for p in DEFAULT_BUNDLED_PATHS),
            home
            / "Projects/Seren_Projects/seren-desktop/"
            "mcp-servers/playwright-stealth/dist/index.js",
            home
            / "Projects/Seren_Projects/seren-desktop/src-tauri/target/debug/"
            "mcp-servers/playwright-stealth/dist/index.js",
        ]
        for candidate in candidates:
            if candidate.exists():
                return [node_bin, str(candidate)]
        return None

    @classmethod
    def is_available(cls) -> bool:
        return cls._resolve_default_command() is not None

    def __enter__(self) -> "PlaywrightStealthGateway":
        # Issue #647: reclaim peer playwright-stealth MCP children before
        # spawning our own. ~10 idle peers from concurrent Claude Code /
        # Codex sessions push the new child's synchronous Playwright registry
        # walk past our `initialize` timeout. Idle MCP children are
        # single-tenant and not serving active callers, so reclaiming is
        # safe — the matcher already rules out the current process tree.
        self._last_kill_report = kill_stale_playwright_mcp_processes(
            grace_seconds=1.0,
        )
        # Issue #652: re-arm the cold-launch marker for this new lifetime.
        # Diagnostic-only — no longer affects timeout dispatch.
        self._first_tool_call_pending = True

        # Issue #681: propagate Privy-compatible env overrides into the Node
        # child via Popen(env=...). When no overrides are configured, the
        # kwarg is intentionally omitted so the child keeps inheriting parent
        # env — preserving the OTP cold-start path's full-stealth profile.
        popen_kwargs: dict[str, Any] = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if self._env_overrides:
            popen_kwargs["env"] = {**os.environ, **self._env_overrides}
        self._proc = subprocess.Popen(self._command, **popen_kwargs)
        self._request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "prophet-arb-bot", "version": "1.0"},
            },
        )
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
        except Exception:
            # Best-effort cleanup; never mask the original exit exception.
            pass

    def reenter(self) -> "PlaywrightStealthGateway":
        """Restart the MCP child while preserving this gateway object."""
        self.__exit__(None, None, None)
        return self.__enter__()

    def reset_for_next_entry(self, *, stable_url: str = PROPHET_STABLE_URL) -> None:
        """Return a warm browser context to a clean page between `/create` entries.

        Issue #654: the Playwright MCP child and Chromium context stay open for
        the whole pending-ui batch. Per-entry state is only the fetch wrapper
        used to capture `startOddsCalculation.sessionId`, so reset it in-place
        and navigate away from the form without clearing origin storage.
        """
        try:
            self.playwright_evaluate(script=_RESET_ENTRY_CAPTURE_SCRIPT)
        except Exception:
            # The next navigation is enough to discard the current document in
            # normal cases. If navigation also fails, let that exception surface
            # so the caller can reopen the warm context.
            pass
        self.playwright_navigate(url=stable_url)

    def is_session_healthy(self) -> bool:
        """Return False when the warm Prophet browser has lost Privy auth."""
        try:
            url = _coerce_evaluate_value(
                self.playwright_evaluate(script=_CURRENT_URL_SCRIPT)
            )
        except Exception:
            return False

        try:
            privy_state = _coerce_evaluate_value(
                self.playwright_evaluate(script=_PRIVY_STATE_SCRIPT)
            )
        except Exception:
            privy_state = ""

        url_s = url if isinstance(url, str) else ""
        has_privy_state = bool(privy_state)
        if "app.prophetmarket.ai" not in url_s:
            return False
        if "returnTo=" in url_s and not has_privy_state:
            return False
        if "/login" in url_s and not has_privy_state:
            return False
        return has_privy_state

    # -- Attribute access ---------------------------------------------------

    def __getattr__(self, name: str) -> Callable[..., Any]:
        # Python looks up `__getattr__` only after normal attribute lookup
        # fails, so internal attributes like `_proc` never reach here.
        if not name.startswith("playwright_"):
            raise AttributeError(name)
        tool = name

        def _invoke(**kwargs: Any) -> Any:
            return self._call_tool(tool, kwargs)

        return _invoke

    # -- Internals ----------------------------------------------------------

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if self._proc is None:
            raise RuntimeError(
                "PlaywrightStealthGateway used outside its `with` block"
            )
        # Issue #652: no per-call timeout switcheroo. Track only whether
        # this is the cold-launch call so a TimeoutError can be annotated
        # with `phase=cold_launch` (diagnostic-only).
        is_cold_launch = self._first_tool_call_pending
        if is_cold_launch:
            self._in_cold_launch_call = True
        try:
            result = self._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments or {}},
            )
            return _extract_tool_body(result)
        finally:
            if is_cold_launch:
                self._first_tool_call_pending = False
                self._in_cold_launch_call = False

    def _request(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)
        while True:
            message = self._read()
            if message.get("id") != request_id:
                continue
            error = message.get("error")
            if isinstance(error, dict):
                raise RuntimeError(
                    str(error.get("message") or "MCP request failed.")
                )
            result = message.get("result")
            if isinstance(result, dict):
                return result
            return {"value": result}

    def _write(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("playwright-stealth MCP stdin is not available.")
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        proc.stdin.write(header)
        proc.stdin.write(body)
        proc.stdin.flush()

    def _read(self) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise RuntimeError("playwright-stealth MCP stdout is not available.")
        fd = proc.stdout.fileno()
        header_buf = bytearray()
        while b"\r\n\r\n" not in header_buf:
            header_buf.extend(self._read_exact_with_stderr(fd, 1))
            if len(header_buf) > 16384:
                raise RuntimeError("Invalid MCP header: too large.")
        header_raw, _ = header_buf.split(b"\r\n\r\n", 1)
        content_length = -1
        for line in header_raw.decode("ascii", errors="ignore").split("\r\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip().lower() == "content-length":
                try:
                    content_length = int(value.strip())
                except ValueError:
                    content_length = -1
        if content_length < 0:
            raise RuntimeError("Invalid MCP header: missing content-length.")
        body = self._read_exact_with_stderr(fd, content_length)
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError("Invalid MCP response payload.")
        return parsed

    def _read_exact_with_stderr(self, fd: int, size: int) -> bytes:
        try:
            return _read_exact(fd, size, self._timeout_seconds)
        except TimeoutError as exc:
            # Issue #638: surface what the MCP child wrote to stderr so
            # future regressions are diagnosable instead of opaque.
            # Issue #647: also surface the pre-spawn cleanup pass so the
            # operator can tell whether contention was already cleared.
            # Issue #652: when the timeout fires during the first
            # `tools/call`, embed `phase=cold_launch` so operators can
            # disambiguate from steady-state stalls. Diagnostic-only —
            # the per-entry budget on the /create driver is the real cap.
            stderr_tail = self._drain_stderr_nonblocking()
            report = self._last_kill_report
            diagnostics: list[str] = []
            if self._in_cold_launch_call:
                diagnostics.append("phase=cold_launch")
            if stderr_tail:
                diagnostics.append(f"stderr_tail={stderr_tail!r}")
            if report.killed or report.skipped_self_tree or report.errors:
                diagnostics.append(f"stale_killed={report.killed!r}")
                if report.errors:
                    diagnostics.append(f"stale_errors={report.errors!r}")
            else:
                diagnostics.append("stale_killed=[]")
            raise TimeoutError(f"{exc} " + " ".join(diagnostics)) from exc

    def _drain_stderr_nonblocking(self, *, max_bytes: int = 2048) -> str:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return ""
        fd = proc.stderr.fileno()
        chunks: list[bytes] = []
        remaining = max_bytes
        try:
            while remaining > 0:
                ready, _, _ = select.select([fd], [], [], 0)
                if not ready:
                    break
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except Exception:
            return ""
        return b"".join(chunks).decode("utf-8", errors="replace")


def _read_exact(fd: int, size: int, timeout_seconds: float) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        ready, _, _ = select.select([fd], [], [], timeout_seconds)
        if not ready:
            raise TimeoutError("Timed out waiting for response from playwright-stealth MCP.")
        chunk = os.read(fd, size - len(buf))
        if not chunk:
            raise RuntimeError("playwright-stealth MCP closed stdout before completing a response.")
        buf.extend(chunk)
    return bytes(buf)


def _extract_tool_body(result: dict[str, Any]) -> Any:
    """Unwrap the MCP ``tools/call`` envelope to the underlying body."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        body = structured.get("body")
        if isinstance(body, (dict, list)):
            return body
        return structured
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text") or ""
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                # Issue #668: the bundled playwright-stealth MCP serializes
                # tool results as `typeof result === "string" ? result :
                # JSON.stringify(result)`, so bare-string returns
                # (window.location.href, localStorage.getItem on most keys)
                # land here as text that does NOT parse as JSON. The prior
                # `continue` skipped them silently — every downstream string
                # consumer (`get_url()`, `get_local_storage()`) then read
                # None, which masqueraded as a missing Privy SDK session.
                # Preserve the raw text for these callers.
                return text
            if isinstance(parsed, dict):
                body = parsed.get("body")
                if isinstance(body, (dict, list)):
                    return body
                return parsed
            return parsed
    body = result.get("body")
    if isinstance(body, (dict, list)):
        return body
    return result.get("value")


def _coerce_evaluate_value(result: Any) -> Any:
    """Unwrap common Playwright evaluate response shapes."""
    if isinstance(result, dict):
        for key in ("result", "value", "output"):
            if key in result:
                return result[key]
    return result
