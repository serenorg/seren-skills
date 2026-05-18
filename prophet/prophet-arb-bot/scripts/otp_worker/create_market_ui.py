"""Drive Prophet's `/create` UI through a Python-owned Playwright browser.

Issue #636: the orchestrating LLM cannot drive Seren Desktop's connected
`mcp__playwright__*` browser autonomously — it lands in an unauthenticated
context every cron tick and stalls on Prophet's sign-in modal. The Python
subprocess already owns a `playwright-stealth` MCP for the OTP cold-start
path, and Privy persists both tokens in localStorage (#583), so we can
restore the session into a Python-owned browser and run `/create` end to
end without human intervention.

Selectors live as module-level constants. Re-validate them against
https://app.prophetmarket.ai/create whenever Prophet's UI rotates.

Selectors live-validated 2026-05-17 (Phase 14 follow-on for #636).

Issue #655: the original capture shim wrapped `window.fetch` only, used
an exact `data.startOddsCalculation.sessionId` path lookup, matched only
URLs containing `/graphql`, and was installed via inline `evaluate(...)`
AFTER `navigate(/create)`. On a contended host, Prophet's bootstrap kept
firing the OCS request through a transport or path the shim could not
see, producing a 100% `ocs_session_id_not_captured` miss rate. The fix:

  1. Install the wrapper via `add_init_script` BEFORE navigation so the
     wrapper is in place for every fetch/XHR Prophet's bootstrap fires.
  2. Wrap `XMLHttpRequest` in addition to `fetch` — Apollo/Relay clients
     can switch transports, and Prophet's odds calc may use XHR.
  3. Widen the URL match to anything containing `graphql` or `odds`.
  4. Walk response bodies recursively for any `sessionId`-shaped key
     nested under an `odds`-shaped ancestor — schema drift no longer
     produces a silent miss.
  5. Keep a small ring buffer of observed URLs + response shapes so the
     `ocs_session_id_not_captured` troubleshooting surface can answer
     "did we observe any odds traffic at all?".
"""

from __future__ import annotations

import json
import time
from typing import Any

PROPHET_CREATE_URL = "https://app.prophetmarket.ai/create"

SEL_QUESTION_INPUT = 'textarea[name="question"], #question-input'
SEL_VALIDATE_QUESTION_BUTTON = 'button:has-text("Validate Question")'
SEL_CREATE_MARKET_BUTTON = 'button:has-text("Create Market")'
SEL_BET_AMOUNT_INPUT = 'input[name="bet-amount"], #bet-amount-input'
SEL_BUY_TAB = 'button:has-text("Buy")'
SEL_SELL_TAB = 'button:has-text("Sell")'
SEL_PROPHET_CONFIRM_BUTTON = 'button:has-text("Confirm")'

# Time budgets — Prophet's odds calculation runs 60-180s, and the Privy
# embedded wallet's `createMarketWithBet` signing prompt typically resolves
# in <10s once Confirm is clicked.
DEFAULT_OCS_WAIT_S = 30.0
DEFAULT_CONFIRM_REDIRECT_TIMEOUT_S = 60.0

