"""Issue #607 — `computeProxyAddress(address)` selector must match the
Polygonscan-verified SafeProxyFactory ABI.

PR #606 introduced `polymarket_v2_broadcast._COMPUTE_PROXY_ADDRESS_SELECTOR`
with the wrong 4-byte selector (`0xc46cfaff`). `eth_call` to a contract
with a missing selector returns `"0x"`, so `fetch_proxy_address_for_eoa`
returned `None` and the V2 onboarding orchestrator aborted with
`proxy_address_unavailable` on every fresh-wallet live cycle.

The original `test_polymarket_v2_onboarding.py` monkeypatched
`fetch_proxy_address_for_eoa` directly, so the bad selector was never
exercised. These two tests are the critical-path coverage we lacked.
"""

from __future__ import annotations

from typing import Any

import pytest
from eth_utils import keccak


def test_compute_proxy_address_selector_matches_keccak_of_signature() -> None:
    """The module constant must be the first 4 bytes of
    `keccak256("computeProxyAddress(address)")`.

    This is the test that would have caught #606's bug at PR time. If
    Polymarket's factory ever renames the function, this test fails at
    import-time during the next CI run — no live RPC required.
    """
    import polymarket_v2_broadcast as broadcast_mod

    expected_selector = "0x" + keccak(text="computeProxyAddress(address)")[:4].hex()
    assert broadcast_mod._COMPUTE_PROXY_ADDRESS_SELECTOR == expected_selector, (
        f"selector drift: constant is "
        f"{broadcast_mod._COMPUTE_PROXY_ADDRESS_SELECTOR!r} but "
        f"keccak256('computeProxyAddress(address)')[:4] is {expected_selector!r}; "
        f"verify against polygonscan.com/address/<SAFE_PROXY_FACTORY>"
    )


def test_fetch_proxy_address_builds_calldata_with_correct_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`fetch_proxy_address_for_eoa` must ship `selector || padded(eoa)`
    over the wire. Captures the `eth_call` params via a stubbed
    `_seren_polygon_rpc` and asserts the calldata exactly.

    This is the test that proves the wire format is right end-to-end —
    not just that the constant happens to match keccak.
    """
    import polymarket_v2_broadcast as broadcast_mod

    eoa = "0xAE10914F91E122D73aBFA651c64302EFB8cb9A04"
    # 32-byte word: 20 bytes of address, left-padded with 12 zero bytes.
    expected_calldata_suffix = "ae10914f91e122d73abfa651c64302efb8cb9a04".rjust(64, "0")

    captured: dict[str, Any] = {}

    def fake_rpc(*, method: str, params: list, seren_publisher: str, timeout_seconds: float):
        captured["method"] = method
        captured["params"] = params
        captured["publisher"] = seren_publisher
        # Return a syntactically-valid 32-byte address response so the
        # caller's parser succeeds — we only care that the calldata was
        # built right.
        return "0x" + "00" * 12 + "f5824d9b7e7ad2ec36df19915067613111be3e10"

    monkeypatch.setattr(broadcast_mod, "_seren_polygon_rpc", fake_rpc)

    result = broadcast_mod.fetch_proxy_address_for_eoa(eoa_address=eoa)

    # Calldata-shape assertions (the load-bearing checks).
    assert captured["method"] == "eth_call"
    call_obj = captured["params"][0]
    assert call_obj["to"].lower() == "0xaacfeea03eb1561c4e67d661e40682bd20e3541b", (
        f"factory address drifted: {call_obj['to']!r}"
    )
    expected_selector = "0x" + keccak(text="computeProxyAddress(address)")[:4].hex()
    assert call_obj["data"].startswith(expected_selector), (
        f"calldata does not start with the correct selector; "
        f"got {call_obj['data'][:10]!r}, expected {expected_selector!r}"
    )
    assert call_obj["data"][10:] == expected_calldata_suffix, (
        f"EOA padding is wrong: got {call_obj['data'][10:]!r}, "
        f"expected {expected_calldata_suffix!r}"
    )

    # Sanity: the function returned a valid 20-byte address from the
    # canned response (proves the parser is wired to the same call we
    # asserted on). #613 normalized to EIP-55 checksum at this seam.
    assert result == "0xf5824d9B7E7ad2eC36dF19915067613111BE3e10"
