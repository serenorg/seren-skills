from __future__ import annotations

import base64
import json
import os
import re
import select
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

from common import (
    ensure_dir,
    mask_account,
    parse_date_to_iso,
    safe_filename,
    sha256_file,
    sha256_text,
    utc_now_iso,
)


class WorkflowError(RuntimeError):
    error_code = "workflow_error"


class SelectorError(WorkflowError):
    error_code = "blocked_selector"


class AuthError(WorkflowError):
    error_code = "blocked_auth"


@dataclass
class Credentials:
    username: str
    password: str


@dataclass
class DownloadedStatement:
    file_id: str
    local_file_path: str
    account_masked: str
    statement_period_start: str | None
    statement_period_end: str | None
    sha256: str
    bytes: int
    download_status: str
    source_hint: str


def load_selector_profile(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Selector profile not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_browser_family(browser_family: str) -> str:
    token = str(browser_family or "").strip().lower()
    if token in {"firefox", "moz-firefox"} or "firefox" in token:
        return "firefox"
    if token in {"chrome", "chromium", "edge", "msedge"}:
        return "chrome"
    if "chrome" in token or "chromium" in token or "brave" in token or "edge" in token:
        return "chrome"
    return "firefox"


def _resolve_selector_profile_for_browser(
    selector_profile: dict[str, Any], browser_family: str
) -> tuple[dict[str, Any], str, bool]:
    resolved_family = _normalize_browser_family(browser_family)
    browser_overrides = selector_profile.get("browser_overrides")
    if not isinstance(browser_overrides, dict):
        return selector_profile, resolved_family, False

    override = browser_overrides.get(resolved_family)
    if not isinstance(override, dict):
        return selector_profile, resolved_family, False

    return _merge_dict(selector_profile, override), resolved_family, True


def _parse_statement_metadata(raw_text: str) -> tuple[str, str | None, str | None]:
    account_raw = None
    account_match = re.search(r"(?:account|acct)[^\d]*(\d{4,17})", raw_text, re.IGNORECASE)
    if account_match:
        account_raw = account_match.group(1)

    period_start = None
    period_end = None
    period_match = re.search(
        r"(\d{1,2}/\d{1,2}/\d{2,4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        raw_text,
    )
    if period_match:
        period_start = parse_date_to_iso(period_match.group(1))
        period_end = parse_date_to_iso(period_match.group(2))

    return mask_account(account_raw), period_start, period_end


def _css_safe_selectors(selectors: Sequence[str] | None) -> list[str]:
    safe: list[str] = []
    for selector in selectors or []:
        if not isinstance(selector, str):
            continue
        if "text=" in selector or ":has-text(" in selector:
            continue
        safe.append(selector)
    return safe


class _PlaywrightMcpStdioClient:
    def __init__(self, command: str, args: Sequence[str], timeout_ms: int) -> None:
        self.command = command
        self.args = list(args)
        self.timeout_s = max(timeout_ms / 1000.0, 10.0)
        self._rpc_id = 1
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()

        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

        if not self._proc.stdin or not self._proc.stdout or not self._proc.stderr:
            raise WorkflowError("Failed to start Playwright MCP process")

        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            with self._stderr_lock:
                self._stderr_lines.append(line.rstrip("\n"))
                if len(self._stderr_lines) > 200:
                    self._stderr_lines = self._stderr_lines[-200:]

    def _stderr_tail(self) -> str:
        with self._stderr_lock:
            if not self._stderr_lines:
                return ""
            return "\n".join(self._stderr_lines[-20:])

    def _next_id(self) -> int:
        rpc_id = self._rpc_id
        self._rpc_id += 1
        return rpc_id

    def _request(self, payload: dict[str, Any], expect_response: bool = True) -> dict[str, Any]:
        if not self._proc.stdin or not self._proc.stdout:
            raise WorkflowError("Playwright MCP process not available")

        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except Exception as exc:
            stderr = self._stderr_tail()
            detail = f". Stderr: {stderr}" if stderr else ""
            raise WorkflowError(f"Failed to write to Playwright MCP process: {exc}{detail}")

        if not expect_response:
            return {}

        target_id = payload.get("id")
        if target_id is None:
            return {}

        deadline = time.monotonic() + self.timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stderr = self._stderr_tail()
                detail = f". Stderr: {stderr}" if stderr else ""
                raise WorkflowError(
                    f"Timed out waiting for MCP response id={target_id} after {self.timeout_s:.1f}s{detail}"
                )

            ready, _, _ = select.select([self._proc.stdout], [], [], min(0.5, remaining))
            if not ready:
                rc = self._proc.poll()
                if rc is not None:
                    stderr = self._stderr_tail()
                    detail = f". Stderr: {stderr}" if stderr else ""
                    raise WorkflowError(
                        f"Playwright MCP process exited while waiting for response (code={rc}){detail}"
                    )
                continue

            line = self._proc.stdout.readline().strip()
            if not line:
                rc = self._proc.poll()
                stderr = self._stderr_tail()
                detail = f". Stderr: {stderr}" if stderr else ""
                if rc is not None:
                    raise WorkflowError(
                        f"Playwright MCP process exited while waiting for response (code={rc}){detail}"
                    )
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Ignore notifications and responses for other ids.
            if message.get("id") != target_id:
                continue
            return message

    @staticmethod
    def _content_text(result: dict[str, Any]) -> str:
        content = result.get("content") or []
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        return "\n".join(part for part in text_parts if part)

    @staticmethod
    def _parse_text_payload(text: str) -> Any:
        payload = text.strip()
        if not payload:
            return None
        if payload.startswith("{") or payload.startswith("["):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return payload
        return payload

    def initialize(self) -> None:
        init_payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "wellsfargo-bank-statements-download",
                    "version": "1.0.0",
                },
            },
        }
        init_resp = self._request(init_payload)
        if "error" in init_resp:
            message = init_resp.get("error", {}).get("message", "initialize failed")
            raise WorkflowError(f"MCP initialize failed: {message}")

        notify_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        self._request(notify_payload, expect_response=False)

    def list_tools(self) -> list[str]:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        }
        response = self._request(payload)
        if "error" in response:
            message = response.get("error", {}).get("message", "tools/list failed")
            raise WorkflowError(f"MCP tools/list failed: {message}")
        tools = response.get("result", {}).get("tools", [])
        names: list[str] = []
        for tool in tools:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                names.append(tool["name"])
        return names

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> tuple[Any, str]:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        response = self._request(payload)
        if "error" in response:
            message = response.get("error", {}).get("message", "tool call failed")
            raise WorkflowError(f"MCP tool '{name}' failed: {message}")

        result = response.get("result", {})
        text = self._content_text(result)
        if result.get("isError"):
            raise WorkflowError(text or f"MCP tool '{name}' returned an error")
        return self._parse_text_payload(text), text

    def evaluate(self, script: str) -> Any:
        result, _ = self.call_tool("playwright_evaluate", {"script": script})
        return result

    def close_browser(self) -> None:
        try:
            self.call_tool("playwright_close", {})
        except Exception:
            pass

    def shutdown(self) -> None:
        if self._proc.poll() is not None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=2)
        except Exception:
            self._proc.kill()


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _capture_mcp_debug(client: _PlaywrightMcpStdioClient, debug_dir: Path, prefix: str) -> dict[str, str]:
    ensure_dir(debug_dir)
    prefix_name = safe_filename(prefix)
    captured: dict[str, str] = {}

    try:
        screenshot_value, _ = client.call_tool("playwright_screenshot", {})
        if isinstance(screenshot_value, str) and screenshot_value:
            screenshot_path = debug_dir / f"{prefix_name}.png"
            screenshot_path.write_bytes(base64.b64decode(screenshot_value))
            captured["screenshot"] = str(screenshot_path.resolve())
    except Exception:
        pass

    try:
        page_text, _ = client.call_tool("playwright_extract_content", {})
        if isinstance(page_text, str):
            snapshot_path = debug_dir / f"{prefix_name}.snapshot.txt"
            snapshot_path.write_text(page_text, encoding="utf-8")
            captured["snapshot"] = str(snapshot_path.resolve())
    except Exception:
        pass

    return captured


def _classify_error(exc: WorkflowError, debug_paths: dict[str, str] | None = None) -> WorkflowError:
    message = str(exc)
    if debug_paths:
        message = f"{message}. Debug: {debug_paths}"

    lower = message.lower()
    if (
        "authentication appears to have failed" in lower
        or "otp" in lower
        or "sign on" in lower
        or "invalid username" in lower
    ):
        return AuthError(message)

    if "no selector matched" in lower or "statement rows" in lower or "blocked_selector" in lower:
        return SelectorError(message)

    return WorkflowError(message)


