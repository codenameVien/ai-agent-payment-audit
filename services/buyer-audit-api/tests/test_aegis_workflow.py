from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.aa_policy import (
    AA_SCORING_POLICY_VERSION,
    LOCAL_QWEN_PRIORITY_CLASSIFICATION_METHOD,
    PriorityClassification,
    PriorityReason,
    RequestPriority,
)
from buyer_audit_api.core.documents import verify_immutable_document
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.errors import (
    EvidenceImmutabilityError,
    EvidenceIntegrityError,
    SelectionAbortedError,
)
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.domains.ai_inference import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_CATALOG_PATH,
    FIXTURE_PAGE_PATHS,
    load_model_catalog,
)
from buyer_audit_api.domains.ai_inference.aa_models import AaSourceMode, TokenIdentity
from buyer_audit_api.domains.ai_inference.aa_workflow import (
    AegisDecisionWorkflow,
    AegisEvidenceReader,
)

OWNER = "0x0000000000000000000000000000000000000001"
ALPHA = "openai:fixture-openai-alpha:fixture-2026-09-09"
BETA = "anthropic:fixture-anthropic-beta:fixture-2026-09-09"
GAMMA = "google:fixture-google-gamma:fixture-2026-09-09"

#: 100 prompt bytes with no system prompt: ceil(100/4) = 25 estimated input tokens.
PROMPT = "x" * 100
MAX_OUTPUT_TOKENS = 1024

AEGIS_TOKEN = TokenIdentity(
    name="AEGIS",
    symbol="AEGIS",
    decimals=6,
    address="0x0000000000000000000000000000000000000000",
    chain_id=84532,
    status="prepared",
)


class FrozenClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 9, 3, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


@pytest.fixture
def repository() -> InMemoryEvidenceRepository:
    return InMemoryEvidenceRepository()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def purchases(
    repository: InMemoryEvidenceRepository, clock: FrozenClock
) -> PurchaseService:
    return PurchaseService(
        repository=repository,
        cipher=LocalEnvelopeCipher(master_key=b"p" * 32),
        domains=DomainRegistry(
            [AiInferenceDomainModule(default_max_output_tokens=MAX_OUTPUT_TOKENS)]
        ),
        clock=clock,
    )


@pytest.fixture
def workflow(
    repository: InMemoryEvidenceRepository, clock: FrozenClock
) -> AegisDecisionWorkflow:
    return AegisDecisionWorkflow(
        repository=repository,
        clock=clock,
        catalog=load_model_catalog(FIXTURE_CATALOG_PATH),
        source=FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS),
        token=AEGIS_TOKEN,
    )


async def _create(
    purchases: PurchaseService,
    *,
    budget_units: int,
    request: Mapping[str, Any] | None = None,
) -> str:
    created = await purchases.create(
        owner_address=OWNER,
        domain_id="ai_inference",
        raw_request={
            "requestSchema": "aegis-aa-v1",
            "prompt": PROMPT,
            **(request or {}),
        },
        budget_units=budget_units,
        policy={"scoringPolicyVersion": AA_SCORING_POLICY_VERSION},
    )
    return created.purchase_id