# The capture wrapper that runs in the browser. It MUST be idempotent
# (multiple installs land but only the first one wraps), MUST not throw
# even if the page calls `fetch`/`XHR` with unexpected shapes, and MUST
# stash diagnostics so the operator can debug a silent miss.
_CAPTURE_SCRIPT = """
(() => {
  if (window.__seren_capture_installed__) { return true; }
  window.__seren_capture__ = {
    startOddsCalculation: null,
    observed: [],
    error: null,
    // Issue #701: counters and unmatched-URL ring buffer so the operator
    // can tell three states apart on an empty `observed`:
    //   (a) wrapper not installed → __seren_capture__ undefined
    //   (b) wrapper installed, 0 fetches → total_fetch_calls === 0
    //   (c) wrapper installed, N fetches, none matched URL_RE → N>0 and
    //       unmatched_sample populated → see what /create actually issues
    total_fetch_calls: 0,
    unmatched_sample: [],
  };
  const URL_RE = /(graphql|odds)/i;
  const SESSION_KEY_RE = /^(sessionId|session_id|oddsSessionId|odds_session_id)$/;
  const ODDS_KEY_RE = /odds/i;

  function record(url, ok, shape) {
    try {
      const obs = window.__seren_capture__.observed;
      if (obs.length >= 20) { obs.shift(); }
      obs.push({ url: String(url || '').slice(0, 200), ok: !!ok, shape: shape || '' });
    } catch (e) { /* swallow */ }
  }

  function recordUnmatched(url) {
    // Issue #701/#703: sample of URLs the page issued but URL_RE
    // rejected. #701 capped at 10 — too tight: /create issues ~79
    // fetches per cycle (mostly Next.js RSC prefetches) and any early
    // /api/* request got pushed out before we could see it. #703 bumps
    // to 50 so the OCS-request window survives the prefetch noise.
    try {
      const um = window.__seren_capture__.unmatched_sample;
      if (um.length >= 50) { um.shift(); }
      um.push(String(url || '').slice(0, 200));
    } catch (e) { /* swallow */ }
  }

  function topLevelShape(obj) {
    try {
      if (!obj || typeof obj !== 'object') { return typeof obj; }
      const keys = Object.keys(obj);
      const head = keys.slice(0, 8).join(',');
      return keys.length > 8 ? head + ',…' : head;
    } catch (e) { return 'unknown'; }
  }

  function walkForSessionId(node, ancestorHasOdds, depth, budget) {
    if (budget.n <= 0 || depth > 8 || node == null) { return null; }
    budget.n -= 1;
    if (typeof node !== 'object') { return null; }
    if (Array.isArray(node)) {
      for (let i = 0; i < node.length; i++) {
        const found = walkForSessionId(node[i], ancestorHasOdds, depth + 1, budget);
        if (found) { return found; }
      }
      return null;
    }
    const keys = Object.keys(node);
    // First pass: prefer extracting a sessionId at THIS level if any sibling
    // key is odds-shaped, or if we're already under an odds-shaped ancestor.
    const localHasOdds = ancestorHasOdds || keys.some((k) => ODDS_KEY_RE.test(k));
    if (localHasOdds) {
      for (const k of keys) {
        if (SESSION_KEY_RE.test(k)) {
          const v = node[k];
          if (typeof v === 'string' && v.length > 0) { return v; }
        }
      }
    }
    // Then recurse — child subtree may itself be the odds container.
    for (const k of keys) {
      const child = node[k];
      const childAncestorHasOdds = localHasOdds || ODDS_KEY_RE.test(k);
      const found = walkForSessionId(child, childAncestorHasOdds, depth + 1, budget);
      if (found) { return found; }
    }
    return null;
  }

  function extractAndStash(url, parsed) {
    try {
      const sid = walkForSessionId(parsed, false, 0, { n: 1000 });
      record(url, !!sid, topLevelShape(parsed && parsed.data ? parsed.data : parsed));
      if (sid && !window.__seren_capture__.startOddsCalculation) {
        window.__seren_capture__.startOddsCalculation = sid;
      }
    } catch (e) {
      window.__seren_capture__.error = String(e);
    }
  }

  // -- fetch wrapper -------------------------------------------------------
  const origFetch = window.fetch ? window.fetch.bind(window) : null;
  if (origFetch) {
    window.fetch = async (...args) => {
      // Issue #701: increment total counter BEFORE the await so even a
      // crashed origFetch leaves a visible signal that the wrapper ran.
      try { window.__seren_capture__.total_fetch_calls += 1; } catch (e) {}
      const resp = await origFetch(...args);
      try {
        let url = '';
        const a0 = args && args[0];
        // Issue #699: fetch's first arg can be a string, a Request, a
        // URL object, or any object with a `Symbol.toPrimitive`. Next.js
        // and modern Apollo Client pass URL objects (`.href`, not
        // `.url`), which the pre-#699 wrapper extracted as `''` —
        // URL_RE.test('') was false, the record/walk branch was
        // skipped, and the diagnostic ring buffer stayed empty on every
        // ocs_session_id_not_captured block. Cover all four shapes.
        if (typeof a0 === 'string') { url = a0; }
        else if (a0 && typeof a0.url === 'string') { url = a0.url; }
        else if (a0 && typeof a0.href === 'string') { url = a0.href; }
        else if (a0 != null) { try { url = String(a0); } catch (e) { url = ''; } }
        if (URL_RE.test(url)) {
          // Issue #716: Apollo's stream consumer races our clone().text() —
          // the body resolves empty before we can parse it, so the prior
          // walker found no sessionId across 51 matched URLs in live diag.
          // For OddsCalculationSession the sessionId is already in the URL
          // (variables={"id":"<sessionId>"}); parse it synchronously and
          // skip the body altogether. Body parse below is kept as the
          // fallback for non-polling matched URLs and as belt-and-suspenders
          // diagnostic for the matched buffer.
          if (/operationName=OddsCalculationSession/.test(url)) {
            try {
              const m = url.match(/[?&]variables=([^&]+)/);
              if (m) {
                const v = JSON.parse(decodeURIComponent(m[1]));
                if (v && v.id && !window.__seren_capture__.startOddsCalculation) {
                  window.__seren_capture__.startOddsCalculation = v.id;
                  record(url, true, 'OddsCalculationSession');
                }
              }
            } catch (e) { /* URL is malformed, fall through to body parse */ }
          }
          const clone = resp.clone();
          clone.text().then((txt) => {
            try {
              const parsed = JSON.parse(txt);
              extractAndStash(url, parsed);
            } catch (e) { record(url, false, 'unparseable'); }
          }).catch(() => {});
        } else {
          recordUnmatched(url);
        }
      } catch (e) { window.__seren_capture__.error = String(e); }
      return resp;
    };
  }

  // -- XMLHttpRequest wrapper ---------------------------------------------
  try {
    const XHR = window.XMLHttpRequest;
    if (XHR && XHR.prototype && XHR.prototype.open && XHR.prototype.send) {
      const origOpen = XHR.prototype.open;
      const origSend = XHR.prototype.send;
      XHR.prototype.open = function (method, url) {
        try { this.__seren_url = String(url || ''); } catch (e) {}
        return origOpen.apply(this, arguments);
      };
      XHR.prototype.send = function () {
        try {
          const url = this.__seren_url || '';
          if (URL_RE.test(url)) {
            const prior = this.onreadystatechange;
            this.onreadystatechange = function () {
              try {
                if (this.readyState === 4) {
                  let txt = '';
                  try { txt = this.responseText || ''; } catch (e) {}
                  try {
                    const parsed = JSON.parse(txt);
                    extractAndStash(url, parsed);
                  } catch (e) { record(url, false, 'unparseable'); }
                }
              } catch (e) { window.__seren_capture__.error = String(e); }
              if (typeof prior === 'function') {
                try { return prior.apply(this, arguments); } catch (e) {}
              }
            };
          }
        } catch (e) { window.__seren_capture__.error = String(e); }
        return origSend.apply(this, arguments);
      };
    }
  } catch (e) { window.__seren_capture__.error = String(e); }

  window.__seren_capture_installed__ = true;
  return true;
})()
"""

