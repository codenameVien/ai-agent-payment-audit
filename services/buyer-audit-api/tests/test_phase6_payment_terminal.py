from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.errors import (
    PaymentConflictError,
    PaymentEvidenceError,
)
from buyer_audit_api.core.hashing import sha256_bytes
from buyer_audit_api.core.models import (
    EventType,
    EvidenceSource,
    EvmTransactionRef,
    LocalTransactionRef,
    ScenarioMetadata,
)
from buyer_audit_api.core.payment import (
    ActualTransfer,
    ConfirmedMismatchProof,
    NoTransferProof,
    PaymentIntent,
    PaymentIntentState,
    PaymentService,
    ReconciliationCheck,
    ReconciliationVerifierOutcome,
    WalletPolicy,
    mismatched_quote_fields,
)

OWNER = "0x0000000000000000000000000000000000000001"
BUYER = "0x0000000000000000000000000000000000000002"
TOKEN = "0x0000000000000000000000000000000000000003"
SELLER = "0x0000000000000000000000000000000000000004"
CONTRACT = "0x0000000000000000000000000000000000000005"
OTHER_TOKEN = "0x0000000000000000000000000000000000000006"
WRONG_RECIPIENT = "0x0000000000000000000000000000000000000007"
PURCHASE_ID = "purchase-terminal"
QUOTED_UNITS = 100_000
RUN_ID = "a" * 32
LOCAL_TX_ID = f"localtx:{RUN_ID}:payment:000001"
EVM_TX = "0x" + "ab" * 32
SCENARIO = ScenarioMetadata(
    run_id=RUN_ID,
    scenario_id="P6-A04-WRONG-AMOUNT",
    catalog_version="phase6.v1",
    catalog_hash="sha256:" + "cd" * 32,
)


async def seed(
    repository: InMemoryEvidenceRepository,
    clock,
    *,
    purchase_id: str = PURCHASE_ID,
    configure_policy: bool = True,
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
            "budgetUnits": 250_000,
            "domain": "fake",
            "normalizedRequest": {"value": "terminal"},
            "policy": {},
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
                    "quote_id": "quote-terminal",
                    "purchase_id": purchase_id,
                    "seller_agent_id": "seller-agent-1",
                    "provider_id": "gemini",
                    "model_id": "model-a",
                    "model_version": "v1",
                    "amount_units": QUOTED_UNITS,
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
                    "quoteId": "quote-terminal",
                    "erc8004AgentId": "1",
                    "identityVerified": True,
                }
            ],
        },
    )
    await repository.append_event(
        purchase_id=purchase_id,
        event_type=EventType.DECIDED,
        occurred_at=clock.now(),
        actor={"id": "buyer-agent", "type": "agent"},
        payload={"winner": {"quote_id": "quote-terminal"}},
    )
    if not configure_policy:
        return
    await repository.put_wallet_policy(
        WalletPolicy(
            buyer_wallet_address=BUYER,
            policy_date=clock.now().date().isoformat(),
            token=TOKEN,
            per_transaction_limit_units=250_000,
            daily_limit_units=1_000_000,
        )
    )


async def synthetic_service(
    repository: InMemoryEvidenceRepository, clock, *, purchase_id: str = PURCHASE_ID
) -> PaymentService:
    """A purchase whose submission identity is an attested `localtx:` proof."""
    service = PaymentService(repository=repository, clock=clock)
    await seed(repository, clock, purchase_id=purchase_id)
    await service.claim(purchase_id)
    await service.authorize(
        purchase_id=purchase_id,
        authorization_hash="0xauthorization",
        signature="0xsignature",
    )
    await service.require_reconciliation(
        purchase_id=purchase_id,
        reason="synthetic facilitator returned a local submission",
        local_transaction_id=LOCAL_TX_ID,
        scenario=SCENARIO,
    )
    return service


async def onchain_service(
    repository: InMemoryEvidenceRepository, clock, *, purchase_id: str = PURCHASE_ID
) -> PaymentService:
    """A purchase whose submission identity is a Base Sepolia transaction hash."""
    service = PaymentService(repository=repository, clock=clock)
    await seed(repository, clock, purchase_id=purchase_id)
    await service.claim(purchase_id)
    await service.authorize(
        purchase_id=purchase_id,
        authorization_hash="0xauthorization",
        signature="0xsignature",
    )
    await service.require_reconciliation(
        purchase_id=purchase_id,
        reason="facilitator returned submitted transaction",
        transaction_hash=EVM_TX,
    )
    return service


def check(
    attempt: int,
    outcome: ReconciliationVerifierOutcome,
    *,
    submission_ref: str = LOCAL_TX_ID,
    evidence_source: EvidenceSource = EvidenceSource.SYNTHETIC_LOCAL,
    scenario: ScenarioMetadata | None = SCENARIO,
    confirmations: int = 3,
) -> ReconciliationCheck:
    return ReconciliationCheck(
        attempt_number=attempt,
        checked_at=datetime(2026, 9, 4, 12, attempt, tzinfo=UTC),
        checked_chain_id=84532,
        submission_ref=submission_ref,
        verifier_outcome=outcome,
        finality_confirmations=confirmations,
        proof_ref=f"sha256:{'1' * 63}{attempt}",
        evidence_source=evidence_source,
        receipt_status=1,
        block_number=46_349_821,
        scenario=scenario,
    )


