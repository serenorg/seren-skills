"""V2 OrderBuilder override for the Polymarket CLOB (#738).

py-clob-client 0.34.6's bundled `OrderBuilder` resolves the EIP-712
`verifyingContract` from `get_contract_config(chain_id, neg_risk)`, which
returns the v1 exchange addresses on Polygon (chain 137):

    standard  : 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E
    neg-risk  : 0xC5d563A36AE78145C45a50134d48A1215220f80a

Polymarket has since rotated its live CLOB validator to v2:

    standard  : 0xE111180000d2663C0091e4f400237545B87B996B
    neg-risk  : 0xe2222d279d744050d28e00520010520000310F59

A v1-signed order against the v2 validator is rejected at submission with
`PolyApiException[status_code=400, error_message={'error': 'order_version_mismatch'}]`.
This subclass overrides only `create_order` and `create_market_order`, both of
which were previously constructing the underlying py_order_utils builder with the
v1 exchange address — that address is exactly what BaseBuilder bakes into the
EIP-712 domain separator. Switching it to v2 here is sufficient to clear the
mismatch on every Polymarket order path used by prophet-arb-bot.

Collateral (USDC.e) and conditional-tokens addresses are intentionally NOT
rotated. Allowance state and CTF ids are unchanged across the v1/v2 cutover;
the SKILL.md auto-approve list (#600) already pins the right spender addresses
for both v1 and v2 exchanges.
"""

from __future__ import annotations

from typing import Any

from py_clob_client.clob_types import CreateOrderOptions, MarketOrderArgs, OrderArgs
from py_clob_client.order_builder.builder import ROUNDING_CONFIG, OrderBuilder
from py_order_utils.builders import OrderBuilder as UtilsOrderBuilder
from py_order_utils.model import OrderData, SignedOrder
from py_order_utils.signer import Signer as UtilsSigner

POLYMARKET_V2_EXCHANGE_STANDARD: str = "0xE111180000d2663C0091e4f400237545B87B996B"
POLYMARKET_V2_EXCHANGE_NEG_RISK: str = "0xe2222d279d744050d28e00520010520000310F59"


def _v2_exchange_address(neg_risk: bool) -> str:
    return POLYMARKET_V2_EXCHANGE_NEG_RISK if neg_risk else POLYMARKET_V2_EXCHANGE_STANDARD


class V2OrderBuilder(OrderBuilder):
    """OrderBuilder subclass that signs against the v2 Polymarket exchange."""

    def create_order(self, order_args: OrderArgs, options: CreateOrderOptions) -> SignedOrder:
        side, maker_amount, taker_amount = self.get_order_amounts(
            order_args.side,
            order_args.size,
            order_args.price,
            ROUNDING_CONFIG[options.tick_size],
        )
        data = OrderData(
            maker=self.funder,
            taker=order_args.taker,
            tokenId=order_args.token_id,
            makerAmount=str(maker_amount),
            takerAmount=str(taker_amount),
            side=side,
            feeRateBps=str(order_args.fee_rate_bps),
            nonce=str(order_args.nonce),
            signer=self.signer.address(),
            expiration=str(order_args.expiration),
            signatureType=self.sig_type,
        )
        utils_builder: Any = UtilsOrderBuilder(
            _v2_exchange_address(options.neg_risk),
            self.signer.get_chain_id(),
            UtilsSigner(key=self.signer.private_key),
        )
        return utils_builder.build_signed_order(data)

    def create_market_order(
        self, order_args: MarketOrderArgs, options: CreateOrderOptions
    ) -> SignedOrder:
        side, maker_amount, taker_amount = self.get_market_order_amounts(
            order_args.side,
            order_args.amount,
            order_args.price,
            ROUNDING_CONFIG[options.tick_size],
        )
        data = OrderData(
            maker=self.funder,
            taker=order_args.taker,
            tokenId=order_args.token_id,
            makerAmount=str(maker_amount),
            takerAmount=str(taker_amount),
            side=side,
            feeRateBps=str(order_args.fee_rate_bps),
            nonce=str(order_args.nonce),
            signer=self.signer.address(),
            expiration="0",
            signatureType=self.sig_type,
        )
        utils_builder: Any = UtilsOrderBuilder(
            _v2_exchange_address(options.neg_risk),
            self.signer.get_chain_id(),
            UtilsSigner(key=self.signer.private_key),
        )
        return utils_builder.build_signed_order(data)
