from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.audit import (
    AuditFinding,
    AuditService,
    AuditSeverity,
)
from buyer_audit_api.core.errors import (
    EvidenceIntegrityError,
    PaymentConflictError,
    PaymentEvidenceError,
    PaymentPolicyError,
)
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.payment import PaymentIntentState, PaymentService, WalletPolicy

OWNER = "0x0000000000000000000000000000000000000001"
BUYER = "0x0000000000000000000000000000000000000002"
TOKEN = "0x0000000000000000000000000000000000000003"
SELLER = "0x0000000000000000000000000000000000000004"
CONTRACT = "0x0000000000000000000000000000000000000005"


async def seed_payment_ready(
    repository: InMemoryEvidenceRepository,
    clock,
    *,
    purchase_id: str = "purchase-payment",
    amount_units: int = 100_000,
    budget_units: int = 250_000,
    request_policy: dict[str, object] | None = None,
    identity_verified: bool = True,
) -> None:
    await repository.bind_buyer_wallet(
        owner_address=OWNER,
        buyer_wallet_address=BUYER,
        bound_at=clock.now(),
    )
    await repository.append_event(
        purchase_id=purchase_id,
        event_type=EventType.REQUESTED,
        occurred_at=clock.now(),
        actor={"id": OWNER, "type": "user"},
        payload={
            "budgetUnits": budget_units,
            "domain": "fake",
            "normalizedRequest": {"value": "domain-neutral"},
            "policy": request_policy or {},
        },
    )
    await repository.append_event(
        purchase_id=purchase_id,
        event_type=EventType.QUOTED,
        occurred_at=clock.now(),
        actor={"id": "buyer-orchestrator", "type": "service"},
        payload={
            "signedQuotes": [
                {
                    "quote_id": "quote-payment",
                    "purchase_id": purchase_id,
                    "seller_agent_id": "seller-agent-1",
                    "provider_id": "gemini",
                    "model_id": "model-a",
                    "model_version": "v1",
                    "amount_units": amount_units,
                    "token": TOKEN,
                    "pay_to": SELLER,
                    "available": True,
                    "expires_at": (clock.now() + timedelta(hours=1)).isoformat(),
                    "signer_address": SELLER,
                    "chain_id": 84532,
                    "verifying_contract": CONTRACT,
                }
            ],
            "quoteIdentityEvidence": [
                {
                    "quoteId": "quote-payment",
                    "erc8004AgentId": "1",
                    "identityVerified": identity_verified,
                }
            ],
        },
        evidence_refs=("quote-payment",),
    )
    await repository.append_event(
        purchase_id=purchase_id,
        event_type=EventType.DECIDED,
        occurred_at=clock.now(),
        actor={"id": "buyer-agent", "type": "agent"},
        payload={"winner": {"quote_id": "quote-payment"}},
        evidence_refs=("quote-payment",),
    )


async def configure_policy(
    repository: InMemoryEvidenceRepository,
    clock,
    *,
    per_transaction_limit_units: int = 200_000,
    daily_limit_units: int = 1_000_000,
) -> None:
    await repository.put_wallet_policy(
        WalletPolicy(
            buyer_wallet_address=BUYER,
            policy_date=clock.now().date().isoformat(),
            token=TOKEN,
            per_transaction_limit_units=per_transaction_limit_units,
            daily_limit_units=daily_limit_units,
        )
    )


@pytest.mark.asyncio
async def test_concurrent_claim_is_atomic_idempotent_and_domain_neutral(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await seed_payment_ready(repository, clock)
    await configure_policy(repository, clock)
    service = PaymentService(repository=repository, clock=clock)

    intents = await asyncio.gather(*(service.claim("purchase-payment") for _ in range(12)))

    assert len({intent.permit2_nonce for intent in intents}) == 1
    assert all(intent.state == PaymentIntentState.CLAIMED for intent in intents)
    assert intents[0].permit2_nonce.isdecimal()
    events = await repository.list_events("purchase-payment")
    assert [event.type for event in events] == [
        EventType.REQUESTED,
        EventType.QUOTED,
        EventType.DECIDED,
        EventType.PAYMENT_INTENT_CLAIMED,
    ]
    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER,
        policy_date=clock.now().date().isoformat(),
    )
    assert policy is not None
    assert policy.reserved_units == 100_000
    assert policy.spent_units == 0
    assert events[-1].payload["decisionEventHash"] == events[2].event_hash


