from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from buyer_audit_api.domains.ai_inference.models import SellerQuote


class SellerQuoteWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quoteId: str = Field(min_length=1)
    purchaseId: str = Field(min_length=1)
    sellerAgentId: str = Field(min_length=1)
    erc8004AgentId: str = Field(pattern=r"^[0-9]+$")
    providerId: str = Field(min_length=1)
    modelId: str = Field(min_length=1)
    modelVersion: str = Field(min_length=1)
    amount: str = Field(pattern=r"^[0-9]+$")
    token: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    payTo: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    expectedLatencyMs: str = Field(pattern=r"^[1-9][0-9]*$")
    inputLimit: str = Field(pattern=r"^[1-9][0-9]*$")
    outputLimit: str = Field(pattern=r"^[1-9][0-9]*$")
    expiresAt: str = Field(pattern=r"^[0-9]+$")
    quoteNonce: str = Field(pattern=r"^[0-9]+$")
    counterofferOf: str
    available: bool
    counterofferReason: str


class SignedSellerQuoteWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: SellerQuoteWire
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]+$")
    signer: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")


def seller_quote_from_wire(
    payload: dict[str, Any],
    *,
    chain_id: int,
    verifying_contract: str,
) -> SellerQuote:
    signed = SignedSellerQuoteWire.model_validate(payload)
    quote = signed.quote
    return SellerQuote(
        quote_id=quote.quoteId,
        purchase_id=quote.purchaseId,
        seller_agent_id=quote.sellerAgentId,
        erc8004_agent_id=quote.erc8004AgentId,
        provider_id=quote.providerId,
        model_id=quote.modelId,
        model_version=quote.modelVersion,
        amount_units=int(quote.amount),
        token=quote.token,
        pay_to=quote.payTo,
        expected_latency_ms=int(quote.expectedLatencyMs),
        input_limit=int(quote.inputLimit),
        output_limit=int(quote.outputLimit),
        available=quote.available,
        identity_verified=False,
        expires_at=datetime.fromtimestamp(int(quote.expiresAt), tz=UTC),
        quote_nonce=quote.quoteNonce,
        counteroffer_of=quote.counterofferOf or None,
        counteroffer_reason=quote.counterofferReason or None,
        signature=signed.signature,
        signer_address=signed.signer,
        chain_id=chain_id,
        verifying_contract=verifying_contract,
    )
