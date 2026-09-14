"""The pure purchase projection over AEGIS-shaped evidence.

Ported from the superseded Phase 6 projection tests: the five payment statuses, the
audit status, fail-closed handling of contradictory terminal or audit evidence, audit
coverage and the evidence-source axis are shared behaviour. The transaction reference
union the read model exposes is covered at the end.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from buyer_audit_api.core.audit import AuditReportReader
from buyer_audit_api.core.errors import EvidenceIntegrityError
from buyer_audit_api.core.events import create_event, verify_event_chain
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceSource,
    EvmTransactionRef,
    JsonObject,
    LocalTransactionRef,
    transaction_ref_from_payload,
)
from buyer_audit_api.core.projections import (
    AuditStatus,
    PaymentStatus,
    PurchaseProjectionService,
)

PURCHASE_ID = "purchase-projection"
OWNER = "0x0000000000000000000000000000000000000001"
BUYER = "0x0000000000000000000000000000000000000002"
TOKEN = "0x0000000000000000000000000000000000000000"
RECIPIENT = "0x0000000000000000000000000000000000000004"
TERMS = "sha256:" + "77" * 32
RUN_ID = "a" * 32
LOCAL_TX_ID = f"localtx:{RUN_ID}:payment:000001"
EVM_TX = "0x" + "ab" * 32

PROJECTIONS = PurchaseProjectionService()
READER = AuditReportReader()

AUDIT_HEAD_SENTINEL = "<predecessor>"


def chain(*specs: tuple[EventType, JsonObject]) -> list[EvidenceEvent]:
    events: list[EvidenceEvent] = []
    previous: str | None = None
    for index, (event_type, payload) in enumerate(specs, start=1):
        if (
            event_type is EventType.AUDITED
            and payload.get("evidenceHeadEventHash") == AUDIT_HEAD_SENTINEL
        ):
            # A real audit records the head it was appended to; only negative tests deviate.
            payload = {**payload, "evidenceHeadEventHash": previous}
        event = create_event(
            purchase_id=PURCHASE_ID,
            sequence=index,
            event_type=event_type,
            occurred_at=datetime(2026, 9, 4, 12, index, tzinfo=UTC),
            actor={"id": OWNER if index == 1 else "payment-executor", "type": "service"},
            payload=payload,
            previous_event_hash=previous,
            evidence_refs=(),
        )
        previous = event.event_hash
        events.append(event)
    verify_event_chain(events)
    return events


def prefix() -> tuple[tuple[EventType, JsonObject], ...]:
    return (
        (
            EventType.REQUESTED,
            {
                "budgetUnits": 5_000,
                "domain": "ai_inference",
                "normalizedRequest": {"request_schema_version": "aegis-aa-v1"},
                "policy": {"scoringPolicyVersion": "aa-three-factor-v1"},
            },
        ),
        (EventType.AA_SNAPSHOT_RECORDED, {"snapshotHash": "sha256:" + "11" * 32}),
        (EventType.DECIDED, {"termsBindingHash": TERMS, "amountUnits": 1_649}),
    )


def claim_payload() -> JsonObject:
    return {
        "amountUnits": 1_649,
        "authorizationNonce": "0x" + "a7" * 32,
        "buyerWalletAddress": BUYER,
        "payTo": RECIPIENT,
        "quoteId": TERMS,
        "termsBindingHash": TERMS,
        "token": TOKEN,
        "transferMethod": "eip3009",
    }


def settled_payload() -> JsonObject:
    return {
        "amountUnits": 1_649,
        "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
        "executionMode": "mock",
        "settlementReference": "x402mock:" + "1" * 32,
        "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
        "termsBindingHash": TERMS,
        "to": RECIPIENT,
        "token": TOKEN,
        "verificationBasis": "facilitator_response",
    }


def failed_payload() -> JsonObject:
    return {
        "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
        "executionMode": "mock",
        "reason": "facilitator refused the payment",
        "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
        "termsBindingHash": TERMS,
        "verificationBasis": "facilitator_response",
    }


def audited_payload(
    severity: str,
    *,
    head: str | None = AUDIT_HEAD_SENTINEL,
    ruleset_version: str = "phase6.rules.v1",
) -> JsonObject:
    return {
        "auditBundleHash": "sha256:" + "44" * 32,
        "evidenceHeadEventHash": head,
        "findings": [] if severity == "NORMAL" else [{"code": "AUD-X", "severity": severity}],
        "reportId": "audit:" + "66" * 32,
        "rulesetVersion": ruleset_version,
        "severity": severity,
    }


PAYMENT_STATUS_CASES = [
    ("not started", [*prefix()], PaymentStatus.PAYMENT_NOT_STARTED),
    (
        "pending after claim",
        [*prefix(), (EventType.PAYMENT_INTENT_CLAIMED, claim_payload())],
        PaymentStatus.PAYMENT_PENDING,
    ),
    (
        "pending after authorization",
        [
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        ],
        PaymentStatus.PAYMENT_PENDING,
    ),
    (
        "unknown while reconciliation is open",
        [
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
            (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "outcome unknown"}),
        ],
        PaymentStatus.PAYMENT_CONFIRMATION_UNKNOWN,
    ),
    (
        "settled",
        [
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
            (EventType.PAYMENT_SETTLED, settled_payload()),
        ],
        PaymentStatus.PAYMENT_SETTLED,
    ),
    (
        "failed",
        [
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_FAILED, failed_payload()),
        ],
        PaymentStatus.PAYMENT_FAILED,
    ),
]


@pytest.mark.parametrize(
    ("label", "specs", "expected"),
    PAYMENT_STATUS_CASES,
    ids=[case[0] for case in PAYMENT_STATUS_CASES],
)
def test_payment_status_precedence_never_conflates_states(
    label: str,
    specs: list[tuple[EventType, JsonObject]],
    expected: PaymentStatus,
) -> None:
    del label
    assert PROJECTIONS.project(chain(*specs)).payment_status is expected


def test_five_payment_statuses_are_all_reachable_and_distinct() -> None:
    reached = {
        PROJECTIONS.project(chain(*specs)).payment_status for _, specs, _ in PAYMENT_STATUS_CASES
    }
    assert reached == set(PaymentStatus)
    assert len(PaymentStatus) == 5


AUDIT_STATUS_CASES = [
    (None, AuditStatus.PENDING_AUDIT),
    ("NORMAL", AuditStatus.AUDITED_NORMAL),
    ("CAUTION", AuditStatus.AUDITED_WARNING),
    ("RISK", AuditStatus.AUDITED_RISK),
]


@pytest.mark.parametrize(("severity", "expected"), AUDIT_STATUS_CASES)
def test_audit_status_only_follows_a_persisted_audited_event(
    severity: str | None, expected: AuditStatus
) -> None:
    specs: list[tuple[EventType, JsonObject]] = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (EventType.PAYMENT_SETTLED, settled_payload()),
    ]
    if severity is not None:
        specs.append((EventType.AUDITED, audited_payload(severity)))
    projection = PROJECTIONS.project(chain(*specs))
    assert projection.audit_status is expected
    assert projection.audit_severity == severity


def test_settled_delivered_purchase_without_audited_event_is_pending_audit() -> None:
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
            (EventType.PAYMENT_SETTLED, settled_payload()),
            (EventType.DELIVERED, {"responseHash": "sha256:" + "2" * 64}),
        )
    )
    assert projection.payment_status is PaymentStatus.PAYMENT_SETTLED
    assert projection.audit_status is AuditStatus.PENDING_AUDIT
    assert projection.finding_count == 0
    assert projection.amount_units == 1_649
    assert projection.token == TOKEN


def test_conflicting_terminal_events_fail_closed() -> None:
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (EventType.PAYMENT_SETTLED, settled_payload()),
        (EventType.PAYMENT_FAILED, failed_payload()),
    ]
    with pytest.raises(EvidenceIntegrityError, match="conflicting terminal"):
        PROJECTIONS.project(chain(*specs))


def test_two_audited_events_fail_closed() -> None:
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_SETTLED, settled_payload()),
        (EventType.AUDITED, audited_payload("NORMAL")),
        (EventType.AUDITED, audited_payload("RISK")),
    ]
    with pytest.raises(EvidenceIntegrityError, match="multiple audit reports"):
        PROJECTIONS.project(chain(*specs))


def test_audit_coverage_is_reported_when_evidence_advances() -> None:
    """H4: the read model discloses that later evidence passed the audited head."""
    covered = chain(
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_SETTLED, settled_payload()),
        (EventType.AUDITED, audited_payload("NORMAL")),
    )
    advanced = chain(
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_SETTLED, settled_payload()),
        (EventType.AUDITED, audited_payload("NORMAL")),
        (EventType.SENSITIVE_PAYLOAD_ACCESSED, {"kind": "request", "payloadId": "p-1"}),
    )

    assert PROJECTIONS.project(covered).audit_covers_head is True
    assert PROJECTIONS.project(advanced).audit_covers_head is False


def test_summary_rejects_an_audit_whose_head_hash_is_not_its_predecessor() -> None:
    """H4: the summary must fail closed on exactly the audit the reader rejects."""
    events = chain(
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_SETTLED, settled_payload()),
        (EventType.AUDITED, audited_payload("NORMAL", head="sha256:" + "ee" * 32)),
    )

    with pytest.raises(EvidenceIntegrityError, match="does not describe the head"):
        READER.persisted_audit(events)
    with pytest.raises(EvidenceIntegrityError, match="does not describe the head"):
        PROJECTIONS.project(events)


def test_summary_rejects_an_audit_with_a_malformed_payload() -> None:
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_SETTLED, settled_payload()),
        (EventType.AUDITED, {"severity": "NORMAL"}),
    ]
    with pytest.raises(EvidenceIntegrityError, match="malformed"):
        PROJECTIONS.project(chain(*specs))


def test_summary_still_reads_an_older_ruleset_audit_as_history() -> None:
    """An older-ruleset audit is real history: readable, but never current."""
    events = chain(
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_SETTLED, settled_payload()),
        (EventType.AUDITED, audited_payload("NORMAL", ruleset_version="phase6.rules.old")),
    )
    projection = PROJECTIONS.project(events)

    assert projection.audit_status is AuditStatus.AUDITED_NORMAL
    assert projection.audit_covers_head is True
    with pytest.raises(EvidenceIntegrityError, match="not 'phase6.rules.v1'"):
        READER.current(events)


def test_access_records_do_not_advance_the_lifecycle_label() -> None:
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.SENSITIVE_PAYLOAD_ACCESSED, {"kind": "request", "payloadId": "p-1"}),
        )
    )
    assert projection.lifecycle_status == EventType.PAYMENT_INTENT_CLAIMED.value
    assert projection.event_count == 5


# --------------------------------------------------------------------------------------
# Evidence source and transaction reference
# --------------------------------------------------------------------------------------


def test_an_aegis_settlement_is_synthetic_and_carries_no_chain_reference() -> None:
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
            (EventType.PAYMENT_SETTLED, settled_payload()),
        )
    )
    assert projection.evidence_source is EvidenceSource.SYNTHETIC_LOCAL
    assert projection.transaction_ref is None


def test_a_raw_transaction_hash_is_never_promoted_to_a_reference() -> None:
    """Only a validated `transactionRef` union is exposed, never a bare payload string."""
    payload = {**settled_payload(), "transactionHash": EVM_TX}
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_SETTLED, payload),
        )
    )
    assert projection.transaction_ref is None


def test_absent_source_is_null_and_not_a_fourth_truth_state() -> None:
    projection = PROJECTIONS.project(chain(*prefix()))
    assert projection.evidence_source is None
    assert projection.payment_status is PaymentStatus.PAYMENT_NOT_STARTED
    assert projection.audit_status is AuditStatus.PENDING_AUDIT


def test_unknown_evidence_source_is_rejected_not_coerced() -> None:
    """H1 probe: an unknown source must never silently become a trusted one."""
    payload = {**settled_payload(), "evidenceSource": "TOTALLY_TRUSTED"}
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_SETTLED, payload),
    ]
    with pytest.raises(EvidenceIntegrityError, match="unknown evidence source"):
        PROJECTIONS.project(chain(*specs))


def test_one_base_event_plus_one_synthetic_event_fails_closed() -> None:
    """H1 probe: a nonterminal BASE source must not be ignored while synthetic wins."""
    reconciliation = {
        "reason": "outcome unknown",
        "evidenceSource": EvidenceSource.BASE_SEPOLIA_VERIFIED.value,
    }
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_RECONCILIATION_REQUIRED, reconciliation),
        (EventType.PAYMENT_FAILED, failed_payload()),
    ]
    with pytest.raises(EvidenceIntegrityError, match="mixes synthetic"):
        PROJECTIONS.project(chain(*specs))


def test_base_and_historical_sources_cannot_coexist() -> None:
    specs = [
        *prefix(),
        (
            EventType.PAYMENT_INTENT_CLAIMED,
            {**claim_payload(), "evidenceSource": EvidenceSource.HISTORICAL_ON_CHAIN.value},
        ),
        (
            EventType.PAYMENT_RECONCILIATION_REQUIRED,
            {"reason": "x", "evidenceSource": EvidenceSource.BASE_SEPOLIA_VERIFIED.value},
        ),
    ]
    with pytest.raises(EvidenceIntegrityError, match="contradictory evidence sources"):
        PROJECTIONS.project(chain(*specs))


def test_a_terminal_reference_must_agree_with_the_evidence_source() -> None:
    """H1: the projection source and the terminal reference source are one fact."""
    payload = {
        **failed_payload(),
        "evidenceSource": EvidenceSource.BASE_SEPOLIA_VERIFIED.value,
        "transactionRef": EvmTransactionRef(hash=EVM_TX).to_payload(),
    }
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_FAILED, payload),
        )
    )
    assert projection.transaction_ref == EvmTransactionRef(hash=EVM_TX)
    assert projection.evidence_source is EvidenceSource.BASE_SEPOLIA_VERIFIED

    synthetic = {
        **failed_payload(),
        "transactionRef": EvmTransactionRef(hash=EVM_TX).to_payload(),
    }
    with pytest.raises(EvidenceIntegrityError, match="mixes synthetic"):
        PROJECTIONS.project(
            chain(
                *prefix(),
                (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
                (EventType.PAYMENT_FAILED, synthetic),
            )
        )


def test_a_malformed_terminal_reference_fails_closed() -> None:
    payload = {**failed_payload(), "transactionRef": {"kind": "OTHER"}}
    with pytest.raises(EvidenceIntegrityError, match="malformed"):
        PROJECTIONS.project(
            chain(
                *prefix(),
                (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
                (EventType.PAYMENT_FAILED, payload),
            )
        )


def test_synthetic_evidence_cannot_use_an_evm_transaction_reference() -> None:
    with pytest.raises(ValueError, match="synthetic evidence"):
        EvmTransactionRef(hash=EVM_TX, evidence_source=EvidenceSource.SYNTHETIC_LOCAL)


def test_uppercase_and_short_hashes_are_rejected() -> None:
    with pytest.raises(ValueError, match="0-9a-f"):
        EvmTransactionRef(hash="0x" + "AB" * 32)
    with pytest.raises(ValueError, match="0-9a-f"):
        EvmTransactionRef(hash="0x" + "ab" * 31)


def test_local_transaction_reference_must_belong_to_its_run() -> None:
    with pytest.raises(ValueError, match="does not belong"):
        LocalTransactionRef(id=LOCAL_TX_ID, run_id="b" * 32)


def test_local_transaction_reference_requires_the_canonical_namespace() -> None:
    with pytest.raises(ValueError, match="localtx"):
        LocalTransactionRef(id=f"synthetic:{RUN_ID}:payment:000001", run_id=RUN_ID)


def test_transaction_reference_round_trips_through_its_payload() -> None:
    evm = EvmTransactionRef(hash=EVM_TX, block_number=1, log_index=2)
    local = LocalTransactionRef(id=LOCAL_TX_ID, run_id=RUN_ID)
    assert transaction_ref_from_payload(evm.to_payload()) == evm
    assert transaction_ref_from_payload(local.to_payload()) == local
    with pytest.raises(ValueError, match="kind must be"):
        transaction_ref_from_payload({"kind": "OTHER"})


def test_negative_reference_coordinates_are_rejected() -> None:
    """H1 probe: negative block/log coordinates are not a provable position."""
    with pytest.raises(ValueError, match="blockNumber"):
        EvmTransactionRef(hash=EVM_TX, block_number=-1)
    with pytest.raises(ValueError, match="logIndex"):
        EvmTransactionRef(hash=EVM_TX, log_index=-7)
    with pytest.raises(ValueError, match="blockNumber"):
        transaction_ref_from_payload(
            {
                "kind": "EVM",
                "hash": EVM_TX,
                "chainId": 84532,
                "evidenceSource": EvidenceSource.BASE_SEPOLIA_VERIFIED.value,
                "blockNumber": -1,
            }
        )


def test_unknown_source_in_a_nested_reference_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid EvidenceSource"):
        transaction_ref_from_payload(
            {
                "kind": "EVM",
                "hash": EVM_TX,
                "chainId": 84532,
                "evidenceSource": "TOTALLY_TRUSTED",
            }
        )