@pytest.mark.asyncio
async def test_erc3009_claim_uses_one_random_bytes32_nonce_and_preserves_idempotency(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await seed_payment_ready(repository, clock, purchase_id="purchase-erc3009")
    await configure_policy(repository, clock)
    service = PaymentService(repository=repository, clock=clock, transfer_method="eip3009")

    first, second = await asyncio.gather(
        service.claim("purchase-erc3009"), service.claim("purchase-erc3009")
    )

    assert first == second
    assert first.transfer_method == "eip3009"
    assert first.authorization_nonce is not None
    assert len(first.authorization_nonce) == 66
    assert first.authorization_nonce.startswith("0x")
    int(first.authorization_nonce[2:], 16)
    events = await repository.list_events("purchase-erc3009")
    assert events[-1].payload["transferMethod"] == "eip3009"
    assert events[-1].payload["authorizationNonce"] == first.authorization_nonce


@pytest.mark.asyncio
async def test_ai_selection_audit_detects_filter_score_and_explanation_mismatch(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await repository.append_event(
        purchase_id="purchase-bad-selection",
        event_type=EventType.REQUESTED,
        occurred_at=clock.now(),
        actor={"id": OWNER, "type": "user"},
        payload={
            "domain": "ai_inference",
            "budgetUnits": 100_000,
            "policy": {},
            "normalizedRequest": {
                "priority": "price",
                "allowed_sellers": [],
                "required_capabilities": [],
                "min_input_limit": 1,
                "min_output_limit": 1,
                "max_latency_ms": None,
            },
        },
    )
    quoted = await repository.append_event(
        purchase_id="purchase-bad-selection",
        event_type=EventType.QUOTED,
        occurred_at=clock.now(),
        actor={"id": "buyer-agent", "type": "agent"},
        payload={
            "signedQuotes": [
                {
                    "quote_id": "over-budget",
                    "provider_id": "gemini",
                    "model_id": "m",
                    "model_version": "v1",
                    "amount_units": 200_000,
                    "available": True,
                    "expires_at": (clock.now() + timedelta(hours=1)).isoformat(),
                    "input_limit": 1,
                    "output_limit": 1,
                    "expected_latency_ms": 1,
                }
            ],
            "benchmarkSnapshots": [
                {
                    "snapshot_id": "snapshot-1",
                    "provider_id": "gemini",
                    "model_id": "m",
                    "model_version": "v1",
                    "observed_at": clock.now().isoformat(),
                    "capabilities": [],
                }
            ],
            "quoteIdentityEvidence": [
                {
                    "quoteId": "over-budget",
                    "identityVerified": True,
                }
            ],
        },
    )
    await repository.append_event(
        purchase_id="purchase-bad-selection",
        event_type=EventType.DECIDED,
        occurred_at=clock.now(),
        actor={"id": "buyer-agent", "type": "agent"},
        payload={
            "preset": "quality",
            "winner": {
                "quote_id": "over-budget",
                "provider_id": "gemini",
                "model_id": "m",
                "total_score": 1,
            },
            "eligible": [
                {
                    "quote_id": "over-budget",
                    "provider_id": "gemini",
                    "model_id": "m",
                    "total_score": 99,
                    "weights": {"quality": 100},
                }
            ],
            "rejected": [],
            "generatedExplanation": "unrelated",
        },
        expected_event_count=quoted.sequence,
        expected_head_event_hash=quoted.event_hash,
    )

    report = await AuditService(repository=repository, clock=clock).audit("purchase-bad-selection")
    codes = {finding.code for finding in report.findings}
    assert {
        "AUD-PRIORITY-PRESET-MISMATCH",
        "AUD-SELECTION-WEIGHTS-MISMATCH",
        "AUD-INFERIOR-CANDIDATE-SELECTED",
        "AUD-EXPLANATION-SELECTION-MISMATCH",
        "AUD-HARD-FILTER-MISMATCH",
    }.issubset(codes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("budget_units", "request_limit", "wallet_limit", "match"),
    [
        (99_999, None, 200_000, "request budget"),
        (250_000, 99_999, 200_000, "request transaction limit"),
        (250_000, None, 99_999, "wallet transaction limit"),
    ],
)
async def test_claim_rejects_every_budget_boundary_without_reserving(
    clock,
    budget_units: int,
    request_limit: int | None,
    wallet_limit: int,
    match: str,
) -> None:
    repository = InMemoryEvidenceRepository()
    request_policy = {"maxTransactionUnits": request_limit} if request_limit is not None else {}
    await seed_payment_ready(
        repository,
        clock,
        budget_units=budget_units,
        request_policy=request_policy,
    )
    await configure_policy(
        repository,
        clock,
        per_transaction_limit_units=wallet_limit,
    )
    service = PaymentService(repository=repository, clock=clock)

    with pytest.raises(PaymentPolicyError, match=match):
        await service.claim("purchase-payment")

    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER,
        policy_date=clock.now().date().isoformat(),
    )
    assert policy is not None and policy.reserved_units == 0
    assert [event.type for event in await repository.list_events("purchase-payment")] == [
        EventType.REQUESTED,
        EventType.QUOTED,
        EventType.DECIDED,
    ]


@pytest.mark.asyncio
async def test_claim_rejects_unverified_identity_and_later_lifecycle(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await seed_payment_ready(repository, clock, identity_verified=False)
    await configure_policy(repository, clock)
    service = PaymentService(repository=repository, clock=clock)

    with pytest.raises(PaymentEvidenceError, match="identity"):
        await service.claim("purchase-payment")

    events = await repository.list_events("purchase-payment")
    quoted = events[1]
    quoted.payload["quoteIdentityEvidence"][0]["identityVerified"] = True
    repository._events["purchase-payment"][1] = quoted  # type: ignore[attr-defined]
    with pytest.raises(EvidenceIntegrityError, match="payload hash"):
        await service.claim("purchase-payment")


@pytest.mark.asyncio
async def test_authorize_reconcile_and_settle_is_exactly_once(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await seed_payment_ready(repository, clock)
    await configure_policy(repository, clock)
    service = PaymentService(repository=repository, clock=clock)
    await service.claim("purchase-payment")

    authorized = await asyncio.gather(
        *(
            service.authorize(
                purchase_id="purchase-payment",
                authorization_hash="0xauthorization",
                signature="0xsignature",
            )
            for _ in range(12)
        )
    )
    assert all(item.state == PaymentIntentState.AUTHORIZED for item in authorized)
    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER,
        policy_date=clock.now().date().isoformat(),
    )
    assert policy is not None and policy.reserved_units == 100_000

    transaction_hash = "0x" + "ab" * 32
    with pytest.raises(PaymentConflictError, match="cannot fail"):
        await service.fail_confirmed(
            purchase_id="purchase-payment",
            reason="facilitator timed out",
            transaction_hash=transaction_hash,
            block_number=42,
            receipt_status=0,
        )
    reconciled = await service.require_reconciliation(
        purchase_id="purchase-payment",
        reason="facilitator timed out after submission",
        transaction_hash=transaction_hash,
    )
    assert reconciled.state == PaymentIntentState.RECONCILIATION_REQUIRED
    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER,
        policy_date=clock.now().date().isoformat(),
    )
    assert policy is not None and policy.reserved_units == 100_000

    settled = await asyncio.gather(
        *(
            service.settle(
                purchase_id="purchase-payment",
                transaction_hash=transaction_hash,
                block_number=42,
                transfer_log_index=3,
                receipt_status=1,
                token=TOKEN,
                from_address=BUYER,
                to_address=SELLER,
                amount_units=100_000,
            )
            for _ in range(12)
        )
    )
    assert all(item.state == PaymentIntentState.SETTLED for item in settled)
    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER,
        policy_date=clock.now().date().isoformat(),
    )
    assert policy is not None
    assert policy.reserved_units == 0
    assert policy.spent_units == 100_000
    events = await repository.list_events("purchase-payment")
    assert [event.type for event in events] == [
        EventType.REQUESTED,
        EventType.QUOTED,
        EventType.DECIDED,
        EventType.PAYMENT_INTENT_CLAIMED,
        EventType.PAYMENT_AUTHORIZED,
        EventType.PAYMENT_RECONCILIATION_REQUIRED,
        EventType.PAYMENT_SETTLED,
    ]