def _save_pdf_bytes(
    pdf_bytes: bytes,
    out_pdf_dir: Path,
    index: int,
    row_text: str,
    suggested_filename: str | None,
) -> DownloadedStatement:
    account_masked, period_start, period_end = _parse_statement_metadata(row_text)

    preferred_name = ""
    if suggested_filename:
        preferred_name = safe_filename(Path(suggested_filename).stem)
    file_stub = preferred_name or safe_filename(
        f"statement_{index + 1}_{account_masked}_{period_end or 'unknown'}"
    )

    target_path = out_pdf_dir / f"{file_stub}.pdf"
    suffix = 2
    while target_path.exists():
        target_path = out_pdf_dir / f"{file_stub}_{suffix}.pdf"
        suffix += 1

    ensure_dir(out_pdf_dir)
    target_path.write_bytes(pdf_bytes)

    sha = sha256_file(target_path)
    file_id = sha256_text(f"{sha}:{target_path.name}")
    return DownloadedStatement(
        file_id=file_id,
        local_file_path=str(target_path.resolve()),
        account_masked=account_masked,
        statement_period_start=period_start,
        statement_period_end=period_end,
        sha256=sha,
        bytes=target_path.stat().st_size,
        download_status="ok",
        source_hint=row_text[:200],
    )


def _wait_ms(client: _PlaywrightMcpStdioClient, timeout_ms: int = 2000) -> None:
    script = f"new Promise(resolve => setTimeout(resolve, {int(timeout_ms)}))"
    try:
        client.evaluate(script)
    except Exception:
        # Non-fatal, best-effort wait only.
        return


def _navigate_with_recovery(client: _PlaywrightMcpStdioClient, url: str) -> None:
    """
    Some targets keep background activity open, so MCP navigate can timeout even
    after the destination page is visibly loaded. Treat that specific timeout as
    recoverable when the browser is already on the expected host with content.
    """
    target_parts = urlparse(url)
    target_host = target_parts.netloc.lower()
    target_path = target_parts.path.lower().rstrip("/")
    target_fragment = target_parts.fragment.lower()
    last_error: str | None = None

    try:
        client.call_tool("playwright_navigate", {"url": url})
    except WorkflowError as exc:
        last_error = str(exc)
        # Fallback: force browser location and wait manually.
        client.evaluate(f"window.location.assign({_js(url)}); true")

    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        try:
            state = client.evaluate(
                """
(() => ({
  href: window.location.href || '',
  body_len: (document.body?.innerText || '').length,
  ready_state: document.readyState || ''
}))()
"""
            )
            if isinstance(state, dict):
                current_href = str(state.get("href", "")).strip()
                body_len = int(state.get("body_len", 0) or 0)
                ready_state = str(state.get("ready_state", "")).lower()
                current_parts = urlparse(current_href)
                current_host = current_parts.netloc.lower()
                current_path = current_parts.path.lower().rstrip("/")
                current_fragment = current_parts.fragment.lower()
                path_matches = True
                if target_path and target_path != "/":
                    path_matches = current_path.startswith(target_path)
                fragment_matches = True
                if target_fragment:
                    fragment_matches = target_fragment in current_fragment
                if (
                    current_host
                    and current_host == target_host
                    and path_matches
                    and fragment_matches
                    and body_len > 50
                    and ready_state in {"interactive", "complete"}
                ):
                    return
        except Exception:
            # Navigation can temporarily invalidate execution context.
            pass
        time.sleep(1.0)

    suffix = f" Original navigate error: {last_error}" if last_error else ""
    raise WorkflowError(f"Navigation to {url} did not stabilize within 45s.{suffix}")


def _fill_first_selector(
    client: _PlaywrightMcpStdioClient,
    selectors: Sequence[str],
    value: str,
    field_name: str,
) -> str:
    for selector in selectors:
        try:
            client.call_tool("playwright_fill", {"selector": selector, "value": value})
            return selector
        except WorkflowError:
            continue
    raise SelectorError(
        f"No selector matched for '{field_name}'. Candidates: {json.dumps(list(selectors))}"
    )


def _click_first_selector(
    client: _PlaywrightMcpStdioClient,
    selectors: Sequence[str],
    field_name: str,
) -> str:
    for selector in selectors:
        try:
            client.call_tool("playwright_click", {"selector": selector})
            return selector
        except WorkflowError:
            continue
    raise SelectorError(
        f"No selector matched for '{field_name}'. Candidates: {json.dumps(list(selectors))}"
    )


def _click_by_text_patterns(
    client: _PlaywrightMcpStdioClient,
    patterns: Sequence[str],
    selector_scope: str = "a,button,[role='button'],[role='menuitem'],[role='tab'],[role='link']",
) -> bool:
    script = f"""
(() => {{
  const patterns = {_js(list(patterns))}.map((value) => new RegExp(value, "i"));
  const selectorScope = {_js(selector_scope)};
  const isVisible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const nodes = Array.from(document.querySelectorAll(selectorScope)).filter((el) => {{
    if (!isVisible(el)) return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    if (el.getAttribute('aria-disabled') === 'true') return false;
    if ('disabled' in el && el.disabled) return false;
    return true;
  }});

  const nodeText = (node) =>
    normalize(node.innerText || node.textContent || node.getAttribute('aria-label'));
  const matches = (node, pattern) =>
    pattern.test(nodeText(node)) || pattern.test(normalize(node.getAttribute('aria-label')));

  for (const pattern of patterns) {{
    for (const node of nodes) {{
      if (!matches(node, pattern)) {{
        continue;
      }}
      node.scrollIntoView({{ block: 'center', inline: 'center' }});
      node.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true }}));
      node.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true }}));
      node.click();
      return {{
        clicked: true,
        pattern: pattern.source,
        selector_scope: selectorScope,
        text: nodeText(node),
      }};
    }}
  }}
  return {{ clicked: false, selector_scope: selectorScope }};
}})()
"""
    result = client.evaluate(script)
    return isinstance(result, dict) and bool(result.get("clicked"))


def _fallback_nav_accounts_to_statements(
    client: _PlaywrightMcpStdioClient,
    selector_profile: dict[str, Any] | None = None,
) -> bool:
    statements_patterns = [
        r"view\s+statements?\s*(?:&|and)\s*(?:documents?|disclosures?)",
        r"statements?\s*(?:&|and)\s*(?:documents?|disclosures?)",
        r"\bstatements?\b",
    ]
    accounts_patterns = [r"^accounts$", r"\baccounts\b", r"accounts\s+opens\s+(?:dialog|menu)"]
    selector_profile = selector_profile or {}
    account_selectors = _css_safe_selectors(selector_profile.get("nav_accounts", []))

    if _click_by_text_patterns(client, statements_patterns):
        _wait_ms(client, 1200)
        return True

    if account_selectors:
        try:
            _click_first_selector(client, account_selectors, "nav_accounts")
            _wait_ms(client, 700)
            clicked_statements = _click_by_text_patterns(client, statements_patterns)
            if clicked_statements:
                _wait_ms(client, 1200)
            return clicked_statements
        except SelectorError:
            pass

    opened_accounts = _click_by_text_patterns(
        client,
        patterns=accounts_patterns,
        selector_scope="a,button,[role='button'],[role='menuitem'],[role='tab']",
    )
    if not opened_accounts:
        return False

    _wait_ms(client, 700)
    clicked_statements = _click_by_text_patterns(client, statements_patterns)
    if clicked_statements:
        _wait_ms(client, 1200)
    return clicked_statements


def _statement_links_count(client: _PlaywrightMcpStdioClient) -> int:
    result = client.evaluate(
        r"""
(() => {
  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const docs = [document];
  for (const frame of Array.from(document.querySelectorAll("iframe,frame"))) {
    try {
      if (frame.contentDocument) {
        docs.push(frame.contentDocument);
      }
    } catch (_err) {
      // ignore cross-origin frames
    }
  }
  const isStatementLike = (text) =>
    /statement\b/i.test(text) &&
    (/\d{1,2}[\/-]\d{1,2}[\/-](?:\d{2}|\d{4})/.test(text) || /\bpdf\b/i.test(text));
  const count = docs
    .flatMap((doc) => Array.from(doc.querySelectorAll("a[role='link'],a")))
    .filter((el) => isStatementLike(normalize(el.innerText || el.textContent || '')))
    .length;
  return { count };
})()
"""
    )
    if not isinstance(result, dict):
        return 0
    return int(result.get("count", 0) or 0)


