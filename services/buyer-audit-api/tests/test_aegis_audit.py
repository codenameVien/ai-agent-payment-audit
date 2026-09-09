from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.audit import AuditEvaluator, AuditSeverity
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.models import EventType, EvidenceEvent
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.domains.ai_inference import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_CATALOG_PATH,
    FIXTURE_PAGE_PATHS,
    load_model_catalog,
)
from buyer_audit_api.domains.ai_inference.aa_models import TokenIdentity
from buyer_audit_api.domains.ai_inference.aa_workflow import AegisDecisionWorkflow

OWNER = "0x0000000000000000000000000000000000000001"
PROMPT = "x" * 100
MAX_OUTPUT_TOKENS = 1024
BETA = "anthropic:fixture-anthropic-beta:fixture-2026-09-09"


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 9, 3, 0, tzinfo=UTC)


async def _decided_events(
    *, budget_units: int = 5_000, request: Mapping[str, Any] | None = None
) -> tuple[list[EvidenceEvent], dict[str, Any]]:
    """A real decision plus the immutable snapshot bodies the audit recomputes from."""
    repository = InMemoryEvidenceRepository()
    clock = FrozenClock()
    purchases = PurchaseService(
        repository=repository,
        cipher=LocalEnvelopeCipher(master_key=b"p" * 32),
        domains=DomainRegistry(
            [AiInferenceDomainModule(default_max_output_tokens=MAX_OUTPUT_TOKENS)]
        ),
        clock=clock,
    )
    created = await purchases.create(
        owner_address=OWNER,
        domain_id="ai_inference",
        raw_request={
            "requestSchema": "aegis-aa-v1",
            "prompt": PROMPT,
            **(request or {}),
        },
        budget_units=budget_units,
        policy={"scoringPolicyVersion": "aa-three-factor-v1"},
    )
    workflow = AegisDecisionWorkflow(
        repository=repository,
        clock=clock,
        catalog=load_model_catalog(FIXTURE_CATALOG_PATH),
        source=FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS),
        token=TokenIdentity(
            name="AEGIS",
            symbol="AEGIS",
            decimals=6,
            address="0x0000000000000000000000000000000000000000",
            chain_id=84532,
        ),
    )
    decision = await workflow.decide(purchase_id=created.purchase_id)
    document = await repository.get_document(decision.snapshot_id)
    assert document is not None
    return (
        await repository.list_events(created.purchase_id),
        {document.document_id: {**document.payload, "snapshotId": document.document_id}},
    )


def _rule_ids(
    events: list[EvidenceEvent], documents: dict[str, Any] | None = None
) -> set[str]:
    return {
        finding.rule_id
        for finding in AuditEvaluator.deterministic_findings(
            events, referenced_documents=documents or {}
        )
    }


def _tamper(
    events: list[EvidenceEvent],
    mutate: Callable[[dict[str, Any]], None],
    *,
    index: int = 2,
) -> list[EvidenceEvent]:
    """Rewrite one stored payload so the recalculation has something to disagree with."""
    payload = copy.deepcopy(events[index].payload)
    mutate(payload)
    return [
        replace(event, payload=payload) if position == index else event
        for position, event in enumerate(events)
    ]


async def test_an_honest_decision_produces_no_risk_finding() -> None:
    events, documents = await _decided_events()
    findings = AuditEvaluator.deterministic_findings(
        events, referenced_documents=documents
    )
    assert [finding.rule_id for finding in findings] == ["AUD-PAYMENT-PENDING"]
    assert findings[0].severity is AuditSeverity.CAUTION


async def test_audit_recomputes_the_filters_of_a_partly_rejected_request() -> None:
    events, documents = await _decided_events(budget_units=3_000)
    assert _rule_ids(events, documents) == {"AUD-PAYMENT-PENDING"}


async def test_a_swapped_winner_is_detected() -> None:
    events, documents = await _decided_events()

    def swap(payload: dict[str, Any]) -> None:
        runner_up = payload["eligible"][1]
        payload["winner"] = runner_up
        payload["amountUnits"] = runner_up["amountUnits"]

    assert "AUD-AA-WINNER-MISMATCH" in _rule_ids(_tamper(events, swap), documents)