@pytest.mark.asyncio
async def test_hashless_reconciliation_binds_recovered_transaction_once(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await seed_payment_ready(repository, clock)
    await configure_policy(repository, clock)
    service = PaymentService(repository=repository, clock=clock)
    await service.claim("purchase-payment")
    await service.authorize(
        purchase_id="purchase-payment",
        authorization_hash="0xauthorization",
        signature="0xsignature",
    )
    pending = await service.require_reconciliation(
        purchase_id="purchase-payment",
        reason="seller response lost after authorization",
    )
    assert pending.transaction_hash is None
    transaction_hash = "0x" + "ab" * 32
    bound = await asyncio.gather(
        *(
            service.bind_reconciliation_transaction(
                purchase_id="purchase-payment",
                transaction_hash=transaction_hash,
            )
            for _ in range(8)
        )
    )
    assert all(intent.transaction_hash == transaction_hash for intent in bound)
    with pytest.raises(PaymentConflictError, match="different transaction"):
        await service.bind_reconciliation_transaction(
            purchase_id="purchase-payment",
            transaction_hash="0x" + "cd" * 32,
        )
    settled = await service.settle(
        purchase_id="purchase-payment",
        transaction_hash=transaction_hash,
        block_number=42,
        transfer_log_index=3,
        receipt_status=1,
        token=TOKEN,
        from_address=BUYER,
        to_address=SELLER,
        amount_units=100_000,
    )
    assert settled.state == PaymentIntentState.SETTLED
    assert [event.type for event in await repository.list_events("purchase-payment")] == [
        EventType.REQUESTED,
        EventType.QUOTED,
        EventType.DECIDED,
        EventType.PAYMENT_INTENT_CLAIMED,
        EventType.PAYMENT_AUTHORIZED,
        EventType.PAYMENT_RECONCILIATION_REQUIRED,
        EventType.PAYMENT_SUBMISSION_IDENTIFIED,
        EventType.PAYMENT_SETTLED,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token", "sender", "recipient", "amount", "match"),
    [
        (CONTRACT, BUYER, SELLER, 100_000, "token contract"),
        (TOKEN, CONTRACT, SELLER, 100_000, "sender"),
        (TOKEN, BUYER, CONTRACT, 100_000, "recipient"),
        (TOKEN, BUYER, SELLER, 99_999, "amount"),
    ],
)
async def test_wrong_receipt_binding_never_spends_reserved_budget(
    clock,
    token: str,
    sender: str,
    recipient: str,
    amount: int,
    match: str,
) -> None:
    repository = InMemoryEvidenceRepository()
    await seed_payment_ready(repository, clock)
    await configure_policy(repository, clock)
    service = PaymentService(repository=repository, clock=clock)
    await service.claim("purchase-payment")
    await service.authorize(
        purchase_id="purchase-payment",
        authorization_hash="0xauthorization",
        signature="0xsignature",
    )
    await service.require_reconciliation(
        purchase_id="purchase-payment",
        reason="submitted; receipt pending",
        transaction_hash="0x" + "cd" * 32,
    )

    with pytest.raises(PaymentEvidenceError, match=match):
        await service.settle(
            purchase_id="purchase-payment",
            transaction_hash="0x" + "cd" * 32,
            block_number=42,
            transfer_log_index=0,
            receipt_status=1,
            token=token,
            from_address=sender,
            to_address=recipient,
            amount_units=amount,
        )
    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER,
        policy_date=clock.now().date().isoformat(),
    )
    assert policy is not None
    assert policy.reserved_units == 100_000
    assert policy.spent_units == 0


