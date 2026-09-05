from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from buyer_audit_api.core.errors import EvidenceIntegrityError
from buyer_audit_api.core.events import create_event, verify_event_chain
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceSource,
    EvmTransactionRef,
    JsonObject,
    LocalTransactionRef,
    ScenarioMetadata,
    transaction_ref_from_fields,
    transaction_ref_from_payload,
)
from buyer_audit_api.core.payment import PaymentIntent, PaymentIntentState
from buyer_audit_api.core.projections import (
    AuditStatus,
    PaymentStatus,
    PurchaseProjectionService,
)

PURCHASE_ID = "purchase-projection"
OWNER = "0x0000000000000000000000000000000000000001"
BUYER = "0x0000000000000000000000000000000000000002"
TOKEN = "0x0000000000000000000000000000000000000003"
SELLER = "0x0000000000000000000000000000000000000004"
OTHER_TOKEN = "0x0000000000000000000000000000000000000009"
RUN_ID = "a" * 32
LOCAL_TX_ID = f"localtx:{RUN_ID}:payment:000001"
EVM_TX = "0x" + "ab" * 32
SCENARIO = ScenarioMetadata(
    run_id=RUN_ID,
    scenario_id="P6-A04-WRONG-AMOUNT",
    catalog_version="phase6.v1",
    catalog_hash="sha256:" + "cd" * 32,
)

PROJECTIONS = PurchaseProjectionService()


def chain(*specs: tuple[EventType, JsonObject]) -> list[EvidenceEvent]:
    events: list[EvidenceEvent] = []
    previous: str | None = None
    for index, (event_type, payload) in enumerate(specs, start=1):
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


def requested_payload() -> JsonObject:
    return {
        "budgetUnits": 250_000,
        "domain": "ai_inference",
        "normalizedRequest": {"priority": "balanced"},
        "policy": {},
    }


def claim_payload(*, transfer_method: str = "eip3009") -> JsonObject:
    payload: JsonObject = {
        "amountUnits": 100_000,
        "buyerWalletAddress": BUYER,
        "payTo": SELLER,
        "quoteId": "quote-1",
        "token": TOKEN,
        "transferMethod": transfer_method,
    }
    if transfer_method == "permit2":
        payload["permit2Nonce"] = "123456789"
    return payload


def prefix() -> tuple[tuple[EventType, JsonObject], ...]:
    return (
        (EventType.REQUESTED, requested_payload()),
        (EventType.QUOTED, {"signedQuotes": []}),
        (EventType.DECIDED, {"winner": {"quote_id": "quote-1"}}),
    )


def settled_payload() -> JsonObject:
    return {
        "amountUnits": 100_000,
        "blockNumber": 46_349_821,
        "from": BUYER,
        "receiptStatus": 1,
        "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
        "to": SELLER,
        "token": TOKEN,
        "transactionHash": EVM_TX,
        "transferLogIndex": 3,
    }


def mismatch_payload(
    *,
    evidence_source: EvidenceSource = EvidenceSource.SYNTHETIC_LOCAL,
    local: bool = True,
    mismatched: list[str] | None = None,
) -> JsonObject:
    reference = (
        LocalTransactionRef(id=LOCAL_TX_ID, run_id=RUN_ID).to_payload()
        if local
        else EvmTransactionRef(hash=EVM_TX).to_payload()
    )
    payload: JsonObject = {
        "actualTransfer": {
            "amountUnits": 200_000,
            "from": BUYER,
            "to": SELLER,
            "token": TOKEN,
        },
        "evidenceSource": evidence_source.value,
        "mismatchedFields": mismatched or ["amount"],
        "proofRef": "sha256:" + "11" * 32,
        "quoteBinding": {"amountUnits": 100_000, "payTo": SELLER, "token": TOKEN},
        "reconciliationAttempts": 1,
        "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
        "transactionRef": reference,
    }
    if local:
        payload["scenario"] = SCENARIO.to_payload()
    return payload