async def test_a_rewritten_weight_table_is_detected() -> None:
    events, documents = await _decided_events()

    def rewrite(payload: dict[str, Any]) -> None:
        payload["weights"] = {"price": 90, "completionTime": 5, "intelligence": 5}

    assert "AUD-AA-PRIORITY-WEIGHTS-MISMATCH" in _rule_ids(_tamper(events, rewrite), documents)


async def test_a_rewritten_amount_is_detected() -> None:
    events, documents = await _decided_events()

    def discount(payload: dict[str, Any]) -> None:
        payload["candidates"][0]["amountUnits"] = 1

    assert "AUD-AA-AMOUNT-MISMATCH" in _rule_ids(_tamper(events, discount), documents)


async def test_a_rewritten_completion_time_is_detected() -> None:
    events, documents = await _decided_events()

    def speed_up(payload: dict[str, Any]) -> None:
        payload["candidates"][0]["estimatedCompletionMs"] = "10"

    assert "AUD-AA-COMPLETION-TIME-MISMATCH" in _rule_ids(_tamper(events, speed_up), documents)


async def test_a_dropped_rejection_reason_is_detected() -> None:
    events, documents = await _decided_events(budget_units=3_000)

    def hide(payload: dict[str, Any]) -> None:
        payload["rejected"] = []

    assert "AUD-AA-HARD-FILTER-MISMATCH" in _rule_ids(_tamper(events, hide), documents)


async def test_an_over_budget_candidate_smuggled_into_eligible_is_detected() -> None:
    events, documents = await _decided_events(budget_units=3_000)

    def smuggle(payload: dict[str, Any]) -> None:
        payload["rejected"] = []
        payload["eligible"].append(
            {
                "amountUnits": 4_116,
                "candidateKey": BETA,
                "estimatedCompletionMs": "12500.0",
                "intelligenceIndex": "55.2",
                "modelVersion": "fixture-2026-09-09",
                "providerId": "anthropic",
                "providerModelId": "fixture-anthropic-beta",
                "rank": 3,
                "scores": {
                    "completionTime": "38",
                    "intelligence": "100",
                    "price": "15",
                    "total": "99",
                },
            }
        )

    assert "AUD-AA-HARD-FILTER-MISMATCH" in _rule_ids(_tamper(events, smuggle), documents)


async def test_a_rewritten_component_score_is_detected() -> None:
    events, documents = await _decided_events()

    def inflate(payload: dict[str, Any]) -> None:
        payload["eligible"][0]["scores"]["intelligence"] = "100"

    assert "AUD-AA-SCORE-MISMATCH" in _rule_ids(_tamper(events, inflate), documents)


async def test_a_rewritten_normalization_denominator_is_detected() -> None:
    events, documents = await _decided_events()

    def shift(payload: dict[str, Any]) -> None:
        payload["scoreReferences"]["minAmountUnits"] = 1

    assert "AUD-AA-SCORE-REFERENCE-MISMATCH" in _rule_ids(_tamper(events, shift), documents)


async def test_a_decision_citing_another_snapshot_is_detected() -> None:
    events, documents = await _decided_events()

    def relabel(payload: dict[str, Any]) -> None:
        payload["snapshotHash"] = "sha256:" + "0" * 64

    assert "AUD-AA-SNAPSHOT-BINDING-MISMATCH" in _rule_ids(_tamper(events, relabel), documents)


async def test_an_inflated_token_estimate_is_detected() -> None:
    events, documents = await _decided_events()

    def inflate(payload: dict[str, Any]) -> None:
        payload["tokens"]["estimatedInputTokens"] = 250

    assert "AUD-AA-TOKEN-ESTIMATE-MISMATCH" in _rule_ids(_tamper(events, inflate), documents)


async def test_an_explanation_that_hides_the_real_choice_is_detected() -> None:
    events, documents = await _decided_events()

    def blur(payload: dict[str, Any]) -> None:
        payload["generatedExplanation"] = "가장 좋은 모델을 선택했습니다."

    assert "AUD-EXPLANATION-SELECTION-MISMATCH" in _rule_ids(_tamper(events, blur), documents)


async def test_a_foreign_policy_version_is_refused_for_a_new_request() -> None:
    events, documents = await _decided_events()

    def relabel(payload: dict[str, Any]) -> None:
        payload["scoringPolicyVersion"] = "balanced-preset-v0"

    assert "AUD-AA-POLICY-VERSION-MISMATCH" in _rule_ids(_tamper(events, relabel), documents)


