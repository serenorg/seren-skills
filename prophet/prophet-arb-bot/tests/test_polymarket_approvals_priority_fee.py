"""Issue #602 — auto-approve broadcasts must clear Polygon's network gas-tip floor.

Polygon raised the network minimum `maxPriorityFeePerGas` to 25 gwei via
chain governance, but `polymarket_approvals_broadcast.py` hardcoded 2 gwei.
Every fresh `approve()` broadcast failed with `gas tip cap below minimum`
and surfaced as the opaque `broadcast_failed` envelope. The v1 spenders
masked this for months because every cycle returned
`skipped_already_approved` — #600 exposed it by triggering the first real
broadcast.

The fix queries `eth_maxPriorityFeePerGas` from the live RPC and uses
`max(rpc_value, floor)`. This self-heals across future Polygon governance
changes without a code change, and the floor remains a safety net for RPC
failures or garbage responses.

This is the regression test that locks the behavior. One parametrized
test covers the entire contract — no duplicated cases. The on-chain
broadcast itself (nonce/gas/sign/send) is still covered by the
functional smoke test, not by unit tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import polymarket_approvals_broadcast as broadcast_mod  # noqa: E402


@pytest.mark.parametrize(
    "rpc_response, expected_wei, case",
    [
        # RPC returns a value above the floor — use the RPC value (lets the
        # bot ride higher network fees during congestion without operator
        # intervention).
        ("0xba43b7400", 50 * 10**9, "rpc_above_floor"),  # 50 gwei
        # RPC returns a value below the floor — use the floor (Polygon's
        # 25 gwei minimum + headroom; clears the network gate).
        ("0x4a817c800", broadcast_mod.PRIORITY_FEE_FLOOR_WEI, "rpc_below_floor"),  # 20 gwei
        # RPC failure (None) — fall back to the floor instead of stalling
        # the broadcast.
        (None, broadcast_mod.PRIORITY_FEE_FLOOR_WEI, "rpc_none"),
        # Garbage RPC response (e.g. transient gateway corruption) — fall
        # back to the floor.
        ("not-hex", broadcast_mod.PRIORITY_FEE_FLOOR_WEI, "rpc_garbage"),
    ],
)
def test_resolve_priority_fee_uses_max_of_rpc_and_floor(
    monkeypatch: pytest.MonkeyPatch,
    rpc_response: object,
    expected_wei: int,
    case: str,
) -> None:
    """`_resolve_priority_fee` must return max(rpc, floor) and fall back to
    floor on RPC failure or garbage. The floor itself is the safety net
    that prevents the broadcaster from emitting transactions below
    Polygon's chain-governance minimum."""

    captured_calls: list[tuple[str, list]] = []

    def fake_rpc(*, method: str, params: list, seren_publisher: str, timeout_seconds: float) -> object:
        captured_calls.append((method, params))
        return rpc_response

    monkeypatch.setattr(broadcast_mod, "_seren_polygon_rpc", fake_rpc)

    actual = broadcast_mod._resolve_priority_fee(
        seren_publisher="seren-polygon",
        timeout_seconds=10.0,
    )

    assert actual == expected_wei, f"case={case}: expected {expected_wei}, got {actual}"
    assert captured_calls == [("eth_maxPriorityFeePerGas", [])], (
        f"case={case}: expected exactly one eth_maxPriorityFeePerGas call, got {captured_calls}"
    )
    # Floor must clear Polygon's documented 25 gwei minimum with safety
    # margin. Sanity-check the constant so a future edit can't silently
    # lower it back below the network gate.
    assert broadcast_mod.PRIORITY_FEE_FLOOR_WEI >= 25 * 10**9