@pytest.mark.asyncio
async def test_confirmed_revert_releases_reservation_but_ambiguous_does_not(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await seed_payment_ready(repository, clock)
    await configure_policy(repository, clock)
    service = PaymentService(repository=repository, clock=clock)
    await service.claim("purchase-payment")
    await service.authorize(
        purchase_id="purchase-payment",
        authorization_hash="0xauthorization",
        signature="0xsignature",
    )
    transaction_hash = "0x" + "ab" * 32
    await service.require_reconciliation(
        purchase_id="purchase-payment",
        reason="submitted; receipt pending",
        transaction_hash=transaction_hash,
    )

    failed = await service.fail_confirmed(
        purchase_id="purchase-payment",
        reason="receipt status zero",
        transaction_hash=transaction_hash,
        block_number=42,
        receipt_status=0,
    )
    assert failed.state == PaymentIntentState.FAILED
    policy = await repository.get_wallet_policy(
        buyer_wallet_address=BUYER,
        policy_date=clock.now().date().isoformat(),
    )
    assert policy is not None
    assert policy.reserved_units == 0
    assert policy.spent_units == 0
    with pytest.raises(PaymentConflictError, match="different failure reason"):
        await service.fail_confirmed(
            purchase_id="purchase-payment",
            reason="different reason",
            transaction_hash=transaction_hash,
            block_number=42,
            receipt_status=0,
        )


@pytest.mark.asyncio
async def test_existing_intent_resumes_after_quote_expiry_and_midnight(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await seed_payment_ready(repository, clock)
    await configure_policy(repository, clock)
    service = PaymentService(repository=repository, clock=clock)
    original = await service.claim("purchase-payment")

    clock.value += timedelta(days=1, hours=2)
    resumed = await service.claim("purchase-payment")

    assert resumed == original
    assert resumed.policy_date != clock.now().date().isoformat()
    events = await repository.list_events("purchase-payment")
    assert sum(event.type == EventType.PAYMENT_INTENT_CLAIMED for event in events) == 1


@pytest.mark.asyncio
async def test_successful_delivery_has_one_deterministic_normal_audit(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await seed_payment_ready(repository, clock)
    await configure_policy(repository, clock)
    payment = PaymentService(repository=repository, clock=clock)
    await payment.claim("purchase-payment")
    await payment.authorize(
        purchase_id="purchase-payment",
        authorization_hash="0xauthorization",
        signature="0xsecret-signature",
    )
    await payment.require_reconciliation(
        purchase_id="purchase-payment",
        reason="submitted; receipt pending",
        transaction_hash="0x" + "ab" * 32,
    )
    await payment.settle(
        purchase_id="purchase-payment",
        transaction_hash="0x" + "ab" * 32,
        block_number=42,
        transfer_log_index=3,
        receipt_status=1,
        token=TOKEN,
        from_address=BUYER,
        to_address=SELLER,
        amount_units=100_000,
    )
    await repository.append_event(
        purchase_id="purchase-payment",
        event_type=EventType.DELIVERED,
        occurred_at=clock.now(),
        actor={"id": "seller-agent-1", "type": "agent"},
        payload={
            "sellerAgentId": "seller-agent-1",
            "providerId": "gemini",
            "modelId": "model-a",
            "modelVersion": "v1",
            "responseHash": "sha256:response",
            "responseId": "response-1",
        },
    )
    audit = AuditService(repository=repository, clock=clock)
    reports = await asyncio.gather(*(audit.audit("purchase-payment") for _ in range(12)))
    assert all(report.severity == AuditSeverity.NORMAL for report in reports)
    assert all(report.findings == () for report in reports)
    assert len({report.audit_bundle_hash for report in reports}) == 1
    events = await repository.list_events("purchase-payment")
    assert [event.type for event in events].count(EventType.AUDITED) == 1
    authorization = next(event for event in events if event.type == EventType.PAYMENT_AUTHORIZED)
    assert "signature" not in authorization.payload
    assert authorization.payload["signatureHash"].startswith("sha256:")
    assert "0xsecret-signature" not in str(events)


@pytest.mark.asyncio
async def test_settled_without_delivery_is_risk_and_pending_is_caution(clock) -> None:
    settled_repository = InMemoryEvidenceRepository()
    await seed_payment_ready(settled_repository, clock)
    await configure_policy(settled_repository, clock)
    settled_payment = PaymentService(repository=settled_repository, clock=clock)
    await settled_payment.claim("purchase-payment")
    await settled_payment.authorize(
        purchase_id="purchase-payment",
        authorization_hash="0xauthorization",
        signature="0xsignature",
    )
    await settled_payment.require_reconciliation(
        purchase_id="purchase-payment",
        reason="submitted; receipt pending",
        transaction_hash="0x" + "ab" * 32,
    )
    await settled_payment.settle(
        purchase_id="purchase-payment",
        transaction_hash="0x" + "ab" * 32,
        block_number=42,
        transfer_log_index=3,
        receipt_status=1,
        token=TOKEN,
        from_address=BUYER,
        to_address=SELLER,
        amount_units=100_000,
    )
    risk = await AuditService(repository=settled_repository, clock=clock).audit("purchase-payment")
    assert risk.severity == AuditSeverity.RISK
    assert {item.code for item in risk.findings} == {"AUD-DELIVERY-MISSING"}
    assert all(
        event.type != EventType.AUDITED
        for event in await settled_repository.list_events("purchase-payment")
    )

    pending_repository = InMemoryEvidenceRepository()
    await seed_payment_ready(pending_repository, clock, purchase_id="purchase-pending")
    await configure_policy(pending_repository, clock)
    pending_payment = PaymentService(repository=pending_repository, clock=clock)
    await pending_payment.claim("purchase-pending")
    await pending_payment.authorize(
        purchase_id="purchase-pending",
        authorization_hash="0xauthorization",
        signature="0xsignature",
    )
    await pending_payment.require_reconciliation(
        purchase_id="purchase-pending", reason="RPC timeout"
    )
    caution = await AuditService(repository=pending_repository, clock=clock).audit(
        "purchase-pending"
    )
    assert caution.severity == AuditSeverity.CAUTION
    assert {item.code for item in caution.findings} == {"AUD-PAYMENT-UNCONFIRMED"}
    assert all(
        event.type != EventType.AUDITED
        for event in await pending_repository.list_events("purchase-pending")
    )


@pytest.mark.asyncio
async def test_semantic_advisor_can_only_add_non_authoritative_warning(clock) -> None:
    repository = InMemoryEvidenceRepository()
    await seed_payment_ready(repository, clock)

    class InvalidAdvisor:
        async def advise(self, **_kwargs):
            return (
                AuditFinding(
                    "SEM-NORMAL",
                    AuditSeverity.NORMAL,
                    "invalid",
                    "semantic analysis cannot assert normal authority",
                    (),
                    authority="semantic",
                ),
            )

    with pytest.raises(ValueError, match="only add caution/risk"):
        await AuditService(
            repository=repository,
            clock=clock,
            semantic_advisor=InvalidAdvisor(),
        ).audit("purchase-payment")
    assert all(
        event.type != EventType.AUDITED
        for event in await repository.list_events("purchase-payment")
    )
