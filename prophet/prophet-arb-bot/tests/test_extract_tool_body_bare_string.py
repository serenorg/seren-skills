"""Issue #668: bundled playwright-stealth MCP emits bare-string evaluate
results unwrapped.

`playwright-stealth/dist/index.js:377` serializes tool results as:

    text: typeof result === "string" ? result : JSON.stringify(result)

So `playwright_evaluate(script="window.location.href")` puts the literal
URL `https://app.prophetmarket.ai/` in the text body — NOT
`"https://app.prophetmarket.ai/"` (JSON-encoded). `json.loads` on a bare
URL raises `JSONDecodeError`, and pre-#668 `_extract_tool_body` hit
`continue`, exhausted the content list, and silently returned None.

That made `RealBrowserSession.get_url()` return "" and
`get_local_storage(...)` return None for every real value the page had
already planted, which is the exact `observable_check.url=""`,
`privy_token_present=false` shape documented in issue #668 — masquerading
as a Privy SDK rejection when the planted state was actually there.

The fix is one branch: a text item that doesn't parse as JSON must be
returned as-is. Other content shapes (JSON-stringified objects/arrays,
structuredContent envelopes) keep their existing JSON-decoded paths.
"""

from __future__ import annotations

from otp_worker.playwright_mcp_gateway import _extract_tool_body


def _bundled_text_envelope(text: str) -> dict:
    """Exact wire shape emitted by playwright-stealth MCP for tool results."""
    return {"content": [{"type": "text", "text": text}]}


def test_bare_string_evaluate_result_survives_unwrap():
    # window.location.href → bundled MCP wraps as bare text (not JSON-encoded).
    bare_url = _bundled_text_envelope("https://app.prophetmarket.ai/")
    assert _extract_tool_body(bare_url) == "https://app.prophetmarket.ai/"

    # localStorage.getItem("privy:user") when the SDK has populated it: the
    # bundled MCP sees a JS string whose value happens to look like JSON.
    # `typeof === "string"` so it's sent bare — the literal text IS valid JSON,
    # so the existing path parses it. Regression guard for the JSON-string case.
    json_string = _bundled_text_envelope('"\\"eyJ.j.w.t\\""')
    assert _extract_tool_body(json_string) == '"eyJ.j.w.t"'

    # JSON-stringified object (the dump_local_storage_keys case) keeps working.
    json_object = _bundled_text_envelope('{"k":"v","n":1}')
    assert _extract_tool_body(json_object) == {"k": "v", "n": 1}
