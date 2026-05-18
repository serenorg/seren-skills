"""Issue #695: ocs_session_id_not_captured must surface the capture-shim
diagnostic ring buffer (and any captured JS error) in the blocked
envelope payload.

SKILL.md promises:

> When this blocker fires, dump the diagnostic ring buffer via
> `create_market_ui.read_capture_observations(session)` to see whether
> *any* odds traffic was observed; an empty buffer points to a transport
> Prophet adopted that neither `fetch` nor `XHR` covers, while a
> non-empty buffer with `ok: false` rows points to schema drift beyond
> the recursive walker's reach.

Production didn't wire that read into the blocked envelope, so the
operator can't tell the two branches apart without re-running with a
debugger. The fix: attach `capture_observations` and `capture_error` to
the payload returned when `poll_for_ocs_id` times out.

One critical test pins both fields on the blocked envelope.
"""

from __future__ import annotations

from typing import Any

from agent import AgentConfig, CycleResult, cmd_create_market_via_ui
from arbitrage.intelligence import IntelligenceConfig
from arbitrage.scoring import ScoringConfig
from discovery import AutoDiscoverConfig


def _config() -> AgentConfig:
    return AgentConfig(
        inputs={"prophet_email": "jill@volume.finance", "email_provider": "gmail"},
        project_name="prophet",
        database_name="prophet",
        scoring=ScoringConfig(),
        intelligence=IntelligenceConfig(),
        auto_discover=AutoDiscoverConfig(
            enabled=True,
            initial_bet_usdc=1.0,
            create_market_entry_budget_seconds=300.0,
        ),
        live_mode=True,
        max_orders_per_run=5,
        execution_mode="delta_neutral",
        max_hedge_slippage_bps=100.0,
    )


class _FreshCacheEntry:
    jwt = "eyJ.fresh.jwt"
    refresh_token = "rt_fresh"
    prophet_viewer_id = "vid_x"
    state = "fresh"

    def is_fresh(self, *, leeway_seconds: int = 60) -> bool:
        return True


def _stub_establish_session(**_kwargs: Any) -> _FreshCacheEntry:
    return _FreshCacheEntry()


_FAKE_PAGE_URL = "https://app.prophetmarket.ai/markets"


