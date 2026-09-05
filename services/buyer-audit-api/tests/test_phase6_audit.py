from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.audit import (
    LEGACY_RULESET_VERSION,
    RULESET_VERSION,
    AuditAuthority,
    AuditEvaluator,
    AuditFinding,
    AuditReportReader,
    AuditService,
    AuditSeverity,
    finding_from_payload,
    finding_payload,
    is_final_eligible,
)
from buyer_audit_api.core.errors import EvidenceIntegrityError
from buyer_audit_api.core.events import create_event, verify_event_chain
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceSource,
    JsonObject,
    LocalTransactionRef,
    ScenarioMetadata,
)
from buyer_audit_api.core.projections import AuditStatus, PaymentStatus, PurchaseProjectionService

PURCHASE_ID = "purchase-audit"
OWNER = "0x0000000000000000000000000000000000000001"
BUYER = "0x0000000000000000000000000000000000000002"
TOKEN = "0x0000000000000000000000000000000000000003"
SELLER = "0x0000000000000000000000000000000000000004"
OTHER_TOKEN = "0x0000000000000000000000000000000000000006"
WRONG_RECIPIENT = "0x0000000000000000000000000000000000000007"
RUN_ID = "a" * 32
LOCAL_TX_ID = f"localtx:{RUN_ID}:payment:000001"
EVM_TX = "0x" + "ab" * 32
QUOTED_UNITS = 100_000
DECIDED_AT = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
SCENARIO = ScenarioMetadata(
    run_id=RUN_ID,
    scenario_id="P6-A04-WRONG-AMOUNT",
    catalog_version="phase6.v1",
    catalog_hash="sha256:" + "cd" * 32,
)

EVALUATOR = AuditEvaluator()
READER = AuditReportReader()
PROJECTIONS = PurchaseProjectionService()


def chain(*specs: tuple[EventType, JsonObject]) -> list[EvidenceEvent]:
    events: list[EvidenceEvent] = []
    previous: str | None = None
    for index, (event_type, payload) in enumerate(specs, start=1):
        event = create_event(
            purchase_id=PURCHASE_ID,
            sequence=index,
            event_type=event_type,
            occurred_at=DECIDED_AT,
            actor={"id": OWNER if index == 1 else "payment-executor", "type": "service"},
            payload=payload,
            previous_event_hash=previous,
            evidence_refs=(),
        )
        previous = event.event_hash
        events.append(event)
    verify_event_chain(events)
    return events


def signed_quote(quote_id: str, *, amount_units: int = QUOTED_UNITS) -> JsonObject:
    return {
        "quote_id": quote_id,
        "purchase_id": PURCHASE_ID,
        "seller_agent_id": f"seller-{quote_id}",
        "provider_id": quote_id,
        "model_id": "model-a",
        "model_version": "v1",
        "amount_units": amount_units,
        "token": TOKEN,
        "pay_to": SELLER,
        "available": True,
        "expires_at": (DECIDED_AT + timedelta(hours=1)).isoformat(),
        "signer_address": SELLER,
        "chain_id": 84532,
        "verifying_contract": SELLER,
        "input_limit": 1_000,
        "output_limit": 1_000,
        "expected_latency_ms": 100,
    }


def benchmark(quote_id: str) -> JsonObject:
    return {
        "snapshot_id": f"snapshot-{quote_id}",
        "provider_id": quote_id,
        "model_id": "model-a",
        "model_version": "v1",
        "observed_at": DECIDED_AT.isoformat(),
        "capabilities": ["text"],
    }


BALANCED_WEIGHTS = {
    "quality": 40,
    "price": 25,
    "speed": 20,
    "reputation": 10,
    "freshness": 5,
}


def ai_requested() -> JsonObject:
    return {
        "budgetUnits": 250_000,
        "domain": "ai_inference",
        "policy": {},
        "normalizedRequest": {
            "priority": "balanced",
            "allowed_sellers": [],
            "required_capabilities": ["text"],
            "min_input_limit": 10,
            "min_output_limit": 10,
            "max_latency_ms": 1_000,
        },
    }


