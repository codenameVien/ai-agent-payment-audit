"""Pure reputation core: the 100/0/DEFER table, identity/fingerprint, and aggregation.

These tests fix `P6-AC-05.1`-`P6-AC-05.5` and `P6-AC-06.1`-`P6-AC-06.3` before any
persistence or transport exists, so a later refactor cannot quietly move the policy.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest

from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.adapters.reputation_gateway import (
    GatewayReputationProvider,
    HttpReputationQueryClient,
    parse_feedback_clients,
)
from buyer_audit_api.core.audit import (
    RULESET_VERSION,
    AuditAuthority,
    AuditFinding,
    AuditReport,
    AuditSeverity,
)
from buyer_audit_api.core.errors import PaymentConflictError, PaymentEvidenceError
from buyer_audit_api.core.models import (
    EvidenceSource,
    EvmTransactionRef,
    JsonObject,
    LocalTransactionRef,
)
from buyer_audit_api.core.payment import PaymentIntentState
from buyer_audit_api.core.projections import PaymentStatus
from buyer_audit_api.core.reputation import (
    AGGREGATION_METHOD,
    FEEDBACK_TAG1,
    FEEDBACK_TAG2,
    MAX_REPUTATION_LOOKBACK_BLOCKS,
    NEUTRAL_SCORE,
    ConfirmedFeedbackProof,
    DecisionKind,
    FreshnessStatus,
    OutboxStatus,
    PublishIdentity,
    RawFeedbackEvent,
    ReputationAggregationPolicy,
    ReputationDecision,
    ReputationDecisionPolicy,
    ReputationPublishJob,
    ReputationQueryScope,
    ReputationReason,
    ReputationSnapshot,
    SellerAttribution,
    assert_prepared_commitment,
    assert_proof_matches_job,
)

PURCHASE_ID = "purchase-reputation"
REGISTRY = "0x8004b663056a597dffe9eccc1965a193b7388713"
CLIENT = "0x0000000000000000000000000000000000000009"
OTHER_CLIENT = "0x00000000000000000000000000000000000000aa"
CHAIN_ID = 84532
SELLER_AGENT = "gemini-agent"
AGENT_ID = "1"
RUN_ID = "a" * 32
EVM_TX = "0x" + "ab" * 32
LOCAL_TX = f"localtx:{RUN_ID}:feedback:000001"
BUNDLE = "sha256:" + "44" * 32
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

POLICY = ReputationDecisionPolicy()


def identity(*, seller_agent_id: str = SELLER_AGENT) -> PublishIdentity:
    return PublishIdentity(
        chain_id=CHAIN_ID,
        registry_address=REGISTRY,
        purchase_id=PURCHASE_ID,
        seller_agent_id=seller_agent_id,
    )


def finding(
    rule_id: str,
    *,
    severity: AuditSeverity = AuditSeverity.RISK,
    authority: AuditAuthority = AuditAuthority.DETERMINISTIC,
) -> AuditFinding:
    return AuditFinding(
        rule_id=rule_id,
        severity=severity,
        title=rule_id,
        detail=rule_id,
        authority=authority,
    )


def report(
    *,
    severity: AuditSeverity = AuditSeverity.NORMAL,
    findings: tuple[AuditFinding, ...] = (),
    ruleset_version: str = RULESET_VERSION,
) -> AuditReport:
    return AuditReport(
        report_id="audit:" + "66" * 32,
        purchase_id=PURCHASE_ID,
        severity=severity,
        findings=findings,
        evidence_head_event_hash="sha256:" + "55" * 32,
        audit_bundle_hash=BUNDLE,
        ruleset_version=ruleset_version,
    )


def attribution(
    *, identity_verified: bool = True, delivered_as_quoted: bool = True
) -> SellerAttribution:
    return SellerAttribution(
        seller_agent_id=SELLER_AGENT,
        erc8004_agent_id=AGENT_ID,
        identity_verified=identity_verified,
        delivered_as_quoted=delivered_as_quoted,
    )


# --------------------------------------------------------------------------------------
# P6-AC-05.2 decision mapping
# --------------------------------------------------------------------------------------


def test_seller_that_delivered_as_quoted_publishes_100() -> None:
    decision = POLICY.decide(
        audit=report(),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(),
    )

    assert decision.kind is DecisionKind.PUBLISH
    assert decision.value == 100
    assert decision.reason_codes == (ReputationReason.SELLER_DELIVERED_AS_QUOTED,)
    assert decision.audit_bundle_hash == BUNDLE
    assert decision.ruleset_version == RULESET_VERSION


def test_buyer_attributed_findings_still_publish_100() -> None:
    """P6-AC-05.2: buyer selection/explanation risk does not lower the seller score."""
    decision = POLICY.decide(
        audit=report(
            severity=AuditSeverity.RISK,
            findings=(finding("AUD-INFERIOR-CANDIDATE-SELECTED"),),
        ),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(),
    )

    assert decision.value == 100
    assert ReputationReason.BUYER_ATTRIBUTED_FINDINGS_ONLY in decision.reason_codes


def test_semantic_only_advisory_never_lowers_the_seller_score() -> None:
    decision = POLICY.decide(
        audit=report(
            severity=AuditSeverity.CAUTION,
            findings=(
                finding(
                    "SEM-REQUEST-RATIONALE-UNCERTAIN",
                    severity=AuditSeverity.CAUTION,
                    authority=AuditAuthority.SEMANTIC_ADVISORY,
                ),
            ),
        ),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(),
    )

    assert decision.value == 100


@pytest.mark.parametrize(
    ("state", "expected_reason"),
    [
        (
            PaymentIntentState.MISMATCH_CONFIRMED,
            ReputationReason.PAYMENT_MISMATCH_CONFIRMED,
        ),
        (
            PaymentIntentState.RECONCILED_NO_TRANSFER,
            ReputationReason.PAYMENT_RECONCILED_NO_TRANSFER,
        ),
        (
            PaymentIntentState.FAILED,
            ReputationReason.SELLER_ATTRIBUTED_PAYMENT_FAILURE,
        ),
    ],
)
def test_confirmed_payment_failure_publishes_zero(
    state: PaymentIntentState, expected_reason: ReputationReason
) -> None:
    status = {
        PaymentIntentState.MISMATCH_CONFIRMED: PaymentStatus.PAYMENT_MISMATCH_CONFIRMED,
        PaymentIntentState.RECONCILED_NO_TRANSFER: (
            PaymentStatus.RECONCILED_NO_TRANSFER
        ),
        PaymentIntentState.FAILED: PaymentStatus.PAYMENT_FAILED,
    }[state]

    decision = POLICY.decide(
        audit=report(severity=AuditSeverity.RISK),
        payment_status=status,
        payment_state=state,
        attribution=attribution(),
    )

    assert decision.kind is DecisionKind.PUBLISH
    assert decision.value == 0
    assert expected_reason in decision.reason_codes


def test_delivery_integrity_failure_publishes_zero() -> None:
    decision = POLICY.decide(
        audit=report(
            severity=AuditSeverity.RISK,
            findings=(finding("AUD-DELIVERY-SELECTION-MISMATCH"),),
        ),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(),
    )

    assert decision.value == 0
    assert ReputationReason.DELIVERY_INTEGRITY_FAILURE in decision.reason_codes


def test_payment_confirmation_unknown_defers_and_never_publishes() -> None:
    decision = POLICY.decide(
        audit=report(),
        payment_status=PaymentStatus.PAYMENT_CONFIRMATION_UNKNOWN,
        payment_state=PaymentIntentState.RECONCILIATION_REQUIRED,
        attribution=attribution(),
    )

    assert decision.kind is DecisionKind.DEFER
    assert decision.value is None
    assert decision.reason_codes == (ReputationReason.PAYMENT_CONFIRMATION_UNKNOWN,)


def test_unverified_seller_identity_defers() -> None:
    decision = POLICY.decide(
        audit=report(),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(identity_verified=False),
    )

    assert decision.reason_codes == (ReputationReason.SELLER_IDENTITY_UNKNOWN,)


def test_conflicting_proof_defers() -> None:
    decision = POLICY.decide(
        audit=report(),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(),
        conflicting_proof=True,
    )

    assert decision.reason_codes == (ReputationReason.CONFLICTING_PROOF,)


def test_insufficient_attribution_defers() -> None:
    decision = POLICY.decide(
        audit=report(),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(delivered_as_quoted=False),
    )

    assert decision.reason_codes == (ReputationReason.ATTRIBUTION_INSUFFICIENT,)


def test_an_older_ruleset_audit_cannot_drive_a_publication() -> None:
    decision = POLICY.decide(
        audit=report(ruleset_version="phase6.rules.old"),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(),
    )

    assert decision.kind is DecisionKind.DEFER
    assert decision.reason_codes == (ReputationReason.AUDIT_NOT_TERMINAL,)


def test_a_nonterminal_payment_defers_instead_of_publishing() -> None:
    for status in (PaymentStatus.PAYMENT_NOT_STARTED, PaymentStatus.PAYMENT_PENDING):
        decision = POLICY.decide(
            audit=report(),
            payment_status=status,
            payment_state=PaymentIntentState.CLAIMED,
            attribution=attribution(),
        )
        assert decision.kind is DecisionKind.DEFER, status
        assert decision.reason_codes == (ReputationReason.AUDIT_NOT_TERMINAL,)


def test_a_published_value_is_only_ever_100_or_0() -> None:
    with pytest.raises(ValueError, match="exactly 100 or 0"):
        ReputationDecision(
            kind=DecisionKind.PUBLISH,
            value=55,
            reason_codes=(ReputationReason.SELLER_DELIVERED_AS_QUOTED,),
            audit_bundle_hash=BUNDLE,
            ruleset_version=RULESET_VERSION,
            seller_agent_id=SELLER_AGENT,
            erc8004_agent_id=AGENT_ID,
        )


def test_a_deferred_decision_carries_no_value() -> None:
    with pytest.raises(ValueError, match="carries no value"):
        ReputationDecision(
            kind=DecisionKind.DEFER,
            value=0,
            reason_codes=(ReputationReason.CONFLICTING_PROOF,),
            audit_bundle_hash=BUNDLE,
            ruleset_version=RULESET_VERSION,
            seller_agent_id=SELLER_AGENT,
            erc8004_agent_id=AGENT_ID,
        )


# --------------------------------------------------------------------------------------
# P6-AC-05.3 publish identity and immutable fingerprint
# --------------------------------------------------------------------------------------


def test_publish_identity_is_the_six_field_tuple_and_round_trips() -> None:
    subject = identity()

    assert subject.to_payload() == {
        "chainId": CHAIN_ID,
        "purchaseId": PURCHASE_ID,
        "registryAddress": REGISTRY,
        "sellerAgentId": SELLER_AGENT,
        "tag1": FEEDBACK_TAG1,
        "tag2": FEEDBACK_TAG2,
    }
    assert PublishIdentity.from_payload(subject.to_payload()) == subject
    assert subject.identity_hash.startswith("sha256:")


def test_publish_identity_rejects_an_uppercase_registry() -> None:
    with pytest.raises(ValueError, match="lowercase"):
        PublishIdentity(
            chain_id=CHAIN_ID,
            registry_address=REGISTRY.upper(),
            purchase_id=PURCHASE_ID,
            seller_agent_id=SELLER_AGENT,
        )


def test_a_different_seller_agent_is_a_different_identity() -> None:
    assert identity().identity_hash != identity(seller_agent_id="other-agent").identity_hash


def test_the_fingerprint_covers_identity_value_reasons_bundle_and_ruleset() -> None:
    subject = identity()
    base = POLICY.decide(
        audit=report(),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(),
    )
    original = base.payload_fingerprint(subject)

    assert replace(base, value=0).payload_fingerprint(subject) != original
    assert (
        replace(base, reason_codes=(ReputationReason.CONFLICTING_PROOF,)).payload_fingerprint(
            subject
        )
        != original
    )
    assert (
        replace(base, audit_bundle_hash="sha256:" + "99" * 32).payload_fingerprint(subject)
        != original
    )
    assert (
        replace(base, ruleset_version="phase6.rules.old").payload_fingerprint(subject)
        != original
    )
    assert base.payload_fingerprint(identity(seller_agent_id="other")) != original
    # The same decision against the same identity is byte-stable.
    assert base.payload_fingerprint(subject) == original


def test_an_outbox_job_must_describe_its_identity_and_decision() -> None:
    subject = identity()
    decision = POLICY.decide(
        audit=report(),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(),
    )
    with pytest.raises(ValueError, match="identity hash does not describe"):
        ReputationPublishJob(
            job_id="repjob:1",
            identity=subject,
            identity_hash="sha256:" + "00" * 32,
            payload_fingerprint=decision.payload_fingerprint(subject),
            decision=decision,
            status=OutboxStatus.PENDING,
            created_at=NOW,
            updated_at=NOW,
        )
    with pytest.raises(ValueError, match="fingerprint does not describe"):
        ReputationPublishJob(
            job_id="repjob:1",
            identity=subject,
            identity_hash=subject.identity_hash,
            payload_fingerprint="sha256:" + "00" * 32,
            decision=decision,
            status=OutboxStatus.PENDING,
            created_at=NOW,
            updated_at=NOW,
        )


def test_a_prepared_job_must_carry_its_whole_commitment() -> None:
    """`PREPARED` freezes hash + client + URI; `SUBMITTED_UNKNOWN` may have no reference.

    The reviewer's C2 finding was that a restarted `PREPARED` job with no reference had
    nowhere fail-closed to go, so the publisher re-broadcast. The honest sink is an
    unknown submission without a nameable transaction, so that state must be legal - and
    a confirmation must carry its entire proof.
    """
    subject = identity()
    decision = ReputationDecision(
        kind=DecisionKind.PUBLISH,
        value=100,
        reason_codes=(ReputationReason.SELLER_DELIVERED_AS_QUOTED,),
        audit_bundle_hash=BUNDLE,
        ruleset_version=RULESET_VERSION,
        seller_agent_id=SELLER_AGENT,
        erc8004_agent_id=AGENT_ID,
    )

    def build(**overrides: object) -> ReputationPublishJob:
        fields: dict[str, object] = {
            "job_id": "repjob:1",
            "identity": subject,
            "identity_hash": subject.identity_hash,
            "payload_fingerprint": decision.payload_fingerprint(subject),
            "decision": decision,
            "created_at": NOW,
            "updated_at": NOW,
        }
        fields.update(overrides)
        return ReputationPublishJob(**fields)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="feedback hash, client and URI"):
        build(status=OutboxStatus.PREPARED)
    with pytest.raises(ValueError, match="feedback hash, client and URI"):
        build(
            status=OutboxStatus.PREPARED,
            feedback_hash="0x" + "cd" * 32,
            client_address=CLIENT,
        )
    with pytest.raises(ValueError, match="clientAddress must be lowercase"):
        build(
            status=OutboxStatus.PREPARED,
            feedback_hash="0x" + "cd" * 32,
            client_address=CLIENT.upper(),
            feedback_uri=BUNDLE,
        )
    with pytest.raises(ValueError, match="requires the committed feedback hash"):
        build(status=OutboxStatus.SUBMITTED_UNKNOWN)

    # The fail-closed reconciliation state: committed, possibly broadcast, unnameable.
    unresolved = build(
        status=OutboxStatus.SUBMITTED_UNKNOWN, feedback_hash="0x" + "cd" * 32
    )
    assert unresolved.transaction_ref is None
    assert not unresolved.is_terminal

    with pytest.raises(ValueError, match="full receipt and feedback-event proof"):
        build(
            status=OutboxStatus.CONFIRMED,
            feedback_hash="0x" + "cd" * 32,
            transaction_ref=EvmTransactionRef(hash=EVM_TX),
        )
    with pytest.raises(ValueError, match="must keep the proof it accepted"):
        build(
            status=OutboxStatus.CONFIRMED,
            feedback_hash="0x" + "cd" * 32,
            client_address=CLIENT,
            feedback_uri=BUNDLE,
            transaction_ref=EvmTransactionRef(hash=EVM_TX, block_number=42, log_index=3),
            receipt_proof_ref="sha256:" + "99" * 32,
            block_number=42,
            log_index=3,
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
            confirmed_proof=proof(),
        )


def job(*, status: OutboxStatus = OutboxStatus.PREPARED, value: int = 100):
    subject = identity()
    decision = ReputationDecision(
        kind=DecisionKind.PUBLISH,
        value=value,
        reason_codes=(ReputationReason.SELLER_DELIVERED_AS_QUOTED,),
        audit_bundle_hash=BUNDLE,
        ruleset_version=RULESET_VERSION,
        seller_agent_id=SELLER_AGENT,
        erc8004_agent_id=AGENT_ID,
    )
    submitted = status in (OutboxStatus.PREPARED, OutboxStatus.SUBMITTED_UNKNOWN)
    return ReputationPublishJob(
        job_id="repjob:1",
        identity=subject,
        identity_hash=subject.identity_hash,
        payload_fingerprint=decision.payload_fingerprint(subject),
        decision=decision,
        status=status,
        created_at=NOW,
        updated_at=NOW,
        feedback_hash="0x" + "cd" * 32 if submitted else None,
        client_address=CLIENT if submitted else None,
        feedback_uri=BUNDLE if submitted else None,
        transaction_ref=EvmTransactionRef(hash=EVM_TX) if submitted else None,
    )


def proof(**overrides: object) -> ConfirmedFeedbackProof:
    fields: dict[str, object] = {
        "transaction_ref": EvmTransactionRef(hash=EVM_TX, block_number=42, log_index=3),
        "receipt_proof_ref": "sha256:" + "77" * 32,
        "registry_address": REGISTRY,
        "client_address": CLIENT,
        "erc8004_agent_id": AGENT_ID,
        "value": 100,
        "value_decimals": 0,
        "feedback_hash": "0x" + "cd" * 32,
        "block_number": 42,
        "log_index": 3,
        "feedback_uri": BUNDLE,
    }
    fields.update(overrides)
    return ConfirmedFeedbackProof(**fields)  # type: ignore[arg-type]


def test_a_confirmed_proof_requires_a_receipt_reference_and_objective_value() -> None:
    with pytest.raises(ValueError, match="receipt proof reference"):
        proof(receipt_proof_ref="  ")
    with pytest.raises(ValueError, match="exactly 100 or 0"):
        proof(value=42)
    with pytest.raises(ValueError, match="valueDecimals must be 0"):
        proof(value_decimals=2)
    with pytest.raises(ValueError, match="coordinates cannot be negative"):
        proof(log_index=-1)
    with pytest.raises(ValueError, match="registryAddress must be lowercase"):
        proof(registry_address=REGISTRY.upper())


def test_a_proof_cannot_contradict_its_own_transaction_coordinates() -> None:
    """H1: the decoded event and the reference that carries it are one observation."""
    with pytest.raises(ValueError, match="contradicts its transaction block"):
        proof(
            transaction_ref=EvmTransactionRef(hash=EVM_TX, block_number=7, log_index=3),
            block_number=42,
            log_index=3,
        )
    with pytest.raises(ValueError, match="contradicts its transaction log index"):
        proof(
            transaction_ref=EvmTransactionRef(hash=EVM_TX, block_number=42, log_index=9),
            block_number=42,
            log_index=3,
        )


def test_a_confirmed_proof_must_match_the_job_agent_tags_value_and_transaction() -> None:
    subject = job()

    assert_proof_matches_job(job=subject, proof=proof())

    with pytest.raises(PaymentEvidenceError, match="another agent"):
        assert_proof_matches_job(job=subject, proof=proof(erc8004_agent_id="9"))
    with pytest.raises(PaymentEvidenceError, match="different tags"):
        assert_proof_matches_job(job=subject, proof=proof(tag1="other-tag"))
    with pytest.raises(PaymentEvidenceError, match="not the decided value"):
        assert_proof_matches_job(job=subject, proof=proof(value=0))
    with pytest.raises(PaymentEvidenceError, match="not the submitted transaction"):
        assert_proof_matches_job(
            job=subject,
            proof=proof(
                transaction_ref=EvmTransactionRef(
                    hash="0x" + "ef" * 32, block_number=1, log_index=0
                ),
                block_number=1,
                log_index=0,
            ),
        )


def test_a_confirmed_proof_must_match_the_whole_prepared_commitment() -> None:
    """H1: registry, audit bundle URI, prepared client and prepared hash all bind.

    Each of these was accepted by the rejected implementation, so each gets its own
    regression: a proof from another registry, one that commits to another audit
    bundle, one written by another client, and one carrying a payload this job never
    prepared.
    """
    subject = job()

    with pytest.raises(PaymentEvidenceError, match="another registry"):
        assert_proof_matches_job(
            job=subject,
            proof=proof(registry_address="0x" + "11" * 20),
        )
    with pytest.raises(PaymentEvidenceError, match="commit to this audit bundle"):
        assert_proof_matches_job(
            job=subject, proof=proof(feedback_uri="sha256:" + "12" * 32)
        )
    with pytest.raises(PaymentEvidenceError, match="written by another client"):
        assert_proof_matches_job(job=subject, proof=proof(client_address=OTHER_CLIENT))
    with pytest.raises(PaymentEvidenceError, match="not the payload this job prepared"):
        assert_proof_matches_job(
            job=subject, proof=proof(feedback_hash="0x" + "22" * 32)
        )


def test_a_confirmation_cannot_reclassify_a_base_submission_as_historical() -> None:
    """Reviewer probe: same hash, `HISTORICAL_ON_CHAIN` source, was accepted before.

    The evidence source is chosen at submission time, not discovered at confirmation, so
    a job bound to a Base Sepolia submission can never be closed by a historical proof.
    """
    subject = job()
    assert subject.transaction_ref is not None

    with pytest.raises(PaymentEvidenceError, match="not the submitted transaction"):
        assert_proof_matches_job(
            job=subject,
            proof=proof(
                transaction_ref=EvmTransactionRef(
                    hash=EVM_TX,
                    block_number=42,
                    log_index=3,
                    evidence_source=EvidenceSource.HISTORICAL_ON_CHAIN,
                )
            ),
        )


def test_a_confirmation_cannot_move_known_event_coordinates() -> None:
    """A recorded publication is never relocated to another block or log index."""
    confirmed = replace(job(), block_number=42, log_index=3)

    with pytest.raises(PaymentEvidenceError, match="moved to another block"):
        assert_proof_matches_job(
            job=confirmed,
            proof=proof(
                transaction_ref=EvmTransactionRef(
                    hash=EVM_TX, block_number=43, log_index=3
                ),
                block_number=43,
            ),
        )
    with pytest.raises(PaymentEvidenceError, match="moved to another log index"):
        assert_proof_matches_job(
            job=confirmed,
            proof=proof(
                transaction_ref=EvmTransactionRef(
                    hash=EVM_TX, block_number=42, log_index=4
                ),
                log_index=4,
            ),
        )


def test_a_second_prepare_can_never_change_the_commitment() -> None:
    """`assert_prepared_commitment` is what makes `PREPARED` a real commitment."""
    subject = job()

    assert_prepared_commitment(
        job=subject,
        feedback_hash="0x" + "cd" * 32,
        client_address=CLIENT,
        feedback_uri=BUNDLE,
    )

    with pytest.raises(PaymentEvidenceError, match="decided audit bundle"):
        assert_prepared_commitment(
            job=subject,
            feedback_hash="0x" + "cd" * 32,
            client_address=CLIENT,
            feedback_uri="sha256:" + "13" * 32,
        )
    with pytest.raises(PaymentConflictError, match="another feedback hash"):
        assert_prepared_commitment(
            job=subject,
            feedback_hash="0x" + "ee" * 32,
            client_address=CLIENT,
            feedback_uri=BUNDLE,
        )
    with pytest.raises(PaymentConflictError, match="another client"):
        assert_prepared_commitment(
            job=subject,
            feedback_hash="0x" + "cd" * 32,
            client_address=OTHER_CLIENT,
            feedback_uri=BUNDLE,
        )


def test_a_local_feedback_reference_is_accepted_for_a_synthetic_run() -> None:
    subject = replace(
        job(status=OutboxStatus.PREPARED),
        transaction_ref=LocalTransactionRef(id=LOCAL_TX, run_id=RUN_ID),
    )

    assert_proof_matches_job(
        job=subject,
        proof=proof(transaction_ref=LocalTransactionRef(id=LOCAL_TX, run_id=RUN_ID)),
    )


# --------------------------------------------------------------------------------------
# P6-AC-06.1 / P6-AC-06.3 snapshot provenance and aggregation
# --------------------------------------------------------------------------------------


def scope(**overrides: object) -> ReputationQueryScope:
    fields: dict[str, object] = {
        "chain_id": CHAIN_ID,
        "registry_address": REGISTRY,
        "erc8004_agent_id": AGENT_ID,
        "trusted_clients": (CLIENT,),
        "from_block": 100,
        "to_block": 200,
    }
    fields.update(overrides)
    return ReputationQueryScope(**fields)  # type: ignore[arg-type]


def event(
    *,
    value: int = 100,
    decimals: int = 0,
    client: str = CLIENT,
    block: int = 150,
    log_index: int = 1,
    tag1: str = FEEDBACK_TAG1,
    tag2: str = FEEDBACK_TAG2,
) -> RawFeedbackEvent:
    return RawFeedbackEvent(
        value=value,
        value_decimals=decimals,
        client_address=client,
        block_number=block,
        log_index=log_index,
        transaction_ref=EvmTransactionRef(
            hash=EVM_TX, block_number=block, log_index=log_index
        ),
        tag1=tag1,
        tag2=tag2,
    )


def test_no_matching_evidence_is_neutral_fifty_and_labelled() -> None:
    snapshot = ReputationAggregationPolicy().aggregate(
        snapshot_id="snap-1",
        seller_agent_id=SELLER_AGENT,
        scope=scope(),
        queried_at=NOW,
        events=(),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
    )

    assert snapshot.derived_score == NEUTRAL_SCORE
    assert snapshot.event_count == 0
    assert snapshot.freshness_status is FreshnessStatus.NO_EVIDENCE
    assert snapshot.aggregation_method == AGGREGATION_METHOD


def test_the_derived_score_is_the_decimals_normalized_arithmetic_mean() -> None:
    snapshot = ReputationAggregationPolicy().aggregate(
        snapshot_id="snap-2",
        seller_agent_id=SELLER_AGENT,
        scope=scope(),
        queried_at=NOW,
        events=(
            event(value=100, log_index=1),
            event(value=0, log_index=2),
            # 10000 with two decimals is also 100.
            event(value=10_000, decimals=2, log_index=3),
        ),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        now=NOW,
    )

    assert snapshot.event_count == 3
    assert snapshot.derived_score == pytest.approx(200 / 3, abs=1e-6)
    assert snapshot.freshness_status is FreshnessStatus.FRESH


def test_untrusted_clients_and_wrong_tags_are_excluded_from_the_mean() -> None:
    snapshot = ReputationAggregationPolicy().aggregate(
        snapshot_id="snap-3",
        seller_agent_id=SELLER_AGENT,
        scope=scope(),
        queried_at=NOW,
        events=(
            event(value=100, log_index=1),
            event(value=0, client=OTHER_CLIENT, log_index=2),
            event(value=0, tag2="other-outcome", log_index=3),
        ),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        now=NOW,
    )

    assert snapshot.event_count == 1
    assert snapshot.derived_score == 100.0


def test_a_snapshot_records_its_full_query_provenance() -> None:
    snapshot = ReputationAggregationPolicy().aggregate(
        snapshot_id="snap-4",
        seller_agent_id=SELLER_AGENT,
        scope=scope(),
        queried_at=NOW,
        events=(event(),),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        now=NOW,
    )
    payload = snapshot.to_payload()

    assert payload["scope"] == {
        "chainId": CHAIN_ID,
        "erc8004AgentId": AGENT_ID,
        "fromBlock": 100,
        "registryAddress": REGISTRY,
        "tag1": FEEDBACK_TAG1,
        "tag2": FEEDBACK_TAG2,
        "toBlock": 200,
        "trustedClients": [CLIENT],
    }
    assert payload["queriedAt"] == NOW.isoformat()
    assert payload["eventCount"] == 1
    assert payload["aggregationMethod"] == AGGREGATION_METHOD
    assert payload["evidenceSource"] == EvidenceSource.BASE_SEPOLIA_VERIFIED.value
    raw = payload["rawValues"]
    assert isinstance(raw, list)
    assert raw[0]["blockNumber"] == 150
    assert raw[0]["logIndex"] == 1
    assert raw[0]["valueDecimals"] == 0
    assert snapshot.snapshot_hash.startswith("sha256:")


def test_a_snapshot_older_than_the_window_is_stale_not_fresh() -> None:
    policy = ReputationAggregationPolicy(freshness_window=timedelta(hours=1))
    snapshot = policy.aggregate(
        snapshot_id="snap-5",
        seller_agent_id=SELLER_AGENT,
        scope=scope(),
        queried_at=NOW - timedelta(hours=5),
        events=(event(),),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        now=NOW,
    )

    assert snapshot.freshness_status is FreshnessStatus.STALE
    assert snapshot.freshness_age_seconds == 5 * 3600


def test_a_query_failure_reuses_a_fresh_snapshot_as_stale() -> None:
    policy = ReputationAggregationPolicy(freshness_window=timedelta(hours=24))
    stored = policy.aggregate(
        snapshot_id="snap-6",
        seller_agent_id=SELLER_AGENT,
        scope=scope(),
        queried_at=NOW - timedelta(hours=2),
        events=(event(value=100),),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        now=NOW - timedelta(hours=2),
    )

    reused = policy.stale_or_neutral(
        snapshot=stored,
        now=NOW,
        snapshot_id="snap-7",
        seller_agent_id=SELLER_AGENT,
        scope=scope(),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
    )

    assert reused.snapshot_id == "snap-6"
    assert reused.derived_score == 100.0
    assert reused.freshness_status is FreshnessStatus.STALE


def test_a_query_failure_without_a_fresh_snapshot_is_no_evidence_fifty() -> None:
    policy = ReputationAggregationPolicy(freshness_window=timedelta(hours=1))
    stale = policy.aggregate(
        snapshot_id="snap-8",
        seller_agent_id=SELLER_AGENT,
        scope=scope(),
        queried_at=NOW - timedelta(days=3),
        events=(event(value=100),),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        now=NOW - timedelta(days=3),
    )

    fallback = policy.stale_or_neutral(
        snapshot=stale,
        now=NOW,
        snapshot_id="snap-9",
        seller_agent_id=SELLER_AGENT,
        scope=scope(),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
    )

    assert fallback.snapshot_id == "snap-9"
    assert fallback.derived_score == NEUTRAL_SCORE
    assert fallback.freshness_status is FreshnessStatus.NO_EVIDENCE
    assert fallback.event_count == 0


def test_a_query_scope_requires_a_trusted_client_allow_list() -> None:
    with pytest.raises(ValueError, match="trusted client allow-list"):
        scope(trusted_clients=())
    with pytest.raises(ValueError, match="lowercase"):
        scope(trusted_clients=(CLIENT.upper(),))
    with pytest.raises(ValueError, match="block range is malformed"):
        scope(from_block=200, to_block=100)


def test_the_query_fingerprint_identifies_the_question_not_the_window() -> None:
    """H2: the fingerprint is what a fallback compares, so it must ignore the window.

    The window is derived from the chain head, so two answers to the same question
    differ only by how far the chain moved. Everything that makes it a *different*
    question - agent, registry, chain, tags, trusted clients - must change the hash.
    """
    first = scope(from_block=100, to_block=200)
    second = scope(from_block=201, to_block=300)

    assert first.query_fingerprint == second.query_fingerprint
    assert first.to_payload()["toBlock"] != second.to_payload()["toBlock"]

    for different in (
        scope(erc8004_agent_id="999"),
        scope(trusted_clients=(OTHER_CLIENT,)),
        scope(registry_address="0x" + "11" * 20),
        scope(chain_id=1),
        scope(tag2="other-outcome"),
    ):
        assert different.query_fingerprint != first.query_fingerprint


def test_a_resolved_window_must_end_at_a_real_head() -> None:
    """H3: `fromBlock=toBlock=0` is not a bounded latest range, it is genesis."""
    assert scope(from_block=901, to_block=1000).resolved_for_lookback(100)
    assert scope(from_block=0, to_block=50).resolved_for_lookback(100)
    assert not scope(from_block=0, to_block=0).resolved_for_lookback(100)
    assert not scope(from_block=500, to_block=1000).resolved_for_lookback(100)
    assert not scope(from_block=901, to_block=1000).resolved_for_lookback(0)


def test_a_snapshot_cannot_hold_an_untrusted_or_mismatched_event() -> None:
    """Aggregation filters, but the value object itself also refuses bad provenance."""
    filtered = ReputationAggregationPolicy().aggregate(
        snapshot_id="snap-10",
        seller_agent_id=SELLER_AGENT,
        scope=scope(trusted_clients=(OTHER_CLIENT,)),
        queried_at=NOW,
        events=(event(),),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
    )
    assert filtered.event_count == 0
    assert filtered.derived_score == NEUTRAL_SCORE

    with pytest.raises(ValueError, match="untrusted client"):
        ReputationSnapshot(
            snapshot_id="snap-10b",
            seller_agent_id=SELLER_AGENT,
            scope=scope(trusted_clients=(OTHER_CLIENT,)),
            queried_at=NOW,
            raw_values=(event(),),
            derived_score=100.0,
            freshness_status=FreshnessStatus.FRESH,
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        )
    with pytest.raises(ValueError, match="does not match the queried tags"):
        ReputationSnapshot(
            snapshot_id="snap-10c",
            seller_agent_id=SELLER_AGENT,
            scope=scope(),
            queried_at=NOW,
            raw_values=(event(tag2="other-outcome"),),
            derived_score=100.0,
            freshness_status=FreshnessStatus.FRESH,
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        )
    with pytest.raises(ValueError, match="cannot claim NO_EVIDENCE"):
        ReputationSnapshot(
            snapshot_id="snap-10d",
            seller_agent_id=SELLER_AGENT,
            scope=scope(),
            queried_at=NOW,
            raw_values=(event(),),
            derived_score=100.0,
            freshness_status=FreshnessStatus.NO_EVIDENCE,
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        )


def test_a_normalized_feedback_value_outside_the_range_fails_closed() -> None:
    with pytest.raises(PaymentEvidenceError, match="outside 0..100"):
        ReputationAggregationPolicy().aggregate(
            snapshot_id="snap-11",
            seller_agent_id=SELLER_AGENT,
            scope=scope(),
            queried_at=NOW,
            events=(event(value=101),),
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        )


def test_a_snapshot_refuses_an_event_outside_the_queried_window() -> None:
    """Reviewer probe: a block-999 event in a 500..500 scope was scored 100."""
    with pytest.raises(ValueError, match="outside the queried block range"):
        ReputationSnapshot(
            snapshot_id="snap-range",
            seller_agent_id=SELLER_AGENT,
            scope=scope(from_block=500, to_block=500),
            queried_at=NOW,
            raw_values=(event(block=999, log_index=7),),
            derived_score=100.0,
            freshness_status=FreshnessStatus.FRESH,
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        )


def test_a_snapshot_refuses_an_event_that_contradicts_its_own_reference() -> None:
    """Reviewer probe: nested ref coordinates 3/4 with event coordinates 999/7."""
    mismatched_block = RawFeedbackEvent(
        value=100,
        value_decimals=0,
        client_address=CLIENT,
        block_number=150,
        log_index=7,
        transaction_ref=EvmTransactionRef(hash=EVM_TX, block_number=3, log_index=7),
    )
    mismatched_log = RawFeedbackEvent(
        value=100,
        value_decimals=0,
        client_address=CLIENT,
        block_number=150,
        log_index=7,
        transaction_ref=EvmTransactionRef(hash=EVM_TX, block_number=150, log_index=4),
    )

    for candidate, message in (
        (mismatched_block, "contradicts its transaction block"),
        (mismatched_log, "contradicts its transaction log index"),
    ):
        with pytest.raises(ValueError, match=message):
            ReputationSnapshot(
                snapshot_id="snap-coords",
                seller_agent_id=SELLER_AGENT,
                scope=scope(),
                queried_at=NOW,
                raw_values=(candidate,),
                derived_score=100.0,
                freshness_status=FreshnessStatus.FRESH,
                evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
            )


def test_a_snapshot_refuses_a_historical_or_synthetic_event() -> None:
    """Reviewer probe: a `HISTORICAL_ON_CHAIN` reference was scored as a Base answer."""
    historical = RawFeedbackEvent(
        value=100,
        value_decimals=0,
        client_address=CLIENT,
        block_number=150,
        log_index=1,
        transaction_ref=EvmTransactionRef(
            hash=EVM_TX,
            block_number=150,
            log_index=1,
            evidence_source=EvidenceSource.HISTORICAL_ON_CHAIN,
        ),
    )
    synthetic = RawFeedbackEvent(
        value=100,
        value_decimals=0,
        client_address=CLIENT,
        block_number=150,
        log_index=1,
        transaction_ref=LocalTransactionRef(id=LOCAL_TX, run_id=RUN_ID),
    )

    for candidate in (historical, synthetic):
        with pytest.raises(ValueError, match="different evidence source"):
            ReputationSnapshot(
                snapshot_id="snap-source",
                seller_agent_id=SELLER_AGENT,
                scope=scope(),
                queried_at=NOW,
                raw_values=(candidate,),
                derived_score=100.0,
                freshness_status=FreshnessStatus.FRESH,
                evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
            )


def test_a_fallback_never_reuses_another_agents_snapshot() -> None:
    """Reviewer probe: agent 1's score-100 snapshot answered an agent-2 query."""
    policy = ReputationAggregationPolicy(freshness_window=timedelta(hours=24))
    stored = policy.aggregate(
        snapshot_id="snap-agent-1",
        seller_agent_id=SELLER_AGENT,
        scope=scope(erc8004_agent_id="1"),
        queried_at=NOW - timedelta(minutes=5),
        events=(event(value=100),),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        now=NOW - timedelta(minutes=5),
    )

    for different in (
        scope(erc8004_agent_id="2"),
        scope(trusted_clients=(OTHER_CLIENT,)),
        scope(registry_address="0x" + "11" * 20),
    ):
        answer = policy.stale_or_neutral(
            snapshot=stored,
            now=NOW,
            snapshot_id="snap-other",
            seller_agent_id=SELLER_AGENT,
            scope=different,
            evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        )
        assert answer.derived_score == NEUTRAL_SCORE
        assert answer.freshness_status is FreshnessStatus.NO_EVIDENCE

    other_seller = policy.stale_or_neutral(
        snapshot=stored,
        now=NOW,
        snapshot_id="snap-other-seller",
        seller_agent_id="nemotron-agent",
        scope=scope(erc8004_agent_id="1"),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
    )
    assert other_seller.derived_score == NEUTRAL_SCORE