def _extract_wf_navigation_candidates(client: _PlaywrightMcpStdioClient) -> list[str]:
    result = client.evaluate(
        r"""
(() => {
  const globals = window.mwfGlobals || {};
  const origin = String(window.location.origin || '').trim();
  const appPath = String(globals.applicationPath || '').trim().replace(/\/+$/, '');
  const currentHref = String(window.location.href || '').trim();
  const toAbsolute = (rawValue) => {
    const raw = String(rawValue || '').trim();
    if (!raw) return '';
    if (raw.includes('{0}')) return '';
    if (/^(samlurl|nonsamlurl)$/i.test(raw)) return '';
    if (/^https?:\/\//i.test(raw)) return raw;
    if (!origin) return '';
    if (raw.startsWith('/')) {
      if (appPath && !raw.startsWith(appPath + '/')) {
        return `${origin}${appPath}${raw}`;
      }
      return `${origin}${raw}`;
    }
    return appPath ? `${origin}${appPath}/${raw}` : `${origin}/${raw}`;
  };
  const pushCandidate = (value, out) => {
    const absolute = toAbsolute(value);
    if (!absolute) return;
    const lowered = absolute.toLowerCase();
    if (
      lowered.includes('/documents/statement/list') ||
      lowered.includes('/documents/default') ||
      lowered.includes('edocsapp') ||
      lowered.includes('statementsdocs') ||
      lowered.includes('statement') ||
      lowered.includes('identifier=accounts')
    ) {
      out.push(absolute);
    }
  };
  const unwrapWrappedJson = (value) => {
    const text = String(value || '').trim();
    if (!text) return null;
    const firstPercent = text.indexOf('%');
    const lastPercent = text.lastIndexOf('%');
    let payload = text;
    if (firstPercent >= 0 && lastPercent > firstPercent) {
      payload = text.slice(firstPercent + 1, lastPercent);
    }
    payload = payload
      .replace(/\\u0026/g, '&')
      .replace(/\\u003d/g, '=')
      .replace(/\\u002f/g, '/')
      .replace(/\\\//g, '/');
    try {
      return JSON.parse(payload);
    } catch (_err) {
      return null;
    }
  };
  const walkObject = (value, out) => {
    if (!value) return;
    if (Array.isArray(value)) {
      for (const item of value) {
        walkObject(item, out);
      }
      return;
    }
    if (typeof value === 'object') {
      for (const [key, child] of Object.entries(value)) {
        if (typeof child === 'string') {
          if (/url$/i.test(key) || /url/i.test(key) || /navigation/i.test(key)) {
            pushCandidate(child, out);
          }
        } else {
          walkObject(child, out);
        }
      }
    }
  };

  const urls = [];
  pushCandidate(globals.statementDisclosuresUrl, urls);
  pushCandidate(globals.edocsUrl, urls);
  pushCandidate(globals.accountsNavigationUrl, urls);

  const navMenu = unwrapWrappedJson(globals.navMenuConfig);
  walkObject(navMenu, urls);

  const eDocsHomeModel = unwrapWrappedJson(globals.eDocsHomeModel);
  walkObject(eDocsHomeModel, urls);

  const deduped = [];
  const seen = new Set();
  for (const url of urls) {
    const normalized = String(url || '').trim();
    if (!normalized) continue;
    if (normalized === currentHref) continue;
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    deduped.push(normalized);
  }

  return {
    urls: deduped,
  };
})()
"""
    )
    if not isinstance(result, dict):
        return []
    urls = result.get("urls", [])
    if not isinstance(urls, list):
        return []
    filtered: list[str] = []
    for url in urls:
        if url is None:
            continue
        text = str(url).strip()
        if not text:
            continue
        if text.lower() in {"none", "null", "undefined"}:
            continue
        filtered.append(text)
    return filtered


def _score_wf_navigation_candidate(url: str) -> tuple[int, int]:
    lowered = url.lower()
    if "/documents/statement/list" in lowered:
        return (0, len(lowered))
    if "/documents/default" in lowered:
        return (1, len(lowered))
    if "edocsapp" in lowered:
        return (2, len(lowered))
    if "statementsdocs" in lowered or "statement" in lowered:
        return (3, len(lowered))
    if "identifier=accounts" in lowered:
        return (4, len(lowered))
    return (5, len(lowered))


def _route_to_statements_via_known_wf_urls(
    client: _PlaywrightMcpStdioClient,
    statements_patterns: Sequence[str],
) -> bool:
    candidates = sorted(
        _extract_wf_navigation_candidates(client),
        key=_score_wf_navigation_candidate,
    )
    if not candidates:
        return False

    for candidate in candidates:
        try:
            _navigate_with_recovery(client, candidate)
            _wait_ms(client, 1200)
        except WorkflowError:
            continue

        if _statement_links_count(client) > 0:
            return True

        _click_by_text_patterns(client, statements_patterns)
        _wait_ms(client, 900)
        if _statement_links_count(client) > 0:
            return True

    return False


def _ensure_statement_links_visible(
    client: _PlaywrightMcpStdioClient,
    selector_profile: dict[str, Any] | None = None,
) -> bool:
    statements_patterns = [
        r"view\s+statements?\s*(?:&|and)\s*(?:documents?|disclosures?)",
        r"statements?\s*(?:&|and)\s*(?:documents?|disclosures?)",
        r"\bview\s+statements?\b",
        r"\bstatements?\b",
    ]
    accounts_patterns = [r"^accounts$", r"\baccounts\b", r"accounts\s+opens\s+(?:dialog|menu)"]
    selector_profile = selector_profile or {}
    account_selectors = _css_safe_selectors(selector_profile.get("nav_accounts", []))

    if _statement_links_count(client) > 0:
        return True

    if account_selectors:
        try:
            _click_first_selector(client, account_selectors, "nav_accounts")
            _wait_ms(client, 700)
        except SelectorError:
            pass

    _click_by_text_patterns(client, statements_patterns)
    _wait_ms(client, 900)
    if _statement_links_count(client) > 0:
        return True

    _click_by_text_patterns(
        client,
        accounts_patterns,
        selector_scope="a,button,[role='button'],[role='menuitem'],[role='tab']",
    )
    _wait_ms(client, 600)
    _click_by_text_patterns(client, statements_patterns)
    _wait_ms(client, 1200)
    if _statement_links_count(client) > 0:
        return True

    if _route_to_statements_via_known_wf_urls(client, statements_patterns):
        return True

    # If the tile reports a transient error, retry in-page expansion a few times.
    has_tile_error = client.evaluate(
        r"""
(() => {
  const body = String(document.body?.innerText || '').toLowerCase();
  return /there was a problem on our end|please try again a little later/.test(body);
})()
"""
    )
    if bool(has_tile_error):
        for _ in range(3):
            _click_by_text_patterns(client, statements_patterns)
            _wait_ms(client, 1000)
            if _statement_links_count(client) > 0:
                return True

    # Last fallback: use app-relative statements list URL published in mwfGlobals.
    statement_list_url = client.evaluate(
        r"""
(() => {
  const globals = window.mwfGlobals || {};
  const raw = String(globals.statementDisclosuresUrl || '').trim();
  if (!raw) return '';
  if (/^https?:\/\//i.test(raw)) return raw;
  const origin = window.location.origin || '';
  if (!origin) return '';
  const appPath = String(globals.applicationPath || '').trim().replace(/\/+$/, '');
  if (raw.startsWith('/')) {
    if (appPath) {
      return `${origin}${appPath}${raw}`;
    }
    return `${origin}${raw}`;
  }
  return appPath ? `${origin}${appPath}/${raw}` : `${origin}/${raw}`;
})()
"""
    )
    statement_list_url = str(statement_list_url or "").strip()
    if statement_list_url:
        try:
            _navigate_with_recovery(client, statement_list_url)
            _wait_ms(client, 1200)
            if _statement_links_count(client) > 0:
                return True
        except WorkflowError:
            pass

    return _statement_links_count(client) > 0


