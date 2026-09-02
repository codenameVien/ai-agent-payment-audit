from __future__ import annotations

from datetime import timedelta

import pytest

from buyer_audit_api.domains.ai_inference.models import (
    BenchmarkSnapshot,
    Candidate,
    NormalizedAiRequest,
    SellerQuote,
)
from buyer_audit_api.domains.ai_inference.module import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.selection import SelectionEngine


def normalized_request() -> NormalizedAiRequest:
    return NormalizedAiRequest(
        prompt_hash="sha256:" + "0" * 64,
        prompt_length=5,
    )


def candidate(clock, quote_id: str, *, expired: bool) -> Candidate:
    return Candidate(
        quote=SellerQuote(
            quote_id=quote_id,
            purchase_id="purchase-1",
            seller_agent_id="seller-agent",
            erc8004_agent_id="1",
            provider_id="gemini",
            model_id=f"model-{quote_id}",
            model_version="v1",
            amount_units=10,
            token="0x0000000000000000000000000000000000000002",
            pay_to="0x0000000000000000000000000000000000000003",
            expected_latency_ms=500,
            input_limit=8_000,
            output_limit=2_000,
            available=True,
            identity_verified=True,
            expires_at=clock.now() + timedelta(minutes=-1 if expired else 5),
            quote_nonce=quote_id,
            signature="0x1234",
            signer_address="0x0000000000000000000000000000000000000003",
            chain_id=84532,
            verifying_contract="0x0000000000000000000000000000000000000001",
        ),
        benchmark=BenchmarkSnapshot(
            snapshot_id=f"snapshot-{quote_id}",
            provider_id="gemini",
            model_id=f"model-{quote_id}",
            model_version="v1",
            observed_at=clock.now() - timedelta(hours=1),
            source_url="https://example.test/benchmark",
            content_hash=f"hash-{quote_id}",
            quality_score=80,
            speed_score=80,
            reputation_score=80,
        ),
    )


def test_clarification_identifies_missing_or_invalid_user_constraints() -> None:
    clarification = AiInferenceDomainModule().clarification({"priority": "unknown"})
    assert clarification.required is True
    assert len(clarification.questions) == 2


def test_expired_signed_quote_is_rejected_before_decision(clock) -> None:
    decision = SelectionEngine().decide(
        request=normalized_request(),
        budget_units=100,
        candidates=[
            candidate(clock, "expired", expired=True),
            candidate(clock, "valid", expired=False),
        ],
        now=clock.now(),
    )
    assert decision.winner.quote_id == "valid"
    assert decision.rejected[0].reasons == ("quote_expired",)


def test_only_expired_quote_stops_workflow_before_payment(clock) -> None:
    with pytest.raises(ValueError, match="no eligible"):
        SelectionEngine().decide(
            request=normalized_request(),
            budget_units=100,
            candidates=[candidate(clock, "expired", expired=True)],
            now=clock.now(),
        )