class _StubSession:
    def navigate(self, url: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None: ...
    def get_url(self) -> str:
        # Issue #703: stub returns a NON-empty URL so the page_url
        # blocked-envelope test can pin that get_url's value is lifted
        # all the way into the payload rather than being silently dropped.
        return _FAKE_PAGE_URL
    def evaluate(self, script: str) -> Any:
        return ""


class _SessionScope:
    def __init__(self, session: _StubSession) -> None:
        self._s = session

    def __enter__(self) -> _StubSession:
        return self._s

    def __exit__(self, *args: Any) -> None:
        return None


_FAKE_OBSERVATIONS = [
    {
        "url": "https://app.prophetmarket.ai/api/graphql",
        "ok": False,
        "shape": "errors,extensions",
    }
]
_FAKE_CAPTURE_ERROR = "TypeError: Cannot read properties of undefined"


class _CaptureMissUI:
    """UI helper that simulates an OCS capture miss with non-empty buffer.

    `poll_for_ocs_id` returns "" (no sessionId observed). The two
    diagnostic readers return a populated ring buffer and a JS error —
    exactly the shape the operator needs to tell schema drift from a
    new transport.
    """

    def open_create_form(self, session: Any, *, question: str) -> None:
        pass

    def poll_for_ocs_id(self, session: Any, **kwargs: Any) -> str:
        return ""

    def read_capture_observations(self, session: Any) -> list[dict[str, Any]]:
        return list(_FAKE_OBSERVATIONS)

    def read_capture_error(self, session: Any) -> str:
        return _FAKE_CAPTURE_ERROR

    # Unused on the blocked path — kept so the stub satisfies the module
    # surface the inner driver type-checks against at import.
    def fill_bet_form(self, session: Any, *, seed_side: str, bet_usdc: float) -> None:
        pass

    def click_prophet_confirm(self, session: Any) -> None:
        pass

    def wait_for_market_redirect(self, session: Any, **kwargs: Any) -> str:
        return ""


def _seed_intent_unused(**_kwargs: Any) -> CycleResult:
    raise AssertionError(
        "compute_seed_intent must not run when OCS capture missed — "
        "the blocked envelope returns before the AI calc"
    )


def _record_unused(**_kwargs: Any) -> CycleResult:
    raise AssertionError(
        "record_created_market must not run when OCS capture missed — "
        "no hedge can commit without a sessionId"
    )


def test_ocs_capture_miss_surfaces_diagnostic_ring_buffer_in_payload() -> None:
    """Blocked envelope MUST include `capture_observations` and `capture_error`.

    Without these, an operator looking at the blocked payload sees only
    `polymarket_condition_id` + `question` and can't distinguish:
      (a) empty buffer → Prophet adopted a transport our shim doesn't
          wrap (file follow-up to widen capture);
      (b) non-empty buffer w/ `ok: false` → schema drift beyond the
          recursive walker (file follow-up to extend walker).
    SKILL.md already promised this surface; #695 wires it in.
    """
    ui = _CaptureMissUI()

    result = cmd_create_market_via_ui(
        config=_config(),
        gateway=object(),
        transport=object(),
        polymarket_condition_id="0xCID_ETH_2000",
        question="Will the price of Ethereum be above $2,000 on May 19?",
        initial_bet_usdc=1.0,
        open_session_factory=lambda: _SessionScope(_StubSession()),
        establish_session=_stub_establish_session,
        create_market_ui=ui,
        compute_seed_intent=_seed_intent_unused,
        record_created_market=_record_unused,
    )

    assert result.status == "blocked"
    assert result.reason == "ocs_session_id_not_captured"

    # The diagnostic ring buffer — the operator's only window into
    # whether ANY odds-shaped traffic was observed by the capture shim.
    assert "capture_observations" in result.payload, (
        "blocked envelope must surface the diagnostic ring buffer; "
        f"saw payload keys {sorted(result.payload.keys())}"
    )
    assert result.payload["capture_observations"] == _FAKE_OBSERVATIONS

    # The JS error captured by the shim — non-empty when the wrapper
    # itself threw (e.g. Response.clone() blew up on a streamed body).
    assert "capture_error" in result.payload, (
        "blocked envelope must surface any captured JS error; "
        f"saw payload keys {sorted(result.payload.keys())}"
    )
    assert result.payload["capture_error"] == _FAKE_CAPTURE_ERROR


def test_ocs_capture_miss_surfaces_page_url_in_payload() -> None:
    """Issue #703: when 79 fetches happen on the wrapped page but none
    match URL_RE, the operator can't tell whether the bot is on
    /create (where AI calc should fire) or on a different page that
    just happens to have a question textarea (auth-flow redirect to
    /markets quick-create form). Surfacing `session.get_url()` at the
    OCS-poll-timeout disambiguates.
    """
    ui = _CaptureMissUI()

    result = cmd_create_market_via_ui(
        config=_config(),
        gateway=object(),
        transport=object(),
        polymarket_condition_id="0xCID_ETH_2000",
        question="Will the price of Ethereum be above $2,000 on May 19?",
        initial_bet_usdc=1.0,
        open_session_factory=lambda: _SessionScope(_StubSession()),
        establish_session=_stub_establish_session,
        create_market_ui=ui,
        compute_seed_intent=_seed_intent_unused,
        record_created_market=_record_unused,
    )

    assert result.status == "blocked"
    assert result.reason == "ocs_session_id_not_captured"

    # `page_url` MUST be lifted from session.get_url() into the payload.
    # Empty payload[page_url] is acceptable on hosts where get_url
    # raises, but missing the key entirely means the envelope assembler
    # didn't even attempt the read.
    assert "page_url" in result.payload, (
        "blocked envelope must surface session.get_url() so the operator "
        "can tell which page the OCS poll timed out on; saw payload keys "
        f"{sorted(result.payload.keys())}"
    )
    assert result.payload["page_url"] == _FAKE_PAGE_URL
