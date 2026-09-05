from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.errors import EvidenceIntegrityError, EvidenceTransitionError
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import EventType, EvidenceSource, EvmTransactionRef
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.core.reputation import (
    RawFeedbackEvent,
    ReputationAggregationPolicy,
    ReputationQueryScope,
    ReputationSnapshot,
)
from buyer_audit_api.domains.ai_inference.models import (
    BenchmarkSnapshot,
    NormalizedAiRequest,
    PriorityPreset,
    SellerIdentityEvidence,
    SellerQuote,
)
from buyer_audit_api.domains.ai_inference.module import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.workflow import (
    AiInferenceDecisionWorkflow,
    DeterministicExplanationAdapter,
)


def normalized_request() -> NormalizedAiRequest:
    return NormalizedAiRequest(
        prompt_hash="sha256:" + "0" * 64,
        prompt_length=5,
    )


async def seed_requested(
    repository: InMemoryEvidenceRepository,
    clock,
    *,
    request: NormalizedAiRequest | None = None,
    budget_units: int = 250_000,
) -> NormalizedAiRequest:
    persisted = request or normalized_request()
    await repository.append_event(
        purchase_id="purchase-1",
        event_type=EventType.REQUESTED,
        occurred_at=clock.now(),
        actor={"id": "0xowner", "type": "user"},
        payload={
            "budgetUnits": budget_units,
            "normalizedRequest": persisted.model_dump(mode="json"),
            "policy": {},
        },
    )
    return persisted


class FixedBenchmarks:
    def __init__(self, clock) -> None:
        self.clock = clock

    async def candidates(self, request: NormalizedAiRequest) -> list[BenchmarkSnapshot]:
        del request
        return [
            BenchmarkSnapshot(
                snapshot_id="snapshot-gemini",
                provider_id="gemini",
                model_id="gemini-fast",
                model_version="v1",
                observed_at=self.clock.now() - timedelta(hours=1),
                source_url="https://example.test/gemini",
                content_hash="sha256:gemini",
                quality_score=82,
                speed_score=95,
                reputation_score=88,
                capabilities=("korean",),
            ),
            BenchmarkSnapshot(
                snapshot_id="snapshot-nemotron",
                provider_id="nemotron",
                model_id="nemotron-quality",
                model_version="v1",
                observed_at=self.clock.now() - timedelta(hours=1),
                source_url="https://example.test/nemotron",
                content_hash="sha256:nemotron",
                quality_score=96,
                speed_score=70,
                reputation_score=84,
                capabilities=("korean",),
            ),
        ]


class SignedQuoteClient:
    async def quote(
        self,
        *,
        purchase_id: str,
        request: NormalizedAiRequest,
        benchmark: BenchmarkSnapshot,
    ) -> SellerQuote:
        del request
        return SellerQuote(
            quote_id=f"quote-{benchmark.provider_id}",
            purchase_id=purchase_id,
            seller_agent_id=f"{benchmark.provider_id}-agent",
            erc8004_agent_id="1",
            provider_id=benchmark.provider_id,
            model_id=benchmark.model_id,
            model_version=benchmark.model_version,
            amount_units=100_000 if benchmark.provider_id == "gemini" else 180_000,
            token="0x0000000000000000000000000000000000000002",
            pay_to="0x0000000000000000000000000000000000000003",
            expected_latency_ms=700 if benchmark.provider_id == "gemini" else 1_200,
            input_limit=8_000,
            output_limit=2_000,
            available=True,
            identity_verified=True,
            expires_at=benchmark.observed_at + timedelta(hours=2),
            quote_nonce=f"nonce-{benchmark.provider_id}",
            signature=f"0xsigned-{benchmark.provider_id}",
            signer_address="0x0000000000000000000000000000000000000003",
            chain_id=84532,
            verifying_contract="0x0000000000000000000000000000000000000001",
        )


class AcceptingQuoteVerifier:
    def verify(self, quote: SellerQuote) -> bool:
        return bool(quote.signature)


class AcceptingIdentityVerifier:
    async def verify(self, quote: SellerQuote) -> SellerIdentityEvidence:
        return SellerIdentityEvidence(
            quoteId=quote.quote_id,
            erc8004AgentId=quote.erc8004_agent_id,
            identityRegistry="0x8004A818BFB912233c491871b3d84c89A494BD9e",
            agentWallet=quote.signer_address,
            signerAddress=quote.signer_address,
            identityVerified=True,
        )