def ai_quoted(quote_ids: tuple[str, ...]) -> JsonObject:
    return {
        "signedQuotes": [signed_quote(quote_id) for quote_id in quote_ids],
        "benchmarkSnapshots": [benchmark(quote_id) for quote_id in quote_ids],
        "quoteIdentityEvidence": [
            {"quoteId": quote_id, "erc8004AgentId": "1", "identityVerified": True}
            for quote_id in quote_ids
        ],
    }


def ai_decided(
    *,
    winner: str,
    eligible: tuple[tuple[str, int], ...],
    rejected: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> JsonObject:
    winner_score = max(score for _, score in eligible)
    return {
        "preset": "balanced",
        "winner": {
            "quote_id": winner,
            "provider_id": winner,
            "model_id": "model-a",
            "total_score": winner_score,
        },
        "eligible": [
            {
                "quote_id": quote_id,
                "provider_id": quote_id,
                "model_id": "model-a",
                "total_score": score,
                "weights": BALANCED_WEIGHTS,
            }
            for quote_id, score in eligible
        ],
        "rejected": [
            {"quote_id": quote_id, "reasons": list(reasons)} for quote_id, reasons in rejected
        ],
        "generatedExplanation": f"balanced preset selected {winner} model-a",
    }


def claim_payload() -> JsonObject:
    return {
        "amountUnits": QUOTED_UNITS,
        "buyerWalletAddress": BUYER,
        "decisionEventHash": "sha256:" + "55" * 32,
        "payTo": SELLER,
        "quoteId": "gemini",
        "token": TOKEN,
        "transferMethod": "eip3009",
    }


def mismatch_payload(*, amount_units: int, token: str, recipient: str) -> JsonObject:
    return {
        "actualTransfer": {
            "amountUnits": amount_units,
            "from": BUYER,
            "to": recipient,
            "token": token,
        },
        "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
        "mismatchedFields": [],
        "proofRef": "sha256:" + "11" * 32,
        "quoteBinding": {"amountUnits": QUOTED_UNITS, "payTo": SELLER, "token": TOKEN},
        "reconciliationAttempts": 1,
        "scenario": SCENARIO.to_payload(),
        "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
        "transactionRef": LocalTransactionRef(id=LOCAL_TX_ID, run_id=RUN_ID).to_payload(),
    }


def reconciliation_check_payload(attempt: int, outcome: str) -> JsonObject:
    return {
        "attemptNumber": attempt,
        "checkedAt": DECIDED_AT.isoformat(),
        "checkedChainId": 84532,
        "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
        "finalityConfirmations": 3,
        "proofRef": f"sha256:{'2' * 63}{attempt}",
        "scenario": SCENARIO.to_payload(),
        "submissionRef": LOCAL_TX_ID,
        "verifierOutcome": outcome,
    }


def no_transfer_payload() -> JsonObject:
    return {
        "attemptCount": 3,
        "authorizationNonceHash": "sha256:" + "44" * 32,
        "checkedChainId": 84532,
        "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
        "finalityEvidence": {"confirmations": 3},
        "firstCheckedAt": DECIDED_AT.isoformat(),
        "lastCheckedAt": DECIDED_AT.isoformat(),
        "proofRef": "sha256:" + "33" * 32,
        "reasonCode": "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER",
        "scenario": SCENARIO.to_payload(),
        "submissionRef": LOCAL_TX_ID,
        "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
    }


def ai_prefix(
    *,
    eligible: tuple[tuple[str, int], ...] = (("gemini", 90),),
    rejected: tuple[tuple[str, tuple[str, ...]], ...] = (),
    quote_ids: tuple[str, ...] = ("gemini",),
) -> tuple[tuple[EventType, JsonObject], ...]:
    return (
        (EventType.REQUESTED, ai_requested()),
        (EventType.QUOTED, ai_quoted(quote_ids)),
        (
            EventType.DECIDED,
            ai_decided(winner="gemini", eligible=eligible, rejected=rejected),
        ),
    )


def rule_ids(findings: tuple[AuditFinding, ...]) -> set[str]:
    return {item.rule_id for item in findings}


def by_rule(findings: tuple[AuditFinding, ...], rule_id: str) -> AuditFinding:
    matches = [item for item in findings if item.rule_id == rule_id]
    assert len(matches) == 1, f"expected exactly one {rule_id}"
    return matches[0]


def test_structured_findings_serialize_every_canonical_field() -> None:
    finding = AuditFinding(
        rule_id="AUD-QUOTE-PAYMENT-MISMATCH",
        severity=AuditSeverity.RISK,
        title="title",
        detail="detail",
        evidence_refs=("sha256:" + "aa" * 32,),
        expected={"amountUnits": QUOTED_UNITS},
        observed={"amountUnits": 200_000},
        mismatched_fields=("amount",),
    )

    payload = finding_payload(finding)

    assert payload["ruleId"] == "AUD-QUOTE-PAYMENT-MISMATCH"
    assert payload["code"] == payload["ruleId"]
    assert payload["rulesetVersion"] == RULESET_VERSION
    assert payload["authority"] == AuditAuthority.DETERMINISTIC.value
    assert payload["expected"] == {"amountUnits": QUOTED_UNITS}
    assert payload["observed"] == {"amountUnits": 200_000}
    assert payload["mismatchedFields"] == ["amount"]
    assert finding_from_payload(payload) == finding


def test_legacy_finding_documents_still_parse_without_rewriting() -> None:
    legacy = {
        "authority": "deterministic",
        "code": "AUD-PAYMENT-FAILED",
        "detail": "legacy detail",
        "evidenceRefs": ["sha256:" + "bb" * 32],
        "severity": "RISK",
        "title": "legacy title",
    }

    parsed = finding_from_payload(legacy)

    assert parsed.rule_id == "AUD-PAYMENT-FAILED"
    assert parsed.code == "AUD-PAYMENT-FAILED"
    assert parsed.authority is AuditAuthority.DETERMINISTIC
    assert parsed.ruleset_version == LEGACY_RULESET_VERSION
    assert parsed.expected is None
    assert parsed.mismatched_fields == ()


def test_legacy_semantic_authority_maps_to_the_advisory_enum() -> None:
    parsed = finding_from_payload(
        {
            "authority": "semantic",
            "code": "SEM-REQUEST-RATIONALE-UNCERTAIN",
            "detail": "advisory",
            "severity": "CAUTION",
            "title": "advisory",
        }
    )
    assert parsed.authority is AuditAuthority.SEMANTIC_ADVISORY


MISMATCH_CASES = [
    ("amount", {"amount_units": 200_000, "token": TOKEN, "recipient": SELLER}, ("amount",)),
    (
        "token",
        {"amount_units": QUOTED_UNITS, "token": OTHER_TOKEN, "recipient": SELLER},
        ("token",),
    ),
    (
        "recipient",
        {"amount_units": QUOTED_UNITS, "token": TOKEN, "recipient": WRONG_RECIPIENT},
        ("recipient",),
    ),
]


@pytest.mark.parametrize(
    ("label", "actual", "expected_fields"),
    MISMATCH_CASES,
    ids=[case[0] for case in MISMATCH_CASES],
)
def test_quote_payment_mismatch_uses_the_verified_actual_proof(
    label: str, actual: dict[str, object], expected_fields: tuple[str, ...]
) -> None:
    del label
    events = chain(
        *ai_prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
        (EventType.PAYMENT_MISMATCH_CONFIRMED, mismatch_payload(**actual)),  # type: ignore[arg-type]
    )

    findings = EVALUATOR.deterministic_findings(events)
    finding = by_rule(findings, "AUD-QUOTE-PAYMENT-MISMATCH")

    assert finding.mismatched_fields == expected_fields
    assert finding.severity is AuditSeverity.RISK
    assert finding.authority is AuditAuthority.DETERMINISTIC
    assert finding.expected == {
        "amountUnits": QUOTED_UNITS,
        "quoteId": "gemini",
        "recipient": SELLER,
        "token": TOKEN,
    }
    assert finding.observed is not None
    assert finding.observed["actualTransfer"]["amountUnits"] == actual["amount_units"]
    assert events[-1].event_hash in finding.evidence_refs
    assert "AUD-PAYMENT-PENDING" not in rule_ids(findings)
    assert "AUD-PAYMENT-UNCONFIRMED" not in rule_ids(findings)


def test_exact_settlement_produces_no_quote_payment_mismatch() -> None:
    events = chain(
        *ai_prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (
            EventType.PAYMENT_SETTLED,
            {
                "amountUnits": QUOTED_UNITS,
                "blockNumber": 1,
                "from": BUYER,
                "receiptStatus": 1,
                "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
                "to": SELLER,
                "token": TOKEN,
                "transactionHash": EVM_TX,
                "transferLogIndex": 0,
            },
        ),
        (
            EventType.DELIVERED,
            {
                "sellerAgentId": "seller-gemini",
                "providerId": "gemini",
                "modelId": "model-a",
                "modelVersion": "v1",
                "responseHash": "sha256:response",
                "responseId": "response-1",
            },
        ),
    )

    findings = EVALUATOR.deterministic_findings(events)

    assert rule_ids(findings) == set()
    assert EVALUATOR.evaluate(events).severity is AuditSeverity.NORMAL


def test_claimed_intent_mismatch_is_still_detected_without_a_terminal_proof() -> None:
    claimed = claim_payload()
    claimed["amountUnits"] = 999
    events = chain(
        *ai_prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claimed),
    )

    finding = by_rule(
        EVALUATOR.deterministic_findings(events), "AUD-QUOTE-PAYMENT-MISMATCH"
    )

    assert finding.mismatched_fields == ("amount",)
    assert finding.observed is not None
    assert finding.observed["claimedIntent"]["amountUnits"] == 999


