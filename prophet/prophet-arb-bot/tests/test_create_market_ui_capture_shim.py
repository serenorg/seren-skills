"""Issue #655: the OCS capture shim must observe `startOddsCalculation`
regardless of fetch-vs-XHR transport, URL casing, or response schema
drift — and must be installed at `document_start` so it predates
Prophet's bootstrap network calls.

These tests pin the structural invariants the shim must hold. The JS
itself runs in a real browser at runtime; tests assert (a) the install
ordering at the Python level and (b) the JS source contains the
constructs the fix promises (XHR wrapper, recursive walker, widened URL
filter, init-script install path).
"""

from __future__ import annotations

from typing import Any

from otp_worker.create_market_ui import (
    PROPHET_CREATE_URL,
    _CAPTURE_SCRIPT,
    _FETCH_CAPTURE_SCRIPT,
    install_capture_init_script,
    install_fetch_capture,
    open_create_form,
    read_capture_observations,
    read_captured_ocs_id,
)


class _RecordingSession:
    """Stub that mirrors `playwright_client.BrowserSession` enough for
    the capture-install-ordering tests. Records every call in a single
    ordered list so we can assert on sequence, not just presence.
    """

    def __init__(self, *, evaluate_result: Any = "") -> None:
        self.events: list[tuple[str, Any]] = []
        self._evaluate_result = evaluate_result

    def add_init_script(self, script: str) -> None:
        self.events.append(("add_init_script", script))

    def navigate(self, url: str) -> None:
        self.events.append(("navigate", url))

    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        self.events.append(("wait_for", selector))

    def fill(self, selector: str, value: str) -> None:
        self.events.append(("fill", (selector, value)))

    def click(self, selector: str) -> None:
        self.events.append(("click", selector))

    def evaluate(self, script: str) -> Any:
        self.events.append(("evaluate", script))
        return self._evaluate_result


class _EvaluateOnlySession:
    """Stub that lacks `add_init_script` — covers test stubs that pre-
    date #655 and the fallback `install_fetch_capture` path.
    """

    def __init__(self, *, evaluate_result: Any = "") -> None:
        self.evaluations: list[str] = []
        self._evaluate_result = evaluate_result

    def evaluate(self, script: str) -> Any:
        self.evaluations.append(script)
        return self._evaluate_result

    # Deliberately no `add_init_script`, `navigate`, etc.


def test_open_create_form_installs_init_script_before_navigate() -> None:
    """Root cause #4 fix: the init-script install MUST land before the
    navigate to `/create`, so the wrapper is in place for every fetch /
    XHR Prophet's bootstrap fires after page load.
    """
    session = _RecordingSession()

    open_create_form(session, question="Will X happen?")

    kinds = [k for (k, _) in session.events]

    # First event MUST be add_init_script (capture wrapper at document_start).
    assert kinds[0] == "add_init_script", kinds
    # Navigate to /create comes AFTER the init script registration.
    nav_idx = kinds.index("navigate")
    add_idx = kinds.index("add_init_script")
    assert add_idx < nav_idx, kinds
    # And the navigate target is the /create URL.
    nav_url = session.events[nav_idx][1]
    assert nav_url == PROPHET_CREATE_URL, nav_url

    # Belt-and-suspenders evaluate install lands AFTER wait_for, BEFORE fill.
    eval_idx = kinds.index("evaluate")
    wait_idx = kinds.index("wait_for")
    fill_idx = kinds.index("fill")
    assert wait_idx < eval_idx < fill_idx, kinds


def test_open_create_form_falls_back_when_session_lacks_add_init_script() -> None:
    """Test stubs (and any pathological session impl) that don't expose
    `add_init_script` must NOT crash. The inline evaluate install is the
    sole capture path in that case.
    """
    session = _EvaluateOnlySession()

    # No add_init_script attribute — must not raise.
    installed = install_capture_init_script(session)
    assert installed is False

    # The belt-and-suspenders path still works.
    install_fetch_capture(session)
    assert len(session.evaluations) == 1
    assert session.evaluations[0] == _CAPTURE_SCRIPT


def test_capture_script_wraps_both_fetch_and_xhr() -> None:
    """Root cause #1 fix: Apollo/Relay clients can switch transports.
    The shim MUST wrap `XMLHttpRequest` in addition to `fetch`, or every
    XHR-issued OCS response slips through silently.
    """
    script = _CAPTURE_SCRIPT
    # fetch wrapper.
    assert "window.fetch = " in script, "fetch wrapper missing"
    # XHR wrapper — pin both `open` and `send` interception.
    assert "XMLHttpRequest" in script, "XHR wrapper missing"
    assert "XHR.prototype.open" in script, "XHR.open interception missing"
    assert "XHR.prototype.send" in script, "XHR.send interception missing"