# --------------------------------------------------------------------------------------
# H2 / H3 query adapter: strict binding, deployment allow-list, bounded latest window
# --------------------------------------------------------------------------------------


class FakeQueryClient:
    """Records what was asked and returns whatever the test wants to answer with."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[dict[str, object]] = []

    async def query(
        self,
        *,
        erc8004_agent_id: str,
        trusted_clients: tuple[str, ...],
        lookback_blocks: int,
    ) -> JsonObject:
        self.calls.append(
            {
                "erc8004AgentId": erc8004_agent_id,
                "trustedClients": trusted_clients,
                "lookbackBlocks": lookback_blocks,
            }
        )
        if isinstance(self.answer, Exception):
            raise self.answer
        assert isinstance(self.answer, dict)
        return self.answer


def query_answer(**overrides: object) -> JsonObject:
    """A well-formed answer to the question the provider actually asks."""
    answer: JsonObject = {
        "chainId": CHAIN_ID,
        "registryAddress": REGISTRY,
        "erc8004AgentId": AGENT_ID,
        "trustedClients": [CLIENT],
        "tag1": FEEDBACK_TAG1,
        "tag2": FEEDBACK_TAG2,
        "fromBlock": 9_001,
        "toBlock": 10_000,
        "latestBlock": 10_000,
        "queriedAt": NOW.isoformat(),
        "events": [
            {
                "value": 100,
                "valueDecimals": 0,
                "clientAddress": CLIENT,
                "blockNumber": 9_500,
                "logIndex": 2,
                "tag1": FEEDBACK_TAG1,
                "tag2": FEEDBACK_TAG2,
                "transactionRef": EvmTransactionRef(
                    hash=EVM_TX, block_number=9_500, log_index=2
                ).to_payload(),
            }
        ],
    }
    answer.update(overrides)
    return answer


def provider(
    *,
    answer: object,
    trusted_clients: tuple[str, ...] = (CLIENT,),
    lookback_blocks: int = 1_000,
    repository: InMemoryEvidenceRepository | None = None,
) -> tuple[GatewayReputationProvider, FakeQueryClient, InMemoryEvidenceRepository]:
    store = repository or InMemoryEvidenceRepository()
    client = FakeQueryClient(answer)
    return (
        GatewayReputationProvider(
            query_client=cast(HttpReputationQueryClient, client),
            snapshots=store,
            clock=FixedClock(NOW),
            chain_id=CHAIN_ID,
            registry_address=REGISTRY,
            trusted_clients=trusted_clients,
            lookback_blocks=lookback_blocks,
        ),
        client,
        store,
    )


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


@pytest.mark.asyncio
async def test_the_deployment_allow_list_is_parsed_strictly() -> None:
    """Requirements 12.10: an explicit deployment declaration, never a compiled wallet."""
    assert parse_feedback_clients(None) == ()
    assert parse_feedback_clients("") == ()
    assert parse_feedback_clients("   ") == ()
    assert parse_feedback_clients(f" {CLIENT.upper()} ,{OTHER_CLIENT}") == (
        CLIENT,
        OTHER_CLIENT,
    )
    assert parse_feedback_clients(f"{CLIENT},{CLIENT}") == (CLIENT,)

    for malformed in ("not-an-address", "0x1234", CLIENT + "ff", f"{CLIENT},oops"):
        with pytest.raises(ValueError, match="0x-prefixed EVM addresses"):
            parse_feedback_clients(malformed)


@pytest.mark.asyncio
async def test_an_undeclared_allow_list_is_neutral_without_any_call() -> None:
    """H3: the default deployment state fails closed before any I/O."""
    subject, client, store = provider(answer=query_answer(), trusted_clients=())

    assert not subject.is_configured
    snapshot = await subject.snapshot(
        seller_agent_id=SELLER_AGENT, erc8004_agent_id=AGENT_ID
    )

    assert snapshot is not None
    assert snapshot.derived_score == NEUTRAL_SCORE
    assert snapshot.freshness_status is FreshnessStatus.NO_EVIDENCE
    assert client.calls == []
    assert await store.get_snapshot(snapshot.snapshot_id) is None


@pytest.mark.asyncio
async def test_a_declared_allow_list_asks_for_a_bounded_latest_window() -> None:
    """H3: the request carries the lookback, and the answer must resolve it."""
    subject, client, store = provider(answer=query_answer(), lookback_blocks=1_000)

    snapshot = await subject.snapshot(
        seller_agent_id=SELLER_AGENT, erc8004_agent_id=AGENT_ID
    )

    assert client.calls == [
        {
            "erc8004AgentId": AGENT_ID,
            "trustedClients": (CLIENT,),
            "lookbackBlocks": 1_000,
        }
    ]
    assert snapshot is not None
    assert snapshot.derived_score == 100.0
    assert snapshot.scope.from_block == 9_001
    assert snapshot.scope.to_block == 10_000
    assert await store.get_snapshot(snapshot.snapshot_id) is not None


@pytest.mark.asyncio
async def test_an_answer_to_a_different_question_is_never_scored() -> None:
    """Reviewer probe: agent 999 / client B / block 999 / historical was scored 100."""
    cases: tuple[JsonObject, ...] = (
        query_answer(erc8004AgentId="999"),
        query_answer(trustedClients=[OTHER_CLIENT]),
        query_answer(registryAddress="0x" + "11" * 20),
        query_answer(chainId=1),
        query_answer(tag2="other-outcome"),
        # Window that does not end at the reported head, or is wider than requested.
        query_answer(toBlock=9_999),
        query_answer(fromBlock=1, toBlock=10_000, latestBlock=10_000),
        # The genesis-only window the rejected implementation always requested.
        query_answer(fromBlock=0, toBlock=0, latestBlock=0),
    )

    for answer in cases:
        subject, _, store = provider(answer=answer)
        snapshot = await subject.snapshot(
            seller_agent_id=SELLER_AGENT, erc8004_agent_id=AGENT_ID
        )
        assert snapshot is not None
        assert snapshot.derived_score == NEUTRAL_SCORE
        assert snapshot.freshness_status is FreshnessStatus.NO_EVIDENCE
        assert await store.get_snapshot(snapshot.snapshot_id) is None


@pytest.mark.asyncio
async def test_a_broken_event_in_a_valid_answer_fails_closed() -> None:
    """An out-of-window or self-contradicting event is a broken answer, not a weak one."""
    out_of_window = query_answer(
        events=[
            {
                "value": 100,
                "valueDecimals": 0,
                "clientAddress": CLIENT,
                "blockNumber": 42,
                "logIndex": 2,
                "tag1": FEEDBACK_TAG1,
                "tag2": FEEDBACK_TAG2,
                "transactionRef": EvmTransactionRef(
                    hash=EVM_TX, block_number=42, log_index=2
                ).to_payload(),
            }
        ]
    )
    contradicting = query_answer(
        events=[
            {
                "value": 100,
                "valueDecimals": 0,
                "clientAddress": CLIENT,
                "blockNumber": 9_500,
                "logIndex": 2,
                "tag1": FEEDBACK_TAG1,
                "tag2": FEEDBACK_TAG2,
                "transactionRef": EvmTransactionRef(
                    hash=EVM_TX, block_number=9_501, log_index=4
                ).to_payload(),
            }
        ]
    )

    for answer in (out_of_window, contradicting):
        subject, _, _ = provider(answer=answer)
        snapshot = await subject.snapshot(
            seller_agent_id=SELLER_AGENT, erc8004_agent_id=AGENT_ID
        )
        assert snapshot is not None
        assert snapshot.derived_score == NEUTRAL_SCORE


@pytest.mark.asyncio
async def test_a_failed_query_reuses_only_the_same_questions_snapshot() -> None:
    """H2: the stored answer must be for exactly this agent and this scope."""
    subject, _, store = provider(answer=query_answer())
    first = await subject.snapshot(
        seller_agent_id=SELLER_AGENT, erc8004_agent_id=AGENT_ID
    )
    assert first is not None and first.derived_score == 100.0

    failing, _, _ = provider(
        answer=httpx.ConnectError("gateway down"), repository=store
    )
    reused = await failing.snapshot(
        seller_agent_id=SELLER_AGENT, erc8004_agent_id=AGENT_ID
    )
    assert reused is not None
    assert reused.derived_score == 100.0
    assert reused.freshness_status is FreshnessStatus.STALE

    other_agent = await failing.snapshot(
        seller_agent_id=SELLER_AGENT, erc8004_agent_id="2"
    )
    assert other_agent is not None
    assert other_agent.derived_score == NEUTRAL_SCORE
    assert other_agent.freshness_status is FreshnessStatus.NO_EVIDENCE

    other_seller = await failing.snapshot(
        seller_agent_id="nemotron-agent", erc8004_agent_id=AGENT_ID
    )
    assert other_seller is not None
    assert other_seller.derived_score == NEUTRAL_SCORE


@pytest.mark.asyncio
async def test_a_provider_refuses_a_malformed_allow_list_or_window() -> None:
    store = InMemoryEvidenceRepository()
    client = cast(HttpReputationQueryClient, FakeQueryClient(query_answer()))

    def build(**overrides: object) -> GatewayReputationProvider:
        fields: dict[str, object] = {
            "query_client": client,
            "snapshots": store,
            "clock": FixedClock(NOW),
            "chain_id": CHAIN_ID,
            "registry_address": REGISTRY,
            "trusted_clients": (CLIENT,),
        }
        fields.update(overrides)
        return GatewayReputationProvider(**fields)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="lowercase EVM addresses"):
        build(trusted_clients=(CLIENT.upper(),))
    with pytest.raises(ValueError, match="lookback window is out of range"):
        build(lookback_blocks=0)
    with pytest.raises(ValueError, match="lookback window is out of range"):
        build(lookback_blocks=MAX_REPUTATION_LOOKBACK_BLOCKS + 1)
