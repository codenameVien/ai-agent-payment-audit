from __future__ import annotations

from datetime import timedelta

import pytest

from buyer_audit_api.domains.ai_inference.models import (
    BenchmarkSnapshot,
    Candidate,
    NormalizedAiRequest,
    PriorityPreset,
    ReputationScoreEvidence,
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


def reputation_evidence(*, clock, agent: str, score: float) -> ReputationScoreEvidence:
    return ReputationScoreEvidence(
        snapshot_id=f"reputation-{agent}",
        seller_agent_id=agent,
        erc8004_agent_id="1",
        derived_score=score,
        event_count=1,
        freshness="FRESH",
        evidence_source="BASE_SEPOLIA_VERIFIED",
        queried_at=clock.now(),
        snapshot_hash=f"sha256:reputation-{agent}",
        aggregation_method="ARITHMETIC_MEAN_V1",
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
    reputation: ReputationScoreEvidence | None = None,
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
        reputation=reputation,
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



def test_provider_reputation_decides_between_otherwise_equal_candidates(clock) -> None:
    """`P6-AC-06.5`: the agent-level score is the only difference, so it must decide."""
    low = make_candidate(
        clock=clock,
        quote_id="alpha",
        provider="alpha",
        amount=5,
        quality=80,
        speed=80,
        reputation=reputation_evidence(clock=clock, agent="alpha-agent", score=10),
    )
    high = make_candidate(
        clock=clock,
        quote_id="zeta",
        provider="zeta",
        amount=5,
        quality=80,
        speed=80,
        reputation=reputation_evidence(clock=clock, agent="zeta-agent", score=90),
    )
    decision = SelectionEngine().decide(
        request=normalized_request(),
        budget_units=10,
        candidates=[low, high],
        now=clock.now(),
    )

    # Every tie-break key (amount, provider, model, quote id) favours "alpha".
    assert decision.winner.quote_id == "zeta"
    assert decision.winner.component_scores["reputation"] == 90.0
    assert decision.winner.reputation_snapshot_id == "reputation-zeta-agent"
    assert decision.winner.reputation_snapshot_hash == "sha256:reputation-zeta-agent"
    assert decision.reputation_snapshot_id == "reputation-zeta-agent"
    scored = {item.quote_id: item for item in decision.eligible}
    assert scored["alpha"].component_scores["reputation"] == 10.0


def test_a_candidate_without_reputation_evidence_scores_neutral_fifty(clock) -> None:
    candidate = make_candidate(
        clock=clock,
        quote_id="unqueried",
        provider="gemini",
        amount=5,
        quality=80,
        speed=80,
    )
    assert candidate.reputation is None
    assert candidate.benchmark.reputation_score == 80

    decision = SelectionEngine().decide(
        request=normalized_request(),
        budget_units=10,
        candidates=[candidate],
        now=clock.now(),
    )

    # The legacy benchmark field is a historical read (design 18.5.4), never a score.
    assert decision.winner.component_scores["reputation"] == 50.0
    assert decision.winner.reputation_snapshot_id is None
    assert decision.winner.reputation_snapshot_hash is None
    assert decision.reputation_snapshot_id is None


def test_reputation_never_rescues_a_hard_filtered_candidate(clock) -> None:
    """`P6-AC-06.4`: reputation is a weighted component, never a filter override."""
    ineligible = make_candidate(
        clock=clock,
        quote_id="perfect-but-unavailable",
        provider="nemotron",
        amount=5,
        quality=100,
        speed=100,
        available=False,
        reputation=reputation_evidence(clock=clock, agent="nemotron-agent", score=100),
    )
    eligible = make_candidate(
        clock=clock,
        quote_id="available-but-unloved",
        provider="gemini",
        amount=5,
        quality=60,
        speed=60,
        reputation=reputation_evidence(clock=clock, agent="gemini-agent", score=0),
    )
    decision = SelectionEngine().decide(
        request=normalized_request(),
        budget_units=10,
        candidates=[ineligible, eligible],
        now=clock.now(),
    )

    assert decision.winner.quote_id == "available-but-unloved"
    assert decision.winner.component_scores["reputation"] == 0.0
    assert [item.quote_id for item in decision.rejected] == ["perfect-but-unavailable"]
    assert decision.rejected[0].reasons == ("unavailable",)
    assert "perfect-but-unavailable" not in {item.quote_id for item in decision.eligible}