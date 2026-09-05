"""Terminal audit orchestration and the durable reputation outbox.

`P6-AC-05.1`, `P6-AC-05.3`, `P6-AC-05.4`, `P6-AC-05.5`, `P6-AC-05.7` and the terminal part
of `P6-AC-03.3`/`P6-AC-03.5`: exactly one decision and one job per purchase across worker
races and restarts, and no confirmation without a receipt plus a matching feedback event.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.audit import RULESET_VERSION, AuditService
from buyer_audit_api.core.errors import (
    EvidenceIntegrityError,
    PaymentConflictError,
    PaymentEvidenceError,
)
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import (
    EventType,
    EvidenceSource,
    EvmTransactionRef,
    JsonObject,
    LocalTransactionRef,
)
from buyer_audit_api.core.projections import PurchaseProjectionService
from buyer_audit_api.core.reputation import (
    FEEDBACK_TAG1,
    FEEDBACK_TAG2,
    ConfirmedFeedbackProof,
    DecisionKind,
    OutboxStatus,
    PublishIdentity,
    ReputationReason,
    decision_from_event,
)
from buyer_audit_api.core.terminal import TerminalAuditCoordinator

PURCHASE_ID = "purchase-terminal-loop"
OWNER = "0x0000000000000000000000000000000000000001"
BUYER = "0x0000000000000000000000000000000000000002"
TOKEN = "0x0000000000000000000000000000000000000003"
SELLER = "0x0000000000000000000000000000000000000004"
REGISTRY = "0x8004b663056a597dffe9eccc1965a193b7388713"
CLIENT = "0x0000000000000000000000000000000000000009"
CHAIN_ID = 84532
QUOTED_UNITS = 100_000
EVM_TX = "0x" + "ab" * 32
FEEDBACK_TX = "0x" + "cd" * 32
RUN_ID = "a" * 32
DECIDED_AT = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
SELLER_AGENT = "gemini-agent"
AGENT_ID = "1"

PROJECTIONS = PurchaseProjectionService()


def requested_payload() -> JsonObject:
    return {
        "budgetUnits": 250_000,
        "domain": "ai_inference",
        "normalizedRequest": {
            "prompt_hash": "sha256:" + "0" * 64,
            "prompt_length": 5,
            "priority": "balanced",
        },
        "policy": {"maxTransactionUnits": 1_000_000},
    }


def quoted_payload(*, identity_verified: bool = True) -> JsonObject:
    return {
        "benchmarkSnapshots": [
            {
                "snapshot_id": "snapshot-gemini",
                "provider_id": "gemini",
                "model_id": "gemini-fast",
                "model_version": "v1",
                "observed_at": DECIDED_AT.isoformat(),
                "source_url": "https://example.test/benchmark",
                "content_hash": "sha256:benchmark",
                "quality_score": 82,
                "speed_score": 95,
                "reputation_score": 88,
                "capabilities": ["korean"],
            }
        ],
        "signedQuotes": [
            {
                "quote_id": "quote-gemini",
                "purchase_id": PURCHASE_ID,
                "seller_agent_id": SELLER_AGENT,
                "erc8004_agent_id": AGENT_ID,
                "provider_id": "gemini",
                "model_id": "gemini-fast",
                "model_version": "v1",
                "amount_units": QUOTED_UNITS,
                "token": TOKEN,
                "pay_to": SELLER,
                "expected_latency_ms": 700,
                "input_limit": 8_000,
                "output_limit": 2_000,
                "available": True,
                "expires_at": (DECIDED_AT + timedelta(hours=2)).isoformat(),
                "quote_nonce": "nonce-gemini",
                "signature": "0xsigned-gemini",
                "signer_address": SELLER,
                "chain_id": CHAIN_ID,
                "verifying_contract": TOKEN,
            }
        ],
        "quoteIdentityEvidence": [
            {
                "quoteId": "quote-gemini",
                "erc8004AgentId": AGENT_ID,
                "identityRegistry": REGISTRY,
                "agentWallet": SELLER,
                "signerAddress": SELLER,
                "identityVerified": identity_verified,
            }
        ],
    }


def decided_payload() -> JsonObject:
    weights = {"quality": 40, "price": 25, "speed": 20, "reputation": 10, "freshness": 5}
    winner = {
        "quote_id": "quote-gemini",
        "provider_id": "gemini",
        "model_id": "gemini-fast",
        "total_score": 90.0,
        "component_scores": {
            "quality": 82.0,
            "price": 60.0,
            "speed": 95.0,
            "reputation": 88.0,
            "freshness": 100.0,
        },
        "weights": weights,
    }
    return {
        "preset": "balanced",
        "winner": winner,
        "eligible": [winner],
        "rejected": [],
        "benchmark_snapshot_ids": ["snapshot-gemini"],
        "explanation": "deterministic",
        "generatedExplanation": "deterministic",
    }


def claim_payload() -> JsonObject:
    return {
        "amountUnits": QUOTED_UNITS,
        "buyerWalletAddress": BUYER,
        "payTo": SELLER,
        "quoteId": "quote-gemini",
        "token": TOKEN,
        "transferMethod": "eip3009",
    }


def settled_payload() -> JsonObject:
    return {
        "amountUnits": QUOTED_UNITS,
        "blockNumber": 1,
        "from": BUYER,
        "receiptStatus": 1,
        "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
        "to": SELLER,
        "token": TOKEN,
        "transactionHash": EVM_TX,
        "transferLogIndex": 0,
    }


def delivered_payload(*, provider_id: str = "gemini") -> JsonObject:
    return {
        "sellerAgentId": SELLER_AGENT,
        "providerId": provider_id,
        "modelId": "gemini-fast",
        "modelVersion": "v1",
        "responseHash": "sha256:response",
        "responseId": "response-1",
    }


def mismatch_payload() -> JsonObject:
    return {
        "terminalOutcomeKey": f"terminal:{PURCHASE_ID}",
        "quoteBinding": {"amountUnits": QUOTED_UNITS, "token": TOKEN, "payTo": SELLER},
        "actualTransfer": {
            "amountUnits": 200_000,
            "token": TOKEN,
            "from": BUYER,
            "to": SELLER,
        },
        "mismatchedFields": ["amount"],
        "transactionRef": EvmTransactionRef(hash=EVM_TX).to_payload(),
        "proofRef": "sha256:" + "22" * 32,
        "reconciliationAttempts": 1,
        "evidenceSource": EvidenceSource.BASE_SEPOLIA_VERIFIED.value,
    }


async def seed(
    repository: InMemoryEvidenceRepository,
    specs: tuple[tuple[EventType, JsonObject], ...],
    *,
    purchase_id: str = PURCHASE_ID,
) -> None:
    for event_type, payload in specs:
        await repository.append_event(
            purchase_id=purchase_id,
            event_type=event_type,
            occurred_at=DECIDED_AT,
            actor={"id": OWNER, "type": "user"},
            payload=payload,
        )


def prefix(*, identity_verified: bool = True) -> tuple[tuple[EventType, JsonObject], ...]:
    return (
        (EventType.REQUESTED, requested_payload()),
        (EventType.QUOTED, quoted_payload(identity_verified=identity_verified)),
        (EventType.DECIDED, decided_payload()),
        (EventType.PAYMENT_INTENT_CLAIMED, claim_payload()),
        (EventType.PAYMENT_AUTHORIZED, {"authorizationHash": "0xauth"}),
    )


async def settled_delivered(
    *, identity_verified: bool = True, purchase_id: str = PURCHASE_ID
) -> InMemoryEvidenceRepository:
    repository = InMemoryEvidenceRepository()
    await seed(
        repository,
        (
            *prefix(identity_verified=identity_verified),
            (EventType.PAYMENT_SETTLED, settled_payload()),
            (EventType.DELIVERED, delivered_payload()),
        ),
        purchase_id=purchase_id,
    )
    return repository


async def mismatched(purchase_id: str = PURCHASE_ID) -> InMemoryEvidenceRepository:
    repository = InMemoryEvidenceRepository()
    await seed(
        repository,
        (
            *prefix(),
            (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
            (EventType.PAYMENT_MISMATCH_CONFIRMED, mismatch_payload()),
        ),
        purchase_id=purchase_id,
    )
    return repository


def coordinator(repository: InMemoryEvidenceRepository, clock) -> TerminalAuditCoordinator:
    return TerminalAuditCoordinator(
        orchestration=repository,
        outbox=repository,
        clock=clock,
        chain_id=CHAIN_ID,
        registry_address=REGISTRY,
    )


def identity(purchase_id: str = PURCHASE_ID) -> PublishIdentity:
    return PublishIdentity(
        chain_id=CHAIN_ID,
        registry_address=REGISTRY,
        purchase_id=purchase_id,
        seller_agent_id=SELLER_AGENT,
    )


def proof(
    *, transaction_ref=None, value: int = 100, **overrides: object
) -> ConfirmedFeedbackProof:
    fields: dict[str, object] = {
        "transaction_ref": transaction_ref
        or EvmTransactionRef(hash=FEEDBACK_TX, block_number=99, log_index=2),
        "receipt_proof_ref": "sha256:" + "88" * 32,
        "client_address": CLIENT,
        "erc8004_agent_id": AGENT_ID,
        "value": value,
        "value_decimals": 0,
        "feedback_hash": "0x" + "ee" * 32,
        "block_number": 99,
        "log_index": 2,
        "tag1": FEEDBACK_TAG1,
        "tag2": FEEDBACK_TAG2,
    }
    fields.update(overrides)
    return ConfirmedFeedbackProof(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# P6-AC-05.1 only a persisted terminal audit produces a decision
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settled_delivery_finalizes_audit_decision_and_job_together(clock) -> None:
    repository = await settled_delivered()

    result = await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)

    assert result.finalized is True
    assert result.decision is not None
    assert result.decision.kind is DecisionKind.PUBLISH
    assert result.decision.value == 100
    assert result.job is not None
    assert result.job.status is OutboxStatus.PENDING
    assert result.job.identity_hash == identity().identity_hash

    events = await repository.list_events(PURCHASE_ID)
    types = [event.type for event in events]
    assert types.count(EventType.AUDITED) == 1
    assert types.count(EventType.REPUTATION_DECIDED) == 1
    # The decision is appended directly after the audit it is bound to.
    assert types[-2:] == [EventType.AUDITED, EventType.REPUTATION_DECIDED]
    head = await repository.get_event_head(PURCHASE_ID)
    assert head is not None
    verify_event_chain(
        events,
        expected_event_count=head.event_count,
        expected_head_event_hash=head.head_event_hash,
    )
    decided = decision_from_event(events[-1])
    assert decided == result.decision
    assert events[-1].payload["publishIdentityHash"] == identity().identity_hash
    assert events[-1].payload["payloadFingerprint"] == result.job.payload_fingerprint
    assert events[-1].payload["auditBundleHash"] == events[-2].payload["auditBundleHash"]
    assert events[-1].payload["rulesetVersion"] == RULESET_VERSION


@pytest.mark.asyncio
async def test_a_nonterminal_purchase_creates_no_decision_and_no_job(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await seed(
        repository,
        (
            *prefix(),
            (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
        ),
    )

    result = await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)

    assert result.finalized is False
    assert result.decision is None
    assert result.job is None
    events = await repository.list_events(PURCHASE_ID)
    assert all(
        event.type not in (EventType.AUDITED, EventType.REPUTATION_DECIDED)
        for event in events
    )
    assert await repository.get_by_identity(identity().identity_hash) is None


@pytest.mark.asyncio
async def test_a_confirmed_mismatch_publishes_zero_and_still_enqueues_one_job(clock) -> None:
    repository = await mismatched()

    result = await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)

    assert result.decision is not None
    assert result.decision.value == 0
    assert ReputationReason.PAYMENT_MISMATCH_CONFIRMED in result.decision.reason_codes
    assert result.job is not None
    assert result.job.status is OutboxStatus.PENDING


@pytest.mark.asyncio
async def test_an_unverified_seller_identity_defers_without_a_publishable_job(clock) -> None:
    repository = await settled_delivered(identity_verified=False)

    result = await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)

    assert result.decision is not None
    assert result.decision.kind is DecisionKind.DEFER
    assert result.decision.reason_codes == (ReputationReason.SELLER_IDENTITY_UNKNOWN,)
    assert result.job is not None
    assert result.job.status is OutboxStatus.DEFERRED
    # A deferred job is terminal: no worker will ever claim it.
    assert result.job.is_terminal is True
    assert await repository.claim_job(
        worker_id="w1", lease_until=DECIDED_AT + timedelta(seconds=30), now=DECIDED_AT
    ) is None


@pytest.mark.asyncio
async def test_finalize_is_idempotent_and_never_appends_a_second_decision(clock) -> None:
    repository = await settled_delivered()
    subject = coordinator(repository, clock)

    first = await subject.finalize_if_eligible(PURCHASE_ID)
    events_after_first = await repository.list_events(PURCHASE_ID)
    second = await subject.finalize_if_eligible(PURCHASE_ID)

    assert first.finalized is True
    assert second.finalized is False
    assert second.decision == first.decision
    assert second.job is not None
    assert second.job.job_id == first.job.job_id  # type: ignore[union-attr]
    assert len(await repository.list_events(PURCHASE_ID)) == len(events_after_first)


@pytest.mark.asyncio
async def test_a_purchase_audited_by_the_read_path_still_gets_exactly_one_decision(
    clock,
) -> None:
    """`AuditService` may have already persisted the audit; only the decision is missing."""
    repository = await settled_delivered()
    audit = await AuditService(repository=repository, clock=clock).audit(PURCHASE_ID)
    events_before = await repository.list_events(PURCHASE_ID)

    result = await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)

    assert result.finalized is True
    assert result.audit is not None
    assert result.audit.audit_bundle_hash == audit.audit_bundle_hash
    events = await repository.list_events(PURCHASE_ID)
    assert [event.type for event in events].count(EventType.AUDITED) == 1
    assert len(events) == len(events_before) + 1
    assert events[-1].type is EventType.REPUTATION_DECIDED
    assert events[-1].payload["auditBundleHash"] == audit.audit_bundle_hash


@pytest.mark.asyncio
async def test_a_stale_persisted_audit_blocks_the_decision(clock) -> None:
    """P6-AC-03.5 still governs: a head that moved past the audit needs a policy."""
    repository = await settled_delivered()
    await AuditService(repository=repository, clock=clock).audit(PURCHASE_ID)
    await repository.append_event(
        purchase_id=PURCHASE_ID,
        event_type=EventType.SENSITIVE_PAYLOAD_ACCESSED,
        occurred_at=DECIDED_AT,
        actor={"id": OWNER, "type": "user"},
        payload={"kind": "request", "payloadId": "payload-1"},
    )

    with pytest.raises(EvidenceIntegrityError, match="advanced after the terminal audit"):
        await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)

    events = await repository.list_events(PURCHASE_ID)
    assert all(event.type != EventType.REPUTATION_DECIDED for event in events)


# --------------------------------------------------------------------------------------
# P6-AC-05.3 / P6-AC-05.7 races and restarts
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_workers_finalizing_concurrently_create_one_decision_and_one_job(
    clock,
) -> None:
    repository = await settled_delivered()
    first = coordinator(repository, clock)
    second = coordinator(repository, clock)

    results = await asyncio.gather(
        first.finalize_if_eligible(PURCHASE_ID),
        second.finalize_if_eligible(PURCHASE_ID),
        return_exceptions=True,
    )

    finalized = [
        item
        for item in results
        if not isinstance(item, BaseException) and item.finalized
    ]
    assert len(finalized) == 1
    events = await repository.list_events(PURCHASE_ID)
    types = [event.type for event in events]
    assert types.count(EventType.AUDITED) == 1
    assert types.count(EventType.REPUTATION_DECIDED) == 1
    jobs = [
        job
        for job in await repository.list_recoverable_jobs(now=DECIDED_AT)
    ]
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_only_one_worker_holds_the_lease_at_a_time(clock) -> None:
    repository = await settled_delivered()
    await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)
    lease_until = DECIDED_AT + timedelta(seconds=30)

    first = await repository.claim_job(
        worker_id="worker-a", lease_until=lease_until, now=DECIDED_AT
    )
    second = await repository.claim_job(
        worker_id="worker-b", lease_until=lease_until, now=DECIDED_AT
    )

    assert first is not None
    assert first.worker_id == "worker-a"
    assert first.status is OutboxStatus.LEASED
    assert first.attempt_count == 1
    assert second is None


@pytest.mark.asyncio
async def test_an_expired_lease_is_reclaimable_and_counts_the_attempt(clock) -> None:
    repository = await settled_delivered()
    await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)
    await repository.claim_job(
        worker_id="worker-a",
        lease_until=DECIDED_AT + timedelta(seconds=30),
        now=DECIDED_AT,
    )

    later = DECIDED_AT + timedelta(minutes=5)
    reclaimed = await repository.claim_job(
        worker_id="worker-b", lease_until=later + timedelta(seconds=30), now=later
    )

    assert reclaimed is not None
    assert reclaimed.worker_id == "worker-b"
    assert reclaimed.attempt_count == 2


@pytest.mark.asyncio
async def test_a_prepared_job_is_never_prepared_twice_with_another_transaction(
    clock,
) -> None:
    repository = await settled_delivered()
    job = (await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)).job
    assert job is not None
    lease_until = DECIDED_AT + timedelta(seconds=30)
    leased = await repository.claim_job(
        worker_id="worker-a", lease_until=lease_until, now=DECIDED_AT
    )
    assert leased is not None

    prepared = await repository.mark_prepared(
        job_id=job.job_id,
        worker_id="worker-a",
        payload_fingerprint=job.payload_fingerprint,
        transaction_ref=EvmTransactionRef(hash=FEEDBACK_TX),
        feedback_hash="0x" + "ee" * 32,
        now=DECIDED_AT,
    )
    assert prepared.status is OutboxStatus.PREPARED

    # Same submission: the committed record comes back.
    again = await repository.mark_prepared(
        job_id=job.job_id,
        worker_id="worker-a",
        payload_fingerprint=job.payload_fingerprint,
        transaction_ref=EvmTransactionRef(hash=FEEDBACK_TX),
        feedback_hash="0x" + "ee" * 32,
        now=DECIDED_AT,
    )
    assert again.transaction_ref == prepared.transaction_ref

    # A different submission for an already-prepared job is a conflict, not a new write.
    with pytest.raises(
        PaymentConflictError, match="already bound to another submitted transaction"
    ):
        await repository.mark_prepared(
            job_id=job.job_id,
            worker_id="worker-a",
            payload_fingerprint=job.payload_fingerprint,
            transaction_ref=EvmTransactionRef(hash="0x" + "99" * 32),
            feedback_hash="0x" + "ee" * 32,
            now=DECIDED_AT,
        )


@pytest.mark.asyncio
async def test_a_different_fingerprint_can_never_move_a_job(clock) -> None:
    repository = await settled_delivered()
    job = (await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)).job
    assert job is not None
    await repository.claim_job(
        worker_id="worker-a",
        lease_until=DECIDED_AT + timedelta(seconds=30),
        now=DECIDED_AT,
    )

    with pytest.raises(PaymentConflictError, match="different payload fingerprint"):
        await repository.mark_prepared(
            job_id=job.job_id,
            worker_id="worker-a",
            payload_fingerprint="sha256:" + "00" * 32,
            transaction_ref=EvmTransactionRef(hash=FEEDBACK_TX),
            feedback_hash="0x" + "ee" * 32,
            now=DECIDED_AT,
        )


@pytest.mark.asyncio
async def test_a_worker_without_the_lease_cannot_move_a_job(clock) -> None:
    repository = await settled_delivered()
    job = (await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)).job
    assert job is not None
    await repository.claim_job(
        worker_id="worker-a",
        lease_until=DECIDED_AT + timedelta(seconds=30),
        now=DECIDED_AT,
    )

    with pytest.raises(PaymentConflictError, match="lease is not held"):
        await repository.mark_prepared(
            job_id=job.job_id,
            worker_id="worker-b",
            payload_fingerprint=job.payload_fingerprint,
            transaction_ref=EvmTransactionRef(hash=FEEDBACK_TX),
            feedback_hash="0x" + "ee" * 32,
            now=DECIDED_AT,
        )


@pytest.mark.asyncio
async def test_one_feedback_transaction_cannot_back_two_jobs(clock) -> None:
    other_purchase = "purchase-terminal-second"
    repository = await settled_delivered()
    await seed(
        repository,
        (
            *prefix(),
            (EventType.PAYMENT_SETTLED, settled_payload()),
            (EventType.DELIVERED, delivered_payload()),
        ),
        purchase_id=other_purchase,
    )
    subject = coordinator(repository, clock)
    first = (await subject.finalize_if_eligible(PURCHASE_ID)).job
    second = (await subject.finalize_if_eligible(other_purchase)).job
    assert first is not None and second is not None
    assert first.job_id != second.job_id

    lease_until = DECIDED_AT + timedelta(seconds=30)
    await repository.claim_job(worker_id="w1", lease_until=lease_until, now=DECIDED_AT)
    await repository.mark_prepared(
        job_id=first.job_id,
        worker_id="w1",
        payload_fingerprint=first.payload_fingerprint,
        transaction_ref=EvmTransactionRef(hash=FEEDBACK_TX),
        feedback_hash="0x" + "ee" * 32,
        now=DECIDED_AT,
    )
    await repository.claim_job(worker_id="w2", lease_until=lease_until, now=DECIDED_AT)

    with pytest.raises(PaymentConflictError, match="transaction reference is already recorded"):
        await repository.mark_prepared(
            job_id=second.job_id,
            worker_id="w2",
            payload_fingerprint=second.payload_fingerprint,
            transaction_ref=EvmTransactionRef(hash=FEEDBACK_TX),
            feedback_hash="0x" + "ee" * 32,
            now=DECIDED_AT,
        )


# --------------------------------------------------------------------------------------
# P6-AC-05.4 / P6-AC-05.5 recovery and confirmed-only recording
# --------------------------------------------------------------------------------------


async def prepared_job(repository: InMemoryEvidenceRepository, clock):
    job = (await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)).job
    assert job is not None
    await repository.claim_job(
        worker_id="worker-a",
        lease_until=DECIDED_AT + timedelta(seconds=30),
        now=DECIDED_AT,
    )
    return await repository.mark_prepared(
        job_id=job.job_id,
        worker_id="worker-a",
        payload_fingerprint=job.payload_fingerprint,
        transaction_ref=EvmTransactionRef(hash=FEEDBACK_TX),
        feedback_hash="0x" + "ee" * 32,
        now=DECIDED_AT,
    )


@pytest.mark.asyncio
async def test_an_unknown_submission_must_restate_the_prepared_transaction(clock) -> None:
    repository = await settled_delivered()
    job = await prepared_job(repository, clock)

    unknown = await repository.mark_submitted_unknown(
        job_id=job.job_id,
        worker_id="worker-a",
        payload_fingerprint=job.payload_fingerprint,
        transaction_ref=EvmTransactionRef(hash=FEEDBACK_TX),
        reason="receipt lookup timed out",
        now=DECIDED_AT,
    )
    assert unknown.status is OutboxStatus.SUBMITTED_UNKNOWN
    assert unknown.transaction_ref == job.transaction_ref

    with pytest.raises(
        PaymentConflictError, match="already bound to another submitted transaction"
    ):
        await repository.mark_submitted_unknown(
            job_id=job.job_id,
            worker_id="worker-a",
            payload_fingerprint=job.payload_fingerprint,
            transaction_ref=EvmTransactionRef(hash="0x" + "77" * 32),
            reason="a brand new transaction is forbidden",
            now=DECIDED_AT,
        )


@pytest.mark.asyncio
async def test_a_recovered_job_is_still_claimable_after_a_restart(clock) -> None:
    repository = await settled_delivered()
    job = await prepared_job(repository, clock)

    later = DECIDED_AT + timedelta(minutes=10)
    recoverable = await repository.list_recoverable_jobs(now=later)

    assert [item.job_id for item in recoverable] == [job.job_id]
    assert recoverable[0].status is OutboxStatus.PREPARED
    # The recorded submission survives the restart, so recovery looks it up instead of
    # building a new transaction.
    assert recoverable[0].transaction_ref == job.transaction_ref
    assert recoverable[0].feedback_hash == "0x" + "ee" * 32


@pytest.mark.asyncio
async def test_confirmation_requires_a_matching_agent_value_and_transaction(clock) -> None:
    repository = await settled_delivered()
    job = await prepared_job(repository, clock)

    with pytest.raises(PaymentEvidenceError, match="another agent"):
        await repository.mark_confirmed(
            job_id=job.job_id,
            worker_id="worker-a",
            payload_fingerprint=job.payload_fingerprint,
            proof=proof(erc8004_agent_id="9"),
            now=DECIDED_AT,
        )
    with pytest.raises(PaymentEvidenceError, match="not the decided value"):
        await repository.mark_confirmed(
            job_id=job.job_id,
            worker_id="worker-a",
            payload_fingerprint=job.payload_fingerprint,
            proof=proof(value=0),
            now=DECIDED_AT,
        )
    with pytest.raises(PaymentEvidenceError, match="not the submitted transaction"):
        await repository.mark_confirmed(
            job_id=job.job_id,
            worker_id="worker-a",
            payload_fingerprint=job.payload_fingerprint,
            proof=proof(
                transaction_ref=EvmTransactionRef(
                    hash="0x" + "55" * 32, block_number=1, log_index=0
                )
            ),
            now=DECIDED_AT,
        )

    stored = await repository.get_job(job.job_id)
    assert stored is not None
    assert stored.status is OutboxStatus.PREPARED


@pytest.mark.asyncio
async def test_a_confirmed_job_is_terminal_and_idempotent(clock) -> None:
    repository = await settled_delivered()
    job = await prepared_job(repository, clock)

    confirmed = await repository.mark_confirmed(
        job_id=job.job_id,
        worker_id="worker-a",
        payload_fingerprint=job.payload_fingerprint,
        proof=proof(),
        now=DECIDED_AT,
    )

    assert confirmed.status is OutboxStatus.CONFIRMED
    assert confirmed.receipt_proof_ref == "sha256:" + "88" * 32
    assert confirmed.block_number == 99
    assert confirmed.log_index == 2
    assert confirmed.is_terminal is True

    again = await repository.mark_confirmed(
        job_id=job.job_id,
        worker_id="worker-a",
        payload_fingerprint=job.payload_fingerprint,
        proof=proof(),
        now=DECIDED_AT,
    )
    assert again.status is OutboxStatus.CONFIRMED

    with pytest.raises(PaymentConflictError, match="different confirmation"):
        await repository.mark_confirmed(
            job_id=job.job_id,
            worker_id="worker-a",
            payload_fingerprint=job.payload_fingerprint,
            proof=proof(receipt_proof_ref="sha256:" + "11" * 32),
            now=DECIDED_AT,
        )
    assert await repository.claim_job(
        worker_id="w9",
        lease_until=DECIDED_AT + timedelta(seconds=30),
        now=DECIDED_AT,
    ) is None


@pytest.mark.asyncio
async def test_a_confirmed_publication_cannot_be_turned_into_a_conflict(clock) -> None:
    repository = await settled_delivered()
    job = await prepared_job(repository, clock)
    await repository.mark_confirmed(
        job_id=job.job_id,
        worker_id="worker-a",
        payload_fingerprint=job.payload_fingerprint,
        proof=proof(),
        now=DECIDED_AT,
    )

    with pytest.raises(PaymentConflictError, match="cannot be turned into a conflict"):
        await repository.record_conflict(
            identity_hash=job.identity_hash,
            requested_fingerprint="sha256:" + "00" * 32,
            reason_code="FINGERPRINT_MISMATCH",
            now=DECIDED_AT,
        )


@pytest.mark.asyncio
async def test_a_conflict_stops_the_job_without_a_new_transaction(clock) -> None:
    repository = await settled_delivered()
    job = (await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)).job
    assert job is not None

    conflicted = await repository.record_conflict(
        identity_hash=job.identity_hash,
        requested_fingerprint="sha256:" + "00" * 32,
        reason_code="FINGERPRINT_MISMATCH",
        now=DECIDED_AT,
    )

    assert conflicted.status is OutboxStatus.CONFLICT
    assert conflicted.is_terminal is True
    assert conflicted.transaction_ref is None
    assert await repository.claim_job(
        worker_id="w1",
        lease_until=DECIDED_AT + timedelta(seconds=30),
        now=DECIDED_AT,
    ) is None


@pytest.mark.asyncio
async def test_a_local_feedback_submission_is_accepted_for_a_synthetic_run(clock) -> None:
    repository = await settled_delivered()
    job = (await coordinator(repository, clock).finalize_if_eligible(PURCHASE_ID)).job
    assert job is not None
    local = LocalTransactionRef(id=f"localtx:{RUN_ID}:feedback:000001", run_id=RUN_ID)
    await repository.claim_job(
        worker_id="worker-a",
        lease_until=DECIDED_AT + timedelta(seconds=30),
        now=DECIDED_AT,
    )
    await repository.mark_prepared(
        job_id=job.job_id,
        worker_id="worker-a",
        payload_fingerprint=job.payload_fingerprint,
        transaction_ref=local,
        feedback_hash="0x" + "ee" * 32,
        now=DECIDED_AT,
    )

    confirmed = await repository.mark_confirmed(
        job_id=job.job_id,
        worker_id="worker-a",
        payload_fingerprint=job.payload_fingerprint,
        proof=proof(transaction_ref=local),
        now=DECIDED_AT,
    )

    assert confirmed.status is OutboxStatus.CONFIRMED
    assert confirmed.evidence_source is EvidenceSource.SYNTHETIC_LOCAL


# --------------------------------------------------------------------------------------
# Recovery sweep
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_recovery_sweep_only_finalizes_purchases_without_a_decision(clock) -> None:
    finalized_purchase = PURCHASE_ID
    pending_purchase = "purchase-terminal-pending"
    nonterminal_purchase = "purchase-terminal-open"
    repository = await settled_delivered()
    await seed(
        repository,
        (
            *prefix(),
            (EventType.PAYMENT_SETTLED, settled_payload()),
            (EventType.DELIVERED, delivered_payload()),
        ),
        purchase_id=pending_purchase,
    )
    await seed(
        repository,
        (
            *prefix(),
            (EventType.PAYMENT_RECONCILIATION_REQUIRED, {"reason": "receipt pending"}),
        ),
        purchase_id=nonterminal_purchase,
    )
    subject = coordinator(repository, clock)
    await subject.finalize_if_eligible(finalized_purchase)
    events_before = {
        purchase: len(await repository.list_events(purchase))
        for purchase in (finalized_purchase, pending_purchase, nonterminal_purchase)
    }

    results = await subject.recover_pending(
        [finalized_purchase, pending_purchase, nonterminal_purchase]
    )

    by_purchase = {item.purchase_id: item for item in results}
    assert by_purchase[finalized_purchase].finalized is False
    assert by_purchase[pending_purchase].finalized is True
    assert by_purchase[nonterminal_purchase].finalized is False
    assert by_purchase[nonterminal_purchase].job is None
    assert (
        len(await repository.list_events(finalized_purchase))
        == events_before[finalized_purchase]
    )
    assert (
        len(await repository.list_events(nonterminal_purchase))
        == events_before[nonterminal_purchase]
    )
    assert (
        len(await repository.list_events(pending_purchase))
        == events_before[pending_purchase] + 2
    )


@pytest.mark.asyncio
async def test_rerunning_the_sweep_is_free_of_new_effects(clock) -> None:
    repository = await settled_delivered()
    subject = coordinator(repository, clock)
    await subject.recover_pending([PURCHASE_ID])
    events_after_first = await repository.list_events(PURCHASE_ID)

    await subject.recover_pending([PURCHASE_ID])
    await subject.recover_pending([PURCHASE_ID])

    assert len(await repository.list_events(PURCHASE_ID)) == len(events_after_first)
    jobs = await repository.list_recoverable_jobs(now=DECIDED_AT)
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_snapshots_are_immutable_once_stored(clock) -> None:
    from buyer_audit_api.core.reputation import (
        RawFeedbackEvent,
        ReputationAggregationPolicy,
        ReputationQueryScope,
    )

    repository = InMemoryEvidenceRepository()
    scope = ReputationQueryScope(
        chain_id=CHAIN_ID,
        registry_address=REGISTRY,
        erc8004_agent_id=AGENT_ID,
        trusted_clients=(CLIENT,),
        from_block=1,
        to_block=100,
    )
    policy = ReputationAggregationPolicy()
    snapshot = policy.aggregate(
        snapshot_id="snap-terminal-1",
        seller_agent_id=SELLER_AGENT,
        scope=scope,
        queried_at=DECIDED_AT,
        events=(
            RawFeedbackEvent(
                value=100,
                value_decimals=0,
                client_address=CLIENT,
                block_number=50,
                log_index=1,
                transaction_ref=EvmTransactionRef(
                    hash=FEEDBACK_TX, block_number=50, log_index=1
                ),
            ),
        ),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        now=DECIDED_AT,
    )

    stored = await repository.put_snapshot(snapshot)
    assert stored.snapshot_hash == snapshot.snapshot_hash
    assert await repository.get_snapshot("snap-terminal-1") == snapshot
    assert await repository.latest_snapshot(SELLER_AGENT) == snapshot

    # Re-storing the identical snapshot is a no-op; a different answer is a conflict.
    assert (await repository.put_snapshot(snapshot)).snapshot_hash == snapshot.snapshot_hash
    mutated = policy.aggregate(
        snapshot_id="snap-terminal-1",
        seller_agent_id=SELLER_AGENT,
        scope=scope,
        queried_at=DECIDED_AT,
        events=(),
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
    )
    with pytest.raises(PaymentConflictError, match="immutable once stored"):
        await repository.put_snapshot(mutated)