def test_no_transfer_terminal_yields_risk_rule_and_projection_pair() -> None:
    events = chain(
        *ai_prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
        (
            EventType.PAYMENT_RECONCILIATION_CHECKED,
            reconciliation_check_payload(1, "AUTHORIZATION_UNUSED_AFTER_EXPIRY"),
        ),
        (EventType.PAYMENT_RECONCILED_NO_TRANSFER, no_transfer_payload()),
    )

    findings = EVALUATOR.deterministic_findings(events)
    draft = EVALUATOR.evaluate(events)
    projection = PROJECTIONS.project(events)

    assert "AUD-PAYMENT-RECONCILED-NO-TRANSFER" in rule_ids(findings)
    assert "AUD-FACILITATOR-SUCCESS-WITHOUT-TRANSFER" not in rule_ids(findings)
    assert "AUD-PAYMENT-FAILED" not in rule_ids(findings)
    assert draft.severity is AuditSeverity.RISK
    assert draft.final_eligible is True
    assert projection.payment_status is PaymentStatus.RECONCILED_NO_TRANSFER


def test_facilitator_success_without_transfer_adds_its_own_rule() -> None:
    events = chain(
        *ai_prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "facilitator claimed success"}),
        (
            EventType.PAYMENT_RECONCILIATION_CHECKED,
            reconciliation_check_payload(1, "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER"),
        ),
        (EventType.PAYMENT_RECONCILED_NO_TRANSFER, no_transfer_payload()),
    )

    findings = EVALUATOR.deterministic_findings(events)

    assert {
        "AUD-FACILITATOR-SUCCESS-WITHOUT-TRANSFER",
        "AUD-PAYMENT-RECONCILED-NO-TRANSFER",
    }.issubset(rule_ids(findings))
    facilitator = by_rule(findings, "AUD-FACILITATOR-SUCCESS-WITHOUT-TRANSFER")
    assert facilitator.observed == {"matchingTransfers": 0, "checkedAttempts": 1}