async def test_missing_decision_evidence_is_reported() -> None:
    events, documents = await _decided_events()
    assert _rule_ids(events[:2], documents) == {"AUD-AA-EVIDENCE-INCOMPLETE"}


async def test_a_duplicated_decision_event_is_reported() -> None:
    events, documents = await _decided_events()
    duplicated = [*events, replace(events[2], sequence=4)]
    assert "AUD-AA-DUPLICATE-DECISION-EVENT" in _rule_ids(duplicated, documents)


async def test_a_budget_breaking_decision_is_reported() -> None:
    events, documents = await _decided_events()

    def overspend(payload: dict[str, Any]) -> None:
        payload["amountUnits"] = 9_999_999

    assert "AUD-BUDGET-EXCEEDED" in _rule_ids(_tamper(events, overspend), documents)


def test_a_legacy_purchase_is_not_judged_by_the_new_rules() -> None:
    from buyer_audit_api.core.events import create_event

    legacy = create_event(
        purchase_id="legacy-1",
        sequence=1,
        event_type=EventType.REQUESTED,
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        actor={"id": OWNER, "type": "user"},
        payload={
            "budgetUnits": 100,
            "domain": "ai_inference",
            "normalizedRequest": {"priority": "balanced", "prompt_length": 3},
            "policy": {},
        },
        previous_event_hash=None,
        evidence_refs=(),
    )
    rules = _rule_ids([legacy])
    assert rules == {"AUD-EVIDENCE-INCOMPLETE"}


async def test_a_decision_without_its_aa_snapshot_is_reported() -> None:
    events, documents = await _decided_events()
    without_snapshot = [events[0], events[2]]
    assert "AUD-AA-EVIDENCE-INCOMPLETE" in _rule_ids(without_snapshot, documents)


async def test_evidence_without_a_request_cannot_be_classified_as_new_policy() -> None:
    events, documents = await _decided_events()
    # No REQUESTED means no policy discriminator, so the generic incompleteness rule
    # fires rather than a policy-specific recalculation.
    assert _rule_ids(events[1:], documents) == {"AUD-EVIDENCE-INCOMPLETE"}


async def test_a_candidate_erased_from_the_comparison_is_detected() -> None:
    """Review reproduction: the snapshot, not the decision, defines the candidate set."""
    events, documents = await _decided_events(budget_units=3_000)

    def erase(payload: dict[str, Any]) -> None:
        payload["candidates"] = [
            item for item in payload["candidates"] if item["candidateKey"] != BETA
        ]
        payload["rejected"] = []

    assert "AUD-AA-CANDIDATE-SET-INCOMPLETE" in _rule_ids(
        _tamper(events, erase), documents
    )


async def test_a_price_rewritten_together_with_its_derived_numbers_is_detected() -> None:
    """A self-consistent decision still loses against the preserved AA source values."""
    events, documents = await _decided_events()

    def rewrite(payload: dict[str, Any]) -> None:
        candidate = payload["candidates"][0]
        candidate["source"]["inputPricePerMillion"] = "0.01"
        candidate["source"]["outputPricePerMillion"] = "0.01"
        # 25*0.01 + 1024*0.01 = 10.49 -> 11, so the decision looks internally consistent.
        candidate["amountUnits"] = 11

    rules = _rule_ids(_tamper(events, rewrite), documents)
    assert "AUD-AA-SOURCE-VALUE-MISMATCH" in rules
    assert "AUD-AA-AMOUNT-MISMATCH" in rules


async def test_a_recipient_swapped_after_capture_is_detected() -> None:
    events, documents = await _decided_events()

    def redirect(payload: dict[str, Any]) -> None:
        payload["candidates"][0]["recipient"] = (
            "0x00000000000000000000000000000000000000ff"
        )

    assert "AUD-AA-CATALOG-BINDING-MISMATCH" in _rule_ids(
        _tamper(events, redirect), documents
    )


async def test_a_rewritten_snapshot_body_is_not_used_as_the_audit_input() -> None:
    events, documents = await _decided_events()
    tampered = {
        key: {**body, "models": [{**body["models"][0], "inputPricePerMillion": "0.01"}]}
        for key, body in documents.items()
    }
    # The loader only hands over documents whose hash still describes them, so a rewritten
    # body reaches the evaluator as "unavailable" rather than as trusted input.
    assert "AUD-AA-SOURCE-VALUE-MISSING" in _rule_ids(events, tampered)
    assert "AUD-AA-SNAPSHOT-BODY-UNAVAILABLE" in _rule_ids(events, {})


