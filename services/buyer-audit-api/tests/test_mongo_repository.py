from __future__ import annotations

import asyncio
import inspect
import os
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.mongo import (
    _PHASE6_SINGLETON_INDEXES,
    MongoEvidenceRepository,
)
from buyer_audit_api.core.errors import EvidenceIntegrityError, PaymentConflictError
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import (
    EventType,
    EvidenceSource,
    LocalTransactionRef,
    ScenarioMetadata,
)
from buyer_audit_api.core.payment import (
    ActualTransfer,
    ConfirmedMismatchProof,
    NoTransferProof,
    PaymentIntentState,
    PaymentService,
    ReconciliationCheck,
    ReconciliationVerifierOutcome,
    WalletPolicy,
)
from buyer_audit_api.core.seller_execution import SellerExecution, SellerExecutionState

pytestmark = pytest.mark.mongo


class FailingHeadMongoRepository(MongoEvidenceRepository):
    async def _insert_event_head(self, **kwargs) -> None:
        del kwargs
        raise RuntimeError("injected head write failure")


@pytest.fixture
def mongo_uri() -> str:
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI is not configured")
    return uri


@pytest.mark.asyncio
async def test_real_mongo_atomic_nonce_hash_chain_and_ciphertext(mongo_uri, clock) -> None:
    database = f"pbl_phase1_test_{uuid.uuid4().hex}"
    repository = MongoEvidenceRepository(uri=mongo_uri, database=database)
    failing_repository = FailingHeadMongoRepository(uri=mongo_uri, database=database)
    cleanup: AsyncMongoClient = AsyncMongoClient(mongo_uri)
    try:
        await cleanup[database]["walletPolicies"].create_index(
            [("buyerWalletAddress", 1), ("policyDate", 1)],
            unique=True,
            name="unique_wallet_policy_day",
        )
        await repository.ensure_indexes()
        policy_index = (
            await cleanup[database]["walletPolicies"].index_information()
        )["unique_wallet_policy_day"]
        assert policy_index["key"] == [
            ("buyerWalletAddress", 1),
            ("policyDate", 1),
            ("token", 1),
        ]
        with pytest.raises(RuntimeError, match="injected head write failure"):
            await failing_repository.append_event(
                purchase_id="purchase-rollback",
                event_type=EventType.CORRECTION_RECORDED,
                occurred_at=clock.now(),
                actor={"id": "test", "type": "test"},
                payload={"mustRollback": True},
            )
        assert (
            await cleanup[database]["purchaseEvents"].count_documents(
                {"purchaseId": "purchase-rollback"}
            )
            == 0
        )
        assert (
            await cleanup[database]["evidenceHeads"].count_documents(
                {"purchaseId": "purchase-rollback"}
            )
            == 0
        )
        recovered = await repository.append_event(
            purchase_id="purchase-rollback",
            event_type=EventType.CORRECTION_RECORDED,
            occurred_at=clock.now(),
            actor={"id": "test", "type": "test"},
            payload={"retry": True},
        )
        recovered_head = await repository.get_event_head("purchase-rollback")
        assert recovered.sequence == 1
        assert recovered_head is not None
        assert recovered_head.event_count == 1
        assert recovered_head.head_event_hash == recovered.event_hash

        await repository.append_event(
            purchase_id="purchase-millisecond-round-trip",
            event_type=EventType.REQUESTED,
            occurred_at=datetime(2026, 9, 2, 12, 40, 21, 123456, tzinfo=UTC),
            actor={"id": "test", "type": "test"},
            payload={"value": "round-trip"},
        )
        round_trip_events = await repository.list_events("purchase-millisecond-round-trip")
        assert round_trip_events[0].occurred_at.microsecond == 123000
        verify_event_chain(round_trip_events)

        await repository.issue_siwe_nonce(
            owner_address="0xabc",
            nonce_hash="nonce-hash",
            expires_at=clock.now().replace(year=2027),
        )
        consumed = await asyncio.gather(
            *(
                repository.consume_siwe_nonce(
                    owner_address="0xabc",
                    nonce_hash="nonce-hash",
                    consumed_at=clock.now(),
                )
                for _ in range(8)
            )
        )
        assert consumed.count(True) == 1

        binding = await repository.bind_buyer_wallet(
            owner_address="0xowner",
            buyer_wallet_address="0xbuyer",
            bound_at=clock.now(),
        )
        assert binding.buyer_wallet_address == "0xbuyer"
        with pytest.raises(ValueError, match="already bound"):
            await repository.bind_buyer_wallet(
                owner_address="0xother",
                buyer_wallet_address="0xbuyer",
                bound_at=clock.now(),
            )
        with pytest.raises(ValueError, match="already bound"):
            await repository.bind_buyer_wallet(
                owner_address="0xowner",
                buyer_wallet_address="0xreplacement",
                bound_at=clock.now(),
            )

        race_policy = WalletPolicy(
            buyer_wallet_address="0xbuyer-race",
            policy_date=clock.now().date().isoformat(),
            token="0xtoken-race",
            per_transaction_limit_units=200_000,
            daily_limit_units=1_000_000,
        )
        await repository.put_wallet_policy(race_policy)

        async def reserve_during_policy_retry() -> None:
            result = await cleanup[database]["walletPolicies"].find_one_and_update(
                {
                    "buyerWalletAddress": race_policy.buyer_wallet_address,
                    "policyDate": race_policy.policy_date,
                    "reservedUnits": 0,
                },
                {"$inc": {"reservedUnits": 100_000}},
            )
            assert result is not None

        await asyncio.gather(
            reserve_during_policy_retry(),
            *(repository.put_wallet_policy(race_policy) for _ in range(24)),
            return_exceptions=True,
        )
        raced_policy = await repository.get_wallet_policy(
            buyer_wallet_address=race_policy.buyer_wallet_address,
            policy_date=race_policy.policy_date,
            token=race_policy.token,
        )
        assert raced_policy is not None
        assert raced_policy.reserved_units == 100_000

        second_token_policy = replace(
            race_policy,
            token="0xtoken-race-v2",
            spent_units=0,
            reserved_units=0,
        )
        await repository.put_wallet_policy(second_token_policy)
        assert (
            await cleanup[database]["walletPolicies"].count_documents(
                {
                    "buyerWalletAddress": race_policy.buyer_wallet_address,
                    "policyDate": race_policy.policy_date,
                }
            )
            == 2
        )
        assert await repository.get_wallet_policy(
            buyer_wallet_address=race_policy.buyer_wallet_address,
            policy_date=race_policy.policy_date,
            token=second_token_policy.token,
        ) == second_token_policy

        payment_purchase_id = "purchase-payment-atomic"
        payment_token = "0xtoken"
        await repository.put_wallet_policy(
            WalletPolicy(
                buyer_wallet_address="0xbuyer",
                policy_date=clock.now().date().isoformat(),
                token=payment_token,
                per_transaction_limit_units=200_000,
                daily_limit_units=1_000_000,
            )
        )
        await repository.append_event(
            purchase_id=payment_purchase_id,
            event_type=EventType.REQUESTED,
            occurred_at=clock.now(),
            actor={"id": "0xowner", "type": "user"},
            payload={
                "budgetUnits": 250_000,
                "domain": "fake",
                "normalizedRequest": {"value": "mongo-payment"},
                "policy": {},
            },
        )
        await repository.append_event(
            purchase_id=payment_purchase_id,
            event_type=EventType.QUOTED,
            occurred_at=clock.now(),
            actor={"id": "buyer", "type": "service"},
            payload={
                "signedQuotes": [
                    {
                        "quote_id": "quote-mongo-payment",
                        "purchase_id": payment_purchase_id,
                        "seller_agent_id": "seller-mongo",
                        "provider_id": "gemini",
                        "model_id": "model-a",
                        "model_version": "v1",
                        "amount_units": 100_000,
                        "token": payment_token,
                        "pay_to": "0xseller",
                        "available": True,
                        "expires_at": clock.now().replace(year=2027).isoformat(),
                        "signer_address": "0xseller",
                        "chain_id": 84532,
                        "verifying_contract": "0xquotecontract",
                    }
                ],
                "quoteIdentityEvidence": [
                    {
                        "quoteId": "quote-mongo-payment",
                        "erc8004AgentId": "1",
                        "identityVerified": True,
                    }
                ],
            },
        )
        decided = await repository.append_event(
            purchase_id=payment_purchase_id,
            event_type=EventType.DECIDED,
            occurred_at=clock.now(),
            actor={"id": "buyer", "type": "agent"},
            payload={"winner": {"quote_id": "quote-mongo-payment"}},
        )
        failing_payment_service = PaymentService(
            repository=failing_repository,
            clock=clock,
        )
        with pytest.raises(RuntimeError, match="injected head write failure"):
            await failing_payment_service.claim(payment_purchase_id)
        raw_policy = await cleanup[database]["walletPolicies"].find_one(
            {
                "buyerWalletAddress": "0xbuyer",
                "policyDate": clock.now().date().isoformat(),
            }
        )
        assert raw_policy is not None
        assert raw_policy["reservedUnits"] == 0
        assert (
            await cleanup[database]["paymentIntents"].count_documents(
                {"purchaseId": payment_purchase_id}
            )
            == 0
        )
        assert (
            await cleanup[database]["purchaseEvents"].count_documents(
                {"purchaseId": payment_purchase_id}
            )
            == 3
        )
        assert (
            await cleanup[database]["evidenceHeads"].count_documents(
                {"purchaseId": payment_purchase_id}
            )
            == 3
        )

        payment_service = PaymentService(repository=repository, clock=clock)
        intents = await asyncio.gather(
            *(payment_service.claim(payment_purchase_id) for _ in range(12))
        )
        assert len({intent.authorization_nonce for intent in intents}) == 1
        assert intents[0].permit2_nonce is None
        assert intents[0].transfer_method == "eip3009"
        stored_intent = await repository.get_payment_intent(payment_purchase_id)
        assert stored_intent is not None
        assert stored_intent.decision_event_hash == decided.event_hash
        payment_policy = await repository.get_wallet_policy(
            buyer_wallet_address="0xbuyer",
            policy_date=clock.now().date().isoformat(),
        )
        assert payment_policy is not None
        assert payment_policy.reserved_units == 100_000
        assert (
            await cleanup[database]["paymentIntents"].count_documents(
                {"purchaseId": payment_purchase_id}
            )
            == 1
        )
        payment_events = await repository.list_events(payment_purchase_id)
        assert [event.type for event in payment_events] == [
            EventType.REQUESTED,
            EventType.QUOTED,
            EventType.DECIDED,
            EventType.PAYMENT_INTENT_CLAIMED,
        ]
        with pytest.raises(RuntimeError, match="injected head write failure"):
            await failing_payment_service.authorize(
                purchase_id=payment_purchase_id,
                authorization_hash="0xauth",
                signature="0xsig",
            )
        rolled_back_intent = await repository.get_payment_intent(payment_purchase_id)
        assert rolled_back_intent is not None
        assert rolled_back_intent.state == PaymentIntentState.CLAIMED
        payment_policy = await repository.get_wallet_policy(
            buyer_wallet_address="0xbuyer",
            policy_date=clock.now().date().isoformat(),
        )
        assert payment_policy is not None
        assert payment_policy.reserved_units == 100_000
        assert len(await repository.list_events(payment_purchase_id)) == 4

        authorized = await asyncio.gather(
            *(
                payment_service.authorize(
                    purchase_id=payment_purchase_id,
                    authorization_hash="0xauth",
                    signature="0xsig",
                )
                for _ in range(12)
            )
        )
        assert all(item.state == PaymentIntentState.AUTHORIZED for item in authorized)
        reconciled = await payment_service.require_reconciliation(
            purchase_id=payment_purchase_id,
            reason="RPC timed out after facilitator broadcast",
        )
        assert reconciled.state == PaymentIntentState.RECONCILIATION_REQUIRED
        assert reconciled.transaction_hash is None
        transaction_hash = "0x" + "ab" * 32
        identified = await asyncio.gather(
            *(
                payment_service.bind_reconciliation_transaction(
                    purchase_id=payment_purchase_id,
                    transaction_hash=transaction_hash,
                )
                for _ in range(12)
            )
        )
        assert all(item.transaction_hash == transaction_hash for item in identified)
        settled = await asyncio.gather(
            *(
                payment_service.settle(
                    purchase_id=payment_purchase_id,
                    transaction_hash=transaction_hash,
                    block_number=42,
                    transfer_log_index=3,
                    receipt_status=1,
                    token=payment_token,
                    from_address="0xbuyer",
                    to_address="0xseller",
                    amount_units=100_000,
                )
                for _ in range(12)
            )
        )
        assert all(item.state == PaymentIntentState.SETTLED for item in settled)
        payment_policy = await repository.get_wallet_policy(
            buyer_wallet_address="0xbuyer",
            policy_date=clock.now().date().isoformat(),
        )
        assert payment_policy is not None
        assert payment_policy.reserved_units == 0
        assert payment_policy.spent_units == 100_000
        assert [event.type for event in await repository.list_events(payment_purchase_id)] == [
            EventType.REQUESTED,
            EventType.QUOTED,
            EventType.DECIDED,
            EventType.PAYMENT_INTENT_CLAIMED,
            EventType.PAYMENT_AUTHORIZED,
            EventType.PAYMENT_RECONCILIATION_REQUIRED,
            EventType.PAYMENT_SUBMISSION_IDENTIFIED,
            EventType.PAYMENT_SETTLED,
        ]

        async def append(index: int):
            return await repository.append_event(
                purchase_id="purchase-mongo",
                event_type=EventType.CORRECTION_RECORDED,
                occurred_at=clock.now(),
                actor={"id": "test", "type": "test"},
                payload={"index": index},
            )

        await asyncio.gather(*(append(index) for index in range(12)))
        events = await repository.list_events("purchase-mongo")
        assert [event.sequence for event in events] == list(range(1, 13))
        assert all(
            current.previous_event_hash == previous.event_hash
            for previous, current in zip(events, events[1:], strict=False)
        )
        head = await repository.get_event_head("purchase-mongo")
        assert head is not None
        verify_event_chain(
            events,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )

        cipher = LocalEnvelopeCipher(master_key=b"m" * 32)
        payload = cipher.encrypt_json(
            purchase_id="purchase-mongo",
            kind="request",
            payload={"prompt": "mongo-private-value"},
            created_at=clock.now(),
        )
        await repository.store_sensitive_payload(payload)
        raw = await cleanup[database]["sensitivePayloads"].find_one(
            {"payloadId": payload.payload_id}
        )
        assert raw is not None
        assert "mongo-private-value" not in str(raw)
        stored = await repository.get_sensitive_payload(payload.payload_id)
        assert stored is not None
        assert cipher.decrypt_json(stored)["prompt"] == "mongo-private-value"

        seller_execution = SellerExecution(
            purchase_id="purchase-seller-recovery",
            quote_id="quote-seller-recovery",
            seller_agent_id="seller-gemini",
            prompt_hash="prompt-hash",
            payment_proof_hash="payment-proof-hash",
            prompt_payload_id=payload.payload_id,
            state=SellerExecutionState.CLAIMED,
            claimed_at=clock.now(),
        )
        claimed_execution = await repository.claim_seller_execution(seller_execution)
        assert claimed_execution == seller_execution
        submitted_execution = replace(
            claimed_execution,
            state=SellerExecutionState.SUBMITTED,
            authorization_payload_id="payload-authorization",
        )
        transitioned_execution = await repository.transition_seller_execution(
            purchase_id=seller_execution.purchase_id,
            expected_state=SellerExecutionState.CLAIMED,
            next_execution=submitted_execution,
        )
        assert transitioned_execution == submitted_execution

        reopened_repository = MongoEvidenceRepository(uri=mongo_uri, database=database)
        try:
            recovered_execution = await reopened_repository.get_seller_execution(
                seller_execution.purchase_id
            )
            assert recovered_execution == submitted_execution
        finally:
            await reopened_repository.close()
        settled_execution = replace(
            submitted_execution,
            state=SellerExecutionState.SETTLED,
            settlement_payload_id="payload-settlement",
        )
        await repository.transition_seller_execution(
            purchase_id=seller_execution.purchase_id,
            expected_state=SellerExecutionState.SUBMITTED,
            next_execution=settled_execution,
        )
        provider_submitted_execution_a = replace(
            settled_execution,
            state=SellerExecutionState.PROVIDER_SUBMITTED,
            provider_attempt_id="sha256:" + "33" * 32,
            provider_attempt_token="provider-attempt-token-000000000001",
        )
        provider_submitted_execution_b = replace(
            provider_submitted_execution_a,
            provider_attempt_token="provider-attempt-token-000000000002",
        )
        provider_attempt_race = await asyncio.gather(
            *(
                repository.transition_seller_execution(
                    purchase_id=seller_execution.purchase_id,
                    expected_state=SellerExecutionState.SETTLED,
                    next_execution=candidate,
                )
                for candidate in (
                    provider_submitted_execution_a,
                    provider_submitted_execution_b,
                )
            )
        )
        provider_submitted_execution = provider_attempt_race[0]
        assert all(
            item.provider_attempt_token
            == provider_submitted_execution.provider_attempt_token
            for item in provider_attempt_race
        )
        assert provider_submitted_execution.provider_attempt_token in {
            "provider-attempt-token-000000000001",
            "provider-attempt-token-000000000002",
        }
        provider_reopened_repository = MongoEvidenceRepository(
            uri=mongo_uri, database=database
        )
        try:
            recovered_provider_attempt = (
                await provider_reopened_repository.get_seller_execution(
                    seller_execution.purchase_id
                )
            )
            assert recovered_provider_attempt == provider_submitted_execution
        finally:
            await provider_reopened_repository.close()

        for index in range(3):
            await repository.append_event(
                purchase_id="purchase-corrupt-head",
                event_type=EventType.CORRECTION_RECORDED,
                occurred_at=clock.now(),
                actor={"id": "test", "type": "test"},
                payload={"index": index},
            )
        await cleanup[database]["evidenceHeads"].delete_one(
            {"purchaseId": "purchase-corrupt-head", "eventCount": 2}
        )
        with pytest.raises(EvidenceIntegrityError, match="counts diverged"):
            await repository.append_event(
                purchase_id="purchase-corrupt-head",
                event_type=EventType.CORRECTION_RECORDED,
                occurred_at=clock.now(),
                actor={"id": "test", "type": "test"},
                payload={"mustNotAppend": True},
            )
        assert (
            await cleanup[database]["purchaseEvents"].count_documents(
                {"purchaseId": "purchase-corrupt-head"}
            )
            == 3
        )
        assert (
            await cleanup[database]["evidenceHeads"].count_documents(
                {"purchaseId": "purchase-corrupt-head"}
            )
            == 2
        )

        await cleanup[database]["purchaseEvents"].delete_one(
            {"purchaseId": "purchase-mongo", "sequence": head.event_count}
        )
        truncated = await repository.list_events("purchase-mongo")
        with pytest.raises(EvidenceIntegrityError, match="event count"):
            verify_event_chain(
                truncated,
                expected_event_count=head.event_count,
                expected_head_event_hash=head.head_event_hash,
            )
    finally:
        await failing_repository.close()
        await repository.close()
        await cleanup.drop_database(database)
        await cleanup.close()