def onchain_check(
    attempt: int, outcome: ReconciliationVerifierOutcome, *, confirmations: int = 3
) -> ReconciliationCheck:
    return check(
        attempt,
        outcome,
        submission_ref=EVM_TX,
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        scenario=None,
        confirmations=confirmations,
    )


def mismatch_proof(
    *,
    amount_units: int = 200_000,
    token: str = TOKEN,
    recipient: str = SELLER,
    from_address: str = BUYER,
    proof_ref: str = "sha256:" + "22" * 32,
    local_tx_id: str = LOCAL_TX_ID,
) -> ConfirmedMismatchProof:
    return ConfirmedMismatchProof(
        actual_transfer=ActualTransfer(
            amount_units=amount_units,
            token=token,
            from_address=from_address,
            to_address=recipient,
        ),
        transaction_ref=LocalTransactionRef(id=local_tx_id, run_id=RUN_ID),
        proof_ref=proof_ref,
        evidence_source=EvidenceSource.SYNTHETIC_LOCAL,
        scenario=SCENARIO,
    )


def onchain_mismatch_proof(
    *,
    amount_units: int = 200_000,
    token: str = TOKEN,
    recipient: str = SELLER,
    transaction_hash: str = EVM_TX,
    proof_ref: str = "sha256:" + "66" * 32,
) -> ConfirmedMismatchProof:
    return ConfirmedMismatchProof(
        actual_transfer=ActualTransfer(
            amount_units=amount_units,
            token=token,
            from_address=BUYER,
            to_address=recipient,
        ),
        transaction_ref=EvmTransactionRef(hash=transaction_hash),
        proof_ref=proof_ref,
        evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        scenario=None,
    )


async def exhaust_bounded_checks(
    service: PaymentService,
    *,
    purchase_id: str = PURCHASE_ID,
    outcome: ReconciliationVerifierOutcome = (
        ReconciliationVerifierOutcome.SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER
    ),
    builder: Callable[[int, ReconciliationVerifierOutcome], ReconciliationCheck] = check,
    confirmations: int | None = None,
) -> None:
    for attempt in (1, 2, 3):
        recorded = builder(attempt, outcome)
        if confirmations is not None:
            recorded = replace(recorded, finality_confirmations=confirmations)
        await service.record_reconciliation_check(
            purchase_id=purchase_id, check=recorded
        )


async def build_no_transfer_proof(
    repository: InMemoryEvidenceRepository,
    *,
    purchase_id: str = PURCHASE_ID,
    **overrides: object,
) -> NoTransferProof:
    """A proof that restates the persisted reconciliation series exactly."""
    intent = await repository.get_payment_intent(purchase_id)
    assert intent is not None
    events = await repository.list_events(purchase_id)
    checks = [
        event
        for event in events
        if event.type == EventType.PAYMENT_RECONCILIATION_CHECKED
    ]
    first, last = checks[0], checks[-1]
    submission = (
        intent.local_transaction_id
        if intent.local_transaction_id is not None
        else intent.transaction_hash
    )
    assert submission is not None
    scenario_payload = last.payload.get("scenario")
    fields: dict[str, object] = {
        "reason_code": str(last.payload["verifierOutcome"]),
        "checked_chain_id": int(last.payload["checkedChainId"]),
        "attempt_count": len(checks),
        "first_checked_at": datetime.fromisoformat(str(first.payload["checkedAt"])),
        "last_checked_at": datetime.fromisoformat(str(last.payload["checkedAt"])),
        "authorization_nonce_hash": sha256_bytes(
            (intent.authorization_nonce or intent.quote_id).encode("utf-8")
        ),
        "finality_evidence": {"confirmations": int(last.payload["finalityConfirmations"])},
        "proof_ref": "sha256:" + "33" * 32,
        "evidence_source": EvidenceSource(str(last.payload["evidenceSource"])),
        "submission_ref": submission,
        "scenario": (
            ScenarioMetadata.from_payload(scenario_payload)
            if scenario_payload is not None
            else None
        ),
    }
    fields.update(overrides)
    return NoTransferProof(**fields)  # type: ignore[arg-type]


async def policy_of(repository: InMemoryEvidenceRepository, clock, token: str = TOKEN):
    return await repository.get_wallet_policy(
        buyer_wallet_address=BUYER,
        policy_date=clock.now().date().isoformat(),
        token=token,
    )


@pytest.mark.asyncio
async def test_reconciliation_check_appends_without_changing_payment_state(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)

    updated = await service.record_reconciliation_check(
        purchase_id=PURCHASE_ID,
        check=check(1, ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND),
    )

    assert updated.state is PaymentIntentState.RECONCILIATION_REQUIRED
    assert updated.reconciliation_attempt_count == 1
    assert updated.reconciliation_first_checked_at == datetime(2026, 9, 4, 12, 1, tzinfo=UTC)
    assert (
        updated.reconciliation_last_outcome
        is ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND
    )
    events = await repository.list_events(PURCHASE_ID)
    assert [event.type for event in events][-1] == EventType.PAYMENT_RECONCILIATION_CHECKED
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.reserved_units == QUOTED_UNITS
    assert policy.spent_units == 0