def _click_statement_link_and_capture_url(
    client: _PlaywrightMcpStdioClient,
    statement_hint: str,
) -> str | None:
    clicked = False
    url_candidate = ""
    try:
        result = client.evaluate(
            f"""
(() => {{
  const hint = {_js(statement_hint)};
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const hintNorm = normalize(hint);
  const hintLower = hintNorm.toLowerCase();
  const hintDateMatch = hintNorm.match(/\\d{{1,2}}[\\/-]\\d{{1,2}}[\\/-](?:\\d{{2}}|\\d{{4}})/);
  const hintDate = hintDateMatch ? hintDateMatch[0].toLowerCase() : '';
  const docs = [document];
  for (const frame of Array.from(document.querySelectorAll("iframe,frame"))) {{
    try {{
      if (frame.contentDocument) {{
        docs.push(frame.contentDocument);
      }}
    }} catch (_err) {{
      // ignore cross-origin frames
    }}
  }}
  const interactive = docs.flatMap((doc) =>
    Array.from(
      doc.querySelectorAll(
        "a[role='link'],a,button,[role='link'],[role='button'],[tabindex]"
      )
    )
  );
  const nodes = interactive
    .map((el) => ({{ el, text: normalize(el.innerText || el.textContent || '') }}))
    .filter((entry) => !!entry.text);

  const matchesHint = (text) => {{
    if (!hintLower) return false;
    const textLower = text.toLowerCase();
    if (textLower.includes(hintLower)) return true;
    if (hintDate && textLower.includes(hintDate) && /statement\\b/i.test(text)) return true;
    return false;
  }};
  const isStatementText = (text) => {{
    return /statement\\b/i.test(text)
      && (/\\d{{1,2}}[\\/-]\\d{{1,2}}[\\/-](?:\\d{{2}}|\\d{{4}})/.test(text) || /\\bpdf\\b/i.test(text));
  }};
  const pickSmallest = (entries) => {{
    if (!entries.length) return null;
    const sorted = entries
      .slice()
      .sort((a, b) => a.text.length - b.text.length);
    return sorted[0].el;
  }};

  const matchHintAndStatement = nodes.filter((entry) => matchesHint(entry.text) && isStatementText(entry.text));
  const matchHintOnly = nodes.filter((entry) => matchesHint(entry.text));
  const matchStatementOnly = nodes.filter((entry) => isStatementText(entry.text));
  const link =
    pickSmallest(matchHintAndStatement) ||
    pickSmallest(matchHintOnly) ||
    pickSmallest(matchStatementOnly);
  if (!link) {{
    return {{ clicked: false, url: '' }};
  }}

  const rawHref =
    link.getAttribute('href') ||
    link.getAttribute('data-href') ||
    link.getAttribute('data-url') ||
    (typeof link.href === 'string' ? link.href : '') ||
    '';
  if (rawHref) {{
    const absoluteHref = new URL(rawHref, window.location.href).toString();
    if (/\\/edocs\\/documents\\/retrieve\\//i.test(absoluteHref) || /\\.pdf(\\?|$)/i.test(absoluteHref)) {{
      return {{ clicked: true, url: absoluteHref }};
    }}
  }}

  link.scrollIntoView({{ block: 'center', inline: 'center' }});
  link.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true }}));
  link.dispatchEvent(new MouseEvent('mousedown', {{ bubbles: true }}));
  link.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true }}));
  if (typeof link.click === 'function') {{
    link.click();
  }}
  const role = String(link.getAttribute('role') || '').toLowerCase();
  if (role === 'link' || role === 'button') {{
    link.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', bubbles: true }}));
    link.dispatchEvent(new KeyboardEvent('keyup', {{ key: 'Enter', code: 'Enter', bubbles: true }}));
    link.dispatchEvent(new KeyboardEvent('keydown', {{ key: ' ', code: 'Space', bubbles: true }}));
    link.dispatchEvent(new KeyboardEvent('keyup', {{ key: ' ', code: 'Space', bubbles: true }}));
  }}
  return {{ clicked: true, url: '' }};
}})()
"""
        )
        if isinstance(result, dict):
            clicked = bool(result.get("clicked"))
            url_candidate = str(result.get("url", "")).strip()
    except WorkflowError as exc:
        # Navigation can happen immediately after click and invalidate the current execution context.
        if "Execution context was destroyed" in str(exc):
            clicked = True
        else:
            raise

    if url_candidate:
        lowered = url_candidate.lower()
        if "/edocs/documents/retrieve/" in lowered or ".pdf" in lowered:
            return url_candidate

    if not clicked:
        return None

    deadline = time.time() + 20.0
    while time.time() < deadline:
        try:
            current_url = str(client.evaluate("window.location.href || ''") or "").strip()
        except WorkflowError as exc:
            if "Execution context was destroyed" in str(exc):
                time.sleep(0.25)
                continue
            return None

        lowered = current_url.lower()
        if "/edocs/documents/retrieve/" in lowered or ".pdf" in lowered:
            return current_url
        time.sleep(0.25)

    return None


def _extract_statement_click_hint(row_text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(row_text or "")).strip()
    if not normalized:
        return ""

    statement_match = re.search(
        r"(Statement\s+\d{1,2}[/-]\d{1,2}[/-](?:\d{2}|\d{4}))",
        normalized,
        flags=re.IGNORECASE,
    )
    if statement_match:
        return statement_match.group(1)

    date_match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-](?:\d{2}|\d{4}))", normalized)
    if date_match:
        return date_match.group(1)

    return normalized[:180]


def _wait_for_browser_download_pdf(
    download_dir: Path,
    known_paths: set[Path],
    timeout_s: float = 12.0,
) -> Path | None:
    if not download_dir.exists() or not download_dir.is_dir():
        return None

    deadline = time.time() + max(timeout_s, 1.0)
    while time.time() < deadline:
        candidates: list[Path] = []
        for candidate in download_dir.iterdir():
            if candidate in known_paths:
                continue
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() != ".pdf":
                continue
            candidates.append(candidate)

        if candidates:
            latest = max(candidates, key=lambda p: p.stat().st_mtime)
            size_before = latest.stat().st_size
            _sleep = min(0.8, max(0.1, deadline - time.time()))
            if _sleep > 0:
                time.sleep(_sleep)
            if latest.exists() and latest.is_file():
                size_after = latest.stat().st_size
                if size_after > 0 and size_after == size_before:
                    known_paths.add(latest)
                    return latest
        time.sleep(0.4)

    return None