class RejectingQuoteVerifier:
    def verify(self, quote: SellerQuote) -> bool:
        return not bool(quote.signature)


REPUTATION_REGISTRY = "0x8004b663056a597dffe9eccc1965a193b7388713"
REPUTATION_CLIENT = "0x0000000000000000000000000000000000000009"
REPUTATION_TX = "0x" + "ab" * 32


class FixedReputation:
    """A `SellerReputationProvider` that answers from a fixed per-agent score table."""

    def __init__(self, clock, scores: dict[str, int] | None = None) -> None:
        self.clock = clock
        self.scores = {"gemini-agent": 96, "nemotron-agent": 4} if scores is None else scores
        self.calls: list[tuple[str, str]] = []
        self.policy = ReputationAggregationPolicy()

    async def snapshot(
        self, *, seller_agent_id: str, erc8004_agent_id: str
    ) -> ReputationSnapshot | None:
        self.calls.append((seller_agent_id, erc8004_agent_id))
        value = self.scores.get(seller_agent_id)
        if value is None:
            return None
        return self.policy.aggregate(
            snapshot_id=f"reputation-{seller_agent_id}",
            seller_agent_id=seller_agent_id,
            scope=ReputationQueryScope(
                chain_id=84532,
                registry_address=REPUTATION_REGISTRY,
                erc8004_agent_id=erc8004_agent_id,
                trusted_clients=(REPUTATION_CLIENT,),
                from_block=1,
                to_block=100,
            ),
            queried_at=self.clock.now(),
            events=(
                RawFeedbackEvent(
                    value=value,
                    value_decimals=0,
                    client_address=REPUTATION_CLIENT,
                    block_number=50,
                    log_index=1,
                    transaction_ref=EvmTransactionRef(
                        hash=REPUTATION_TX, block_number=50, log_index=1
                    ),
                ),
            ),
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
            now=self.clock.now(),
        )


class ExplodingReputation:
    def __init__(self) -> None:
        self.calls = 0

    async def snapshot(
        self, *, seller_agent_id: str, erc8004_agent_id: str
    ) -> ReputationSnapshot | None:
        del seller_agent_id, erc8004_agent_id
        self.calls += 1
        raise RuntimeError("reputation registry unavailable")


class SharedAgentQuoteClient(SignedQuoteClient):
    """Two models quoted by one provider-level seller agent (`P6-AC-06.6`)."""

    async def quote(self, **kwargs) -> SellerQuote:
        quote = await super().quote(**kwargs)
        return quote.model_copy(update={"seller_agent_id": "shared-agent"})


@pytest.mark.asyncio
async def test_request_to_decision_creates_reproducible_evidence(clock) -> None:
    repository = InMemoryEvidenceRepository()
    purchase_service = PurchaseService(
        repository=repository,
        cipher=LocalEnvelopeCipher(master_key=b"p" * 32),
        domains=DomainRegistry([AiInferenceDomainModule()]),
        clock=clock,
    )
    created = await purchase_service.create(
        owner_address="0xowner",
        domain_id="ai_inference",
        raw_request={
            "prompt": "한국어로 요약해줘",
            "priority": "quality",
            "required_capabilities": ["korean"],
        },
        budget_units=250_000,
        policy={"maxTransactionUnits": 1_000_000},
    )
    normalized = NormalizedAiRequest.model_validate(created.event.payload["normalizedRequest"])
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=SignedQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
    )
    decision = await workflow.decide(
        purchase_id=created.purchase_id,
        normalized_request=normalized,
        budget_units=250_000,
    )
    events = await repository.list_events(created.purchase_id)
    assert decision.winner.provider_id == "nemotron"
    assert [event.type for event in events] == [
        EventType.REQUESTED,
        EventType.QUOTED,
        EventType.DECIDED,
    ]
    assert "한국어로 요약해줘" not in str(events[0].payload)
    assert events[1].payload["signedQuotes"][0]["signature"].startswith("0x")
    assert "identity_verified" not in events[1].payload["signedQuotes"][0]
    assert [item["quoteId"] for item in events[1].payload["quoteIdentityEvidence"]] == [
        "quote-gemini",
        "quote-nemotron",
    ]
    assert all(
        item["identityVerified"] is True
        and item["erc8004AgentId"] == "1"
        and item["agentWallet"] == item["signerAddress"]
        for item in events[1].payload["quoteIdentityEvidence"]
    )
    assert events[2].payload["winner"]["quote_id"] == "quote-nemotron"
    verify_event_chain(events)


