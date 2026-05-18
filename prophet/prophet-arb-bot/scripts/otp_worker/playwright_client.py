"""Playwright-only logic for the Privy OTP browser dance.

Selectors come from the live Privy modal on app.prophetmarket.ai. They
are isolated here so token_acquirer.py stays unit-testable: the acquirer
takes a `browser_session` interface and tests inject a stub.

Live capture validation: re-confirm selectors against
https://app.prophetmarket.ai before each Phase-14 acceptance run.
Privy's modal has rotated id/name attributes in the past; if any of
these break, fix them in this file only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from . import OtpEmailTimeout, PrivyAuthFailed

PROPHET_APP_URL = "https://app.prophetmarket.ai"

# Selectors — keep these as named constants for easy diffing on UI changes.
# Re-validated against https://app.prophetmarket.ai 2026-05-08 (Phase 14).
# The login button text rotated from "Connect" to "SIGN IN" since the
# plan was written; fix selector rotations here only.
SEL_CONNECT_BUTTON = 'button:has-text("SIGN IN")'
SEL_EMAIL_INPUT = "#email-input"
SEL_EMAIL_SUBMIT = 'button:has-text("Submit")'
SEL_OTP_INPUT_TEMPLATE = 'input[name="code-{i}"]'  # 0..5

# Onboarding form selectors. After a successful Privy OTP the Prophet
# webapp redirects first-time users to `/onboarding` and gates User-row
# creation behind these two fields. The skill auto-fills both per
# operator direction (Prophet team approved on 2026-05-08).
SEL_ONBOARDING_USERNAME = "#username"
SEL_ONBOARDING_GEO_ATTESTATION = "#geo-attestation"
SEL_ONBOARDING_CONTINUE = 'button:has-text("Continue")'
ONBOARDING_URL_FRAGMENT = "/onboarding"

PRIVY_TOKEN_LOCAL_STORAGE_KEY = "privy:token"
# Issue #583: Privy migrated the refresh token from an HttpOnly cookie to
# `window.localStorage["privy:refresh_token"]`. Keep the legacy cookie name
# as a fallback so operators on older Privy installs don't regress.
PRIVY_REFRESH_LOCAL_STORAGE_KEY = "privy:refresh_token"
# Issue #676: roll back the privy:pat / privy:id_token contract that PR #675
# (#674) introduced. A manual MCP walk-through against app.prophetmarket.ai
# (2026-05-18) enumerated the live SDK's localStorage and found neither key
# present — they are not part of Privy's session contract on Prophet.
# The keys the SDK actually writes (and that warm-context restore needs to
# replay) are `privy:connections` (embedded wallet metadata used by the
# `/create` signing flow), `privy:caid` (anonymous client UUID), and the
# namespaced `privy:<app_id>:recent-login-method` entry.
PROPHET_PRIVY_APP_ID = "cmm1b0n2n00tq0clhm0ooeq1b"
PRIVY_CONNECTIONS_LOCAL_STORAGE_KEY = "privy:connections"
PRIVY_CAID_LOCAL_STORAGE_KEY = "privy:caid"
PRIVY_RECENT_LOGIN_METHOD_LOCAL_STORAGE_KEY = (
    f"privy:{PROPHET_PRIVY_APP_ID}:recent-login-method"
)
PRIVY_REFRESH_COOKIE = "privy-refresh-token"
PRIVY_TOKEN_COOKIE = "privy-token"
PRIVY_SESSION_COOKIE = "privy-session"


@dataclass
class PrivyAuthArtifacts:
    jwt: str
    refresh_token: str
    privy_token_cookie: str
    privy_session_cookie: str
    # Issue #676: the embedded wallet that signs `createMarketWithBet` on
    # `/create` lives inside `privy:connections`. Without it on restore,
    # the SDK has no signer. Persist alongside the JWT.
    privy_connections: str = ""
    privy_caid: str = ""
    privy_recent_login_method: str = ""


class BrowserSession(Protocol):
    """Minimal interface around Playwright we care about.

    The real implementation wraps Playwright's chromium/page; tests stub
    it without touching the network. Plan §11.5 explicitly skips
    test_playwright_client — this Protocol is the seam.
    """

    def navigate(self, url: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None: ...
    def get_local_storage(self, key: str) -> str | None: ...
    def get_cookie(self, name: str) -> str | None: ...
    def get_url(self) -> str: ...
    def is_checked(self, selector: str) -> bool: ...
    def add_init_script(self, script: str) -> None: ...


def open_privy_modal(session: BrowserSession) -> None:
    """Navigate to Prophet, open the Privy auth modal."""
    session.navigate(PROPHET_APP_URL)
    session.wait_for(SEL_CONNECT_BUTTON, timeout_ms=30_000)
    session.click(SEL_CONNECT_BUTTON)
    session.wait_for(SEL_EMAIL_INPUT, timeout_ms=15_000)


def submit_email(session: BrowserSession, email: str) -> None:
    """Type the email and click Submit; Privy then sends the OTP."""
    session.fill(SEL_EMAIL_INPUT, email)
    session.click(SEL_EMAIL_SUBMIT)


def submit_otp_code(session: BrowserSession, code: str) -> None:
    """Fill the 6-digit OTP boxes; Privy submits on the last digit."""
    if len(code) != 6 or not code.isdigit():
        raise PrivyAuthFailed(f"refusing to submit non-6-digit code: {code!r}")
    for i, digit in enumerate(code):
        session.fill(SEL_OTP_INPUT_TEMPLATE.format(i=i), digit)


def _unwrap_jwt(raw: str | None) -> str | None:
    """Strip JSON-quote wrapping the Privy SDK adds when persisting tokens.

    Phase-14 live probe (2026-05-08): the Privy web SDK serializes
    `privy:token` via `JSON.stringify`, so `localStorage.getItem` returns
    `'"eyJ..."'` (literal surrounding double-quotes). Sending that as
    `Bearer "eyJ..."` makes Prophet's GraphQL upstream return 401
    Unauthorized. Real JWTs always start with `eyJ` (base64 `{"alg":...`),
    so stripping balanced surrounding quotes is safe.
    """
    if not raw:
        return raw
    s = raw.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s or None


def wait_for_jwt(
    session: BrowserSession,
    *,
    poll_seconds: float = 1.0,
    timeout_seconds: float = 60.0,
) -> str:
    """Poll localStorage for privy:token until it appears or times out."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        token = _unwrap_jwt(session.get_local_storage(PRIVY_TOKEN_LOCAL_STORAGE_KEY))
        if token:
            return token
        time.sleep(poll_seconds)
    raise OtpEmailTimeout(
        f"privy:token did not appear in localStorage within {timeout_seconds:.0f}s"
    )