def test_intermediate_reconciliation_state_is_unknown_and_pending_audit() -> None:
    events = chain(
        *ai_prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
        (
            EventType.PAYMENT_RECONCILIATION_CHECKED,
            reconciliation_check_payload(1, "RECEIPT_NOT_FOUND"),
        ),
    )

    draft = EVALUATOR.evaluate(events)
    projection = PROJECTIONS.project(events)

    assert draft.final_eligible is False
    assert is_final_eligible(events) is False
    assert draft.severity is AuditSeverity.CAUTION
    assert rule_ids(draft.findings) == {"AUD-PAYMENT-UNCONFIRMED"}
    assert projection.payment_status is PaymentStatus.PAYMENT_CONFIRMATION_UNKNOWN
    assert projection.audit_status is AuditStatus.PENDING_AUDIT


def test_rejected_payment_attempts_produce_duplicate_and_nonce_reuse_rules() -> None:
    events = chain(
        *ai_prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (
            EventType.PAYMENT_ATTEMPT_REJECTED,
            {
                "attemptId": "attempt-2",
                "authorizationNonceHash": "sha256:" + "77" * 32,
                "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
                "guardOutcome": "REJECTED",
                "reasonCode": "ERC3009_NONCE_REUSE",
                "scenario": SCENARIO.to_payload(),
            },
        ),
        (
            EventType.PAYMENT_SETTLED,
            {
                "amountUnits": QUOTED_UNITS,
                "blockNumber": 1,
                "from": BUYER,
                "receiptStatus": 1,
                "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
                "to": SELLER,
                "token": TOKEN,
                "transactionHash": EVM_TX,
                "transferLogIndex": 0,
                "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
                "scenario": SCENARIO.to_payload(),
            },
        ),
        (
            EventType.DELIVERED,
            {
                "sellerAgentId": "seller-gemini",
                "providerId": "gemini",
                "modelId": "model-a",
                "modelVersion": "v1",
                "responseHash": "sha256:response",
                "responseId": "response-1",
            },
        ),
    )

    findings = EVALUATOR.deterministic_findings(events)

    assert {"AUD-DUPLICATE-PAYMENT-ATTEMPT", "AUD-ERC3009-NONCE-REUSE"}.issubset(
        rule_ids(findings)
    )
    duplicate = by_rule(findings, "AUD-DUPLICATE-PAYMENT-ATTEMPT")
    assert duplicate.observed == {
        "rejectedAttempts": 1,
        "reasonCodes": ["ERC3009_NONCE_REUSE"],
    }