def login_and_download_statements(
    credentials: Credentials,
    selector_profile: dict[str, Any],
    out_dir: Path,
    months: int,
    headless: bool,
    timeout_ms: int,
    otp_provider: Callable[[], str],
    mcp_command: str,
    mcp_args: Sequence[str],
    auth_method: str = "password",
    passkey_approval_provider: Callable[[], None] | None = None,
    manual_login_provider: Callable[[], None] | None = None,
    browser_family: str = "firefox",
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    del headless  # Browser mode is controlled by the SerenDesktop Playwright MCP server.

    progress = progress or (lambda _step, _payload: None)
    selector_profile, resolved_browser_family, selector_override_applied = (
        _resolve_selector_profile_for_browser(selector_profile, browser_family)
    )
    chrome_recovery_enabled = resolved_browser_family == "chrome"
    progress(
        "browser_path_selected",
        {
            "browser_family": resolved_browser_family,
            "firefox_stable_path": resolved_browser_family == "firefox",
            "chrome_recovery_enabled": chrome_recovery_enabled,
            "selector_override_applied": selector_override_applied,
        },
    )
    debug_dir = ensure_dir(out_dir / "debug")
    pdf_dir = ensure_dir(out_dir / "pdfs")
    downloads: list[DownloadedStatement] = []

    client = _PlaywrightMcpStdioClient(
        command=mcp_command,
        args=mcp_args,
        timeout_ms=timeout_ms,
    )

    try:
        client.initialize()
        tool_names = set(client.list_tools())
        required_tools = {
            "playwright_navigate",
            "playwright_click",
            "playwright_fill",
            "playwright_evaluate",
            "playwright_extract_content",
        }
        missing = sorted(required_tools - tool_names)
        if missing:
            raise WorkflowError(f"Playwright MCP server missing required tools: {missing}")

        progress(
            "authenticated",
            {
                "phase": "mcp_initialized",
                "mcp_command": mcp_command,
                "mcp_args": list(mcp_args),
            },
        )

        login_url = str(selector_profile.get("login_url", "")).strip()
        if not login_url:
            raise SelectorError("login_url missing in selector profile")

        resolved_auth_method = str(auth_method or "password").strip().lower()
        if resolved_auth_method == "manual":
            # Manual handoff should still open Wells Fargo for the user, but avoid
            # hard-failing on occasional browser-attach navigation instability.
            try:
                _navigate_with_recovery(client, login_url)
            except WorkflowError as exc:
                progress(
                    "manual_login_pre_nav_warning",
                    {"reason": str(exc)[:220], "target_url": login_url},
                )
                try:
                    client.evaluate(f"window.location.assign({_js(login_url)}); true")
                except Exception:
                    pass
            _wait_ms(client, 250)
        else:
            _navigate_with_recovery(client, login_url)

        if resolved_auth_method == "manual":
            progress("manual_login_waiting", {"required": True})
            if manual_login_provider:
                manual_login_provider()
            _wait_ms(client, 1500)
        else:
            _fill_first_selector(
                client,
                selectors=selector_profile.get("username_fields", []),
                value=credentials.username,
                field_name="username_fields",
            )

        if resolved_auth_method == "passkey":
            passkey_selectors = selector_profile.get("passkey_buttons", [])
            if not passkey_selectors:
                passkey_selectors = [
                    "a[href*='passkey=Y']",
                    "form#frmSignon a:has-text('Sign on with a passkey')",
                    "a:has-text('Sign on with a passkey')",
                    "form#frmSignon button:has-text('Use a passkey')",
                    "form#frmSignon a:has-text('Use a passkey')",
                    "button:has-text('Use a passkey')",
                    "a:has-text('Use a passkey')",
                ]
            _click_first_selector(
                client,
                selectors=passkey_selectors,
                field_name="passkey_buttons",
            )
            _wait_ms(client, 1000)
            progress("passkey_waiting", {"required": True, "attempt": 1})
            if passkey_approval_provider:
                passkey_approval_provider()
            _wait_ms(client, 4000)

            passkey_result = client.evaluate(
                """
(() => {
  const body = (document.body?.innerText || '').toLowerCase();
  const url = window.location.href || '';
  const authOk = /account summary|account activity|statements/.test(body);
  const stillOnLogin = /login/.test(url.toLowerCase()) || /sign on/.test(body);
  const passkeyCtaVisible = /use a passkey|sign on with a passkey/.test(body);
  return {
    auth_ok: authOk,
    still_on_login: stillOnLogin,
    passkey_cta_visible: passkeyCtaVisible,
    page_url: url
  };
})()
"""
            )
            if not isinstance(passkey_result, dict):
                raise WorkflowError("Passkey state-check returned invalid MCP result payload")

            if bool(passkey_result.get("still_on_login")) and bool(
                passkey_result.get("passkey_cta_visible")
            ):
                # Retry once because browser focus/gesture issues can suppress OS passkey prompts.
                _click_first_selector(
                    client,
                    selectors=passkey_selectors,
                    field_name="passkey_buttons",
                )
                progress("passkey_waiting", {"required": True, "attempt": 2})
                if passkey_approval_provider:
                    passkey_approval_provider()
                _wait_ms(client, 4000)
        elif resolved_auth_method == "password":
            _fill_first_selector(
                client,
                selectors=selector_profile.get("password_fields", []),
                value=credentials.password,
                field_name="password_fields",
            )
            _click_first_selector(
                client,
                selectors=selector_profile.get("submit_buttons", []),
                field_name="submit_buttons",
            )
            _wait_ms(client, 2000)
        elif resolved_auth_method == "manual":
            session_check_script = """
(() => {
  const body = (document.body?.innerText || '').toLowerCase();
  const url = (window.location.href || '').toLowerCase();
  const hasLoginForm = !!document.querySelector("form#frmSignon,input[name='j_username'],input#userid");
  const stillOnLogin = hasLoginForm || url.includes('signon') || (url.includes('login') && !url.includes('logout'));
  return { still_on_login: stillOnLogin, page_url: window.location.href || '' };
})()
"""
            session_result = client.evaluate(session_check_script)
            if not isinstance(session_result, dict):
                raise WorkflowError("Manual-login check returned invalid MCP result payload")
            if bool(session_result.get("still_on_login")):
                raise AuthError(
                    "Manual login was not completed before handoff. "
                    "Stay in the same browser window, finish Wells Fargo login, then continue."
                )
        else:
            raise WorkflowError(
                f"Unsupported auth_method '{resolved_auth_method}'. "
                "Expected password, passkey, or manual."
            )

        otp_check_script = """
(() => {
  const body = (document.body?.innerText || '').toLowerCase();
  const otpRegex = /one-time|verification code|security code|otp/;
  const otpInput = !!document.querySelector("input[name*='otp'],input[id*='otp'],input[name*='verification']");
  return { otp_required: otpRegex.test(body) || otpInput, page_url: window.location.href };
})()
"""
        if resolved_auth_method in {"password", "passkey"}:
            login_result = client.evaluate(otp_check_script)
            if not isinstance(login_result, dict):
                raise WorkflowError("Login step returned invalid MCP result payload")

            if bool(login_result.get("otp_required")):
                progress("otp_waiting", {"required": True})
                otp_code = otp_provider().strip()
                if not otp_code:
                    raise AuthError("OTP required but no code provided")

                _fill_first_selector(
                    client,
                    selectors=selector_profile.get("otp_fields", []),
                    value=otp_code,
                    field_name="otp_fields",
                )
                _click_first_selector(
                    client,
                    selectors=selector_profile.get("otp_submit_buttons", []),
                    field_name="otp_submit_buttons",
                )
                _wait_ms(client, 2000)

        auth_check_script = """
(() => {
  const body = (document.body?.innerText || '').toLowerCase();
  const authOk = /account summary|account activity|statements/.test(body);
  const url = window.location.href || '';
  const stillOnLogin = url.toLowerCase().includes('login');
  return { auth_ok: authOk, still_on_login: stillOnLogin, page_url: url };
})()
"""
        if resolved_auth_method in {"password", "passkey"}:
            auth_result = client.evaluate(auth_check_script)
            if not isinstance(auth_result, dict):
                raise WorkflowError("Auth-check step returned invalid MCP result payload")
            if not bool(auth_result.get("auth_ok")) and bool(auth_result.get("still_on_login")):
                debug_paths = _capture_mcp_debug(client, debug_dir, "auth_failure")
                raise AuthError(f"Authentication appears to have failed. Debug: {debug_paths}")

        statements_url = str(selector_profile.get("statements_url", "")).strip()
        if statements_url:
            _navigate_with_recovery(client, statements_url)
        else:
            # Fast path first: direct DOM text matching avoids long Playwright actionability waits.
            if _fallback_nav_accounts_to_statements(client, selector_profile=selector_profile):
                progress(
                    "nav_to_statements_fallback",
                    {"method": "accounts_menu_text_match"},
                )
                _wait_ms(client, 1200)
            else:
                try:
                    matched_selector = _click_first_selector(
                        client,
                        selectors=selector_profile.get("nav_to_statements", []),
                        field_name="nav_to_statements",
                    )
                    progress(
                        "nav_to_statements_selector",
                        {
                            "selector": matched_selector,
                            "auth_method": resolved_auth_method,
                        },
                    )
                    _wait_ms(client, 2000)
                except SelectorError as exc:
                    progress(
                        "nav_to_statements_selector_miss",
                        {
                            "auth_method": resolved_auth_method,
                            "reason": str(exc)[:220],
                        },
                    )

        _ensure_statement_links_visible(client, selector_profile=selector_profile)

        rows_script = fr"""
(() => {{
  const selectorProfile = {_js(selector_profile)};
  const maxMonths = {_js(months)};
  const cssSafeControls = {_js(_css_safe_selectors(selector_profile.get("download_controls", [])))};

  const docs = [document];
  for (const frame of Array.from(document.querySelectorAll("iframe,frame"))) {{
    try {{
      if (frame.contentDocument) {{
        docs.push(frame.contentDocument);
      }}
    }} catch (_err) {{
      // ignore cross-origin frames
    }}
  }}

  let rowsSelector = null;
  let rows = [];
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const isStatementLikeText = (text) =>
    /statement\\b/i.test(text) &&
    (/\\d{{1,2}}[\\/-]\\d{{1,2}}[\\/-](?:\\d{{2}}|\\d{{4}})/.test(text) || /\\bpdf\\b/i.test(text));
  const isStatementLikeNode = (node) => {{
    if (!node) return false;
    const text = normalize(node.innerText || node.textContent || '');
    if (isStatementLikeText(text)) return true;
    if (node.matches && node.matches("a[href*='.pdf']")) return true;
    try {{
      return !!node.querySelector("a[href*='.pdf']");
    }} catch (_err) {{
      return false;
    }}
  }};

  for (const selector of (selectorProfile.statement_rows || [])) {{
    try {{
      const found = docs.flatMap((doc) => Array.from(doc.querySelectorAll(selector)));
      const filtered = found.filter((node) => isStatementLikeNode(node));
      if (filtered.length > 0) {{
        rowsSelector = selector;
        rows = filtered;
        break;
      }}
    }} catch (_err) {{
      // keep trying selectors
    }}
  }}

  if (!rowsSelector) {{
    const statementLinks = docs
      .flatMap((doc) => Array.from(doc.querySelectorAll("a[role='link'],a")))
      .filter((el) => isStatementLikeText(normalize(el.innerText || el.textContent || '')));
    if (statementLinks.length > 0) {{
      rowsSelector = "heuristic:statement_links";
      rows = statementLinks;
    }}
  }}

  if (!rowsSelector) {{
    return {{ row_count: 0, rows_selector: null, downloaded: [] }};
  }}

  const downloaded = [];

  for (let index = 0; index < rows.length; index += 1) {{
    if (downloaded.length >= maxMonths) {{
      break;
    }}
    const row = rows[index];
    let rowText = normalize((row.innerText || row.textContent || '')).slice(0, 4000);
    const containerCandidates = [
      row.closest ? row.closest("li") : null,
      row.closest ? row.closest("tr") : null,
      row.parentElement || null,
      row.parentElement && row.parentElement.parentElement ? row.parentElement.parentElement : null,
      row.closest ? row.closest("div,section,article") : null,
    ].filter(Boolean);
    for (const container of containerCandidates) {{
      const contextText = normalize((container.innerText || container.textContent || '')).slice(0, 4000);
      if (isStatementLikeText(contextText)) {{
        rowText = contextText;
        break;
      }}
    }}

    let link = null;
    let controlSelector = null;

    for (const selector of cssSafeControls) {{
      try {{
        const candidate = row.querySelector(selector);
        if (candidate) {{
          link = candidate;
          controlSelector = selector;
          break;
        }}
      }} catch (_err) {{
        // try next control selector
      }}
    }}

    if (!link && row.matches && row.matches("a[href]")) {{
      link = row;
      controlSelector = "self:a[href]";
    }}

    if (!link) {{
      link = row.querySelector("a[href*='.pdf'], a[href], a[role='link'], [role='link'], button, [role='button']");
      if (link) {{
        controlSelector = "generic:interactive";
      }}
    }}

    if (!link) {{
      for (const container of containerCandidates) {{
        const candidate = container.querySelector("a[href*='.pdf'], a[href], a[role='link'], [role='link'], button, [role='button']");
        if (candidate) {{
          link = candidate;
          controlSelector = "container:interactive";
          break;
        }}
      }}
    }}

    if (!link && row.matches && row.matches("a[role='link'],a,[role='link'],button,[role='button']")) {{
      link = row;
      controlSelector = "self:interactive";
    }}

    let href = null;
    let suggestedFilename = null;
    if (link) {{
      let rawHref =
        link.getAttribute('href') ||
        link.getAttribute('data-href') ||
        link.getAttribute('data-url');
      if (!rawHref && typeof link.href === 'string' && link.href) {{
        rawHref = link.href;
      }}
      if (!rawHref && link.querySelector) {{
        const nestedLink = link.querySelector("a[href], a[role='link']");
        if (nestedLink) {{
          const nestedHref =
            nestedLink.getAttribute('href') ||
            nestedLink.getAttribute('data-href') ||
            nestedLink.getAttribute('data-url') ||
            (typeof nestedLink.href === 'string' ? nestedLink.href : '');
          if (nestedHref) {{
            rawHref = nestedHref;
            link = nestedLink;
          }}
        }}
      }}
      if (!rawHref && link.getAttributeNames) {{
        const attrNames = link.getAttributeNames();
        for (const attrName of attrNames) {{
          const value = String(link.getAttribute(attrName) || '');
          if (/\/edocs\/documents\/retrieve\//i.test(value) || /\/documents\/retrieve\//i.test(value) || /\.pdf(\?|$)/i.test(value)) {{
            rawHref = value;
            break;
          }}
        }}
      }}
      if (!rawHref) {{
        const inlineOnclick = String(link.getAttribute('onclick') || '');
        const directMatch = inlineOnclick.match(/(\/edocs\/documents\/retrieve\/[^\s'"]+)/i)
          || inlineOnclick.match(/(https?:\/\/[^'"]+\.pdf(?:\?[^'"]*)?)/i);
        if (directMatch) {{
          rawHref = directMatch[1];
        }}
      }}
      if (rawHref) {{
        try {{
          href = new URL(rawHref, window.location.href).toString();
        }} catch (_err) {{
          href = String(rawHref);
        }}
      }}
      suggestedFilename = link.getAttribute('download') || null;
    }}

    const hasStatementWord = /statement\b/i.test(rowText);
    const hasDate = /\d{{1,2}}[\/-]\d{{1,2}}[\/-](?:\d{{2}}|\d{{4}})/.test(rowText);
    const hasPdfWord = /\bpdf\b/i.test(rowText);
    const isStatementText = hasStatementWord && (hasDate || hasPdfWord);
    const hrefLooksLikeStatement = href
      ? (/\/edocs\/documents\/retrieve\//i.test(href) || /\.pdf(\?|$)/i.test(href))
      : false;
    const rowLooksRelevant =
      isStatementText ||
      hasDate ||
      (hasPdfWord && !!link) ||
      hrefLooksLikeStatement;
    if (!rowLooksRelevant) {{
      continue;
    }}

    downloaded.push({{
      index,
      status: href ? 'ok' : (link ? 'pseudo_link' : 'missing_control'),
      row_text: rowText,
      href,
      suggested_filename: suggestedFilename,
      control_selector: controlSelector,
    }});
  }}

  return {{ row_count: rows.length, rows_selector: rowsSelector, downloaded }};
}})()
"""

        zero_rows_diag_script = r"""
(() => {
  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const docs = [document];
  for (const frame of Array.from(document.querySelectorAll("iframe,frame"))) {
    try {
      if (frame.contentDocument) {
        docs.push(frame.contentDocument);
      }
    } catch (_err) {
      // ignore cross-origin frames
    }
  }
  const links = docs.flatMap((doc) => Array.from(doc.querySelectorAll("a[role='link'],a")));
  const statementLike = links.filter((el) => {
    const text = normalize(el.innerText || el.textContent || '');
    return /statement\b/i.test(text) &&
      (/\d{1,2}[\/-]\d{1,2}[\/-](?:\d{2}|\d{4})/.test(text) || /\bpdf\b/i.test(text));
  });
  return {
    page_url: window.location.href || '',
    frames: Math.max(docs.length - 1, 0),
    links_total: links.length,
    statement_like_links: statementLike.length,
    body_preview: normalize(document.body?.innerText || '').slice(0, 400)
  };
})()
"""
        fallback_statement_links_script = fr"""
(() => {{
  const maxMonths = {_js(months)};
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const hasDateLike = (text) => /\d{{1,2}}[\/-]\d{{1,2}}[\/-](?:\d{{2}}|\d{{4}})/.test(text);
  const docs = [document];
  for (const frame of Array.from(document.querySelectorAll("iframe,frame"))) {{
    try {{
      if (frame.contentDocument) {{
        docs.push(frame.contentDocument);
      }}
    }} catch (_err) {{
      // ignore cross-origin frames
    }}
  }}

  const links = docs
    .flatMap((doc) => Array.from(doc.querySelectorAll("a[role='link'],a")))
    .map((el, idx) => {{
      const text = normalize(el.innerText || el.textContent || '');
      const textLower = text.toLowerCase();
      const rawHref =
        el.getAttribute('href') ||
        el.getAttribute('data-href') ||
        el.getAttribute('data-url') ||
        (typeof el.href === 'string' ? el.href : '') ||
        '';
      let href = '';
      if (rawHref) {{
        try {{
          href = new URL(rawHref, window.location.href).toString();
        }} catch (_err) {{
          href = String(rawHref);
        }}
      }}
      const hrefLower = String(href || '').toLowerCase();
      return {{
        idx,
        text,
        text_lower: textLower,
        href,
        href_lower: hrefLower,
        suggested_filename: el.getAttribute('download') || null,
      }};
    }});

  const isStatementLike = (entry) => {{
    const text = String(entry.text || '');
    const textLower = String(entry.text_lower || '');
    const hrefLower = String(entry.href_lower || '');
    const hasStatementWord = textLower.includes('statement');
    const hasPdfWord = textLower.includes('pdf');
    const hasDate = hasDateLike(text);
    const hasStatementHref =
      hrefLower.includes('/edocs/documents/retrieve/') ||
      hrefLower.includes('/documents/retrieve/') ||
      hrefLower.includes('.pdf');
    return hasStatementWord && (hasDate || hasPdfWord || hasStatementHref);
  }};

  const statementLinks = links.filter((entry) => isStatementLike(entry));
  const downloaded = [];
  const candidateLimit = Math.max(maxMonths * 6, 12);
  for (let i = 0; i < statementLinks.length && downloaded.length < candidateLimit; i += 1) {{
    const item = statementLinks[i];
    const hasPdfHref =
      !!item.href &&
      (
        String(item.href_lower || '').includes('/edocs/documents/retrieve/') ||
        String(item.href_lower || '').includes('/documents/retrieve/') ||
        String(item.href_lower || '').includes('.pdf')
      );
    downloaded.push({{
      index: i,
      status: hasPdfHref ? 'ok' : 'pseudo_link',
      row_text: item.text,
      href: item.href || null,
      suggested_filename: item.suggested_filename,
      control_selector: 'fallback:statement_link',
    }});
  }}

  return {{
    row_count: statementLinks.length,
    rows_selector: 'fallback:statement_links_diag',
    downloaded,
  }};
}})()
"""

        raw_download_result = client.evaluate(rows_script)
        if not isinstance(raw_download_result, dict):
            raise WorkflowError("Download step returned invalid MCP result payload")

        row_count = int(raw_download_result.get("row_count", 0) or 0)
        if row_count == 0 and not statements_url and _fallback_nav_accounts_to_statements(
            client,
            selector_profile=selector_profile,
        ):
            progress(
                "statement_rows_retry",
                {"reason": "initial_zero_rows", "method": "accounts_menu_text_match"},
            )
            _wait_ms(client, 2000)
            raw_download_result = client.evaluate(rows_script)
            if not isinstance(raw_download_result, dict):
                raise WorkflowError("Download step returned invalid MCP result payload")
            row_count = int(raw_download_result.get("row_count", 0) or 0)

        if row_count == 0:
            zero_rows_diag = client.evaluate(zero_rows_diag_script)
            if isinstance(zero_rows_diag, dict):
                links_total = int(zero_rows_diag.get("links_total", 0) or 0)
                statement_like_links = int(zero_rows_diag.get("statement_like_links", 0) or 0)
                body_preview = str(zero_rows_diag.get("body_preview", "")).strip()
                current_url = str(zero_rows_diag.get("page_url", "")).strip()
            else:
                links_total = 0
                statement_like_links = 0
                body_preview = ""
                current_url = ""

            if row_count == 0 and chrome_recovery_enabled and statement_like_links > 0:
                progress(
                    "statement_rows_retry",
                    {
                        "reason": "statement_links_fallback",
                        "method": "fallback_statement_links_script",
                        "statement_like_links": statement_like_links,
                    },
                )
                raw_download_result = client.evaluate(fallback_statement_links_script)
                if isinstance(raw_download_result, dict):
                    row_count = int(raw_download_result.get("row_count", 0) or 0)

            body_lower = body_preview.lower()
            looks_like_docs_landing = (
                "statements and documents" in body_lower
                or "statements and disclosures" in body_lower
            )

            if row_count == 0 and chrome_recovery_enabled and looks_like_docs_landing:
                progress(
                    "statement_rows_retry",
                    {
                        "reason": "expand_statements_section",
                        "method": "dom_text_toggle_click",
                        "page_url": current_url[:220],
                    },
                )
                try:
                    client.evaluate(
                        r"""
(() => {
  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const isVisible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const isInteractive = (el) => {
    if (!el || !el.matches) return false;
    if (el.matches("button,[role='button'],summary,a,[tabindex],[aria-controls],[aria-expanded]")) return true;
    return typeof el.onclick === 'function';
  };
  const clickLikeUser = (el) => {
    if (!el) return false;
    try {
      el.scrollIntoView({ block: 'center', inline: 'center' });
      el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
      el.click();
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true }));
      el.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
      return true;
    } catch (_err) {
      return false;
    }
  };

  const nodes = Array.from(document.querySelectorAll("*"))
    .filter((el) => isVisible(el))
    .map((el) => ({ el, text: normalize(el.innerText || el.textContent || '') }))
    .filter((entry) => /statements\s+and\s+disclosures/i.test(entry.text))
    .slice(0, 40);

  let clicked = 0;
  for (const entry of nodes) {
    const node = entry.el;
    const direct =
      (isInteractive(node) ? node : null) ||
      node.closest("button,[role='button'],summary,a,[tabindex],[aria-controls],[aria-expanded]") ||
      node.querySelector("button,[role='button'],summary,a,[tabindex],[aria-controls],[aria-expanded]");
    if (direct && clickLikeUser(direct)) {
      clicked += 1;
      if (clicked >= 3) break;
    }
  }

  return { clicked };
})()
"""
                    )
                except WorkflowError:
                    pass

                _wait_ms(client, 1800)
                _ensure_statement_links_visible(client, selector_profile=selector_profile)
                raw_download_result = client.evaluate(rows_script)
                if not isinstance(raw_download_result, dict):
                    raise WorkflowError("Download step returned invalid MCP result payload")
                row_count = int(raw_download_result.get("row_count", 0) or 0)
                zero_rows_diag = client.evaluate(zero_rows_diag_script)
                if isinstance(zero_rows_diag, dict):
                    links_total = int(zero_rows_diag.get("links_total", 0) or 0)
                    statement_like_links = int(zero_rows_diag.get("statement_like_links", 0) or 0)
                    body_preview = str(zero_rows_diag.get("body_preview", "")).strip()
                    current_url = str(zero_rows_diag.get("page_url", "")).strip()
                    body_lower = body_preview.lower()
                    looks_like_docs_landing = (
                        "statements and documents" in body_lower
                        or "statements and disclosures" in body_lower
                    )

            # Wells Fargo occasionally renders a blank shell document before hydrating.
            # Retry navigation/reload once before failing hard on selector absence.
            if links_total == 0 and not body_preview:
                progress(
                    "statement_rows_retry",
                    {
                        "reason": "blank_document",
                        "method": "reload_current_or_statements_url",
                        "page_url": current_url[:220],
                    },
                )
                try:
                    retry_url = current_url or statements_url
                    if retry_url:
                        _navigate_with_recovery(client, retry_url)
                    else:
                        client.evaluate("window.location.reload(); true")
                except WorkflowError:
                    try:
                        client.evaluate("window.location.reload(); true")
                    except WorkflowError:
                        pass
                _wait_ms(client, 2200)
                _ensure_statement_links_visible(client, selector_profile=selector_profile)
                raw_download_result = client.evaluate(rows_script)
                if not isinstance(raw_download_result, dict):
                    raise WorkflowError("Download step returned invalid MCP result payload")
                row_count = int(raw_download_result.get("row_count", 0) or 0)
                zero_rows_diag = client.evaluate(zero_rows_diag_script)

            # If we are still on the documents landing shell (tiles visible) with no
            # statement rows, force-navigate to the statements list URL exposed by app globals.
            if (
                row_count == 0
                and chrome_recovery_enabled
                and statement_like_links == 0
                and looks_like_docs_landing
            ):
                forced_statement_list_url = client.evaluate(
                    r"""
(() => {
  const globals = window.mwfGlobals || {};
  const raw = String(globals.statementDisclosuresUrl || '').trim();
  if (!raw) return '';
  if (/^https?:\/\//i.test(raw)) return raw;
  const origin = window.location.origin || '';
  if (!origin) return '';
  const appPath = String(globals.applicationPath || '').trim().replace(/\/+$/, '');
  if (raw.startsWith('/')) {
    if (appPath) return `${origin}${appPath}${raw}`;
    return `${origin}${raw}`;
  }
  return appPath ? `${origin}${appPath}/${raw}` : `${origin}/${raw}`;
})()
"""
                )
                forced_statement_list_url = str(forced_statement_list_url or "").strip()
                if forced_statement_list_url:
                    progress(
                        "statement_rows_retry",
                        {
                            "reason": "force_statement_list_nav",
                            "method": "mwfGlobals.statementDisclosuresUrl",
                            "page_url": current_url[:220],
                            "target_url": forced_statement_list_url[:220],
                        },
                    )
                    try:
                        _navigate_with_recovery(client, forced_statement_list_url)
                        _wait_ms(client, 1800)
                        _ensure_statement_links_visible(client, selector_profile=selector_profile)
                        raw_download_result = client.evaluate(rows_script)
                        if not isinstance(raw_download_result, dict):
                            raise WorkflowError("Download step returned invalid MCP result payload")
                        row_count = int(raw_download_result.get("row_count", 0) or 0)
                        zero_rows_diag = client.evaluate(zero_rows_diag_script)
                    except WorkflowError:
                        pass

            # Some direct list URLs intermittently return a blank shell.
            # Recover by returning to the eDocs landing URL, then re-open statements.
            if row_count == 0 and chrome_recovery_enabled:
                zero_rows_diag = client.evaluate(zero_rows_diag_script)
                zero_body_preview = ""
                zero_page_url = ""
                if isinstance(zero_rows_diag, dict):
                    zero_body_preview = str(zero_rows_diag.get("body_preview", "")).strip()
                    zero_page_url = str(zero_rows_diag.get("page_url", "")).strip()
                if (not zero_body_preview) and "/documents/statement/list" in zero_page_url.lower():
                    landing_url = client.evaluate(
                        r"""
(() => {
  const globals = window.mwfGlobals || {};
  const raw = String(globals.edocsUrl || '').trim();
  if (!raw) return '';
  if (/^https?:\/\//i.test(raw)) return raw;
  const origin = window.location.origin || '';
  if (!origin) return '';
  const appPath = String(globals.applicationPath || '').trim().replace(/\/+$/, '');
  if (raw.startsWith('/')) {
    if (appPath) return `${origin}${appPath}${raw}`;
    return `${origin}${raw}`;
  }
  return appPath ? `${origin}${appPath}/${raw}` : `${origin}/${raw}`;
})()
"""
                    )
                    landing_url = str(landing_url or "").strip()
                    if landing_url:
                        progress(
                            "statement_rows_retry",
                            {
                                "reason": "recover_blank_statement_list",
                                "method": "mwfGlobals.edocsUrl",
                                "page_url": zero_page_url[:220],
                                "target_url": landing_url[:220],
                            },
                        )
                        try:
                            _navigate_with_recovery(client, landing_url)
                            _wait_ms(client, 1800)
                            _ensure_statement_links_visible(client, selector_profile=selector_profile)
                            raw_download_result = client.evaluate(rows_script)
                            if not isinstance(raw_download_result, dict):
                                raise WorkflowError("Download step returned invalid MCP result payload")
                            row_count = int(raw_download_result.get("row_count", 0) or 0)
                        except WorkflowError:
                            pass

        if row_count == 0:
            zero_rows_diag = client.evaluate(zero_rows_diag_script)
            final_statement_like_links = 0
            if isinstance(zero_rows_diag, dict):
                final_statement_like_links = int(
                    zero_rows_diag.get("statement_like_links", 0) or 0
                )
            if chrome_recovery_enabled and final_statement_like_links > 0:
                progress(
                    "statement_rows_retry",
                    {
                        "reason": "final_statement_links_fallback",
                        "method": "fallback_statement_links_script",
                        "statement_like_links": final_statement_like_links,
                    },
                )
                fallback_result = client.evaluate(fallback_statement_links_script)
                if isinstance(fallback_result, dict):
                    fallback_row_count = int(fallback_result.get("row_count", 0) or 0)
                    if fallback_row_count > 0:
                        raw_download_result = fallback_result
                        row_count = fallback_row_count
                        zero_rows_diag = client.evaluate(zero_rows_diag_script)

        if row_count == 0:
            zero_rows_diag = client.evaluate(zero_rows_diag_script)
            post_nav_check = client.evaluate(
                """
(() => {
  const body = (document.body?.innerText || '').toLowerCase();
  const url = (window.location.href || '').toLowerCase();
  const hasLoginForm = !!document.querySelector("form#frmSignon,input[name='j_username'],input#userid");
  const loggedOut = url.includes('/logout') || /you\\'ve securely signed off|thanks for visiting/.test(body);
  const stillOnLogin = hasLoginForm || loggedOut || url.includes('signon') || (url.includes('login') && !url.includes('logout'));
  return { still_on_login: stillOnLogin, logged_out: loggedOut, page_url: window.location.href || '', body_preview: body.slice(0, 400) };
})()
"""
            )
            if isinstance(post_nav_check, dict) and bool(post_nav_check.get("still_on_login")):
                raise AuthError(
                    "No statement rows found and session appears unauthenticated. "
                    "Complete manual login in the same browser window before handoff."
                )
            debug_paths = _capture_mcp_debug(client, debug_dir, "no_statement_rows")
            raise SelectorError(
                f"No statement rows found. Diagnostics: {zero_rows_diag}. Debug: {debug_paths}"
            )

        progress("statement_indexed", {"rows_found": row_count})

        raw_downloads = raw_download_result.get("downloaded", [])
        if not isinstance(raw_downloads, list):
            raw_downloads = []
        status_counts: dict[str, int] = {}
        for _item in raw_downloads:
            if not isinstance(_item, dict):
                continue
            _status = str(_item.get("status", "")).strip().lower() or "unknown"
            status_counts[_status] = status_counts.get(_status, 0) + 1
        progress(
            "statement_candidates",
            {
                "candidates": len(raw_downloads),
                "status_counts": status_counts,
            },
        )
        preview: list[dict[str, Any]] = []
        for _item in raw_downloads[:5]:
            if not isinstance(_item, dict):
                continue
            _row_text = re.sub(r"\s+", " ", str(_item.get("row_text", "")).strip())[:180]
            preview.append(
                {
                    "status": str(_item.get("status", "")).strip().lower(),
                    "control_selector": str(_item.get("control_selector", "")).strip(),
                    "href": str(_item.get("href", "")).strip()[:220],
                    "row_text": _row_text,
                }
            )
        if preview:
            progress("statement_candidates_preview", {"items": preview})

        list_page_url_result = client.evaluate("window.location.href || ''")
        list_page_url = str(list_page_url_result or "").strip()
        browser_download_dir_raw = os.getenv(
            "WF_BROWSER_DOWNLOAD_DIR",
            str(Path.home() / "Downloads"),
        )
        browser_download_dir = Path(browser_download_dir_raw).expanduser()
        known_browser_pdfs: set[Path] = set()
        if browser_download_dir.exists() and browser_download_dir.is_dir():
            for existing in browser_download_dir.iterdir():
                if existing.is_file() and existing.suffix.lower() == ".pdf":
                    known_browser_pdfs.add(existing)

        for item in raw_downloads:
            if not isinstance(item, dict):
                continue

            status = str(item.get("status", "")).strip().lower()
            if status not in {"ok", "pseudo_link", "missing_control"}:
                continue

            statement_hint = _extract_statement_click_hint(str(item.get("row_text", "")).strip())
            href = str(item.get("href", "")).strip()
            if href.lower() in {"none", "null", "undefined", "javascript:void(0)", "#"}:
                href = ""
            href_lower = href.lower()
            is_direct_pdf_href = bool(href) and (
                "/edocs/documents/retrieve/" in href_lower or ".pdf" in href_lower
            )
            was_direct_pdf_href = is_direct_pdf_href

            # Wells statement controls often require click behavior to surface the document URL.
            if statement_hint and (status in {"pseudo_link", "missing_control"} or not is_direct_pdf_href):
                clicked_href = _click_statement_link_and_capture_url(client, statement_hint) or ""
                if clicked_href:
                    href = clicked_href
                    href_lower = href.lower()
                    is_direct_pdf_href = (
                        "/edocs/documents/retrieve/" in href_lower or ".pdf" in href_lower
                    )

            # Prefer browser-native download artifacts if a click already triggered one.
            downloaded_path = _wait_for_browser_download_pdf(
                browser_download_dir,
                known_browser_pdfs,
                timeout_s=4.0 if not was_direct_pdf_href else 2.0,
            )
            if downloaded_path and downloaded_path.exists():
                candidate_bytes = downloaded_path.read_bytes()
                if b"%PDF" in candidate_bytes[:1024]:
                    saved = _save_pdf_bytes(
                        pdf_bytes=candidate_bytes,
                        out_pdf_dir=pdf_dir,
                        index=int(item.get("index", 0) or 0),
                        row_text=statement_hint,
                        suggested_filename=(
                            str(item.get("suggested_filename", "")).strip()
                            or downloaded_path.name
                        ),
                    )
                    downloads.append(saved)
                    if list_page_url:
                        _navigate_with_recovery(client, list_page_url)
                        _wait_ms(client, 800)
                        _ensure_statement_links_visible(client, selector_profile=selector_profile)
                    continue

            if not href:
                continue

            fetch_script = f"""
(async () => {{
  const url = {_js(href)};
  const response = await fetch(url, {{ credentials: 'include' }});
  if (!response.ok) {{
    throw new Error(`Failed to fetch statement PDF: HTTP ${{response.status}}`);
  }}
  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {{
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }}
  return {{
    base64: btoa(binary),
    size: bytes.length,
    content_type: response.headers.get('content-type') || '',
  }};
}})()
"""
            try:
                fetch_result = client.evaluate(fetch_script)
            except WorkflowError:
                # Some statement controls trigger top-level navigation/download and are not script-fetchable.
                if list_page_url and not was_direct_pdf_href:
                    _navigate_with_recovery(client, list_page_url)
                    _wait_ms(client, 800)
                    _ensure_statement_links_visible(client, selector_profile=selector_profile)
                continue
            if not isinstance(fetch_result, dict):
                continue

            encoded = str(fetch_result.get("base64", ""))
            if not encoded:
                continue

            pdf_bytes = base64.b64decode(encoded)
            content_type = str(fetch_result.get("content_type", "")).lower()
            looks_like_pdf = b"%PDF" in pdf_bytes[:1024] or "application/pdf" in content_type
            if not looks_like_pdf:
                # Fallback: some Wells Fargo statement links trigger browser-native download
                # and return HTML for script fetch requests.
                statement_hint = str(item.get("row_text", "")).strip()
                downloaded_path = _wait_for_browser_download_pdf(
                    browser_download_dir,
                    known_browser_pdfs,
                    timeout_s=3.0,
                )
                if not downloaded_path and statement_hint:
                    _click_statement_link_and_capture_url(client, statement_hint)
                    downloaded_path = _wait_for_browser_download_pdf(
                        browser_download_dir,
                        known_browser_pdfs,
                        timeout_s=12.0,
                    )
                if downloaded_path and downloaded_path.exists():
                    candidate_bytes = downloaded_path.read_bytes()
                    if b"%PDF" in candidate_bytes[:1024]:
                        pdf_bytes = candidate_bytes
                        looks_like_pdf = True
                        if not item.get("suggested_filename"):
                            item["suggested_filename"] = downloaded_path.name

            if not looks_like_pdf:
                # Skip non-PDF artifacts (typically logout/error HTML pages).
                continue

            saved = _save_pdf_bytes(
                pdf_bytes=pdf_bytes,
                out_pdf_dir=pdf_dir,
                index=int(item.get("index", 0) or 0),
                row_text=str(item.get("row_text", "")),
                suggested_filename=(
                    str(item.get("suggested_filename", "")).strip() or None
                ),
            )
            downloads.append(saved)

            if list_page_url and not was_direct_pdf_href:
                _navigate_with_recovery(client, list_page_url)
                _wait_ms(client, 800)
                _ensure_statement_links_visible(client, selector_profile=selector_profile)

        if not downloads:
            debug_paths = _capture_mcp_debug(client, debug_dir, "no_valid_pdfs")
            raise SelectorError(f"No valid statement PDFs were downloaded. Debug: {debug_paths}")

        progress("pdf_downloaded", {"count": len(downloads)})
        return {
            "selector_profile_version": selector_profile.get("profile_version", "unknown"),
            "downloaded_statements": [asdict(item) for item in downloads],
            "downloaded_at": utc_now_iso(),
        }

    except (SelectorError, AuthError):
        raise
    except WorkflowError as exc:
        debug_paths = _capture_mcp_debug(client, debug_dir, "workflow_error")
        raise _classify_error(exc, debug_paths)
    finally:
        client.close_browser()
        client.shutdown()