@pytest.mark.asyncio
async def test_workflow_rejects_quote_bound_to_another_purchase(clock) -> None:
    class WrongPurchaseQuoteClient(SignedQuoteClient):
        async def quote(self, **kwargs) -> SellerQuote:
            quote = await super().quote(**kwargs)
            return quote.model_copy(update={"purchase_id": "purchase-other"})

    repository = InMemoryEvidenceRepository()
    request = await seed_requested(repository, clock)
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=WrongPurchaseQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
    )
    with pytest.raises(ValueError, match="purchase binding"):
        await workflow.decide(
            purchase_id="purchase-1",
            normalized_request=request,
            budget_units=250_000,
        )
    assert [event.type for event in await repository.list_events("purchase-1")] == [
        EventType.REQUESTED
    ]


@pytest.mark.asyncio
async def test_workflow_does_not_store_an_invalid_signature(clock) -> None:
    repository = InMemoryEvidenceRepository()
    request = await seed_requested(repository, clock)
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=SignedQuoteClient(),
        quote_verifier=RejectingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
    )
    with pytest.raises(ValueError, match="invalid seller quote signature"):
        await workflow.decide(
            purchase_id="purchase-1",
            normalized_request=request,
            budget_units=250_000,
        )
    assert [event.type for event in await repository.list_events("purchase-1")] == [
        EventType.REQUESTED
    ]


@pytest.mark.asyncio
async def test_workflow_reloads_and_verifies_persisted_quoted_evidence(clock) -> None:
    class TamperingRepository(InMemoryEvidenceRepository):
        async def list_events(self, purchase_id: str):
            events = await super().list_events(purchase_id)
            for index, event in enumerate(events):
                if event.type == EventType.QUOTED:
                    events[index] = replace(
                        event,
                        payload={**event.payload, "signedQuotes": []},
                    )
            return events

    repository = TamperingRepository()
    request = await seed_requested(repository, clock)
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=SignedQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
    )

    with pytest.raises(EvidenceIntegrityError, match="payload hash"):
        await workflow.decide(
            purchase_id="purchase-1",
            normalized_request=request,
            budget_units=250_000,
        )

    stored = await InMemoryEvidenceRepository.list_events(repository, "purchase-1")
    assert [event.type for event in stored] == [EventType.REQUESTED, EventType.QUOTED]


@pytest.mark.asyncio
async def test_workflow_resumes_after_quote_commit_without_requesting_new_quotes(clock) -> None:
    class FailOnceExplanation(DeterministicExplanationAdapter):
        calls = 0

        async def explain(self, *, deterministic_explanation: str) -> str:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("injected crash after QUOTED")
            return deterministic_explanation

    class CountingQuotes(SignedQuoteClient):
        calls = 0

        async def quote(self, **kwargs) -> SellerQuote:
            self.calls += 1
            return await super().quote(**kwargs)

    repository = InMemoryEvidenceRepository()
    request = await seed_requested(repository, clock)
    quotes = CountingQuotes()
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=quotes,
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=FailOnceExplanation(),
    )
    with pytest.raises(RuntimeError, match="injected crash"):
        await workflow.decide(
            purchase_id="purchase-1",
            normalized_request=request,
            budget_units=250_000,
        )
    assert [event.type for event in await repository.list_events("purchase-1")] == [
        EventType.REQUESTED,
        EventType.QUOTED,
    ]
    decision = await workflow.decide(
        purchase_id="purchase-1",
        normalized_request=request,
        budget_units=250_000,
    )
    assert decision.winner.quote_id == "quote-gemini"
    assert quotes.calls == 2
    assert [event.type for event in await repository.list_events("purchase-1")] == [
        EventType.REQUESTED,
        EventType.QUOTED,
        EventType.DECIDED,
    ]


@pytest.mark.asyncio
async def test_workflow_requires_a_leading_requested_event(clock) -> None:
    repository = InMemoryEvidenceRepository()
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=SignedQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
    )

    with pytest.raises(ValueError, match="missing immutable evidence head"):
        await workflow.decide(
            purchase_id="purchase-1",
            normalized_request=normalized_request(),
            budget_units=250_000,
        )