def test_eligible_candidate_excluded_is_reported_next_to_the_filter_mismatch() -> None:
    events = chain(
        *ai_prefix(
            quote_ids=("gemini", "nemotron"),
            eligible=(("gemini", 90),),
            rejected=(("nemotron", ("over_budget",)),),
        ),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
    )

    findings = EVALUATOR.deterministic_findings(events)

    assert {"AUD-ELIGIBLE-CANDIDATE-EXCLUDED", "AUD-HARD-FILTER-MISMATCH"}.issubset(
        rule_ids(findings)
    )
    excluded = by_rule(findings, "AUD-ELIGIBLE-CANDIDATE-EXCLUDED")
    assert excluded.observed is not None
    assert excluded.observed["excludedQuoteIds"] == ["nemotron"]
    assert excluded.severity is AuditSeverity.RISK


def test_faithful_two_candidate_decision_has_no_exclusion_finding() -> None:
    events = chain(
        *ai_prefix(quote_ids=("gemini", "nemotron"), eligible=(("gemini", 90), ("nemotron", 80))),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
    )

    findings = EVALUATOR.deterministic_findings(events)

    assert "AUD-ELIGIBLE-CANDIDATE-EXCLUDED" not in rule_ids(findings)
    assert "AUD-HARD-FILTER-MISMATCH" not in rule_ids(findings)