# Back-compat alias — pre-#655 callers and tests imported this name.
_FETCH_CAPTURE_SCRIPT = _CAPTURE_SCRIPT


def install_capture_init_script(session: Any) -> bool:
    """Register the capture wrapper as a `document_start` init script.

    Issue #655: planting the wrapper via `add_init_script` BEFORE any
    navigation eliminates the race where Prophet's bootstrap fires the
    OCS request before an inline `evaluate(...)` install can land. The
    init script persists for the lifetime of the browser context, so
    subsequent navigations (e.g. to `/create`) also see the wrapper.

    Tolerates sessions without `add_init_script` (unit-test stubs):
    returns False so the caller can fall back to `install_fetch_capture`.
    """
    add_init = getattr(session, "add_init_script", None)
    if not callable(add_init):
        return False
    add_init(_CAPTURE_SCRIPT)
    return True


def install_fetch_capture(session: Any) -> None:
    """Belt-and-suspenders inline install via `evaluate(...)`.

    Idempotent against `install_capture_init_script` — the JS guards on
    `window.__seren_capture_installed__` so the second install is a
    no-op. Kept so test stubs that only implement `evaluate` continue to
    drive the capture path, and so the legacy call site in
    `open_create_form` still works on a contended host where the init
    script for some reason didn't bind (e.g. cross-origin frame race).
    """
    _evaluate(session, _CAPTURE_SCRIPT)


