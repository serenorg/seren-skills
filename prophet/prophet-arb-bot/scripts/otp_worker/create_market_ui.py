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

_FETCH_CAPTURE_SCRIPT = """
(() => {
  if (window.__seren_capture_installed__) { return true; }
  window.__seren_capture__ = { startOddsCalculation: null, error: null };
  window.__seren_original_fetch__ = window.fetch;
  const orig = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const resp = await orig(...args);
    try {
      const url = (args && args[0] && args[0].url) || (typeof args[0] === 'string' ? args[0] : '');
      if (typeof url === 'string' && url.includes('/graphql')) {
        const clone = resp.clone();
        clone.text().then((txt) => {
          try {
            const parsed = JSON.parse(txt);
            const start = parsed && parsed.data && parsed.data.startOddsCalculation;
            if (start && start.sessionId) {
              window.__seren_capture__.startOddsCalculation = start.sessionId;
            }
          } catch (e) { /* swallow */ }
        }).catch(() => {});
      }
    } catch (e) { window.__seren_capture__.error = String(e); }
    return resp;
  };
  window.__seren_capture_installed__ = true;
  return true;
})()
"""


def install_fetch_capture(session: Any) -> None:
    """Wrap `window.fetch` so OCS sessionIds are captured client-side."""
    _evaluate(session, _FETCH_CAPTURE_SCRIPT)


def read_captured_ocs_id(session: Any) -> str:
    """Return the most recent OCS sessionId stashed by `install_fetch_capture`."""
    result = _evaluate(
        session,
        "(() => (window.__seren_capture__ && window.__seren_capture__.startOddsCalculation) || '')()",
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


def open_create_form(session: Any, *, question: str) -> None:
    session.navigate(PROPHET_CREATE_URL)
    session.wait_for(SEL_QUESTION_INPUT, timeout_ms=30_000)
    install_fetch_capture(session)
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
