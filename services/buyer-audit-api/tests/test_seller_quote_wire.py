from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from buyer_audit_api.domains.ai_inference.quote_verification import (
    Eip712SellerQuoteVerifier,
)
from buyer_audit_api.domains.ai_inference.wire import seller_quote_from_wire

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages/schemas/fixtures/typescript-signed-seller-quote.json"
)


def signed_wire_quote() -> dict:
    return {
        "quote": {
            "quoteId": "quote-1",
            "purchaseId": "purchase-1",
            "sellerAgentId": "gemini-agent",
            "erc8004AgentId": "1",
            "providerId": "gemini",
            "modelId": "gemini-test",
            "modelVersion": "v1",
            "amount": "100000",
            "token": "0x0000000000000000000000000000000000000002",
            "payTo": "0x0000000000000000000000000000000000000003",
            "expectedLatencyMs": "700",
            "inputLimit": "8000",
            "outputLimit": "2000",
            "expiresAt": "1800000120",
            "quoteNonce": "41",
            "counterofferOf": "",
            "available": True,
            "counterofferReason": "",
        },
        "signature": "0x1234",
        "signer": "0x0000000000000000000000000000000000000003",
    }


def test_typescript_wire_quote_maps_to_python_evidence_model() -> None:
    quote = seller_quote_from_wire(
        signed_wire_quote(),
        chain_id=84532,
        verifying_contract="0x0000000000000000000000000000000000000001",
    )
    assert quote.amount_units == 100_000
    assert quote.expires_at == datetime.fromtimestamp(1_800_000_120, tz=UTC)
    assert quote.counteroffer_of is None
    assert quote.available is True
    assert quote.signature == "0x1234"


def test_wire_quote_rejects_numeric_json_that_could_lose_precision() -> None:
    payload = signed_wire_quote()
    payload["quote"]["amount"] = 100_000
    with pytest.raises(ValidationError):
        seller_quote_from_wire(
            payload,
            chain_id=84532,
            verifying_contract="0x0000000000000000000000000000000000000001",
        )


def test_wire_quote_rejects_unknown_unsigned_fields() -> None:
    payload = signed_wire_quote()
    payload["quote"]["discount"] = "99999"
    with pytest.raises(ValidationError):
        seller_quote_from_wire(
            payload,
            chain_id=84532,
            verifying_contract="0x0000000000000000000000000000000000000001",
        )


def test_python_verifies_the_typescript_produced_wire_fixture() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    metadata = payload.pop("fixtureMeta")
    quote = seller_quote_from_wire(
        payload,
        chain_id=metadata["chainId"],
        verifying_contract=metadata["verifyingContract"],
    )
    verifier = Eip712SellerQuoteVerifier(
        chain_id=metadata["chainId"],
        verifying_contract=metadata["verifyingContract"],
    )

    assert metadata["producer"] == "services/seller-service/src/eip712.ts"
    assert metadata["testOnlyKey"] is True
    assert verifier.verify(quote) is True
    assert quote.signer_address.lower() == payload["signer"].lower()
    assert verifier.verify(quote.model_copy(update={"counteroffer_reason": "tampered"})) is False