PHASE6_OWNER = "0xowner-phase6"
PHASE6_BUYER = "0xbuyer-phase6"
PHASE6_TOKEN = "0xtoken-phase6"
PHASE6_SELLER = "0xseller-phase6"
PHASE6_OTHER_TOKEN = "0xtoken-phase6-alt"
PHASE6_RUN_ID = "a" * 32
PHASE6_LOCAL_TX = f"localtx:{PHASE6_RUN_ID}:payment:000001"
PHASE6_SECOND_LOCAL_TX = f"localtx:{PHASE6_RUN_ID}:payment:000002"
PHASE6_EVM_TX = "0x" + "ab" * 32
PHASE6_SCENARIO = ScenarioMetadata(
    run_id=PHASE6_RUN_ID,
    scenario_id="P6-A04-WRONG-AMOUNT",
    catalog_version="phase6.v1",
    catalog_hash="sha256:" + "cd" * 32,
)


async def seed_phase6_reconciliation(
    repository: MongoEvidenceRepository,
    clock,
    purchase_id: str,
    *,
    bind_wallet: bool = True,
    configure_policy: bool = True,
    daily_limit_units: int = 1_000_000,
    local_transaction_id: str = PHASE6_LOCAL_TX,
) -> PaymentService:
    if bind_wallet:
        await repository.bind_buyer_wallet(
            owner_address=PHASE6_OWNER,
            buyer_wallet_address=PHASE6_BUYER,
            bound_at=clock.now(),
        )
    if configure_policy:
        await repository.put_wallet_policy(
            WalletPolicy(
                buyer_wallet_address=PHASE6_BUYER,
                policy_date=clock.now().date().isoformat(),
                token=PHASE6_TOKEN,
                per_transaction_limit_units=250_000,
                daily_limit_units=daily_limit_units,
            )
        )
    await repository.append_event(
        purchase_id=purchase_id,
        event_type=EventType.REQUESTED,
        occurred_at=clock.now(),
        actor={"id": PHASE6_OWNER, "type": "user"},
        payload={
            "budgetUnits": 250_000,
            "domain": "fake",
            "normalizedRequest": {"value": "phase6"},
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
                    "quote_id": f"quote-{purchase_id}",
                    "purchase_id": purchase_id,
                    "seller_agent_id": "seller-phase6",
                    "provider_id": "gemini",
                    "model_id": "model-a",
                    "model_version": "v1",
                    "amount_units": 100_000,
                    "token": PHASE6_TOKEN,
                    "pay_to": PHASE6_SELLER,
                    "available": True,
                    "expires_at": clock.now().replace(year=2027).isoformat(),
                    "signer_address": PHASE6_SELLER,
                    "chain_id": 84532,
                    "verifying_contract": "0xquotecontract",
                }
            ],
            "quoteIdentityEvidence": [
                {
                    "quoteId": f"quote-{purchase_id}",
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
        payload={"winner": {"quote_id": f"quote-{purchase_id}"}},
    )
    service = PaymentService(repository=repository, clock=clock)
    await service.claim(purchase_id)
    await service.authorize(
        purchase_id=purchase_id,
        authorization_hash="0xauthorization",
        signature="0xsignature",
    )
    await service.require_reconciliation(
        purchase_id=purchase_id,
        reason="synthetic facilitator returned a local submission",
        local_transaction_id=local_transaction_id,
        scenario=PHASE6_SCENARIO,
    )
    return service


def phase6_check(attempt: int, outcome: ReconciliationVerifierOutcome, *, tag: str = "0"):
    return ReconciliationCheck(
        attempt_number=attempt,
        checked_at=datetime(2026, 9, 4, 12, attempt, tzinfo=UTC),
        checked_chain_id=84532,
        submission_ref=PHASE6_LOCAL_TX,
        verifier_outcome=outcome,
        finality_confirmations=3,
        proof_ref=f"sha256:{'1' * 62}{tag}{attempt}",
        evidence_source=EvidenceSource.SYNTHETIC_LOCAL,
        receipt_status=1,
        block_number=46_349_821,
        scenario=PHASE6_SCENARIO,
    )


def phase6_mismatch(
    *,
    amount_units: int = 200_000,
    token: str = PHASE6_TOKEN,
    local_tx_id: str = PHASE6_LOCAL_TX,
    proof_ref: str = "sha256:" + "22" * 32,
):
    return ConfirmedMismatchProof(
        actual_transfer=ActualTransfer(
            amount_units=amount_units,
            token=token,
            from_address=PHASE6_BUYER,
            to_address=PHASE6_SELLER,
        ),
        transaction_ref=LocalTransactionRef(id=local_tx_id, run_id=PHASE6_RUN_ID),
        proof_ref=proof_ref,
        evidence_source=EvidenceSource.SYNTHETIC_LOCAL,
        scenario=PHASE6_SCENARIO,
    )


def phase6_no_transfer(*, attempts: int = 3):
    return NoTransferProof(
        reason_code="SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER",
        checked_chain_id=84532,
        attempt_count=attempts,
        first_checked_at=datetime(2026, 9, 4, 12, 1, tzinfo=UTC),
        last_checked_at=datetime(2026, 9, 4, 12, 9, tzinfo=UTC),
        authorization_nonce_hash="sha256:" + "44" * 32,
        finality_evidence={"confirmations": 3},
        proof_ref="sha256:" + "33" * 32,
        evidence_source=EvidenceSource.SYNTHETIC_LOCAL,
        submission_ref=PHASE6_LOCAL_TX,
        scenario=PHASE6_SCENARIO,
    )


@pytest.mark.asyncio
async def test_real_mongo_phase6_indexes_are_partial_and_old_documents_stay_readable(
    mongo_uri, clock
) -> None:
    database = f"pbl_phase6_index_test_{uuid.uuid4().hex}"
    repository = MongoEvidenceRepository(uri=mongo_uri, database=database)
    cleanup: AsyncMongoClient = AsyncMongoClient(mongo_uri)
    try:
        # Pre-existing rows without any Phase 6 payload must not block index creation.
        await cleanup[database]["purchaseEvents"].insert_many(
            [
                {
                    "actor": {"id": "legacy", "type": "service"},
                    "eventHash": f"sha256:{index:064d}",
                    "eventId": f"legacy-{index}",
                    "evidenceRefs": [],
                    "occurredAt": clock.now(),
                    "payload": {"transactionHash": "0x" + f"{index:064x}"},
                    "payloadHash": f"sha256:{index:064d}",
                    "previousEventHash": None,
                    "purchaseId": f"legacy-purchase-{index}",
                    "sequence": 1,
                    "type": EventType.PAYMENT_SETTLED.value,
                }
                for index in (1, 2)
            ]
        )
        await cleanup[database]["paymentIntents"].insert_one(
            {
                "purchaseId": "legacy-permit2-purchase",
                "buyerWalletAddress": PHASE6_BUYER,
                "policyDate": "2026-01-01",
                "quoteId": "legacy-quote",
                "decisionEventHash": "sha256:" + "ee" * 32,
                "amountUnits": 100_000,
                "token": PHASE6_TOKEN,
                "payTo": PHASE6_SELLER,
                "permit2Nonce": "123456789",
                "transferMethod": "permit2",
                "state": PaymentIntentState.SETTLED.value,
                "claimedAt": clock.now(),
                "transactionHash": "0x" + "32" * 32,
            }
        )

        # M2: the preflight reports duplicates and never writes, before any unique index.
        preflight = await repository.phase6_index_collision_report()
        assert preflight == []
        before = await cleanup[database]["purchaseEvents"].count_documents({})
        legacy_row = {
            "actor": {"id": "legacy", "type": "service"},
            "eventHash": "sha256:" + "cc" * 32,
            "evidenceRefs": [],
            "occurredAt": clock.now(),
            "payloadHash": "sha256:" + "cc" * 32,
            "previousEventHash": None,
        }
        # One shared terminal outcome key across two purchases: exactly what the new
        # `payload.terminalOutcomeKey` unique index forbids, and legal under the old ones.
        outcome_rows = [
            {
                **legacy_row,
                "eventId": f"legacy-outcome-{index}",
                "payload": {"terminalOutcomeKey": "terminal:shared-legacy"},
                "purchaseId": f"legacy-outcome-purchase-{index}",
                "sequence": 1,
                "type": EventType.PAYMENT_SETTLED.value,
            }
            for index in (1, 2)
        ]
        # Two events of each newly introduced singleton type on a single purchase.
        singleton_types = [
            event_type
            for event_type in (
                EventType.PAYMENT_MISMATCH_CONFIRMED,
                EventType.PAYMENT_RECONCILED_NO_TRANSFER,
            )
            for _ in (1, 2)
        ]
        singleton_rows = [
            {
                **legacy_row,
                "eventId": f"legacy-singleton-{offset}",
                "payload": {},
                "purchaseId": "legacy-singleton-purchase",
                "sequence": offset,
                "type": event_type.value,
            }
            for offset, event_type in enumerate(singleton_types, start=1)
        ]
        await cleanup[database]["purchaseEvents"].insert_many(
            [*outcome_rows, *singleton_rows]
        )
        collisions = await repository.phase6_index_collision_report()
        reported = {item["index"]: item for item in collisions}
        assert reported["unique_phase6_terminal_outcome"]["key"] == {
            "payload_terminalOutcomeKey": "terminal:shared-legacy"
        }
        assert reported["unique_phase6_terminal_outcome"]["count"] == 2
        # M2: the two new per-purchase singletons are covered, not silently created.
        for name in (
            "unique_payment_mismatch_per_purchase",
            "unique_payment_no_transfer_per_purchase",
        ):
            assert reported[name]["key"] == {"purchaseId": "legacy-singleton-purchase"}
            assert reported[name]["count"] == 2
        assert await cleanup[database]["purchaseEvents"].count_documents({}) == before + 6

        # M2: ensure_indexes must refuse to create a new unique index over collisions, and
        # it must refuse before creating any of them.
        with pytest.raises(EvidenceIntegrityError, match="preflight found existing collisions"):
            await repository.ensure_indexes()
        created = await cleanup[database]["purchaseEvents"].index_information()
        for name in (
            "unique_payment_mismatch_per_purchase",
            "unique_payment_no_transfer_per_purchase",
            "unique_phase6_terminal_outcome",
            "unique_phase6_reconciliation_attempt",
            "unique_phase6_rejected_attempt",
        ):
            assert name not in created, f"{name} was created before the preflight"

        await cleanup[database]["purchaseEvents"].delete_many(
            {
                "eventId": {
                    "$in": [
                        str(row["eventId"])
                        for row in (*outcome_rows, *singleton_rows)
                    ]
                }
            }
        )
        await repository.ensure_indexes()
        await repository.ensure_indexes()

        indexes = await cleanup[database]["purchaseEvents"].index_information()
        assert indexes["unique_phase6_terminal_outcome"]["key"] == [
            ("payload.terminalOutcomeKey", 1)
        ]
        assert indexes["unique_phase6_terminal_outcome"]["unique"] is True
        assert indexes["unique_phase6_terminal_outcome"]["partialFilterExpression"] == {
            "type": {
                "$in": [
                    EventType.PAYMENT_SETTLED.value,
                    EventType.PAYMENT_FAILED.value,
                    EventType.PAYMENT_MISMATCH_CONFIRMED.value,
                    EventType.PAYMENT_RECONCILED_NO_TRANSFER.value,
                ]
            },
            "payload.terminalOutcomeKey": {"$type": "string"},
        }
        assert indexes["unique_phase6_reconciliation_attempt"]["key"] == [
            ("purchaseId", 1),
            ("type", 1),
            ("payload.attemptNumber", 1),
        ]
        assert indexes["unique_phase6_rejected_attempt"]["key"] == [
            ("purchaseId", 1),
            ("type", 1),
            ("payload.attemptId", 1),
        ]
        assert "unique_payment_mismatch_per_purchase" in indexes
        assert "unique_payment_no_transfer_per_purchase" in indexes
        outflow_indexes = await cleanup[database]["confirmedOutflows"].index_information()
        assert outflow_indexes["unique_confirmed_outflow_terminal"]["unique"] is True
        assert outflow_indexes["unique_confirmed_outflow_transaction"][
            "partialFilterExpression"
        ] == {"transactionRef.kind": "EVM", "transactionRef.hash": {"$type": "string"}}
        assert outflow_indexes["unique_confirmed_outflow_transaction_local"]["key"] == [
            ("transactionRef.runId", 1),
            ("transactionRef.id", 1),
        ]

        legacy_intent = await repository.get_payment_intent("legacy-permit2-purchase")
        assert legacy_intent is not None
        assert legacy_intent.transfer_method == "permit2"
        assert legacy_intent.reconciliation_attempt_count == 0
        assert legacy_intent.reconciliation_last_outcome is None
        assert legacy_intent.actual_transfer is None
        assert legacy_intent.mismatched_fields == ()
        assert legacy_intent.terminal_outcome_key is None
        assert legacy_intent.terminal_evidence_source is None
        assert legacy_intent.local_transaction_id is None
        legacy_service = PaymentService(repository=repository, clock=clock)
        await repository.append_event(
            purchase_id="legacy-permit2-purchase",
            event_type=EventType.CORRECTION_RECORDED,
            occurred_at=clock.now(),
            actor={"id": "legacy", "type": "service"},
            payload={"note": "historical evidence"},
        )
        with pytest.raises(PaymentConflictError, match="read-only"):
            await legacy_service.confirm_mismatch(
                purchase_id="legacy-permit2-purchase", proof=phase6_mismatch()
            )
        with pytest.raises(PaymentConflictError, match="read-only"):
            await legacy_service.reconcile_no_transfer(
                purchase_id="legacy-permit2-purchase", proof=phase6_no_transfer()
            )
        assert (
            await cleanup[database]["purchaseEvents"].count_documents(
                {
                    "purchaseId": "legacy-permit2-purchase",
                    "type": {
                        "$in": [
                            EventType.PAYMENT_MISMATCH_CONFIRMED.value,
                            EventType.PAYMENT_RECONCILED_NO_TRANSFER.value,
                        ]
                    },
                }
            )
            == 0
        )
        assert (
            await cleanup[database]["confirmedOutflows"].count_documents(
                {"purchaseId": "legacy-permit2-purchase"}
            )
            == 0
        )
    finally:
        await repository.close()
        await cleanup.drop_database(database)
        await cleanup.close()


@pytest.mark.asyncio
async def test_real_mongo_terminal_outcome_is_exclusive_under_concurrency(
    mongo_uri, clock
) -> None:
    database = f"pbl_phase6_terminal_test_{uuid.uuid4().hex}"
    repository = MongoEvidenceRepository(uri=mongo_uri, database=database)
    cleanup: AsyncMongoClient = AsyncMongoClient(mongo_uri)
    purchase_id = "purchase-phase6-race"
    try:
        await repository.ensure_indexes()
        service = await seed_phase6_reconciliation(repository, clock, purchase_id)
        for attempt in (1, 2, 3):
            await service.record_reconciliation_check(
                purchase_id=purchase_id,
                check=phase6_check(
                    attempt,
                    ReconciliationVerifierOutcome.SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER,
                ),
            )
        # A local submission can only be closed by synthetic terminal proofs, so the
        # third racer is a competing mismatch rather than a chain-shaped failure.
        results = await asyncio.gather(
            service.confirm_mismatch(purchase_id=purchase_id, proof=phase6_mismatch()),
            service.reconcile_no_transfer(
                purchase_id=purchase_id, proof=phase6_no_transfer()
            ),
            service.confirm_mismatch(
                purchase_id=purchase_id,
                proof=phase6_mismatch(
                    amount_units=150_000, proof_ref="sha256:" + "77" * 32
                ),
            ),
            return_exceptions=True,
        )

        assert len([item for item in results if not isinstance(item, BaseException)]) == 1
        terminal_documents = await cleanup[database]["purchaseEvents"].count_documents(
            {
                "purchaseId": purchase_id,
                "type": {
                    "$in": [
                        EventType.PAYMENT_SETTLED.value,
                        EventType.PAYMENT_FAILED.value,
                        EventType.PAYMENT_MISMATCH_CONFIRMED.value,
                        EventType.PAYMENT_RECONCILED_NO_TRANSFER.value,
                    ]
                },
            }
        )
        assert terminal_documents == 1
        assert (
            await cleanup[database]["purchaseEvents"].count_documents(
                {"purchaseId": purchase_id, "payload.terminalOutcomeKey": {"$exists": True}}
            )
            == 1
        )
        events = await repository.list_events(purchase_id)
        head = await repository.get_event_head(purchase_id)
        assert head is not None
        verify_event_chain(
            events,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )
        policy = await repository.get_wallet_policy(
            buyer_wallet_address=PHASE6_BUYER,
            policy_date=clock.now().date().isoformat(),
            token=PHASE6_TOKEN,
        )
        assert policy is not None
        assert policy.reserved_units == 0
        outflows = await repository.list_confirmed_outflows(purchase_id)
        assert len(outflows) <= 1

        with pytest.raises(PaymentConflictError):
            await service.record_reconciliation_check(
                purchase_id=purchase_id,
                check=phase6_check(4, ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND),
            )
    finally:
        await repository.close()
        await cleanup.drop_database(database)
        await cleanup.close()


@pytest.mark.asyncio
async def test_real_mongo_reconciliation_attempts_and_actual_spend_are_exact(
    mongo_uri, clock
) -> None:
    database = f"pbl_phase6_spend_test_{uuid.uuid4().hex}"
    repository = MongoEvidenceRepository(uri=mongo_uri, database=database)
    cleanup: AsyncMongoClient = AsyncMongoClient(mongo_uri)
    purchase_id = "purchase-phase6-spend"
    wrong_token_purchase_id = "purchase-phase6-wrong-token"
    try:
        await repository.ensure_indexes()
        service = await seed_phase6_reconciliation(repository, clock, purchase_id)

        attempts = await asyncio.gather(
            *(
                service.record_reconciliation_check(
                    purchase_id=purchase_id,
                    check=phase6_check(
                        1, ReconciliationVerifierOutcome.RECEIPT_NOT_FOUND
                    ),
                )
                for _ in range(8)
            ),
            return_exceptions=True,
        )
        assert any(not isinstance(item, BaseException) for item in attempts)
        assert (
            await cleanup[database]["purchaseEvents"].count_documents(
                {
                    "purchaseId": purchase_id,
                    "type": EventType.PAYMENT_RECONCILIATION_CHECKED.value,
                }
            )
            == 1
        )

        confirmed = await asyncio.gather(
            *(
                service.confirm_mismatch(purchase_id=purchase_id, proof=phase6_mismatch())
                for _ in range(6)
            )
        )
        assert all(
            item.state is PaymentIntentState.MISMATCH_CONFIRMED for item in confirmed
        )
        policy = await repository.get_wallet_policy(
            buyer_wallet_address=PHASE6_BUYER,
            policy_date=clock.now().date().isoformat(),
            token=PHASE6_TOKEN,
        )
        assert policy is not None
        assert policy.reserved_units == 0
        assert policy.spent_units == 200_000
        outflows = await repository.list_confirmed_outflows(purchase_id)
        assert len(outflows) == 1
        assert outflows[0].amount_units == 200_000
        assert outflows[0].token == PHASE6_TOKEN
        assert outflows[0].transaction_ref == LocalTransactionRef(
            id=PHASE6_LOCAL_TX, run_id=PHASE6_RUN_ID
        )
        with pytest.raises(DuplicateKeyError):
            await cleanup[database]["confirmedOutflows"].insert_one(
                {
                    "amountUnits": 1,
                    "buyerWallet": PHASE6_BUYER,
                    "policyDate": clock.now().date().isoformat(),
                    "proofRef": "sha256:" + "99" * 32,
                    "purchaseId": purchase_id,
                    "recipient": PHASE6_SELLER,
                    "terminalOutcomeKey": outflows[0].terminal_outcome_key,
                    "token": PHASE6_TOKEN,
                    "transactionRef": {
                        "evidenceSource": "SYNTHETIC_LOCAL",
                        "id": PHASE6_LOCAL_TX,
                        "kind": "LOCAL",
                        "runId": PHASE6_RUN_ID,
                    },
                }
            )

        wrong_token_service = await seed_phase6_reconciliation(
            repository,
            clock,
            wrong_token_purchase_id,
            bind_wallet=False,
            configure_policy=False,
        )
        with pytest.raises(PaymentConflictError, match="transaction is already recorded"):
            await wrong_token_service.confirm_mismatch(
                purchase_id=wrong_token_purchase_id,
                proof=phase6_mismatch(amount_units=100_000, token=PHASE6_OTHER_TOKEN),
            )
        # A purchase can only be closed by the submission it actually holds, so the
        # wrong-token close needs its own submitted local transaction.
        other_token_service = await seed_phase6_reconciliation(
            repository,
            clock,
            "purchase-phase6-other-token",
            bind_wallet=False,
            configure_policy=False,
            local_transaction_id=PHASE6_SECOND_LOCAL_TX,
        )
        await other_token_service.confirm_mismatch(
            purchase_id="purchase-phase6-other-token",
            proof=phase6_mismatch(
                amount_units=100_000,
                token=PHASE6_OTHER_TOKEN,
                local_tx_id=PHASE6_SECOND_LOCAL_TX,
                proof_ref="sha256:" + "55" * 32,
            ),
        )
        quoted_policy = await repository.get_wallet_policy(
            buyer_wallet_address=PHASE6_BUYER,
            policy_date=clock.now().date().isoformat(),
            token=PHASE6_TOKEN,
        )
        assert quoted_policy is not None
        # The rejected purchase is still open, so its quoted reservation is still held.
        assert quoted_policy.reserved_units == 100_000
        assert quoted_policy.spent_units == 200_000
        assert (
            await repository.get_wallet_policy(
                buyer_wallet_address=PHASE6_BUYER,
                policy_date=clock.now().date().isoformat(),
                token=PHASE6_OTHER_TOKEN,
            )
            is None
        )
        assert await repository.list_confirmed_outflows(wrong_token_purchase_id) == []
        other_token_outflows = await repository.list_confirmed_outflows(
            "purchase-phase6-other-token"
        )
        assert len(other_token_outflows) == 1
        assert other_token_outflows[0].token == PHASE6_OTHER_TOKEN
    finally:
        await repository.close()
        await cleanup.drop_database(database)
        await cleanup.close()


PHASE6_NEW_UNIQUE_INDEXES = (
    "unique_payment_mismatch_per_purchase",
    "unique_payment_no_transfer_per_purchase",
    "unique_phase6_terminal_outcome",
    "unique_phase6_reconciliation_attempt",
    "unique_phase6_rejected_attempt",
    "unique_confirmed_outflow_terminal",
    "unique_confirmed_outflow_transaction",
    "unique_confirmed_outflow_transaction_local",
)


@pytest.mark.asyncio
async def test_preflight_precedes_every_new_phase6_unique_index() -> None:
    """M2: no newly introduced unique index may be created before the collision report.

    This runs without a server: the repository's collections are replaced by recorders, so
    the assertion is about call order in `ensure_indexes()` itself.
    """
    calls: list[str] = []

    class RecordingCollection:
        def __init__(self, label: str) -> None:
            self._label = label

        async def index_information(self) -> dict[str, object]:
            return {}

        async def drop_index(self, _name: str) -> None:
            return None

        async def create_index(self, _keys: object, **kwargs: object) -> str:
            name = str(kwargs.get("name") or f"{self._label}:unnamed")
            calls.append(f"index:{name}")
            return name

        async def aggregate(self, _pipeline: object) -> AsyncIterator[dict[str, object]]:
            calls.append("preflight")

            async def empty() -> AsyncIterator[dict[str, object]]:
                if False:  # pragma: no cover - an empty async iterator
                    yield {}

            return empty()

    class RecordingAdmin:
        async def command(self, _payload: object) -> dict[str, object]:
            return {"ok": 1}

    class RecordingClient:
        admin = RecordingAdmin()

    repository = MongoEvidenceRepository.__new__(MongoEvidenceRepository)
    repository._client = RecordingClient()  # type: ignore[assignment]
    for attribute, label in (
        ("_users", "users"),
        ("_events", "events"),
        ("_heads", "evidenceHeads"),
        ("_sensitive", "sensitive"),
        ("_wallet_policies", "walletPolicies"),
        ("_payment_intents", "paymentIntents"),
        ("_seller_executions", "sellerExecutions"),
        ("_confirmed_outflows", "confirmedOutflows"),
    ):
        setattr(repository, attribute, RecordingCollection(label))

    await repository.ensure_indexes()

    assert "preflight" in calls
    # Every aggregation of the report happens before any new unique index is created.
    first_new_index = min(
        calls.index(f"index:{name}")
        for name in PHASE6_NEW_UNIQUE_INDEXES
        if f"index:{name}" in calls
    )
    assert calls.index("preflight") < first_new_index
    assert calls.count("preflight") == 1 or all(
        position < first_new_index
        for position, item in enumerate(calls)
        if item == "preflight"
    )
    # All eight new unique indexes are still created after the preflight passes.
    for name in PHASE6_NEW_UNIQUE_INDEXES:
        assert f"index:{name}" in calls, name


def test_collision_report_covers_every_new_phase6_unique_index() -> None:
    """M2: the report's coverage cannot drift from the set of new unique indexes."""
    source = inspect.getsource(MongoEvidenceRepository.ensure_indexes)
    created = set(re.findall(r"name=\"(unique_[a-z0-9_]+)\"", source))
    created.update(
        name for _event_type, name in _PHASE6_SINGLETON_INDEXES
    )
    report_source = inspect.getsource(
        MongoEvidenceRepository.phase6_index_collision_report
    )
    covered = set(re.findall(r"\"(unique_[a-z0-9_]+)\"", report_source))
    covered.update(name for _event_type, name in _PHASE6_SINGLETON_INDEXES)

    assert set(PHASE6_NEW_UNIQUE_INDEXES) <= created
    assert set(PHASE6_NEW_UNIQUE_INDEXES) <= covered