class UncertainAdvisor:
    async def advise(self, **_kwargs: object) -> tuple[AuditFinding, ...]:
        return (
            AuditFinding(
                rule_id="SEM-REQUEST-RATIONALE-UNCERTAIN",
                severity=AuditSeverity.CAUTION,
                title="advisory",
                detail="the stored rationale is ambiguous",
                authority=AuditAuthority.SEMANTIC_ADVISORY,
            ),
        )


class OverreachingAdvisor:
    async def advise(self, **_kwargs: object) -> tuple[AuditFinding, ...]:
        return (
            AuditFinding(
                rule_id="SEM-FORCED-RISK",
                severity=AuditSeverity.RISK,
                title="advisory",
                detail="semantic analysis cannot own the verdict",
                authority=AuditAuthority.SEMANTIC_ADVISORY,
            ),
        )


async def settled_delivered_repository(clock) -> InMemoryEvidenceRepository:
    repository = InMemoryEvidenceRepository()
    for event_type, payload in (
        *ai_prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (
            EventType.PAYMENT_SETTLED,
            {
                "amountUnits": QUOTED_UNITS,
                "blockNumber": 1,
                "from": BUYER,
                "receiptStatus": 1,
                "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
                "to": SELLER,
                "token": TOKEN,
                "transactionHash": EVM_TX,
                "transferLogIndex": 0,
            },
        ),
        (
            EventType.DELIVERED,
            {
                "sellerAgentId": "seller-gemini",
                "providerId": "gemini",
                "modelId": "model-a",
                "modelVersion": "v1",
                "responseHash": "sha256:response",
                "responseId": "response-1",
            },
        ),
    ):
        await repository.append_event(
            purchase_id=PURCHASE_ID,
            event_type=event_type,
            occurred_at=DECIDED_AT,
            actor={"id": OWNER, "type": "user"},
            payload=payload,
        )
    return repository


@pytest.mark.asyncio
async def test_semantic_only_advisory_caps_the_verdict_at_a_warning(clock) -> None:
    repository = await settled_delivered_repository(clock)
    service = AuditService(
        repository=repository, clock=clock, semantic_advisor=OverreachingAdvisor()
    )

    report = await service.audit(PURCHASE_ID)

    assert report.severity is AuditSeverity.CAUTION
    assert rule_ids(report.findings) == {"SEM-FORCED-RISK"}
    projection = PROJECTIONS.project(await repository.list_events(PURCHASE_ID))
    assert projection.audit_status is AuditStatus.AUDITED_WARNING


@pytest.mark.asyncio
async def test_semantic_advisory_cannot_lower_a_deterministic_risk(clock) -> None:
    repository = InMemoryEvidenceRepository()
    for event_type, payload in (
        *ai_prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
        (
            EventType.PAYMENT_MISMATCH_CONFIRMED,
            mismatch_payload(amount_units=200_000, token=TOKEN, recipient=SELLER),
        ),
    ):
        await repository.append_event(
            purchase_id=PURCHASE_ID,
            event_type=event_type,
            occurred_at=DECIDED_AT,
            actor={"id": OWNER, "type": "user"},
            payload=payload,
        )
    service = AuditService(
        repository=repository, clock=clock, semantic_advisor=UncertainAdvisor()
    )

    report = await service.audit(PURCHASE_ID)

    assert report.severity is AuditSeverity.RISK
    assert "AUD-QUOTE-PAYMENT-MISMATCH" in rule_ids(report.findings)
    assert "SEM-REQUEST-RATIONALE-UNCERTAIN" in rule_ids(report.findings)
    events = await repository.list_events(PURCHASE_ID)
    assert [event.type for event in events].count(EventType.AUDITED) == 1
    assert PROJECTIONS.project(events).audit_status is AuditStatus.AUDITED_RISK


