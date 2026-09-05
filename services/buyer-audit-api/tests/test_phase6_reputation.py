"""Pure reputation core: the 100/0/DEFER table, identity/fingerprint, and aggregation.

These tests fix `P6-AC-05.1`-`P6-AC-05.5` and `P6-AC-06.1`-`P6-AC-06.3` before any
persistence or transport exists, so a later refactor cannot quietly move the policy.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from buyer_audit_api.core.audit import (
    RULESET_VERSION,
    AuditAuthority,
    AuditFinding,
    AuditReport,
    AuditSeverity,
)
from buyer_audit_api.core.errors import PaymentEvidenceError
from buyer_audit_api.core.models import (
    EvidenceSource,
    EvmTransactionRef,
    LocalTransactionRef,
)
from buyer_audit_api.core.payment import PaymentIntentState
from buyer_audit_api.core.projections import PaymentStatus
from buyer_audit_api.core.reputation import (
    AGGREGATION_METHOD,
    FEEDBACK_TAG1,
    FEEDBACK_TAG2,
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


def test_a_submitted_job_must_carry_its_transaction_reference() -> None:
    subject = identity()
    decision = POLICY.decide(
        audit=report(),
        payment_status=PaymentStatus.PAYMENT_SETTLED,
        payment_state=PaymentIntentState.SETTLED,
        attribution=attribution(),
    )
    # PREPARED freezes the payload commitment; the reference is bound once it exists.
    with pytest.raises(ValueError, match="requires the committed feedback hash"):
        ReputationPublishJob(
            job_id="repjob:1",
            identity=subject,
            identity_hash=subject.identity_hash,
            payload_fingerprint=decision.payload_fingerprint(subject),
            decision=decision,
            status=OutboxStatus.PREPARED,
            created_at=NOW,
            updated_at=NOW,
        )
    with pytest.raises(ValueError, match="requires a submitted transaction reference"):
        ReputationPublishJob(
            job_id="repjob:1",
            identity=subject,
            identity_hash=subject.identity_hash,
            payload_fingerprint=decision.payload_fingerprint(subject),
            decision=decision,
            status=OutboxStatus.SUBMITTED_UNKNOWN,
            created_at=NOW,
            updated_at=NOW,
            feedback_hash="0x" + "cd" * 32,
        )
    with pytest.raises(ValueError, match="requires a receipt proof"):
        ReputationPublishJob(
            job_id="repjob:1",
            identity=subject,
            identity_hash=subject.identity_hash,
            payload_fingerprint=decision.payload_fingerprint(subject),
            decision=decision,
            status=OutboxStatus.CONFIRMED,
            created_at=NOW,
            updated_at=NOW,
            feedback_hash="0x" + "cd" * 32,
            transaction_ref=EvmTransactionRef(hash=EVM_TX),
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
        transaction_ref=EvmTransactionRef(hash=EVM_TX) if submitted else None,
    )


def proof(**overrides: object) -> ConfirmedFeedbackProof:
    fields: dict[str, object] = {
        "transaction_ref": EvmTransactionRef(hash=EVM_TX, block_number=42, log_index=3),
        "receipt_proof_ref": "sha256:" + "77" * 32,
        "client_address": CLIENT,
        "erc8004_agent_id": AGENT_ID,
        "value": 100,
        "value_decimals": 0,
        "feedback_hash": "0x" + "cd" * 32,
        "block_number": 42,
        "log_index": 3,
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
                )
            ),
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


def test_the_query_fingerprint_ignores_to_block_so_a_new_head_is_a_new_snapshot() -> None:
    first = scope(to_block=200)
    second = scope(to_block=300)

    assert first.query_fingerprint == second.query_fingerprint
    assert first.to_payload()["toBlock"] != second.to_payload()["toBlock"]


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