def no_transfer_payload() -> JsonObject:
    return {
        "attemptCount": 3,
        "authorizationNonceHash": "sha256:" + "22" * 32,
        "checkedChainId": 84532,
        "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
        "finalityEvidence": {"confirmations": 3},
        "firstCheckedAt": "2026-09-04T12:00:00+00:00",
        "lastCheckedAt": "2026-09-04T12:10:00+00:00",
        "proofRef": "sha256:" + "33" * 32,
        "reasonCode": "AUTHORIZATION_UNUSED_AFTER_EXPIRY",
        "scenario": SCENARIO.to_payload(),
        "submissionRef": LOCAL_TX_ID,
        "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
    }


def audited_payload(severity: str) -> JsonObject:
    return {
        "auditBundleHash": "sha256:" + "44" * 32,
        "evidenceHeadEventHash": "sha256:" + "55" * 32,
        "findings": [] if severity == "NORMAL" else [{"code": "AUD-X", "severity": severity}],
        "reportId": "audit:" + "66" * 32,
        "rulesetVersion": "phase6.rules.v1",
        "severity": severity,
    }


PAYMENT_STATUS_CASES = [
    (
        "not started",
        [*prefix()],
        PaymentStatus.PAYMENT_NOT_STARTED,
    ),
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
            (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
        ],
        PaymentStatus.PAYMENT_CONFIRMATION_UNKNOWN,
    ),
    (
        "unknown stays unknown after bounded checks",
        [
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
            (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
            (
                EventType.PAYMENT_RECONCILIATION_CHECKED,
                {"attemptNumber": 1, "verifierOutcome": "RECEIPT_NOT_FOUND"},
            ),
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
            (
                EventType.PAYMENT_FAILED,
                {
                    "blockNumber": 1,
                    "reason": "receipt status zero",
                    "receiptStatus": 0,
                    "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
                    "transactionHash": EVM_TX,
                },
            ),
        ],
        PaymentStatus.PAYMENT_FAILED,
    ),
    (
        "mismatch confirmed",
        [
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_MISMATCH_CONFIRMED, mismatch_payload()),
        ],
        PaymentStatus.PAYMENT_MISMATCH_CONFIRMED,
    ),
    (
        "reconciled no transfer",
        [
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
            (EventType.PAYMENT_RECONCILED_NO_TRANSFER, no_transfer_payload()),
        ],
        PaymentStatus.RECONCILED_NO_TRANSFER,
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
    projection = PROJECTIONS.project(chain(*specs))
    assert projection.payment_status is expected


def test_seven_payment_statuses_are_all_reachable_and_distinct() -> None:
    reached = {
        PROJECTIONS.project(chain(*specs)).payment_status for _, specs, _ in PAYMENT_STATUS_CASES
    }
    assert reached == set(PaymentStatus)


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
            (EventType.DELIVERED, {"responseHash": "sha256:response"}),
        )
    )
    assert projection.payment_status is PaymentStatus.PAYMENT_SETTLED
    assert projection.audit_status is AuditStatus.PENDING_AUDIT
    assert projection.finding_count == 0


def test_verified_base_sepolia_settlement_reports_verified_source_and_legacy_hash() -> None:
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
            (EventType.PAYMENT_SETTLED, settled_payload()),
        )
    )
    assert projection.evidence_source is EvidenceSource.BASE_SEPOLIA_VERIFIED
    assert isinstance(projection.transaction_ref, EvmTransactionRef)
    assert projection.legacy_transaction_hash == EVM_TX


def test_legacy_permit2_record_is_historical_and_not_base_verified() -> None:
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload(transfer_method="permit2")),
            (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
            (EventType.PAYMENT_SETTLED, settled_payload()),
        )
    )
    assert projection.evidence_source is EvidenceSource.HISTORICAL_ON_CHAIN
    assert isinstance(projection.transaction_ref, EvmTransactionRef)
    assert projection.transaction_ref.evidence_source is EvidenceSource.HISTORICAL_ON_CHAIN


def test_synthetic_mismatch_is_local_and_never_exposes_a_legacy_hash() -> None:
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_MISMATCH_CONFIRMED, mismatch_payload()),
        )
    )
    assert projection.evidence_source is EvidenceSource.SYNTHETIC_LOCAL
    assert isinstance(projection.transaction_ref, LocalTransactionRef)
    assert projection.transaction_ref.id == LOCAL_TX_ID
    assert projection.legacy_transaction_hash is None
    assert projection.scenario == SCENARIO
    assert projection.mismatched_fields == ("amount",)