async def test_requested_evidence_carries_the_new_normalization(
    purchases: PurchaseService, repository: InMemoryEvidenceRepository
) -> None:
    purchase_id = await _create(purchases, budget_units=5_000)
    events = await repository.list_events(purchase_id)
    normalized = events[0].payload["normalizedRequest"]
    assert normalized["request_schema_version"] == "aegis-aa-v1"
    assert normalized["effective_priority"] == "default"
    assert normalized["priority_reason"] == "no_keyword_match"
    assert normalized["estimated_input_tokens"] == 25
    assert normalized["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert normalized["estimation_method"] == "utf8-bytes-div4-v1"
    assert PROMPT not in str(events[0].payload)


async def test_superseded_priority_names_are_refused_by_the_new_request_api(
    purchases: PurchaseService,
) -> None:
    with pytest.raises(ValueError) as error:
        await _create(purchases, budget_units=5_000, request={"priority": "balanced"})
    assert "intelligence" in str(error.value)


async def test_decision_records_snapshot_then_decision_evidence(
    purchases: PurchaseService,
    workflow: AegisDecisionWorkflow,
    repository: InMemoryEvidenceRepository,
) -> None:
    purchase_id = await _create(purchases, budget_units=5_000)
    decision = await workflow.decide(purchase_id=purchase_id)

    events = await repository.list_events(purchase_id)
    assert [event.type for event in events] == [
        EventType.REQUESTED,
        EventType.AA_SNAPSHOT_RECORDED,
        EventType.DECIDED,
    ]
    snapshot_event = events[1]
    assert snapshot_event.payload["mode"] == AaSourceMode.FIXTURE.value
    assert snapshot_event.payload["catalogProvenance"] == ["fixture"]
    assert snapshot_event.payload["pageCount"] == 2
    assert snapshot_event.evidence_refs == (decision.snapshot_id,)

    document = await repository.get_document(decision.snapshot_id)
    assert document is not None
    verify_immutable_document(document)
    assert document.content_hash == decision.snapshot_hash
    assert snapshot_event.payload["snapshotHash"] == document.content_hash

    decided = events[2].payload
    assert decided["scoringPolicyVersion"] == AA_SCORING_POLICY_VERSION
    assert decided["weights"] == {"price": 40, "completionTime": 30, "intelligence": 30}
    assert decided["token"]["symbol"] == "AEGIS"
    assert decided["token"]["status"] == "prepared"


async def test_local_qwen_priority_reaches_selection_and_deterministic_explanation(
    repository: InMemoryEvidenceRepository,
    clock: FrozenClock,
    workflow: AegisDecisionWorkflow,
) -> None:
    class LocalClassifier:
        def classify(self, *, prompt: str) -> PriorityClassification:
            return PriorityClassification(
                effective=RequestPriority.INTELLIGENCE,
                original=None,
                reason=PriorityReason.LOCAL_MODEL,
                matched_keywords=(),
                matched_priorities=(),
                classification_method=LOCAL_QWEN_PRIORITY_CLASSIFICATION_METHOD,
                classification_model="qwen3.5:4b",
                classification_evidence="intelligence",
            )

    purchases = PurchaseService(
        repository=repository,
        cipher=LocalEnvelopeCipher(master_key=b"p" * 32),
        domains=DomainRegistry(
            [
                AiInferenceDomainModule(
                    default_max_output_tokens=MAX_OUTPUT_TOKENS,
                    priority_classifier=LocalClassifier(),
                )
            ]
        ),
        clock=clock,
    )
    purchase_id = await _create(purchases, budget_units=5_000)
    decision = await workflow.decide(purchase_id=purchase_id)
    assert decision.classification.reason is PriorityReason.LOCAL_MODEL
    assert "로컬 Qwen 요청 의미 분류" in decision.explanation
    events = await repository.list_events(purchase_id)
    priority = events[-1].payload["priority"]
    assert priority["classificationMethod"] == LOCAL_QWEN_PRIORITY_CLASSIFICATION_METHOD
    assert priority["classificationModel"] == "qwen3.5:4b"


async def test_amounts_are_the_exact_ceiling_of_the_aa_prices(
    purchases: PurchaseService, workflow: AegisDecisionWorkflow
) -> None:
    purchase_id = await _create(purchases, budget_units=5_000)
    decision = await workflow.decide(purchase_id=purchase_id)
    amounts = {item.key: item.amount_units for item in decision.candidates}
    # 25*0.40 + 1024*1.60 = 1648.4 -> 1649
    assert amounts[ALPHA] == 1_649
    # 25*1 + 1024*5 = 5145 exactly
    assert amounts[BETA] == 5_145
    # 25*0.30 + 1024*2.5 = 2567.5 -> 2568
    assert amounts[GAMMA] == 2_568
    assert decision.amount_units == amounts[GAMMA]
    assert decision.winner.candidate.entry.provider_id == "google"


async def test_every_candidate_and_rejection_is_preserved(
    purchases: PurchaseService, workflow: AegisDecisionWorkflow
) -> None:
    purchase_id = await _create(purchases, budget_units=3_000)
    decision = await workflow.decide(purchase_id=purchase_id)
    assert {item.key for item in decision.candidates} == {ALPHA, BETA, GAMMA}
    assert {item.candidate_key: item.reasons for item in decision.rejected} == {
        BETA: ("over_budget",)
    }
    assert [item.key for item in decision.eligible] == [GAMMA, ALPHA]
    # Normalization uses only the survivors: the cheapest eligible sets the price scale.
    assert decision.references.min_amount_units == 1_649


@pytest.mark.parametrize(
    ("request_overrides", "expected_provider"),
    [
        ({"priority": "speed"}, "google"),
        ({"priority": "intelligence"}, "google"),
        ({"priority": "price"}, "openai"),
        ({"required_capabilities": ["vision"]}, "anthropic"),
        ({"max_completion_ms": 5_000}, "google"),
        ({"allowed_providers": ["google"]}, "google"),
    ],
)
async def test_request_conditions_change_the_winner(
    purchases: PurchaseService,
    workflow: AegisDecisionWorkflow,
    request_overrides: Mapping[str, Any],
    expected_provider: str,
) -> None:
    purchase_id = await _create(
        purchases,
        budget_units=6_000 if request_overrides.get("required_capabilities") else 5_000,
        request=dict(request_overrides),
    )
    decision = await workflow.decide(purchase_id=purchase_id)
    assert decision.winner.candidate.entry.provider_id == expected_provider


async def test_no_eligible_candidate_aborts_before_any_payment(
    purchases: PurchaseService,
    workflow: AegisDecisionWorkflow,
    repository: InMemoryEvidenceRepository,
) -> None:
    purchase_id = await _create(purchases, budget_units=10)
    with pytest.raises(SelectionAbortedError, match="no_eligible_candidate"):
        await workflow.decide(purchase_id=purchase_id)
    events = await repository.list_events(purchase_id)
    # The captured snapshot stays as evidence; no decision was invented to replace it.
    assert [event.type for event in events] == [
        EventType.REQUESTED,
        EventType.AA_SNAPSHOT_RECORDED,
    ]


async def test_a_second_decision_cannot_overwrite_the_first(
    purchases: PurchaseService,
    workflow: AegisDecisionWorkflow,
    repository: InMemoryEvidenceRepository,
) -> None:
    purchase_id = await _create(purchases, budget_units=5_000)
    first = await workflow.decide(purchase_id=purchase_id)
    with pytest.raises(EvidenceIntegrityError, match="not positioned"):
        await workflow.decide(purchase_id=purchase_id)
    events = await repository.list_events(purchase_id)
    assert [event.type for event in events].count(EventType.DECIDED) == 1
    assert events[2].payload["termsBindingHash"] == first.terms_binding_hash


async def test_the_snapshot_body_cannot_be_rewritten_under_its_id(
    purchases: PurchaseService,
    workflow: AegisDecisionWorkflow,
    repository: InMemoryEvidenceRepository,
) -> None:
    purchase_id = await _create(purchases, budget_units=5_000)
    decision = await workflow.decide(purchase_id=purchase_id)
    stored = await repository.get_document(decision.snapshot_id)
    assert stored is not None
    tampered = type(stored)(
        document_id=stored.document_id,
        purchase_id=stored.purchase_id,
        kind=stored.kind,
        content_hash=stored.content_hash,
        created_at=stored.created_at,
        payload={**stored.payload, "totalModelCount": 99},
    )
    with pytest.raises(EvidenceImmutabilityError):
        await repository.put_document(tampered)
    unchanged = await repository.get_document(decision.snapshot_id)
    assert unchanged is not None
    assert unchanged.payload["totalModelCount"] == 4


async def test_storing_the_same_snapshot_twice_is_idempotent(
    purchases: PurchaseService,
    workflow: AegisDecisionWorkflow,
    repository: InMemoryEvidenceRepository,
) -> None:
    purchase_id = await _create(purchases, budget_units=5_000)
    decision = await workflow.decide(purchase_id=purchase_id)
    stored = await repository.get_document(decision.snapshot_id)
    assert stored is not None
    assert (await repository.put_document(stored)).content_hash == stored.content_hash


async def test_evidence_reader_serves_the_fixed_decision_for_the_runtime(
    purchases: PurchaseService,
    workflow: AegisDecisionWorkflow,
    repository: InMemoryEvidenceRepository,
) -> None:
    purchase_id = await _create(purchases, budget_units=5_000)
    decision = await workflow.decide(purchase_id=purchase_id)
    bundle = await AegisEvidenceReader(repository=repository).decision_evidence(
        purchase_id
    )
    assert bundle["decision"]["amountUnits"] == decision.amount_units
    assert bundle["decision"]["termsBindingHash"] == decision.terms_binding_hash
    assert bundle["snapshotHash"] == decision.snapshot_hash
    assert bundle["snapshot"]["snapshotId"] == decision.snapshot_id
    # The gateway recomputes from the same preserved source values, never from a client.
    assert bundle["snapshot"]["models"][0]["inputPricePerMillion"] == "0.4"


async def test_evidence_reader_refuses_a_purchase_without_a_new_policy_decision(
    purchases: PurchaseService, repository: InMemoryEvidenceRepository
) -> None:
    purchase_id = await _create(purchases, budget_units=5_000)
    with pytest.raises(SelectionAbortedError, match="no recorded decision"):
        await AegisEvidenceReader(repository=repository).decision_evidence(purchase_id)
