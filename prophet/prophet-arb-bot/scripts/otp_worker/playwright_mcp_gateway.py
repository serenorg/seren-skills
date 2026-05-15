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
from typing import Any, Callable


DEFAULT_BUNDLED_PATH = (
    "/Applications/SerenDesktop.app/Contents/Resources/"
    "mcp-servers/playwright-stealth/dist/index.js"
)
DEFAULT_TIMEOUT_SECONDS = 30.0
MCP_PROTOCOL_VERSION = "2024-11-05"


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
    ) -> None:
        resolved = command if command is not None else self._resolve_default_command()
        if not resolved:
            raise PlaywrightMcpUnavailable(
                "No playwright-stealth MCP command resolvable. Set "
                "SEREN_PLAYWRIGHT_MCP_COMMAND or run inside Seren Desktop."
            )
        self._command = resolved
        self._timeout_seconds = timeout_seconds
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_request_id = 1

    # -- Lifecycle ----------------------------------------------------------

    @classmethod
    def _resolve_default_command(cls) -> list[str] | None:
        """Pick a spawn command. None means "no path available — fail closed"."""
        override = (os.environ.get("SEREN_PLAYWRIGHT_MCP_COMMAND") or "").strip()
        if override:
            parts = shlex.split(override)
            return parts or None
        node_bin = (os.environ.get("SEREN_EMBEDDED_NODE_BIN") or "").strip() or "node"
        if Path(DEFAULT_BUNDLED_PATH).exists():
            return [node_bin, DEFAULT_BUNDLED_PATH]
        return None

    @classmethod
    def is_available(cls) -> bool:
        return cls._resolve_default_command() is not None

    def __enter__(self) -> "PlaywrightStealthGateway":
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
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
        result = self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )
        return _extract_tool_body(result)

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
            header_buf.extend(_read_exact(fd, 1, self._timeout_seconds))
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
        body = _read_exact(fd, content_length, self._timeout_seconds)
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError("Invalid MCP response payload.")
        return parsed


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
                continue
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