@pytest.mark.asyncio
async def test_workflow_rejects_inputs_that_differ_from_persisted_requested(clock) -> None:
    repository = InMemoryEvidenceRepository()
    persisted = normalized_request().model_copy(update={"priority": PriorityPreset.QUALITY})
    await seed_requested(repository, clock, request=persisted, budget_units=250_000)
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=SignedQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
    )

    supplied = persisted.model_copy(update={"priority": PriorityPreset.PRICE})
    with pytest.raises(ValueError, match="normalized request does not match"):
        await workflow.decide(
            purchase_id="purchase-1",
            normalized_request=supplied,
            budget_units=100_000,
        )
    assert [event.type for event in await repository.list_events("purchase-1")] == [
        EventType.REQUESTED
    ]


@pytest.mark.asyncio
async def test_workflow_rejects_non_auxiliary_event_before_quote(clock) -> None:
    repository = InMemoryEvidenceRepository()
    request = await seed_requested(repository, clock)
    await repository.append_event(
        purchase_id="purchase-1",
        event_type=EventType.PAYMENT_SETTLED,
        occurred_at=clock.now(),
        actor={"id": "facilitator", "type": "service"},
        payload={"txHash": "0xunexpected"},
    )
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=SignedQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
    )

    with pytest.raises(ValueError, match="not ready for quoting"):
        await workflow.decide(
            purchase_id="purchase-1",
            normalized_request=request,
            budget_units=250_000,
        )

    assert [event.type for event in await repository.list_events("purchase-1")] == [
        EventType.REQUESTED,
        EventType.PAYMENT_SETTLED,
    ]


@pytest.mark.asyncio
async def test_concurrent_decisions_commit_one_quoted_decided_transition(clock) -> None:
    class CoordinatedBenchmarks(FixedBenchmarks):
        def __init__(self, fixed_clock) -> None:
            super().__init__(fixed_clock)
            self.arrivals = 0
            self.lock = asyncio.Lock()
            self.release = asyncio.Event()

        async def candidates(self, request: NormalizedAiRequest) -> list[BenchmarkSnapshot]:
            async with self.lock:
                self.arrivals += 1
                if self.arrivals == 2:
                    self.release.set()
            await self.release.wait()
            return await super().candidates(request)

    repository = InMemoryEvidenceRepository()
    request = await seed_requested(repository, clock)
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=CoordinatedBenchmarks(clock),
        quotes=SignedQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
    )

    results = await asyncio.gather(
        workflow.decide(
            purchase_id="purchase-1",
            normalized_request=request,
            budget_units=250_000,
        ),
        workflow.decide(
            purchase_id="purchase-1",
            normalized_request=request,
            budget_units=250_000,
        ),
        return_exceptions=True,
    )
    failures = [result for result in results if isinstance(result, BaseException)]
    successes = [result for result in results if not isinstance(result, BaseException)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], EvidenceTransitionError)
    assert [event.type for event in await repository.list_events("purchase-1")] == [
        EventType.REQUESTED,
        EventType.QUOTED,
        EventType.DECIDED,
    ]


@pytest.mark.asyncio
async def test_provider_reputation_is_persisted_and_changes_the_winner(clock) -> None:
    """`P6-AC-06.5`: under the quality preset nemotron wins on benchmarks alone.

    `test_request_to_decision_creates_reproducible_evidence` is the control: the same
    quality-preset request without a reputation provider selects nemotron. A high
    agent-level score for gemini and a low one for nemotron must flip that.
    """
    repository = InMemoryEvidenceRepository()
    persisted = normalized_request().model_copy(update={"priority": PriorityPreset.QUALITY})
    request = await seed_requested(repository, clock, request=persisted)
    reputation = FixedReputation(clock)
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=SignedQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
        reputation=reputation,
    )

    decision = await workflow.decide(
        purchase_id="purchase-1",
        normalized_request=request,
        budget_units=250_000,
    )

    events = await repository.list_events("purchase-1")
    snapshots = events[1].payload["reputationSnapshots"]
    assert sorted(item["seller_agent_id"] for item in snapshots) == [
        "gemini-agent",
        "nemotron-agent",
    ]
    gemini = next(item for item in snapshots if item["seller_agent_id"] == "gemini-agent")
    assert gemini["derived_score"] == 96.0
    assert gemini["event_count"] == 1
    assert gemini["freshness"] == "FRESH"
    assert "reputation-gemini-agent" in events[1].evidence_refs
    assert "reputation-nemotron-agent" in events[1].evidence_refs

    assert decision.winner.quote_id == "quote-gemini"
    assert decision.winner.component_scores["reputation"] == 96.0
    assert decision.winner.weights["reputation"] == 10
    assert decision.winner.reputation_snapshot_id == "reputation-gemini-agent"
    assert decision.winner.reputation_snapshot_hash == gemini["snapshot_hash"]
    assert decision.reputation_snapshot_id == "reputation-gemini-agent"

    decided = events[2].payload
    assert decided["reputation_snapshot_id"] == "reputation-gemini-agent"
    assert decided["winner"]["reputation_snapshot_id"] == "reputation-gemini-agent"
    assert decided["winner"]["reputation_snapshot_hash"] == gemini["snapshot_hash"]
    verify_event_chain(events)