def read_captured_ocs_id(session: Any) -> str:
    """Return the most recent OCS sessionId stashed by the capture wrapper."""
    result = _evaluate(
        session,
        "(() => (window.__seren_capture__ && window.__seren_capture__.startOddsCalculation) || '')()",
    )
    return result if isinstance(result, str) else ""


def read_capture_observations(session: Any) -> list[dict[str, Any]]:
    """Return the diagnostic ring buffer of observed odds/graphql requests.

    Used by the `ocs_session_id_not_captured` troubleshooting surface to
    answer "did we observe any odds traffic at all, and what shape did
    Prophet return?". Returns [] if the wrapper isn't installed or the
    page hasn't issued any matching requests yet.
    """
    result = _evaluate(
        session,
        "(() => (window.__seren_capture__ && window.__seren_capture__.observed) || [])()",
    )
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    return []


def read_capture_total_fetch_calls(session: Any) -> int:
    """Return how many times the fetch wrapper saw a call.

    Issue #701: lets the operator distinguish three states that all
    produced an empty `observed` buffer under the pre-#701 wrapper:
      - 0 → wrapper installed but page never fired a fetch
      - N>0 with `observed==[]` → wrapper installed, but no URL matched
        the `(graphql|odds)` filter; check `unmatched_sample` for hints
      - N>0 with `observed` populated → matched and walker ran (handled
        by existing `read_capture_observations`)

    Returns 0 if the wrapper isn't installed.
    """
    result = _evaluate(
        session,
        "(() => (window.__seren_capture__ && window.__seren_capture__.total_fetch_calls) || 0)()",
    )
    if isinstance(result, bool):
        return 0
    if isinstance(result, int):
        return result
    if isinstance(result, float):
        return int(result)
    return 0


def read_capture_unmatched_sample(session: Any) -> list[str]:
    """Return up to 10 URLs the fetch wrapper saw that didn't match URL_RE.

    Issue #701: when `observed` is empty but `total_fetch_calls` > 0, the
    sample shows what endpoints the page DID hit so the operator can
    decide whether the URL filter needs widening or the OCS endpoint
    moved entirely.
    """
    result = _evaluate(
        session,
        "(() => (window.__seren_capture__ && window.__seren_capture__.unmatched_sample) || [])()",
    )
    if isinstance(result, list):
        return [s for s in result if isinstance(s, str)]
    return []


def read_capture_error(session: Any) -> str:
    """Return the last JS error stashed by the capture wrapper.

    The wrapper writes ``String(e)`` to ``window.__seren_capture__.error``
    whenever it catches an exception in the fetch/XHR interception path
    (e.g. ``Response.clone()`` blew up on a streamed body, or the URL
    regex threw on a non-string argument). Surfaced alongside the
    diagnostic ring buffer on the `ocs_session_id_not_captured` blocked
    envelope so operators can tell capture-script failures from quiet
    transport / schema drift without re-driving the cycle.

    Returns "" if no error was captured or the wrapper isn't installed.
    """
    result = _evaluate(
        session,
        "(() => (window.__seren_capture__ && window.__seren_capture__.error) || '')()",
    )
    return result if isinstance(result, str) else ""


def poll_for_ocs_id(
    session: Any,
    *,
    timeout_s: float = DEFAULT_OCS_WAIT_S,
    interval_s: float = 0.5,
    sleep: Any = time.sleep,
    now: Any = time.monotonic,
) -> str:
    deadline = now() + max(timeout_s, 0.0)
    while True:
        ocs_id = read_captured_ocs_id(session)
        if ocs_id:
            return ocs_id
        if now() >= deadline:
            return ""
        sleep(interval_s)