@pytest.mark.asyncio
async def test_same_attempt_evidence_is_idempotent_and_different_evidence_conflicts(
    clock,
) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    first = await service.record_reconciliation_check(
        purchase_id=PURCHASE_ID,
        check=check(1, ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND),
    )

    repeated = await service.record_reconciliation_check(
        purchase_id=PURCHASE_ID,
        check=check(1, ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND),
    )
    assert repeated == first
    events = await repository.list_events(PURCHASE_ID)
    assert [event.type for event in events].count(
        EventType.PAYMENT_RECONCILIATION_CHECKED
    ) == 1

    with pytest.raises(PaymentConflictError, match="different evidence"):
        await service.record_reconciliation_check(
            purchase_id=PURCHASE_ID,
            check=check(1, ReconciliationVerifierOutcome.RECEIPT_REVERTED),
        )
    assert len(await repository.list_events(PURCHASE_ID)) == len(events)


@pytest.mark.asyncio
async def test_reconciliation_attempts_must_be_consecutive(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)

    with pytest.raises(PaymentEvidenceError, match="consecutive"):
        await service.record_reconciliation_check(
            purchase_id=PURCHASE_ID,
            check=check(2, ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND),
        )
    assert all(
        event.type != EventType.PAYMENT_RECONCILIATION_CHECKED
        for event in await repository.list_events(PURCHASE_ID)
    )


@pytest.mark.asyncio
async def test_concurrent_checks_keep_one_event_per_attempt_number(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)

    results = await asyncio.gather(
        *(
            service.record_reconciliation_check(
                purchase_id=PURCHASE_ID,
                check=check(1, ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND),
            )
            for _ in range(8)
        ),
        return_exceptions=True,
    )

    assert any(not isinstance(item, BaseException) for item in results)
    events = await repository.list_events(PURCHASE_ID)
    assert [event.type for event in events].count(
        EventType.PAYMENT_RECONCILIATION_CHECKED
    ) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "kwargs", "expected_fields", "expected_spent", "expected_outflow_token"),
    [
        ("wrong amount", {"amount_units": 200_000}, ("amount",), 200_000, TOKEN),
        (
            "wrong token",
            {"token": OTHER_TOKEN, "amount_units": QUOTED_UNITS},
            ("token",),
            0,
            OTHER_TOKEN,
        ),
        (
            "wrong recipient",
            {"recipient": WRONG_RECIPIENT, "amount_units": QUOTED_UNITS},
            ("recipient",),
            QUOTED_UNITS,
            TOKEN,
        ),
    ],
    ids=["wrong amount", "wrong token", "wrong recipient"],
)
async def test_confirmed_mismatch_records_actual_outflow_exactly_once(
    clock,
    label: str,
    kwargs: dict[str, object],
    expected_fields: tuple[str, ...],
    expected_spent: int,
    expected_outflow_token: str,
) -> None:
    del label
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    proof = mismatch_proof(**kwargs)  # type: ignore[arg-type]

    confirmed = await service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=proof)

    assert confirmed.state is PaymentIntentState.MISMATCH_CONFIRMED
    assert confirmed.mismatched_fields == expected_fields
    assert confirmed.local_transaction_id == LOCAL_TX_ID
    assert confirmed.transaction_hash is None  # a synthetic run never fabricates a hash
    assert confirmed.terminal_proof_fingerprint is not None
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.reserved_units == 0
    assert policy.spent_units == expected_spent
    outflows = await service.list_confirmed_outflows(PURCHASE_ID)
    assert len(outflows) == 1
    assert outflows[0].token == expected_outflow_token
    assert outflows[0].amount_units == proof.actual_transfer.amount_units
    events = await repository.list_events(PURCHASE_ID)
    assert [event.type for event in events].count(EventType.PAYMENT_MISMATCH_CONFIRMED) == 1
    terminal = events[-1]
    assert terminal.payload["mismatchedFields"] == list(expected_fields)
    assert terminal.payload["terminalOutcomeKey"] == f"terminal:{PURCHASE_ID}"
    assert "signature" not in str(terminal.payload)


@pytest.mark.asyncio
async def test_wrong_token_mismatch_never_creates_a_policy_for_the_other_token(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)

    await service.confirm_mismatch(
        purchase_id=PURCHASE_ID,
        proof=mismatch_proof(token=OTHER_TOKEN, amount_units=QUOTED_UNITS),
    )

    assert await policy_of(repository, clock, OTHER_TOKEN) is None
    outflows = await service.list_confirmed_outflows(PURCHASE_ID)
    assert outflows[0].token == OTHER_TOKEN


