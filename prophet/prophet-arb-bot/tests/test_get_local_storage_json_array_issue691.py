"""Issue #691 — get_local_storage must return JSON-array values as strings.

The bundled playwright-stealth MCP serializes evaluate results as
``typeof result === "string" ? result : JSON.stringify(result)``. For
``localStorage.getItem("privy:connections")``, the JS string returned
to the MCP is itself a JSON-array literal like
``[{"address":"0x...","connectorType":"embedded",...}]``. The MCP
sends that text verbatim.

In the Python client, ``_extract_tool_body`` calls ``json.loads(text)``
on every non-empty text content item. For ``privy:connections`` the
parse succeeds (it's valid JSON) and the function returns a Python
``list[dict]`` — losing the string form. Downstream,
``RealBrowserSession.get_local_storage`` filters with
``isinstance(unwrapped, str) else None`` and returns ``None``.

The result: ``wait_for_privy_connections`` (issue #678) reads ``None``,
polls again, times out at the 30s budget, and reports
``OtpEmailTimeout: privy:connections did not appear in localStorage``
— despite the value being present the whole time.

The empirical proof was a ``PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE=1`` run:

    [diag] localStorage keys: [..., 'privy:connections', 'privy:token', ...]
    [diag] jwt_len=413  (valid ES256 JWT, OTP succeeded)

then immediately:

    OtpEmailTimeout: privy:connections did not appear within 30s

This was the actual root cause masked by the entire env-profile saga
(#680 → #689). Fix: in ``get_local_storage``, accept ``str`` *and*
JSON-shaped (``list``/``dict``) results, re-serializing the latter so
callers see the same string the page wrote.

One critical test, no duplicates: pins the JSON-array localStorage
case that this fix unblocks. The two negative paths (bare string
preserved, ``None`` for missing key) are the existing contract and
do not need new pinning.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from otp_worker.playwright_client import RealBrowserSession


class _FakeGateway:
    """Minimal gateway stub: returns a Python list for a JSON-array localStorage value.

    Mirrors what the real gateway returns after ``_extract_tool_body``
    parses the MCP text payload via ``json.loads``: the JSON-array string
    from the page comes back as a parsed Python list. The fix must
    re-serialize this list so ``get_local_storage`` returns the string
    form the localStorage value originally had.
    """

    def __init__(self, payload):
        self._payload = payload
        self.last_script = None

    def mcp__playwright__playwright_evaluate(self, script):
        self.last_script = script
        return self._payload


def test_get_local_storage_returns_json_array_value_as_string() -> None:
    """A JSON-array localStorage value must come back as the string the page wrote.

    The page writes ``[{"address":"0x...","connectorType":"embedded",...}]``
    as the value of ``privy:connections``. The MCP roundtrip parses that
    into a Python list. ``get_local_storage`` must re-serialize it to
    the original string form, so downstream consumers like
    ``wait_for_privy_connections`` see a non-empty non-``[]`` string
    and exit their poll loop within their first tick.
    """
    parsed_value = [
        {
            "address": "0x8C2D2B60D40dF744235fB4918064955C193bDaEf",
            "connectorType": "embedded",
            "walletClientType": "privy",
            "id": "io.privy.wallet",
        }
    ]
    gateway = _FakeGateway(payload=parsed_value)
    session = RealBrowserSession(gateway=gateway)

    raw = session.get_local_storage("privy:connections")

    assert raw is not None, (
        "get_local_storage returned None for a JSON-array localStorage "
        "value; this is the #691 bug — _extract_tool_body parsed the "
        "value into a Python list and the isinstance(str) filter trashed "
        "it. The fix must re-serialize list/dict back to a JSON string."
    )
    assert isinstance(raw, str), (
        f"get_local_storage must return str; got {type(raw).__name__}"
    )
    # The re-serialized string must satisfy the same predicate
    # wait_for_privy_connections uses (#678):
    assert raw.strip() and raw.strip() not in ("[]", '""'), (
        f"Re-serialized value must satisfy wait_for_privy_connections's "
        f"non-empty non-[] predicate. Got {raw!r}."
    )
    # Sanity: address is preserved in the re-serialized form.
    assert "0x8C2D2B60D40dF744235fB4918064955C193bDaEf" in raw, (
        f"Wallet address must survive the re-serialization. Got {raw!r}."
    )