# Issue #716: Prophet's `/create` preview-mode dialog ("GOT IT!" button)
# appears on the first load of every fresh BrowserContext. It traps
# clicks on the form behind it. My manual MCP walkthrough dismissed it
# before clicking Validate Question; the bot's pre-#716 flow did not.
# When the modal was up, the Validate click landed on the modal backdrop,
# the form never advanced, and `StartOddsCalculation` was never fired —
# producing `ocs_session_id_not_captured` blockers indistinguishable from
# the Apollo-clone race that #716 also fixes. This helper is best-effort:
# no-op when the dialog isn't present, never raises.
_DISMISS_GOT_IT_DIALOG_SCRIPT = """
(() => {
  // Find a visible "got it" button and click it. Issue #716 dismissal.
  try {
    const btns = Array.from(document.querySelectorAll('button'));
    const got = btns.find((b) => {
      if (!b.offsetParent) { return false; }
      return /got\\s*it/i.test((b.textContent || '').trim());
    });
    if (got) { got.click(); return true; }
  } catch (e) { /* swallow */ }
  return false;
})()
"""


def dismiss_preview_dialog(session: Any) -> None:
    """Click Prophet's preview-mode 'GOT IT!' modal if it's visible.

    Best-effort. Stubs without ``evaluate`` are tolerated. Any exception
    is swallowed — the caller proceeds to fill/click regardless.
    """
    evaluate = getattr(session, "evaluate", None)
    if not callable(evaluate):
        return
    try:
        evaluate(_DISMISS_GOT_IT_DIALOG_SCRIPT)
    except Exception:
        pass


def open_create_form(session: Any, *, question: str) -> None:
    # Issue #655: install the capture wrapper at `document_start` BEFORE
    # navigating to `/create`, so it's in place for every fetch/XHR that
    # Prophet's bootstrap fires. The inline install below is a fallback
    # for test stubs (and any pathological page where the init script
    # didn't bind in time).
    install_capture_init_script(session)
    session.navigate(PROPHET_CREATE_URL)
    session.wait_for(SEL_QUESTION_INPUT, timeout_ms=30_000)
    install_fetch_capture(session)
    # Issue #716: dismiss the first-load preview modal BEFORE filling +
    # clicking, or Validate Question lands on the modal backdrop and the
    # form never advances to Create Market.
    dismiss_preview_dialog(session)
    session.fill(SEL_QUESTION_INPUT, question)
    session.click(SEL_VALIDATE_QUESTION_BUTTON)
    session.click(SEL_CREATE_MARKET_BUTTON)


def fill_bet_form(session: Any, *, seed_side: str, bet_usdc: float) -> None:
    """Pick Buy/Sell and type the seed amount; do NOT click Confirm."""
    if seed_side == "buy":
        session.click(SEL_BUY_TAB)
    elif seed_side == "sell":
        session.click(SEL_SELL_TAB)
    else:
        raise ValueError(f"seed_side must be 'buy' or 'sell', got {seed_side!r}")
    session.wait_for(SEL_BET_AMOUNT_INPUT, timeout_ms=15_000)
    session.fill(SEL_BET_AMOUNT_INPUT, f"{bet_usdc:.6f}".rstrip("0").rstrip("."))


def click_prophet_confirm(session: Any) -> None:
    session.click(SEL_PROPHET_CONFIRM_BUTTON)


def wait_for_market_redirect(
    session: Any,
    *,
    timeout_s: float = DEFAULT_CONFIRM_REDIRECT_TIMEOUT_S,
    interval_s: float = 1.0,
    sleep: Any = time.sleep,
    now: Any = time.monotonic,
) -> str:
    """Block until the URL changes to `/markets/<id>`; return the id or ""."""
    deadline = now() + max(timeout_s, 0.0)
    while True:
        url = session.get_url() or ""
        market_id = _extract_market_id(url)
        if market_id:
            return market_id
        if now() >= deadline:
            return ""
        sleep(interval_s)


def _extract_market_id(url: str) -> str:
    needle = "/markets/"
    idx = url.find(needle)
    if idx < 0:
        return ""
    tail = url[idx + len(needle) :]
    # Strip query string / fragment / trailing slash.
    for sep in ("?", "#", "/"):
        cut = tail.find(sep)
        if cut >= 0:
            tail = tail[:cut]
    return tail.strip()


def _evaluate(session: Any, script: str) -> Any:
    evaluate = getattr(session, "evaluate", None)
    if callable(evaluate):
        return evaluate(script)
    return session._call("/evaluate", {"script": script})  # type: ignore[attr-defined]


# JSON helper kept in-module so callers do not need to import json just to
# serialize a single value for the capture script's poll path.
_JSON = json