@pytest.mark.asyncio
async def test_same_mismatch_proof_returns_the_existing_terminal_result(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    proof = mismatch_proof()
    first = await service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=proof)

    repeated = await asyncio.gather(
        *(service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=proof) for _ in range(8))
    )

    assert all(item == first for item in repeated)
    events = await repository.list_events(PURCHASE_ID)
    assert [event.type for event in events].count(EventType.PAYMENT_MISMATCH_CONFIRMED) == 1
    assert len(await service.list_confirmed_outflows(PURCHASE_ID)) == 1
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.spent_units == 200_000


@pytest.mark.asyncio
async def test_different_mismatch_proof_on_a_terminal_payment_is_a_conflict(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=mismatch_proof())
    before = await repository.list_events(PURCHASE_ID)

    with pytest.raises(PaymentConflictError, match="different mismatch proof"):
        await service.confirm_mismatch(
            purchase_id=PURCHASE_ID,
            proof=mismatch_proof(amount_units=150_000, proof_ref="sha256:" + "99" * 32),
        )

    assert len(await repository.list_events(PURCHASE_ID)) == len(before)
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.spent_units == 200_000


@pytest.mark.asyncio
async def test_proof_matching_the_quote_is_not_a_mismatch(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)

    with pytest.raises(PaymentEvidenceError, match="not a mismatch"):
        await service.confirm_mismatch(
            purchase_id=PURCHASE_ID,
            proof=mismatch_proof(amount_units=QUOTED_UNITS),
        )
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.reserved_units == QUOTED_UNITS


@pytest.mark.asyncio
async def test_mismatch_proof_must_be_a_buyer_outflow(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)

    with pytest.raises(PaymentEvidenceError, match="buyer outflow"):
        await service.confirm_mismatch(
            purchase_id=PURCHASE_ID,
            proof=mismatch_proof(from_address=CONTRACT),
        )