def test_capture_script_extracts_url_from_url_objects() -> None:
    """Issue #699: Prophet's Next.js client calls `fetch(new URL(...))`.
    URL objects have `.href`, not `.url`, so the pre-#699 wrapper
    extracted `url=''` for every call, `URL_RE.test('')` returned false,
    and the recording branch was never reached. 100% empty observation
    buffer on every `ocs_session_id_not_captured` block.

    The fetch URL extraction MUST cover:
      - bare string (already covered)
      - Request object: `.url`
      - URL object: `.href`
      - Anything else stringifiable: `String(a0)` fallback

    Empirical evidence (connected Playwright MCP against
    https://app.prophetmarket.ai/markets): every fetch arg is a URL
    object with `urlProp: null, hrefProp: "https://..."`. Without `.href`,
    capture silently fails for the entire page.
    """
    script = _CAPTURE_SCRIPT
    # The Request-object branch must still be present (back-compat).
    assert "a0.url" in script, "Request-object URL extraction missing"
    # NEW: URL-object branch.
    assert "a0.href" in script, (
        "URL-object extraction missing — fetch(new URL(...)) yields "
        "args[0] with `.href`, not `.url`. Prophet's Next.js client "
        "lands here on every request; without this, observed buffer "
        "stays empty and ocs_session_id_not_captured fires 100% of the time."
    )
    # NEW: stringify fallback for anything else stringifiable.
    assert "String(a0)" in script, (
        "String(a0) fallback missing — covers exotic args[0] shapes "
        "(URL polyfills, custom Request wrappers) so future drift won't "
        "silently zero the buffer again."
    )


def test_capture_script_records_total_fetch_calls_and_unmatched_sample() -> None:
    """Issue #701: a 100% empty `observed` buffer used to look identical
    whether the wrapper failed to install or the page issued only URLs
    that didn't match URL_RE. The wrapper MUST also expose:

      - `total_fetch_calls` counter, incremented on every `fetch(...)`
        invocation, so the operator can tell 0 fetches (wrapper installed
        but page silent) from N fetches (wrapper installed and active).
      - `unmatched_sample` ring buffer of URLs that didn't match URL_RE,
        so the operator can see what endpoints `/create` actually hits
        and decide whether the filter needs widening.

    Both are structural pins on the JS source; runtime semantics are
    exercised in the production browser.
    """
    script = _CAPTURE_SCRIPT
    # Counter is declared on the capture object and incremented in fetch.
    assert "total_fetch_calls: 0" in script, "counter init missing"
    assert "total_fetch_calls += 1" in script, "counter increment missing"
    # Unmatched-URL ring buffer is declared and capped.
    assert "unmatched_sample: []" in script, "unmatched_sample init missing"
    assert "function recordUnmatched(" in script, "recordUnmatched helper missing"
    # Issue #703: cap bumped from 10 to 50 — Prophet's /create page
    # issues ~79 fetches per cycle (mostly RSC prefetches); a 10-entry
    # buffer never preserves the early-window fetches where the OCS
    # request would have fired.
    assert "um.length >= 50" in script, (
        "unmatched_sample cap should be 50 (#703 bumped from 10) so the "
        "OCS-request fetch window survives the prefetch noise"
    )
    # The fetch wrapper invokes recordUnmatched in the else branch.
    assert "recordUnmatched(url)" in script, (
        "recordUnmatched not invoked on URL_RE-miss path"
    )


def test_capture_script_uses_widened_url_filter() -> None:
    """Root cause #2 fix: the URL filter MUST match `graphql` OR `odds`
    (case-insensitive). The old `url.includes('/graphql')` was brittle —
    any rename to `/api/v2/odds` would have silently broken capture.
    """
    script = _CAPTURE_SCRIPT
    assert "/(graphql|odds)/i" in script, script[:500]


def test_capture_script_uses_recursive_session_id_walker() -> None:
    """Root cause #3 fix: schema drift (e.g. `oddsCalculation.id` instead
    of `startOddsCalculation.sessionId`) MUST NOT silently break capture.
    The walker recurses, looking for any `sessionId`-shaped key nested
    under an `odds`-shaped ancestor.
    """
    script = _CAPTURE_SCRIPT
    assert "walkForSessionId" in script, "recursive walker missing"
    # The walker must accept multiple key spellings.
    assert "sessionId" in script
    assert "session_id" in script
    assert "oddsSessionId" in script
    # And it must qualify by an odds-shaped ancestor.
    assert "ODDS_KEY_RE" in script


