"""V2OrderBuilder's EIP-712 domain must use version="2" (#740).

The v2 Polymarket exchanges (standard 0xE11118…, neg-risk 0xe2222d…) both
expose `eip712Domain().version == "2"`, verified on-chain via
`seren-polygon` against selector 0x84b0196e on 2026-05-19. The bundled
py-order-utils `BaseBuilder._get_domain_separator` hardcodes
`version="1"`, so even with the correct `verifyingContract` rotated by
#739, the EIP-712 struct hash is wrong and the live CLOB validator
returns `order_version_mismatch`. This was confirmed end-to-end after
PR #739 shipped — every hedge submission still failed with the exact
same error class.

This test pins the domain separator that `V2OrderBuilder.create_order`
constructs to version="2", matching what the v2 contracts actually
validate against. Asserting on the domain object is sufficient — the
signature is fully derived from it via EIP-712 struct hashing.
"""

from __future__ import annotations

from typing import Any


def test_v2_order_builder_signs_with_eip712_version_two() -> None:
    """V2OrderBuilder must build the underlying py_order_utils OrderBuilder
    in a way that produces an EIP-712 domain with version="2".

    The cleanest seam is the domain separator object on the utils builder
    that V2OrderBuilder constructs. Its `version` field is what gets
    keccak'd into the EIP-712 struct hash, and is what the CLOB validator
    checks against. The captured value MUST be "2" — anything else
    (including the py-clob-client default "1") reproduces the bug.
    """
    import polymarket_v2_order_builder as mod
    from py_clob_client.clob_types import CreateOrderOptions, OrderArgs
    from py_clob_client.signer import Signer as ClobSigner

    private_key = "0x" + "11" * 32
    signer = ClobSigner(private_key=private_key, chain_id=137)
    builder = mod.V2OrderBuilder(signer)

    # Build a signed order. The underlying py_order_utils builder is
    # constructed inside create_order and exposes its EIP-712 domain
    # via the `domain_separator` attribute on BaseBuilder.
    captured_domains: list[Any] = []

    original_utils_builder = mod.UtilsOrderBuilder

    def _capturing_utils_builder(*args: Any, **kwargs: Any) -> Any:
        instance = original_utils_builder(*args, **kwargs)
        captured_domains.append(instance.domain_separator)
        return instance

    mod.UtilsOrderBuilder = _capturing_utils_builder  # type: ignore[assignment]
    try:
        builder.create_order(
            OrderArgs(token_id="123", price=0.51, size=10.0, side="BUY"),
            CreateOrderOptions(tick_size="0.01", neg_risk=True),
        )
    finally:
        mod.UtilsOrderBuilder = original_utils_builder  # type: ignore[assignment]

    assert captured_domains, "V2OrderBuilder never constructed a utils builder"
    domain = captured_domains[0]

    # `make_domain` returns an EIP712Struct subclass instance whose member
    # values are stored as `.values["version"]`. Be defensive about how
    # poly_eip712_structs exposes the field across versions.
    version = None
    if hasattr(domain, "values"):
        values = domain.values
        if isinstance(values, dict):
            version = values.get("version")
    if version is None:
        # Fallback: introspect the struct dict on the class for any
        # attribute literally named "version".
        version = getattr(domain, "version", None)

    assert str(version) == "2", (
        "V2OrderBuilder produced an EIP-712 domain with version="
        f"{version!r}; the v2 Polymarket exchanges require version='2'. "
        "Signing against version='1' (py-clob-client default) reproduces "
        "the live order_version_mismatch (#740)."
    )