def wait_for_privy_connections(
    session: BrowserSession,
    *,
    poll_seconds: float = 1.0,
    timeout_seconds: float = 30.0,
) -> str:
    """Poll localStorage for ``privy:connections`` until it appears non-empty.

    Issue #678: ``privy:token`` lands the moment OTP verifies, but the
    Privy SDK provisions the embedded wallet *after* the JWT — so
    ``privy:connections`` (which carries the wallet metadata that
    Prophet's ``/create`` signing flow uses) lands seconds later. If
    ``capture_artifacts`` reads it immediately after ``wait_for_jwt``
    returns, capture races and writes an empty value to the cache.

    This helper closes the race: callers run it between
    ``wait_for_jwt`` and ``capture_artifacts``. The empty-string
    fail-closed branch matches the policy spelled out in #678 — an
    empty ``privy:connections`` in the cache is the failure mode this
    function exists to prevent, so we raise ``OtpEmailTimeout`` rather
    than silently let capture proceed.

    Unlike the JWT, ``privy:connections`` is a JSON-stringified ARRAY
    (no outer string wrapping). Bare ``getItem`` is the right read —
    no ``_unwrap_jwt`` indirection.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        raw = session.get_local_storage(PRIVY_CONNECTIONS_LOCAL_STORAGE_KEY)
        if raw and raw.strip() and raw.strip() not in ("[]", '""'):
            return raw
        time.sleep(poll_seconds)
    raise OtpEmailTimeout(
        f"privy:connections did not appear in localStorage within "
        f"{timeout_seconds:.0f}s — embedded wallet failed to provision; "
        f"refusing to capture an empty wallet value (#678)"
    )


def at_onboarding_screen(session: BrowserSession) -> bool:
    """True iff the browser is sitting on Prophet's first-time onboarding form.

    First-time Privy logins land at `/onboarding?returnTo=/`; returning
    users skip straight back to `/`. Used by the token-acquirer flow to
    decide whether to drive the username + geo-attestation form.
    """
    try:
        return ONBOARDING_URL_FRAGMENT in session.get_url()
    except Exception:
        return False


def fill_onboarding_form(session: BrowserSession, *, username: str) -> None:
    """Auto-fill Prophet's first-time onboarding form and submit it.

    Per operator direction (Prophet team approved 2026-05-08):
    - username is generated from `prophet_email` upstream and passed in
    - the geo-attestation checkbox is auto-ticked

    On Prophet-side username uniqueness collision the caller is
    responsible for retrying with a hashed-suffix username; this
    function just drives the form once.
    """
    session.wait_for(SEL_ONBOARDING_USERNAME, timeout_ms=15_000)
    session.fill(SEL_ONBOARDING_USERNAME, username)
    if not session.is_checked(SEL_ONBOARDING_GEO_ATTESTATION):
        session.click(SEL_ONBOARDING_GEO_ATTESTATION)
    session.click(SEL_ONBOARDING_CONTINUE)


_PRIVY_DEPRECATION_SENTINEL = "deprecated"


def _is_privy_deprecation_marker(value: str | None) -> bool:
    """True iff ``value`` is Privy's refresh-token retirement sentinel.

    Issue #666: Privy now writes the literal ``"deprecated"`` into
    ``privy:refresh_token`` post-login to signal the cookie/localStorage
    refresh-token mechanism is retired. The bare form arrives after
    ``_unwrap_jwt`` strips the JSON quotes; the wrapped form may also
    turn up directly when the unwrap helper is bypassed.
    """
    if not value:
        return False
    return (
        value == _PRIVY_DEPRECATION_SENTINEL
        or value == f'"{_PRIVY_DEPRECATION_SENTINEL}"'
    )


def capture_artifacts(session: BrowserSession, *, jwt: str) -> PrivyAuthArtifacts:
    """Snapshot the JWT plus the refresh material needed for steady-state refresh.

    Issue #583: Privy moved refresh from a cookie to localStorage
    (``privy:refresh_token``). Read the localStorage value first —
    JSON-unwrapped via ``_unwrap_jwt`` — and fall back to the legacy
    cookie so operators on older Privy installs are not regressed.

    Issue #666: Privy then retired refresh tokens entirely. The
    server-side state is now a single long-lived JWT, and Privy writes
    the literal sentinel ``"deprecated"`` into the localStorage key as
    a migration marker. Capturing that marker verbatim caused the SDK
    to reject the planted session on restore. Normalize the marker to
    an empty string so downstream consumers see a clean "JWT-only
    session" signal. The legacy cookie fallback is intentionally NOT
    consulted when localStorage carries the marker — the cookie value
    is from a prior Privy era and re-introducing it would just
    resurrect the poison pill under a different name.
    """
    unwrapped_local = _unwrap_jwt(
        session.get_local_storage(PRIVY_REFRESH_LOCAL_STORAGE_KEY)
    )
    if _is_privy_deprecation_marker(unwrapped_local):
        refresh_token = ""
    else:
        refresh_token = (
            unwrapped_local
            or session.get_cookie(PRIVY_REFRESH_COOKIE)
            or ""
        )
        if _is_privy_deprecation_marker(refresh_token):
            refresh_token = ""
    # Issue #676: read the three SDK-written keys (privy:connections,
    # privy:caid, privy:<app_id>:recent-login-method). privy:connections
    # is a JSON-stringified ARRAY (no outer surrounding quotes), so do
    # NOT route it through `_unwrap_jwt`, which would corrupt it by
    # stripping the leading `[` and trailing `]` if they happened to be
    # quote characters. The other two are JSON-quoted strings and unwrap
    # the same way the JWT does.
    privy_connections = (
        session.get_local_storage(PRIVY_CONNECTIONS_LOCAL_STORAGE_KEY) or ""
    )
    privy_caid = _unwrap_jwt(
        session.get_local_storage(PRIVY_CAID_LOCAL_STORAGE_KEY)
    ) or ""
    privy_recent_login_method = _unwrap_jwt(
        session.get_local_storage(PRIVY_RECENT_LOGIN_METHOD_LOCAL_STORAGE_KEY)
    ) or ""
    return PrivyAuthArtifacts(
        jwt=jwt,
        refresh_token=refresh_token,
        privy_token_cookie=session.get_cookie(PRIVY_TOKEN_COOKIE) or "",
        privy_session_cookie=session.get_cookie(PRIVY_SESSION_COOKIE) or "",
        privy_connections=privy_connections,
        privy_caid=privy_caid,
        privy_recent_login_method=privy_recent_login_method,
    )


class RealBrowserSession:
    """Concrete BrowserSession that drives Seren Desktop Playwright MCP.

    Playwright is a connected MCP service in Seren Desktop, not a Seren
    publisher. This class therefore dispatches only to MCP-style gateway
    callables (for example `mcp_playwright_navigate`) and deliberately
    refuses to fall back to `gateway.call("playwright", ...)`. The fallback
    caused issue #576: cold-start auth attempted to query a non-existent
    Playwright publisher and blocked with `Publisher 'playwright' not found`.

    Use as a context manager so the MCP browser session is reliably closed
    on exit when a close callable is available:

        with RealBrowserSession(gateway=gateway, headless=True) as session:
            open_privy_modal(session)
            ...
    """

    def __init__(self, *, gateway: Any, headless: bool = True) -> None:
        self._gateway = gateway
        self._headless = headless

    # -- BrowserSession Protocol surface ------------------------------------

    def navigate(self, url: str) -> None:
        self._call("/navigate", {"url": url})

    def click(self, selector: str) -> None:
        self._call("/click", {"selector": selector})

    def fill(self, selector: str, value: str) -> None:
        self._call("/fill", {"selector": selector, "value": value})

    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        wait_fn = self._resolve_mcp_callable("wait_for_selector")
        if wait_fn is not None:
            wait_fn(selector=selector, timeout_ms=timeout_ms)
            return

        # Seren Desktop's public Playwright MCP tool surface does not expose
        # a dedicated wait_for_selector tool. Poll via evaluate when possible.
        deadline = time.monotonic() + (timeout_ms / 1000)
        script = (
            "(() => !!document.querySelector("
            + _js_string_literal(selector)
            + "))()"
        )
        while time.monotonic() < deadline:
            if bool(_coerce_evaluate_result(self._call("/evaluate", {"script": script}))):
                return
            time.sleep(0.25)
        raise TimeoutError(f"selector did not appear within {timeout_ms}ms: {selector}")

    def get_local_storage(self, key: str) -> str | None:
        # `wait_for_jwt` runs `_unwrap_jwt` on the return value, so the
        # raw JSON-quoted form is preserved here intentionally.
        # `playwright_evaluate` only takes a `script` field, so the key
        # is templated inline (JSON-quoted to escape safely).
        script = (
            "(() => { const v = window.localStorage.getItem("
            + _js_string_literal(key)
            + "); return v === null ? null : v; })()"
        )
        result = self._call("/evaluate", {"script": script})
        unwrapped = _coerce_evaluate_result(result)
        return unwrapped if isinstance(unwrapped, str) else None

    def dump_local_storage_keys(self) -> dict[str, str]:
        """Diagnostic enumeration; gated on PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE.

        Truncates each value to 80 chars so JWTs don't end up in logs.
        """
        script = (
            "(() => {"
            "  const out = {};"
            "  for (let i = 0; i < window.localStorage.length; i++) {"
            "    const k = window.localStorage.key(i);"
            "    const v = window.localStorage.getItem(k);"
            "    out[k] = (v || '').slice(0, 80);"
            "  }"
            "  return out;"
            "})()"
        )
        try:
            result = self._call("/evaluate", {"script": script})
        except Exception:
            return {}
        unwrapped = _coerce_evaluate_result(result)
        return unwrapped if isinstance(unwrapped, dict) else {}

    def get_cookie(self, name: str) -> str | None:
        # `document.cookie` cannot read HttpOnly cookies (privy-refresh-
        # token is HttpOnly), so the cookie read goes through a
        # publisher-side endpoint with access to BrowserContext.cookies().
        # The `PrivyAuthFailed("Privy did not set privy-refresh-token
        # cookie")` check in token_acquirer fails closed if missing.
        try:
            result = self._call("/get_cookie", {"name": name})
        except Exception:
            return None
        if isinstance(result, dict):
            value = result.get("value")
            if isinstance(value, str):
                return value
        if isinstance(result, str):
            return result
        return None

    def get_url(self) -> str:
        try:
            result = self._call("/evaluate", {"script": "window.location.href"})
        except Exception:
            return ""
        unwrapped = _coerce_evaluate_result(result)
        return unwrapped if isinstance(unwrapped, str) else ""

    def is_checked(self, selector: str) -> bool:
        try:
            result = self._call(
                "/evaluate",
                {
                    "script": (
                        "(() => { const el = document.querySelector("
                        + _js_string_literal(selector)
                        + "); return !!(el && el.checked); })()"
                    )
                },
            )
        except Exception:
            return False
        return bool(_coerce_evaluate_result(result))

    def add_init_script(self, script: str) -> None:
        """Register a script that runs at `document_start` on every navigation.

        Issue #638: planting Privy state via `evaluate` after navigate is racy —
        the SDK has already decided "no session" by the time the write lands.
        `add_init_script` runs before any page JS boots, so the Privy SDK
        observes the planted state on its first read.
        """
        self._call("/add_init_script", {"script": script})

    # -- Lifecycle ----------------------------------------------------------

    def close(self) -> None:
        try:
            self._call("/close", {})
        except Exception:
            pass

    def __enter__(self) -> "RealBrowserSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- Internal -----------------------------------------------------------

    def _call(self, path: str, body: dict[str, Any]) -> Any:
        tool = path.strip("/").replace("-", "_")
        fn = self._resolve_mcp_callable(tool)
        if fn is None:
            raise RuntimeError(
                "Playwright MCP connected service is required for Prophet UI "
                "automation; do not query a Playwright publisher. Expected a "
                f"gateway method for Playwright MCP tool {tool!r}."
            )
        return fn(**_mcp_tool_args(tool, body))

    def _resolve_mcp_callable(self, tool: str) -> Any | None:
        """Return an MCP callable for a Playwright tool, if the gateway has one.

        Tests and Desktop adapters may expose either compact helper names
        (`mcp_playwright_navigate`) or the fully-qualified Seren tool name
        (`mcp__playwright__playwright_navigate`). Support both, but never
        fall back to publisher routing.
        """
        candidates = (
            f"mcp__playwright__playwright_{tool}",
            f"mcp_playwright_{tool}",
            f"playwright_{tool}",
        )
        for name in candidates:
            fn = getattr(self._gateway, name, None)
            if callable(fn):
                return fn
        return None


def _js_string_literal(value: str) -> str:
    import json as _json

    return _json.dumps(value)


def _coerce_evaluate_result(result: Any) -> Any:
    """Unwrap the `playwright_evaluate` response envelope.

    Different Playwright MCP implementations return the evaluated value
    under different field names (`result`, `value`, `output`); fall back
    to the raw payload so we don't silently swallow something usable.
    """
    if isinstance(result, dict):
        for key in ("result", "value", "output"):
            if key in result:
                return result[key]
        return result
    return result


def _mcp_tool_args(tool: str, body: dict[str, Any]) -> dict[str, Any]:
    """Translate the historical BrowserSession body to MCP tool args.

    The upgraded Seren Desktop Playwright MCP exposes
    `playwright_wait_for_selector` with `{selector, state?, timeout?}` —
    `timeout` is in milliseconds despite the unitless name. Pre-upgrade
    this branch never fired because the gateway fell through to the
    `evaluate` poll path in `RealBrowserSession.wait_for`. Now that the
    dedicated tool exists, we have to send the MCP-native key.
    """
    if tool == "wait_for_selector":
        return {
            "selector": body.get("selector", ""),
            "timeout": body.get("timeout") or body.get("timeout_ms", 30_000),
        }
    return dict(body)