def test_unverified_submission_identity_is_never_promoted_to_a_base_source() -> None:
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
            (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
            (
                EventType.PAYMENT_SUBMISSION_IDENTIFIED,
                {"transactionHash": EVM_TX},
            ),
        )
    )
    assert projection.payment_status is PaymentStatus.PAYMENT_CONFIRMATION_UNKNOWN
    assert projection.evidence_source is None
    assert projection.transaction_ref is None
    assert projection.legacy_transaction_hash is None


def test_absent_verified_source_is_null_and_not_a_fourth_truth_state() -> None:
    projection = PROJECTIONS.project(chain(*prefix()))
    assert projection.evidence_source is None
    assert projection.payment_status is PaymentStatus.PAYMENT_NOT_STARTED
    assert projection.audit_status is AuditStatus.PENDING_AUDIT


def test_conflicting_terminal_events_fail_closed() -> None:
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
        (EventType.PAYMENT_SETTLED, settled_payload()),
        (
            EventType.PAYMENT_MISMATCH_CONFIRMED,
            mismatch_payload(evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED, local=False),
        ),
    ]
    with pytest.raises(EvidenceIntegrityError, match="conflicting terminal"):
        PROJECTIONS.project(chain(*specs))


def test_mixed_synthetic_and_on_chain_sources_fail_closed() -> None:
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload(transfer_method="permit2")),
        (EventType.PAYMENT_MISMATCH_CONFIRMED, mismatch_payload()),
    ]
    with pytest.raises(EvidenceIntegrityError, match="mixes synthetic"):
        PROJECTIONS.project(chain(*specs))


def test_two_audited_events_fail_closed() -> None:
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_SETTLED, settled_payload()),
        (EventType.AUDITED, audited_payload("NORMAL")),
        (EventType.AUDITED, audited_payload("RISK")),
    ]
    with pytest.raises(EvidenceIntegrityError, match="multiple persisted audit"):
        PROJECTIONS.project(chain(*specs))


def intent(state: PaymentIntentState) -> PaymentIntent:
    return PaymentIntent(
        purchase_id=PURCHASE_ID,
        buyer_wallet_address=BUYER,
        policy_date="2026-09-04",
        quote_id="quote-1",
        decision_event_hash="sha256:" + "77" * 32,
        amount_units=100_000,
        token=TOKEN,
        pay_to=SELLER,
        permit2_nonce=None,
        state=state,
        claimed_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )


def test_terminal_intent_without_terminal_evidence_fails_closed() -> None:
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
    ]
    with pytest.raises(EvidenceIntegrityError, match="no terminal evidence"):
        PROJECTIONS.project(chain(*specs), intent(PaymentIntentState.MISMATCH_CONFIRMED))


def test_intent_terminal_state_must_agree_with_terminal_evidence() -> None:
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_MISMATCH_CONFIRMED, mismatch_payload()),
    ]
    with pytest.raises(EvidenceIntegrityError, match="contradicts terminal evidence"):
        PROJECTIONS.project(chain(*specs), intent(PaymentIntentState.SETTLED))


def test_payment_intent_from_another_purchase_is_rejected() -> None:
    with pytest.raises(EvidenceIntegrityError, match="another purchase"):
        PROJECTIONS.project(
            chain(*prefix()),
            replace(intent(PaymentIntentState.CLAIMED), purchase_id="purchase-other"),
        )


def test_reconciliation_checks_do_not_advance_the_lifecycle_label() -> None:
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
            (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
            (
                EventType.PAYMENT_RECONCILIATION_CHECKED,
                {"attemptNumber": 1, "verifierOutcome": "RECEIPT_NOT_FOUND"},
            ),
        )
    )
    assert projection.lifecycle_status == EventType.PAYMENT_RECONCILIATION_REQUIRED.value
    assert projection.event_count == 7


