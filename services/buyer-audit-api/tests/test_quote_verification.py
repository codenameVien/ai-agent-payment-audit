from __future__ import annotations

from datetime import UTC, datetime

from eth_account import Account
from eth_account.messages import encode_typed_data

from buyer_audit_api.domains.ai_inference.models import SellerQuote
from buyer_audit_api.domains.ai_inference.quote_verification import (
    Eip712SellerQuoteVerifier,
    seller_quote_typed_data,
)

PRIVATE_KEY = "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
VERIFYING_CONTRACT = "0x0000000000000000000000000000000000000001"


def unsigned_quote() -> SellerQuote:
    account = Account.from_key(PRIVATE_KEY)
    return SellerQuote(
        quote_id="quote-1",
        purchase_id="purchase-1",
        seller_agent_id="gemini-agent",
        erc8004_agent_id="1",
        provider_id="gemini",
        model_id="gemini-test",
        model_version="v1",
        amount_units=100_000,
        token="0x0000000000000000000000000000000000000002",
        pay_to="0x0000000000000000000000000000000000000003",
        expected_latency_ms=700,
        input_limit=8_000,
        output_limit=2_000,
        available=True,
        identity_verified=True,
        expires_at=datetime.fromtimestamp(1_800_000_120, tz=UTC),
        quote_nonce="41",
        signature="0x00",
        signer_address=account.address,
        chain_id=84532,
        verifying_contract=VERIFYING_CONTRACT,
    )


def sign(quote: SellerQuote) -> SellerQuote:
    signable = encode_typed_data(
        full_message=seller_quote_typed_data(
            quote,
            chain_id=84532,
            verifying_contract=VERIFYING_CONTRACT,
        )
    )
    signature = Account.sign_message(signable, private_key=PRIVATE_KEY).signature.hex()
    return quote.model_copy(update={"signature": signature})


def test_python_recovers_the_same_eip712_seller_signer() -> None:
    quote = sign(unsigned_quote())
    verifier = Eip712SellerQuoteVerifier(
        chain_id=84532,
        verifying_contract=VERIFYING_CONTRACT,
    )
    assert verifier.verify(quote) is True
    assert verifier.verify(quote.model_copy(update={"amount_units": 100_001})) is False
    assert verifier.verify(quote.model_copy(update={"available": False})) is False
    assert verifier.verify(quote.model_copy(update={"counteroffer_reason": "changed"})) is False


def test_quote_domain_cannot_be_substituted() -> None:
    quote = sign(unsigned_quote())
    verifier = Eip712SellerQuoteVerifier(
        chain_id=1,
        verifying_contract=VERIFYING_CONTRACT,
    )
    assert verifier.verify(quote) is False
