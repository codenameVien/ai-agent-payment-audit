from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import replace

import pytest
from pymongo import AsyncMongoClient

from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.mongo import MongoEvidenceRepository
from buyer_audit_api.core.errors import EvidenceIntegrityError
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.payment import PaymentIntentState, PaymentService, WalletPolicy
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
        await repository.ensure_indexes()
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
        )
        assert raced_policy is not None
        assert raced_policy.reserved_units == 100_000

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
        assert len({intent.permit2_nonce for intent in intents}) == 1
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