@pytest.mark.asyncio
async def test_persisted_audit_is_returned_by_the_reader_and_is_reused(clock) -> None:
    repository = await settled_delivered_repository(clock)
    service = AuditService(repository=repository, clock=clock)
    events_before = await repository.list_events(PURCHASE_ID)
    assert READER.persisted(events_before) is None

    first = await service.audit(PURCHASE_ID)
    events_after = await repository.list_events(PURCHASE_ID)
    again = await service.audit(PURCHASE_ID)

    assert READER.persisted(events_after) == first
    assert again == first
    assert len(events_after) == len(events_before) + 1
    assert len(await repository.list_events(PURCHASE_ID)) == len(events_after)
    assert first.ruleset_version == RULESET_VERSION


@pytest.mark.asyncio
async def test_evaluator_is_pure_and_never_appends(clock) -> None:
    repository = await settled_delivered_repository(clock)
    events = await repository.list_events(PURCHASE_ID)

    first = EVALUATOR.evaluate(events)
    second = EVALUATOR.evaluate(events)

    assert first == second
    assert len(await repository.list_events(PURCHASE_ID)) == len(events)


@pytest.mark.asyncio
async def test_nonfinal_purchase_gets_a_preview_without_a_persisted_audit(clock) -> None:
    repository = InMemoryEvidenceRepository()
    for event_type, payload in (
        *ai_prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
    ):
        await repository.append_event(
            purchase_id=PURCHASE_ID,
            event_type=event_type,
            occurred_at=DECIDED_AT,
            actor={"id": OWNER, "type": "user"},
            payload=payload,
        )
    service = AuditService(repository=repository, clock=clock)

    preview = await service.audit(PURCHASE_ID)

    assert preview.severity is AuditSeverity.CAUTION
    events = await repository.list_events(PURCHASE_ID)
    assert all(event.type != EventType.AUDITED for event in events)
    assert READER.persisted(events) is None
    assert PROJECTIONS.project(events).audit_status is AuditStatus.PENDING_AUDIT


@pytest.mark.asyncio
async def test_reaudit_fails_closed_once_evidence_passes_the_audited_head(clock) -> None:
    """H4: a changed head must demand a correction policy, never a silent stale report."""
    repository = await settled_delivered_repository(clock)
    service = AuditService(repository=repository, clock=clock)
    first = await service.audit(PURCHASE_ID)
    await repository.append_event(
        purchase_id=PURCHASE_ID,
        event_type=EventType.SENSITIVE_PAYLOAD_ACCESSED,
        occurred_at=DECIDED_AT,
        actor={"id": OWNER, "type": "user"},
        payload={"kind": "request", "payloadId": "payload-1"},
    )

    with pytest.raises(EvidenceIntegrityError, match="advanced after the terminal audit"):
        await service.audit(PURCHASE_ID)

    events = await repository.list_events(PURCHASE_ID)
    assert [event.type for event in events].count(EventType.AUDITED) == 1
    persisted = READER.persisted_audit(events)
    assert persisted is not None
    assert persisted.report == first
    assert persisted.covers_head is False
    assert PROJECTIONS.project(events).audit_covers_head is False


@pytest.mark.asyncio
async def test_persisted_audit_must_describe_the_head_it_was_appended_to(clock) -> None:
    """H4: a stored audit whose head hash does not match its position is an integrity error."""
    repository = await settled_delivered_repository(clock)
    await repository.append_event(
        purchase_id=PURCHASE_ID,
        event_type=EventType.AUDITED,
        payload={
            "auditBundleHash": "sha256:" + "44" * 32,
            "evidenceHeadEventHash": "sha256:" + "55" * 32,
            "findings": [],
            "reportId": "audit:" + "66" * 32,
            "rulesetVersion": RULESET_VERSION,
            "severity": "NORMAL",
        },
        occurred_at=DECIDED_AT,
        actor={"id": "deterministic-audit-engine", "type": "service"},
    )
    events = await repository.list_events(PURCHASE_ID)

    with pytest.raises(EvidenceIntegrityError, match="does not describe the head"):
        READER.persisted_audit(events)
    with pytest.raises(EvidenceIntegrityError, match="does not describe the head"):
        await AuditService(repository=repository, clock=clock).audit(PURCHASE_ID)