@pytest.mark.asyncio
async def test_no_transfer_requires_exhausted_bounded_retries(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await service.record_reconciliation_check(
        purchase_id=PURCHASE_ID,
        check=check(
            1, ReconciliationVerifierOutcome.SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER
        ),
    )

    with pytest.raises(PaymentEvidenceError, match="not exhausted"):
        await service.reconcile_no_transfer(
            purchase_id=PURCHASE_ID,
            proof=await build_no_transfer_proof(repository),
        )
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.reserved_units == QUOTED_UNITS


@pytest.mark.asyncio
async def test_transient_missing_receipt_can_never_confirm_no_transfer(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await exhaust_bounded_checks(
        service, outcome=ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND
    )

    with pytest.raises(PaymentEvidenceError, match="momentary missing receipt"):
        await service.reconcile_no_transfer(
            purchase_id=PURCHASE_ID,
            proof=await build_no_transfer_proof(repository),
        )
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.reserved_units == QUOTED_UNITS
    assert all(
        event.type != EventType.PAYMENT_RECONCILED_NO_TRANSFER
        for event in await repository.list_events(PURCHASE_ID)
    )


@pytest.mark.asyncio
async def test_no_transfer_requires_configured_finality_evidence(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await exhaust_bounded_checks(service, confirmations=1)

    with pytest.raises(PaymentEvidenceError, match="finality evidence"):
        await service.reconcile_no_transfer(
            purchase_id=PURCHASE_ID,
            proof=await build_no_transfer_proof(repository),
        )


@pytest.mark.asyncio
async def test_no_transfer_proof_cannot_inflate_the_recorded_finality(clock) -> None:
    """H2: the proof may not claim more confirmations than the recorded check."""
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await exhaust_bounded_checks(service, confirmations=1)

    with pytest.raises(PaymentEvidenceError, match="finality does not match"):
        await service.reconcile_no_transfer(
            purchase_id=PURCHASE_ID,
            proof=await build_no_transfer_proof(
                repository, finality_evidence={"confirmations": 9}
            ),
        )
    assert all(
        event.type != EventType.PAYMENT_RECONCILED_NO_TRANSFER
        for event in await repository.list_events(PURCHASE_ID)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "overrides", "match"),
    [
        ("attempt count", {"attempt_count": 9}, "attempt count is not recorded"),
        (
            "submission",
            {"submission_ref": f"localtx:{RUN_ID}:payment:000009"},
            "is not the submitted transaction",
        ),
        ("chain", {"checked_chain_id": 1}, "chain does not match"),
        (
            "nonce hash",
            {"authorization_nonce_hash": "sha256:" + "ff" * 32},
            "nonce hash does not bind",
        ),
        (
            "first checked time",
            {"first_checked_at": datetime(2020, 1, 1, tzinfo=UTC)},
            "check window does not match",
        ),
        (
            "last checked time",
            {"last_checked_at": datetime(2030, 1, 1, tzinfo=UTC)},
            "check window does not match",
        ),
        ("scenario", {"scenario": None}, "scenario is not the submitted scenario run"),
    ],
    ids=[
        "attempt count",
        "submission",
        "chain",
        "nonce hash",
        "first checked time",
        "last checked time",
        "scenario",
    ],
)
async def test_no_transfer_proof_must_restate_the_recorded_series(
    clock, label: str, overrides: dict[str, object], match: str
) -> None:
    del label
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await exhaust_bounded_checks(service)

    with pytest.raises(PaymentEvidenceError, match=match):
        await service.reconcile_no_transfer(
            purchase_id=PURCHASE_ID,
            proof=await build_no_transfer_proof(repository, **overrides),
        )
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.reserved_units == QUOTED_UNITS


@pytest.mark.asyncio
async def test_no_transfer_releases_the_reservation_exactly_once(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await exhaust_bounded_checks(service)
    proof = await build_no_transfer_proof(repository)

    closed = await asyncio.gather(
        *(
            service.reconcile_no_transfer(purchase_id=PURCHASE_ID, proof=proof)
            for _ in range(8)
        )
    )

    assert all(item.state is PaymentIntentState.RECONCILED_NO_TRANSFER for item in closed)
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.reserved_units == 0
    assert policy.spent_units == 0
    events = await repository.list_events(PURCHASE_ID)
    assert [event.type for event in events].count(
        EventType.PAYMENT_RECONCILED_NO_TRANSFER
    ) == 1
    assert await service.list_confirmed_outflows(PURCHASE_ID) == []
    terminal = events[-1]
    assert terminal.payload["reasonCode"] == proof.reason_code
    assert terminal.payload["attemptCount"] == 3
    assert terminal.payload["proofFingerprint"].startswith("sha256:")


@pytest.mark.asyncio
async def test_a_reused_proof_reference_with_other_fields_changed_conflicts(clock) -> None:
    """H2: same-proof idempotency compares the whole proof, never the alias alone."""
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await exhaust_bounded_checks(service)
    original = await build_no_transfer_proof(repository)
    await service.reconcile_no_transfer(purchase_id=PURCHASE_ID, proof=original)
    before = await repository.list_events(PURCHASE_ID)

    with pytest.raises(PaymentConflictError, match="different no-transfer proof"):
        await service.reconcile_no_transfer(
            purchase_id=PURCHASE_ID,
            proof=replace(original, reason_code="AUTHORIZATION_UNUSED_AFTER_EXPIRY"),
        )

    assert len(await repository.list_events(PURCHASE_ID)) == len(before)


@pytest.mark.asyncio
async def test_a_reused_mismatch_proof_reference_with_other_fields_changed_conflicts(
    clock,
) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=mismatch_proof())
    before = await repository.list_events(PURCHASE_ID)

    with pytest.raises(PaymentConflictError, match="different mismatch proof"):
        await service.confirm_mismatch(
            purchase_id=PURCHASE_ID,
            proof=mismatch_proof(amount_units=150_000),
        )

    assert len(await repository.list_events(PURCHASE_ID)) == len(before)
    assert len(await service.list_confirmed_outflows(PURCHASE_ID)) == 1


@pytest.mark.asyncio
async def test_mismatch_proof_must_be_the_submitted_transaction(clock) -> None:
    """H2: an unrelated outflow can never terminalize this purchase."""
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)

    with pytest.raises(PaymentEvidenceError, match="not the submitted transaction"):
        await service.confirm_mismatch(
            purchase_id=PURCHASE_ID,
            proof=mismatch_proof(local_tx_id=f"localtx:{RUN_ID}:payment:000777"),
        )
    with pytest.raises(PaymentEvidenceError, match="not the submitted transaction"):
        await service.confirm_mismatch(
            purchase_id=PURCHASE_ID, proof=onchain_mismatch_proof()
        )
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.reserved_units == QUOTED_UNITS
    assert await service.list_confirmed_outflows(PURCHASE_ID) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "kwargs", "match"),
    [
        (
            "source disagrees with reference",
            {
                "evidence_source": EvidenceSource.BASE_SEPOLIA_VERIFIED,
                "reference": LocalTransactionRef(id=LOCAL_TX_ID, run_id=RUN_ID),
            },
            "source disagree",
        ),
        (
            "historical source on a synthetic reference",
            {
                "evidence_source": EvidenceSource.HISTORICAL_ON_CHAIN,
                "reference": LocalTransactionRef(id=LOCAL_TX_ID, run_id=RUN_ID),
            },
            "source disagree",
        ),
        (
            "synthetic proof without a scenario",
            {
                "evidence_source": EvidenceSource.SYNTHETIC_LOCAL,
                "reference": LocalTransactionRef(id=LOCAL_TX_ID, run_id=RUN_ID),
                "scenario": None,
            },
            "exactly one scenario",
        ),
        (
            "on-chain proof carrying a scenario",
            {
                "evidence_source": EvidenceSource.BASE_SEPOLIA_VERIFIED,
                "reference": EvmTransactionRef(hash=EVM_TX),
                "scenario": SCENARIO,
            },
            "exactly one scenario",
        ),
    ],
    ids=[
        "source disagrees with reference",
        "historical source on a synthetic reference",
        "synthetic proof without a scenario",
        "on-chain proof carrying a scenario",
    ],
)
def test_mismatch_proof_rejects_contradictory_provenance(
    label: str, kwargs: dict[str, object], match: str
) -> None:
    """H1: proof source, transaction variant and scenario must be one consistent fact."""
    del label
    with pytest.raises(ValueError, match=match):
        ConfirmedMismatchProof(
            actual_transfer=ActualTransfer(
                amount_units=200_000,
                token=TOKEN,
                from_address=BUYER,
                to_address=SELLER,
            ),
            transaction_ref=kwargs["reference"],  # type: ignore[arg-type]
            proof_ref="sha256:" + "88" * 32,
            evidence_source=kwargs["evidence_source"],  # type: ignore[arg-type]
            scenario=kwargs.get("scenario", SCENARIO),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_reconciliation_check_must_bind_the_submitted_identity(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)

    with pytest.raises(PaymentEvidenceError, match="is not the submitted transaction"):
        await service.record_reconciliation_check(
            purchase_id=PURCHASE_ID,
            check=check(1, ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND, submission_ref=EVM_TX),
        )
    with pytest.raises(PaymentEvidenceError, match="require exactly one scenario"):
        await service.record_reconciliation_check(
            purchase_id=PURCHASE_ID,
            check=check(
                1,
                ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND,
                evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
            ),
        )
    with pytest.raises(PaymentEvidenceError, match="evidence source is not the submitted"):
        await service.record_reconciliation_check(
            purchase_id=PURCHASE_ID,
            check=check(
                1,
                ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND,
                evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
                scenario=None,
            ),
        )
    assert all(
        event.type != EventType.PAYMENT_RECONCILIATION_CHECKED
        for event in await repository.list_events(PURCHASE_ID)
    )


@pytest.mark.asyncio
async def test_only_one_terminal_outcome_survives_a_race(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await onchain_service(repository, clock)
    await exhaust_bounded_checks(service, builder=onchain_check)

    results = await asyncio.gather(
        service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=onchain_mismatch_proof()),
        service.reconcile_no_transfer(
            purchase_id=PURCHASE_ID, proof=await build_no_transfer_proof(repository)
        ),
        service.fail_confirmed(
            purchase_id=PURCHASE_ID,
            reason="receipt status zero",
            transaction_hash=EVM_TX,
            block_number=42,
            receipt_status=0,
        ),
        service.settle(
            purchase_id=PURCHASE_ID,
            transaction_hash=EVM_TX,
            block_number=42,
            transfer_log_index=3,
            receipt_status=1,
            token=TOKEN,
            from_address=BUYER,
            to_address=SELLER,
            amount_units=QUOTED_UNITS,
        ),
        return_exceptions=True,
    )

    succeeded = [item for item in results if not isinstance(item, BaseException)]
    assert len(succeeded) == 1
    events = await repository.list_events(PURCHASE_ID)
    terminal_types = [
        event.type
        for event in events
        if event.type
        in {
            EventType.PAYMENT_SETTLED,
            EventType.PAYMENT_FAILED,
            EventType.PAYMENT_MISMATCH_CONFIRMED,
            EventType.PAYMENT_RECONCILED_NO_TRANSFER,
        }
    ]
    assert len(terminal_types) == 1
    outcome_keys = [
        event.payload.get("terminalOutcomeKey")
        for event in events
        if event.payload.get("terminalOutcomeKey") is not None
    ]
    assert outcome_keys == [f"terminal:{PURCHASE_ID}"]
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.reserved_units == 0
    assert policy.spent_units in {0, QUOTED_UNITS, 200_000}


@pytest.mark.asyncio
async def test_settlement_after_a_confirmed_mismatch_is_rejected(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=mismatch_proof())
    before = await repository.list_events(PURCHASE_ID)

    with pytest.raises(PaymentConflictError, match="different terminal outcome"):
        await service.settle(
            purchase_id=PURCHASE_ID,
            transaction_hash=EVM_TX,
            block_number=42,
            transfer_log_index=3,
            receipt_status=1,
            token=TOKEN,
            from_address=BUYER,
            to_address=SELLER,
            amount_units=QUOTED_UNITS,
        )

    assert len(await repository.list_events(PURCHASE_ID)) == len(before)


@pytest.mark.asyncio
async def test_reconciliation_checks_stop_after_a_terminal_outcome(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await exhaust_bounded_checks(service)
    await service.reconcile_no_transfer(
        purchase_id=PURCHASE_ID, proof=await build_no_transfer_proof(repository)
    )

    with pytest.raises(PaymentConflictError, match="terminal outcome"):
        await service.record_reconciliation_check(
            purchase_id=PURCHASE_ID,
            check=check(4, ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND),
        )


@pytest.mark.asyncio
async def test_terminal_purchase_still_loads_its_payment_view_and_claim(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    confirmed = await service.confirm_mismatch(
        purchase_id=PURCHASE_ID, proof=mismatch_proof()
    )

    view = await service.load_payment_view(PURCHASE_ID)
    resumed = await service.claim(PURCHASE_ID)

    assert view.quote.amount_units == QUOTED_UNITS
    assert resumed == confirmed


@pytest.mark.asyncio
async def test_historical_permit2_intent_cannot_reconcile_or_close(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await exhaust_bounded_checks(service)
    valid_proof = await build_no_transfer_proof(repository)
    stored = await repository.get_payment_intent(PURCHASE_ID)
    assert stored is not None
    repository._payment_intents[PURCHASE_ID] = replace(  # type: ignore[attr-defined]
        stored, transfer_method="permit2"
    )

    for operation in (
        service.record_reconciliation_check(
            purchase_id=PURCHASE_ID,
            check=check(4, ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND),
        ),
        service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=mismatch_proof()),
        service.reconcile_no_transfer(purchase_id=PURCHASE_ID, proof=valid_proof),
    ):
        with pytest.raises(PaymentConflictError, match="read-only"):
            await operation


@pytest.mark.asyncio
async def test_confirmed_mismatch_over_the_daily_limit_stays_readable_and_blocks_new_claims(
    clock,
) -> None:
    repository = InMemoryEvidenceRepository()
    service = PaymentService(repository=repository, clock=clock)
    await seed(repository, clock)
    await repository.put_wallet_policy(
        WalletPolicy(
            buyer_wallet_address=BUYER,
            policy_date=clock.now().date().isoformat(),
            token=TOKEN,
            per_transaction_limit_units=250_000,
            daily_limit_units=250_000,
        )
    )
    await service.claim(PURCHASE_ID)
    await service.authorize(
        purchase_id=PURCHASE_ID,
        authorization_hash="0xauthorization",
        signature="0xsignature",
    )
    await service.require_reconciliation(
        purchase_id=PURCHASE_ID,
        reason="synthetic facilitator returned a local submission",
        local_transaction_id=LOCAL_TX_ID,
        scenario=SCENARIO,
    )

    await service.confirm_mismatch(
        purchase_id=PURCHASE_ID, proof=mismatch_proof(amount_units=250_000)
    )

    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.spent_units == 250_000
    assert policy.reserved_units == 0
    await seed(
        repository, clock, purchase_id="purchase-blocked", configure_policy=False
    )
    with pytest.raises(Exception, match="daily limit"):
        await service.claim("purchase-blocked")


def test_mismatched_quote_fields_is_canonically_ordered() -> None:
    intent = PaymentIntent(
        purchase_id=PURCHASE_ID,
        buyer_wallet_address=BUYER,
        policy_date="2026-09-04",
        quote_id="quote-terminal",
        decision_event_hash="sha256:" + "55" * 32,
        amount_units=QUOTED_UNITS,
        token=TOKEN,
        pay_to=SELLER,
        permit2_nonce=None,
        state=PaymentIntentState.RECONCILIATION_REQUIRED,
        claimed_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    everything_wrong = ActualTransfer(
        amount_units=1,
        token=OTHER_TOKEN,
        from_address=BUYER,
        to_address=WRONG_RECIPIENT,
    )
    assert mismatched_quote_fields(intent, everything_wrong) == (
        "amount",
        "token",
        "recipient",
    )
    assert mismatched_quote_fields(
        intent,
        ActualTransfer(
            amount_units=QUOTED_UNITS,
            token=TOKEN,
            from_address=BUYER,
            to_address=SELLER,
        ),
    ) == ()


@pytest.mark.asyncio
async def test_one_synthetic_transaction_cannot_back_two_purchases(clock) -> None:
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=mismatch_proof())
    second_purchase_id = "purchase-terminal-second"
    await seed(repository, clock, purchase_id=second_purchase_id, configure_policy=False)
    await service.claim(second_purchase_id)
    await service.authorize(
        purchase_id=second_purchase_id,
        authorization_hash="0xauthorization",
        signature="0xsignature",
    )
    await service.require_reconciliation(
        purchase_id=second_purchase_id,
        reason="synthetic facilitator returned a local submission",
        local_transaction_id=LOCAL_TX_ID,
        scenario=SCENARIO,
    )

    with pytest.raises(PaymentConflictError, match="transaction is already recorded"):
        await service.confirm_mismatch(
            purchase_id=second_purchase_id, proof=mismatch_proof()
        )

    assert await service.list_confirmed_outflows(second_purchase_id) == []
    assert all(
        event.type != EventType.PAYMENT_MISMATCH_CONFIRMED
        for event in await repository.list_events(second_purchase_id)
    )


@pytest.mark.asyncio
async def test_historical_proof_cannot_terminalize_an_active_base_submission(clock) -> None:
    """H1: the same bound hash under HISTORICAL_ON_CHAIN is a different provenance claim."""
    repository = InMemoryEvidenceRepository()
    service = await onchain_service(repository, clock)
    historical = replace(
        onchain_mismatch_proof(),
        transaction_ref=EvmTransactionRef(
            hash=EVM_TX, evidence_source=EvidenceSource.HISTORICAL_ON_CHAIN
        ),
        evidence_source=EvidenceSource.HISTORICAL_ON_CHAIN,
    )

    with pytest.raises(PaymentEvidenceError, match="evidence source is not the submitted"):
        await service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=historical)

    stored = await repository.get_payment_intent(PURCHASE_ID)
    assert stored is not None
    assert stored.state is PaymentIntentState.RECONCILIATION_REQUIRED
    assert stored.terminal_evidence_source is None
    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.spent_units == 0
    assert policy.reserved_units == QUOTED_UNITS
    assert await service.list_confirmed_outflows(PURCHASE_ID) == []
    assert all(
        event.type != EventType.PAYMENT_MISMATCH_CONFIRMED
        for event in await repository.list_events(PURCHASE_ID)
    )


@pytest.mark.asyncio
async def test_historical_check_cannot_answer_an_active_base_submission(clock) -> None:
    """H1: a reconciliation check must also carry the submitted evidence source."""
    repository = InMemoryEvidenceRepository()
    service = await onchain_service(repository, clock)

    with pytest.raises(PaymentEvidenceError, match="evidence source is not the submitted"):
        await service.record_reconciliation_check(
            purchase_id=PURCHASE_ID,
            check=check(
                1,
                ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND,
                submission_ref=EVM_TX,
                evidence_source=EvidenceSource.HISTORICAL_ON_CHAIN,
                scenario=None,
            ),
        )

    assert all(
        event.type != EventType.PAYMENT_RECONCILIATION_CHECKED
        for event in await repository.list_events(PURCHASE_ID)
    )


@pytest.mark.asyncio
async def test_foreign_run_check_cannot_answer_a_local_submission(clock) -> None:
    """H2: a consistent series from another run must fail before any attempt is recorded."""
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    other_run = ScenarioMetadata(
        run_id="b" * 32,
        scenario_id=SCENARIO.scenario_id,
        catalog_version=SCENARIO.catalog_version,
        catalog_hash=SCENARIO.catalog_hash,
    )

    with pytest.raises(PaymentEvidenceError, match="run does not own the submitted transaction"):
        await service.record_reconciliation_check(
            purchase_id=PURCHASE_ID,
            check=check(
                1,
                ReconciliationVerifierOutcome.SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER,
                scenario=other_run,
            ),
        )

    stored = await repository.get_payment_intent(PURCHASE_ID)
    assert stored is not None
    assert stored.reconciliation_attempt_count == 0
    assert all(
        event.type != EventType.PAYMENT_RECONCILIATION_CHECKED
        for event in await repository.list_events(PURCHASE_ID)
    )


@pytest.mark.asyncio
async def test_foreign_run_no_transfer_proof_cannot_release_the_reservation(clock) -> None:
    """H2: a forged run-B proof over a genuine run-A series never terminalizes."""
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    await exhaust_bounded_checks(service)
    other_run = ScenarioMetadata(
        run_id="b" * 32,
        scenario_id=SCENARIO.scenario_id,
        catalog_version=SCENARIO.catalog_version,
        catalog_hash=SCENARIO.catalog_hash,
    )
    forged = await build_no_transfer_proof(repository, scenario=other_run)

    with pytest.raises(PaymentEvidenceError, match="run does not own the submitted transaction"):
        await service.reconcile_no_transfer(purchase_id=PURCHASE_ID, proof=forged)

    stored = await repository.get_payment_intent(PURCHASE_ID)
    assert stored is not None
    assert stored.state is PaymentIntentState.RECONCILIATION_REQUIRED
    policy = await policy_of(repository, clock)
    assert policy is not None
    # The reservation is still held: nothing was released by the foreign-run proof.
    assert policy.reserved_units == QUOTED_UNITS
    assert policy.spent_units == 0
    assert all(
        event.type != EventType.PAYMENT_RECONCILED_NO_TRANSFER
        for event in await repository.list_events(PURCHASE_ID)
    )


@pytest.mark.asyncio
async def test_foreign_run_mismatch_proof_cannot_charge_the_wallet(clock) -> None:
    """H2: a run-B mismatch proof against a run-A submission is not evidence."""
    repository = InMemoryEvidenceRepository()
    service = await synthetic_service(repository, clock)
    other_run = ScenarioMetadata(
        run_id="b" * 32,
        scenario_id=SCENARIO.scenario_id,
        catalog_version=SCENARIO.catalog_version,
        catalog_hash=SCENARIO.catalog_hash,
    )
    # The value object already refuses to mix a run-A reference with run-B provenance.
    with pytest.raises(ValueError, match="belongs to another run"):
        replace(mismatch_proof(), scenario=other_run)
    # A fully self-consistent run-B proof is still not this purchase's submission.
    forged = replace(
        mismatch_proof(),
        transaction_ref=LocalTransactionRef(
            id=f"localtx:{other_run.run_id}:payment:000001", run_id=other_run.run_id
        ),
        scenario=other_run,
    )

    with pytest.raises(PaymentEvidenceError, match="is not the submitted transaction"):
        await service.confirm_mismatch(purchase_id=PURCHASE_ID, proof=forged)

    policy = await policy_of(repository, clock)
    assert policy is not None
    assert policy.spent_units == 0
    assert policy.reserved_units == QUOTED_UNITS
    assert await service.list_confirmed_outflows(PURCHASE_ID) == []
