from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from buyer_audit_api.adapters.ai_inference import (
    HttpSellerQuoteClient,
    JsonRpcSellerIdentityVerifier,
)
from buyer_audit_api.domains.ai_inference.models import (
    BenchmarkSnapshot,
    NormalizedAiRequest,
    SellerQuote,
)


@pytest.mark.asyncio
async def test_quote_omits_absent_optional_latency() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads((await request.aread()).decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "quote": {
                    "quoteId": "quote-1",
                    "purchaseId": "purchase-1",
                    "sellerAgentId": "seller-gemini",
                    "erc8004AgentId": "9154",
                    "providerId": "gemini",
                    "modelId": "gemini-2.5-flash",
                    "modelVersion": "gemini-2.5-flash",
                    "amount": "100000",
                    "token": "0x0000000000000000000000000000000000000001",
                    "payTo": "0x0000000000000000000000000000000000000002",
                    "expectedLatencyMs": "5000",
                    "inputLimit": "32768",
                    "outputLimit": "4096",
                    "expiresAt": "2000000000",
                    "quoteNonce": "1",
                    "counterofferOf": "",
                    "available": True,
                    "counterofferReason": "",
                },
                "signature": "0x12",
                "signer": "0x0000000000000000000000000000000000000002",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = HttpSellerQuoteClient(
            routes={"gemini": "http://seller"},
            chain_id=84532,
            verifying_contract="0x0000000000000000000000000000000000000003",
            internal_service_token="internal-token",
            client=http_client,
        )
        await client.quote(
            purchase_id="purchase-1",
            request=NormalizedAiRequest(
                prompt_hash="sha256:" + "0" * 64,
                prompt_length=1,
            ),
            benchmark=BenchmarkSnapshot(
                snapshot_id="snapshot-1",
                provider_id="gemini",
                model_id="gemini-2.5-flash",
                model_version="gemini-2.5-flash",
                observed_at=datetime(2026, 9, 2, tzinfo=UTC),
                source_url="https://example.test",
                content_hash="sha256:" + "1" * 64,
                quality_score=90,
                speed_score=90,
                reputation_score=90,
            ),
        )

    assert "maxLatencyMs" not in captured


@pytest.mark.asyncio
async def test_identity_verifier_retries_transient_rpc_error() -> None:
    attempts = 0
    signer = "0x0000000000000000000000000000000000000002"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32001}})
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": "0x" + "0" * 24 + signer[2:]},
        )

    quote = SellerQuote(
        quote_id="quote-1",
        purchase_id="purchase-1",
        seller_agent_id="seller-gemini",
        erc8004_agent_id="9154",
        provider_id="gemini",
        model_id="gemini-2.5-flash",
        model_version="gemini-2.5-flash",
        amount_units=100_000,
        token="0x0000000000000000000000000000000000000001",
        pay_to=signer,
        expected_latency_ms=5000,
        input_limit=32768,
        output_limit=4096,
        available=True,
        identity_verified=False,
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        quote_nonce="1",
        signature="0x12",
        signer_address=signer,
        chain_id=84532,
        verifying_contract="0x0000000000000000000000000000000000000003",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        verifier = JsonRpcSellerIdentityVerifier(
            rpc_url="https://rpc.test",
            identity_registry="0x0000000000000000000000000000000000000004",
            client=http_client,
            retry_delay_seconds=0,
        )
        evidence = await verifier.verify(quote)

    assert attempts == 2
    assert evidence.identity_verified is True
