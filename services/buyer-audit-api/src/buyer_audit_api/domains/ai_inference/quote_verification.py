from __future__ import annotations

from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

from buyer_audit_api.domains.ai_inference.models import SellerQuote

SELLER_QUOTE_FIELDS = [
    {"name": "quoteId", "type": "string"},
    {"name": "purchaseId", "type": "string"},
    {"name": "sellerAgentId", "type": "string"},
    {"name": "erc8004AgentId", "type": "uint256"},
    {"name": "providerId", "type": "string"},
    {"name": "modelId", "type": "string"},
    {"name": "modelVersion", "type": "string"},
    {"name": "amount", "type": "uint256"},
    {"name": "token", "type": "address"},
    {"name": "payTo", "type": "address"},
    {"name": "expectedLatencyMs", "type": "uint256"},
    {"name": "inputLimit", "type": "uint256"},
    {"name": "outputLimit", "type": "uint256"},
    {"name": "expiresAt", "type": "uint256"},
    {"name": "quoteNonce", "type": "uint256"},
    {"name": "counterofferOf", "type": "string"},
    {"name": "available", "type": "bool"},
    {"name": "counterofferReason", "type": "string"},
]


def seller_quote_typed_data(
    quote: SellerQuote,
    *,
    chain_id: int,
    verifying_contract: str,
) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "SellerQuote": SELLER_QUOTE_FIELDS,
        },
        "primaryType": "SellerQuote",
        "domain": {
            "name": "PBL Seller Quote",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": verifying_contract,
        },
        "message": {
            "quoteId": quote.quote_id,
            "purchaseId": quote.purchase_id,
            "sellerAgentId": quote.seller_agent_id,
            "erc8004AgentId": int(quote.erc8004_agent_id),
            "providerId": quote.provider_id,
            "modelId": quote.model_id,
            "modelVersion": quote.model_version,
            "amount": quote.amount_units,
            "token": quote.token,
            "payTo": quote.pay_to,
            "expectedLatencyMs": quote.expected_latency_ms,
            "inputLimit": quote.input_limit,
            "outputLimit": quote.output_limit,
            "expiresAt": int(quote.expires_at.timestamp()),
            "quoteNonce": int(quote.quote_nonce),
            "counterofferOf": quote.counteroffer_of or "",
            "available": quote.available,
            "counterofferReason": quote.counteroffer_reason or "",
        },
    }


class Eip712SellerQuoteVerifier:
    def __init__(self, *, chain_id: int, verifying_contract: str) -> None:
        self._chain_id = chain_id
        self._verifying_contract = verifying_contract.lower()

    def verify(self, quote: SellerQuote) -> bool:
        if quote.chain_id != self._chain_id:
            return False
        if quote.verifying_contract.lower() != self._verifying_contract:
            return False
        try:
            message = encode_typed_data(
                full_message=seller_quote_typed_data(
                    quote,
                    chain_id=self._chain_id,
                    verifying_contract=self._verifying_contract,
                )
            )
            recovered = Account.recover_message(message, signature=quote.signature)
        except (TypeError, ValueError):
            return False
        return recovered.lower() == quote.signer_address.lower()