def test_capture_script_is_idempotent() -> None:
    """Installing twice (init-script + inline evaluate) MUST be a no-op
    on the second call so we don't double-wrap fetch/XHR.
    """
    script = _CAPTURE_SCRIPT
    assert "__seren_capture_installed__" in script
    # The guard returns early if already installed.
    assert "if (window.__seren_capture_installed__) { return true; }" in script


def test_capture_script_records_diagnostic_ring_buffer() -> None:
    """The capture wrapper MUST stash every observed odds/graphql URL +
    response shape into `window.__seren_capture__.observed` so the
    operator can diagnose a silent miss without re-driving `/create`.
    """
    script = _CAPTURE_SCRIPT
    assert "observed" in script
    assert "function record(" in script
    # Ring buffer is capped — old observations roll off.
    assert "obs.length >= 20" in script


def test_read_capture_observations_returns_list_from_evaluate() -> None:
    """The Python helper must return a list of dicts (the JS ring buffer
    serialized through `evaluate`). Non-list returns degrade to []."""
    session = _EvaluateOnlySession(
        evaluate_result=[
            {"url": "https://app.prophetmarket.ai/api/graphql", "ok": True, "shape": "startOddsCalculation"},
            {"url": "https://app.prophetmarket.ai/api/odds/v2", "ok": False, "shape": "data"},
        ]
    )

    observations = read_capture_observations(session)
    assert len(observations) == 2
    assert observations[0]["url"].endswith("/api/graphql")
    assert observations[0]["ok"] is True

    # Non-list result → empty.
    session._evaluate_result = "not a list"
    assert read_capture_observations(session) == []


def test_read_captured_ocs_id_returns_evaluated_string() -> None:
    """The Python helper extracts whatever the wrapper stashed at
    `window.__seren_capture__.startOddsCalculation`. Non-string returns
    degrade to ''.
    """
    session = _EvaluateOnlySession(evaluate_result="ocs_test_123")
    assert read_captured_ocs_id(session) == "ocs_test_123"

    session._evaluate_result = None
    assert read_captured_ocs_id(session) == ""


def test_back_compat_alias_still_exported() -> None:
    """Pre-#655 callers and tests imported `_FETCH_CAPTURE_SCRIPT`; that
    name must remain so existing test files / external skill consumers
    don't break on upgrade.
    """
    assert _FETCH_CAPTURE_SCRIPT is _CAPTURE_SCRIPT


def test_question_input_selector_matches_user_facing_textarea() -> None:
    """Issue #720: Prophet's ``/create`` textarea has empty ``name`` and
    ``id`` attributes — only a placeholder describing a future-tense
    question (``e.g. Will Tesla stock close above $300...``). The
    pre-#720 selector ``textarea[name="question"], #question-input``
    matched some hidden form input via Playwright's resolver but never
    the user-facing textarea, so ``fill`` wrote to the wrong target,
    Validate Question stayed disabled (``0/300 chars``), and
    StartOddsCalculation never fired — the cycle blocked with
    ocs_session_id_not_captured even though the page reached /create
    with auth.

    Pin the contract: ``SEL_QUESTION_INPUT`` MUST include a bare
    ``textarea`` selector so the no-attribute textarea is still hit.
    The bare-tag fallback is safe on ``/create`` because Prophet renders
    exactly one textarea there (confirmed live, post-auth).
    """
    from otp_worker.create_market_ui import SEL_QUESTION_INPUT

    parts = [p.strip() for p in SEL_QUESTION_INPUT.split(",")]
    assert "textarea" in parts, (
        f"SEL_QUESTION_INPUT must contain a bare ``textarea`` selector "
        f"so Prophet's empty-name/empty-id textarea on /create is still "
        f"matched. Live evidence: "
        f"textarea_state = [{{name:'', id:'', placeholder:'e.g. Will Tesla...'}}]. "
        f"Got: {SEL_QUESTION_INPUT!r}"
    )