async def test_an_invented_explicit_priority_is_detected() -> None:
    """Review reproduction: the recorded classification justification is checked too."""
    events, documents = await _decided_events()

    def relabel(payload: dict[str, Any]) -> None:
        payload["normalizedRequest"]["original_priority"] = "price"
        payload["normalizedRequest"]["priority_reason"] = "explicit_priority"

    rules = _rule_ids(_tamper(events, relabel, index=0), documents)
    assert "AUD-AA-PRIORITY-WEIGHTS-MISMATCH" in rules
    assert "AUD-AA-PRIORITY-CLASSIFICATION-INCONSISTENT" in rules


async def test_a_fabricated_match_keyword_is_detected() -> None:
    events, documents = await _decided_events()

    def invent(payload: dict[str, Any]) -> None:
        payload["normalizedRequest"]["priority_reason"] = "keyword_match"
        payload["normalizedRequest"]["effective_priority"] = "price"
        payload["normalizedRequest"]["matched_priorities"] = ["price"]
        payload["normalizedRequest"]["matched_keywords"] = ["아무말"]

    assert "AUD-AA-PRIORITY-KEYWORD-UNKNOWN" in _rule_ids(
        _tamper(events, invent, index=0), documents
    )


async def test_keyword_classification_is_reported_as_not_re_derivable() -> None:
    events, documents = await _decided_events(request={"prompt": "최대한 싸게 해줘"})
    findings = {
        finding.rule_id: finding
        for finding in AuditEvaluator.deterministic_findings(
            events, referenced_documents=documents
        )
    }
    limitation = findings["AUD-AA-PRIORITY-CLASSIFICATION-UNVERIFIABLE"]
    assert limitation.severity is AuditSeverity.CAUTION
    # An explicit priority carries its own proof, so no limitation is reported for it.
    explicit_events, explicit_documents = await _decided_events(
        request={"priority": "price"}
    )
    assert "AUD-AA-PRIORITY-CLASSIFICATION-UNVERIFIABLE" not in _rule_ids(
        explicit_events, explicit_documents
    )


async def test_audit_service_loads_the_snapshot_body_it_recomputes_from() -> None:
    """End to end through the service: the immutable body is fetched and verified."""
    from buyer_audit_api.core.audit import AuditService
    from buyer_audit_api.core.errors import EvidenceImmutabilityError

    repository = InMemoryEvidenceRepository()
    clock = FrozenClock()
    purchases = PurchaseService(
        repository=repository,
        cipher=LocalEnvelopeCipher(master_key=b"p" * 32),
        domains=DomainRegistry(
            [AiInferenceDomainModule(default_max_output_tokens=MAX_OUTPUT_TOKENS)]
        ),
        clock=clock,
    )
    created = await purchases.create(
        owner_address=OWNER,
        domain_id="ai_inference",
        raw_request={"requestSchema": "aegis-aa-v1", "prompt": PROMPT},
        budget_units=5_000,
        policy={"scoringPolicyVersion": "aa-three-factor-v1"},
    )
    workflow = AegisDecisionWorkflow(
        repository=repository,
        clock=clock,
        catalog=load_model_catalog(FIXTURE_CATALOG_PATH),
        source=FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS),
        token=TokenIdentity(
            name="AEGIS",
            symbol="AEGIS",
            decimals=6,
            address="0x0000000000000000000000000000000000000000",
            chain_id=84532,
        ),
    )
    decision = await workflow.decide(purchase_id=created.purchase_id)

    report = await AuditService(repository=repository, clock=clock).audit(
        created.purchase_id
    )
    assert [finding.rule_id for finding in report.findings] == ["AUD-PAYMENT-PENDING"]
    assert report.severity is AuditSeverity.CAUTION

    # The stored body cannot be replaced, which is why the audit may rely on it.
    stored = await repository.get_document(decision.snapshot_id)
    assert stored is not None
    with pytest.raises(EvidenceImmutabilityError):
        await repository.put_document(
            replace(stored, payload={**stored.payload, "totalModelCount": 99})
        )