@pytest.mark.asyncio
async def test_one_seller_agent_yields_one_snapshot_for_all_its_models(clock) -> None:
    """`P6-AC-06.6`: reputation is provider-level, so it is queried once per agent."""
    repository = InMemoryEvidenceRepository()
    request = await seed_requested(repository, clock)
    reputation = FixedReputation(clock, scores={"shared-agent": 70})
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=SharedAgentQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
        reputation=reputation,
    )

    decision = await workflow.decide(
        purchase_id="purchase-1",
        normalized_request=request,
        budget_units=250_000,
    )

    assert reputation.calls == [("shared-agent", "1")]
    events = await repository.list_events("purchase-1")
    assert [item["snapshot_id"] for item in events[1].payload["reputationSnapshots"]] == [
        "reputation-shared-agent"
    ]
    assert len(decision.eligible) == 2
    assert {item.reputation_snapshot_id for item in decision.eligible} == {
        "reputation-shared-agent"
    }
    assert {item.component_scores["reputation"] for item in decision.eligible} == {70.0}


@pytest.mark.asyncio
async def test_resuming_after_quoted_scores_from_the_persisted_snapshot(clock) -> None:
    class FailOnceExplanation(DeterministicExplanationAdapter):
        calls = 0

        async def explain(self, *, deterministic_explanation: str) -> str:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("injected crash after QUOTED")
            return deterministic_explanation

    repository = InMemoryEvidenceRepository()
    request = await seed_requested(repository, clock)
    reputation = FixedReputation(clock)
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=SignedQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=FailOnceExplanation(),
        reputation=reputation,
    )
    with pytest.raises(RuntimeError, match="injected crash"):
        await workflow.decide(
            purchase_id="purchase-1",
            normalized_request=request,
            budget_units=250_000,
        )
    assert reputation.calls == [("gemini-agent", "1"), ("nemotron-agent", "1")]

    reputation.calls.clear()
    decision = await workflow.decide(
        purchase_id="purchase-1",
        normalized_request=request,
        budget_units=250_000,
    )

    # The committed QUOTED snapshot is the evidence; the registry is never re-queried.
    assert reputation.calls == []
    assert decision.winner.quote_id == "quote-gemini"
    assert decision.winner.component_scores["reputation"] == 96.0
    assert decision.winner.reputation_snapshot_id == "reputation-gemini-agent"
    nemotron = next(item for item in decision.eligible if item.quote_id == "quote-nemotron")
    assert nemotron.component_scores["reputation"] == 4.0


@pytest.mark.asyncio
async def test_a_failing_reputation_provider_falls_back_to_neutral_fifty(clock) -> None:
    repository = InMemoryEvidenceRepository()
    request = await seed_requested(repository, clock)
    reputation = ExplodingReputation()
    workflow = AiInferenceDecisionWorkflow(
        repository=repository,
        clock=clock,
        benchmarks=FixedBenchmarks(clock),
        quotes=SignedQuoteClient(),
        quote_verifier=AcceptingQuoteVerifier(),
        identity_verifier=AcceptingIdentityVerifier(),
        explanation=DeterministicExplanationAdapter(),
        reputation=reputation,
    )

    decision = await workflow.decide(
        purchase_id="purchase-1",
        normalized_request=request,
        budget_units=250_000,
    )

    assert reputation.calls == 2
    events = await repository.list_events("purchase-1")
    assert events[1].payload["reputationSnapshots"] == []
    assert [item.component_scores["reputation"] for item in decision.eligible] == [50.0, 50.0]
    assert decision.winner.reputation_snapshot_id is None
    assert decision.reputation_snapshot_id is None
    assert [event.type for event in events] == [
        EventType.REQUESTED,
        EventType.QUOTED,
        EventType.DECIDED,
    ]
