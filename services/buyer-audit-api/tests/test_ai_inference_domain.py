from __future__ import annotations

from datetime import timedelta

import pytest

from buyer_audit_api.domains.ai_inference.models import (
    BenchmarkSnapshot,
    Candidate,
    NormalizedAiRequest,
    PriorityPreset,
    SellerQuote,
)
from buyer_audit_api.domains.ai_inference.module import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.selection import WEIGHT_PRESETS, SelectionEngine


def normalized_request(**overrides) -> NormalizedAiRequest:
    return NormalizedAiRequest.model_validate(
        {
            "prompt_hash": "sha256:" + "0" * 64,
            "prompt_length": 4,
            **overrides,
        }
    )


def make_candidate(
    *,
    clock,
    quote_id: str,
    provider: str,
    amount: int,
    quality: float,
    speed: float,
    available: bool = True,
    identity_verified: bool = True,
    age_hours: int = 1,
) -> Candidate:
    return Candidate(
        quote=SellerQuote(
            quote_id=quote_id,
            purchase_id="purchase-1",
            seller_agent_id=f"{provider}-agent",
            erc8004_agent_id="1",
            provider_id=provider,
            model_id=f"{provider}-model",
            model_version="1",
            amount_units=amount,
            token="0xtoken",
            pay_to="0xseller",
            expected_latency_ms=500,
            input_limit=10_000,
            output_limit=2_000,
            available=available,
            identity_verified=identity_verified,
            expires_at=clock.now() + timedelta(minutes=5),
            quote_nonce=f"nonce-{quote_id}",
            signature="0xtest-signature",
            signer_address="0xseller-wallet",
            chain_id=84532,
            verifying_contract="0x0000000000000000000000000000000000000001",
        ),
        benchmark=BenchmarkSnapshot(
            snapshot_id=f"snapshot-{quote_id}",
            provider_id=provider,
            model_id=f"{provider}-model",
            model_version="1",
            observed_at=clock.now() - timedelta(hours=age_hours),
            source_url="https://example.test/benchmark",
            content_hash=f"sha256:{quote_id}",
            quality_score=quality,
            speed_score=speed,
            reputation_score=80,
            capabilities=("chat", "json"),
        ),
    )


def test_normalizer_is_deterministic_and_deduplicates() -> None:
    module = AiInferenceDomainModule()
    first = module.normalize_request(
        {
            "query": "분석해줘",
            "priority": "quality",
            "required_capabilities": ["JSON", "chat", "json"],
            "allowed_sellers": ["Nemotron", "gemini", "nemotron"],
        }
    )
    second = module.normalize_request(
        {
            "prompt": "분석해줘",
            "priority": "quality",
            "required_capabilities": ["chat", "json"],
            "allowed_sellers": ["gemini", "nemotron"],
        }
    )
    assert first == second
    assert "분석해줘" not in str(first)
    assert first["prompt_hash"].startswith("sha256:")


def test_weight_presets_match_approved_requirements() -> None:
    assert WEIGHT_PRESETS[PriorityPreset.BALANCED] == {
        "quality": 40,
        "price": 25,
        "speed": 20,
        "reputation": 10,
        "freshness": 5,
    }
    assert all(sum(weights.values()) == 100 for weights in WEIGHT_PRESETS.values())


def test_priority_changes_winner_but_remains_deterministic(clock) -> None:
    engine = SelectionEngine()
    candidates = [
        make_candidate(
            clock=clock,
            quote_id="quality",
            provider="gemini",
            amount=9,
            quality=98,
            speed=40,
        ),
        make_candidate(
            clock=clock,
            quote_id="cheap-fast",
            provider="nemotron",
            amount=2,
            quality=72,
            speed=95,
        ),
    ]
    quality = engine.decide(
        request=normalized_request(priority="quality"),
        budget_units=10,
        candidates=candidates,
        now=clock.now(),
    )
    price = engine.decide(
        request=normalized_request(priority="price"),
        budget_units=10,
        candidates=candidates,
        now=clock.now(),
    )
    assert quality.winner.quote_id == "quality"
    assert price.winner.quote_id == "cheap-fast"
    assert price == engine.decide(
        request=normalized_request(priority="price"),
        budget_units=10,
        candidates=candidates,
        now=clock.now(),
    )


def test_hard_filters_preserve_all_reasons(clock) -> None:
    engine = SelectionEngine()
    valid = make_candidate(
        clock=clock,
        quote_id="valid",
        provider="gemini",
        amount=5,
        quality=80,
        speed=80,
    )
    invalid = make_candidate(
        clock=clock,
        quote_id="invalid",
        provider="nemotron",
        amount=50,
        quality=100,
        speed=100,
        available=False,
        identity_verified=False,
        age_hours=25,
    )
    decision = engine.decide(
        request=normalized_request(
            allowed_sellers=("gemini",),
            required_capabilities=("json",),
        ),
        budget_units=10,
        candidates=[invalid, valid],
        now=clock.now(),
    )
    assert decision.winner.quote_id == "valid"
    assert set(decision.rejected[0].reasons) >= {
        "unavailable",
        "identity_unverified",
        "over_budget",
        "benchmark_stale",
        "seller_not_allowed",
    }


def test_model_version_must_match_benchmark(clock) -> None:
    candidate = make_candidate(
        clock=clock,
        quote_id="mismatch",
        provider="gemini",
        amount=5,
        quality=80,
        speed=80,
    )
    candidate = candidate.model_copy(
        update={"quote": candidate.quote.model_copy(update={"model_version": "other"})}
    )
    with pytest.raises(ValueError, match="no eligible"):
        SelectionEngine().decide(
            request=normalized_request(),
            budget_units=10,
            candidates=[candidate],
            now=clock.now(),
        )


def test_no_eligible_candidate_stops_before_payment(clock) -> None:
    with pytest.raises(ValueError, match="no eligible"):
        SelectionEngine().decide(
            request=normalized_request(),
            budget_units=1,
            candidates=[
                make_candidate(
                    clock=clock,
                    quote_id="expensive",
                    provider="gemini",
                    amount=2,
                    quality=100,
                    speed=100,
                )
            ],
            now=clock.now(),
        )