def test_capture_script_extracts_session_id_from_polling_url() -> None:
    """Issue #716 bug 1: the prior body-parse path raced Apollo's stream
    consumer — every `clone().text()` returned an empty body and the
    walker found no sessionId. Live diag confirmed it: `matched_text_failed=51`
    across cycles, zero observations recorded.

    The fix is to bypass the body parse entirely for the polling URL.
    Prophet's Apollo client fires `OddsCalculationSession($id: ID!)` as
    a GET with `variables={"id":"<sessionId>"}` in the query string at
    1Hz right after `StartOddsCalculation`. The sessionId is in the URL
    itself — no clone, no text(), no JSON.parse of the response body.

    Pin the new URL-side extraction so a future refactor can't quietly
    revert to the racy body-parse path.
    """
    script = _CAPTURE_SCRIPT
    # The polling-URL matcher must be present.
    assert "OddsCalculationSession" in script, (
        "polling-URL matcher missing — without it the wrapper has no "
        "race-free path to the sessionId and ocs_session_id_not_captured "
        "fires even when the mutation succeeded"
    )
    # The extractor MUST pull `variables=` from the query string and
    # JSON.parse it. Body parsing is async + races Apollo; URL parsing
    # is synchronous.
    assert "variables=" in script, "variables query-param extraction missing"
    assert "decodeURIComponent" in script, (
        "URL extraction needs decodeURIComponent — variables= value is "
        "url-encoded JSON"
    )


def test_dismiss_preview_dialog_works_against_call_based_session() -> None:
    """Issue #718: ``RealBrowserSession`` (the production class) does not
    expose a public ``evaluate`` method — it routes JS evaluation through
    ``self._call('/evaluate', {'script': ...})``. The pre-#718 dismiss
    helper only checked ``getattr(session, 'evaluate', None)`` and
    silently no-op'd when that attribute was absent. Tests passed because
    ``_RecordingSession`` defines ``def evaluate``; production failed
    because ``RealBrowserSession`` does not.

    Pin the contract: a session stub that mimics ``RealBrowserSession``
    (no ``evaluate`` attr, only ``_call``) must still receive the dismiss
    script via the ``/evaluate`` MCP endpoint.
    """
    from otp_worker.create_market_ui import dismiss_preview_dialog

    class _CallOnlySession:
        """Mimics ``RealBrowserSession`` — no ``evaluate``, only ``_call``."""

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def _call(self, path: str, body: dict) -> object:
            self.calls.append((path, body))
            return {"value": False}

    session = _CallOnlySession()
    # MUST NOT raise — best-effort helper.
    dismiss_preview_dialog(session)

    # Production routing: at least one /evaluate call must have fired,
    # carrying the Got-it dismiss script.
    evaluate_calls = [c for c in session.calls if c[0] == "/evaluate"]
    assert evaluate_calls, (
        f"dismiss_preview_dialog must route through _call('/evaluate', ...) "
        f"when the session lacks a public ``evaluate`` attr (the real "
        f"RealBrowserSession case); calls were: {session.calls}"
    )
    script = evaluate_calls[0][1].get("script", "")
    assert "got" in script.lower() and "it" in script.lower(), (
        f"the dispatched script must be the Got-it dismiss JS; got: {script[:200]}"
    )


def test_open_create_form_dismisses_got_it_dialog() -> None:
    """Issue #716 bug 2: Prophet's `/create` page shows a preview-mode
    "GOT IT!" beta dialog on every fresh browser context. My manual MCP
    walkthrough dismissed it before clicking Validate. The bot's flow
    didn't — Validate clicks landed on the modal backdrop, the form
    never advanced, and StartOddsCalculation never fired.

    Pin the dismissal contract: `open_create_form` must call a
    `dismiss_preview_dialog` helper before clicking Validate Question.
    """
    session = _RecordingSession()
    open_create_form(session, question="Will X happen?")

    kinds = [k for (k, _) in session.events]

    # There must be at least one evaluate that targets the Got it dialog,
    # OR a click on a "Got it!"-text selector. We let the implementation
    # pick the safer path (evaluate is preferred — no failure if absent).
    got_it_dismissal_present = any(
        ("evaluate" == k and ("got it" in v.lower() or "got_it" in v.lower()))
        or ("click" == k and "got it" in str(v).lower())
        for (k, v) in session.events
    )
    assert got_it_dismissal_present, (
        f"open_create_form must dismiss the Got-it! preview dialog "
        f"before clicking Validate Question; events were: {kinds}"
    )

    # And the dismissal must come BEFORE the Validate Question click.
    validate_idx = next(
        (i for i, (k, v) in enumerate(session.events)
         if k == "click" and "validate" in str(v).lower()),
        -1,
    )
    dismiss_idx = next(
        (i for i, (k, v) in enumerate(session.events)
         if (k == "evaluate" and "got it" in str(v).lower())
         or (k == "click" and "got it" in str(v).lower())),
        -1,
    )
    assert dismiss_idx >= 0 and validate_idx >= 0, kinds
    assert dismiss_idx < validate_idx, (
        f"Got-it dismissal must come BEFORE Validate Question click; "
        f"dismiss at {dismiss_idx}, validate at {validate_idx}, "
        f"events: {kinds}"
    )