LOCAL_TRANSACTION_TYPE_CONFUSION = [
    ("evm hash cannot be a local id", {"transaction_hash": None, "local": EVM_TX}),
    ("local id cannot be an evm hash", {"transaction_hash": LOCAL_TX_ID, "local": None}),
    ("both fields together", {"transaction_hash": EVM_TX, "local": LOCAL_TX_ID}),
]


@pytest.mark.parametrize(
    ("label", "fields"),
    LOCAL_TRANSACTION_TYPE_CONFUSION,
    ids=[case[0] for case in LOCAL_TRANSACTION_TYPE_CONFUSION],
)
def test_transaction_union_rejects_type_confusion(label: str, fields: dict[str, str]) -> None:
    del label
    with pytest.raises(ValueError):
        transaction_ref_from_fields(
            transaction_hash=fields["transaction_hash"],
            local_transaction_id=fields["local"],
            run_id=RUN_ID,
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


def test_local_transaction_reference_from_another_run_fails_closed() -> None:
    foreign_run = "b" * 32
    payload = mismatch_payload()
    payload["transactionRef"] = LocalTransactionRef(
        id=f"localtx:{foreign_run}:payment:000001", run_id=foreign_run
    ).to_payload()
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_MISMATCH_CONFIRMED, payload),
    ]
    with pytest.raises(EvidenceIntegrityError, match="another run"):
        PROJECTIONS.project(chain(*specs))


def test_wrong_token_mismatch_keeps_the_quoted_amount_in_the_summary() -> None:
    payload = mismatch_payload(mismatched=["token"])
    payload["actualTransfer"] = {
        "amountUnits": 100_000,
        "from": BUYER,
        "to": SELLER,
        "token": OTHER_TOKEN,
    }
    projection = PROJECTIONS.project(
        chain(
            *prefix(),
            (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
            (EventType.PAYMENT_MISMATCH_CONFIRMED, payload),
        )
    )
    assert projection.amount_units == 100_000
    assert projection.token == TOKEN
    assert projection.mismatched_fields == ("token",)


def test_unknown_evidence_source_is_rejected_not_coerced() -> None:
    """H1 probe: an unknown source must never silently become BASE_SEPOLIA_VERIFIED."""
    payload = settled_payload()
    payload["evidenceSource"] = "TOTALLY_TRUSTED"
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_SETTLED, payload),
    ]
    with pytest.raises(EvidenceIntegrityError, match="unknown evidence source"):
        PROJECTIONS.project(chain(*specs))


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


def test_one_base_event_plus_one_synthetic_event_fails_closed() -> None:
    """H1 probe: a nonterminal BASE source must not be ignored while synthetic wins."""
    reconciliation = {
        "reason": "receipt pending",
        "evidenceSource": EvidenceSource.BASE_SEPOLIA_VERIFIED.value,
    }
    checked = {
        "attemptNumber": 1,
        "verifierOutcome": "RECEIPT_NOT_FOUND",
        "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
        "scenario": SCENARIO.to_payload(),
    }
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_RECONCILIATION_REQUIRED, reconciliation),
        (EventType.PAYMENT_RECONCILIATION_CHECKED, checked),
    ]
    with pytest.raises(EvidenceIntegrityError, match="mixes synthetic"):
        PROJECTIONS.project(chain(*specs))


def test_synthetic_terminal_carrying_a_base_reference_fails_closed() -> None:
    """H1 probe: the projection source and the reference source are one fact."""
    payload = mismatch_payload()
    payload["transactionRef"] = EvmTransactionRef(hash=EVM_TX).to_payload()
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_MISMATCH_CONFIRMED, payload),
    ]
    with pytest.raises(EvidenceIntegrityError, match="mixes synthetic"):
        PROJECTIONS.project(chain(*specs))


def test_base_and_historical_sources_cannot_coexist() -> None:
    payload = mismatch_payload(
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED, local=False
    )
    specs = [
        *prefix(),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload(transfer_method="permit2")),
        (EventType.PAYMENT_MISMATCH_CONFIRMED, payload),
    ]
    with pytest.raises(EvidenceIntegrityError, match="contradictory evidence sources"):
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
