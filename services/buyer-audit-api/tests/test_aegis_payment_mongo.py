"""The AEGIS payment attempt against a real MongoDB, including a reopened store.

The in-memory repository can only show that the rules hold in one process. These run the
same reservation, settlement and delivery against an actual server, then throw the client
away and read everything back, so the single-attempt guarantee is shown to be durable
rather than remembered.

Every test uses its own throwaway database and drops only that database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from pymongo import AsyncMongoClient

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.mongo import MongoEvidenceRepository
from buyer_audit_api.core.aegis_payment import AegisPaymentService
from buyer_audit_api.core.audit import AuditService
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.errors import PaymentConflictError
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.payment import PaymentIntentState, PaymentService, WalletPolicy
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.core.request_classification import StoredRequestClassifier
from buyer_audit_api.domains.ai_inference import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_PAGE_PATHS,
    RUNTIME_CATALOG_PATH,
    load_model_catalog,
)
from buyer_audit_api.domains.ai_inference.aa_models import TokenIdentity
from buyer_audit_api.domains.ai_inference.aa_workflow import AegisDecisionWorkflow

pytestmark = pytest.mark.mongo

OWNER = "0x00000000000000000000000000000000000a6e15"
BUYER_WALLET = "0x00000000000000000000000000000000000b0001"
TOKEN_ADDRESS = "0x0000000000000000000000000000000000000000"
PROMPT = "이 작업을 최대한 싸게 처리해 주세요. " + "세부 요구 사항입니다. " * 6


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 9, 3, 0, tzinfo=UTC)


@pytest.fixture
def mongo_uri() -> str:
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI is not configured")
    return uri


def _repository(uri: str, database: str) -> MongoEvidenceRepository:
    return MongoEvidenceRepository(uri=uri, database=database)


def _workflow(repository: MongoEvidenceRepository, clock: FrozenClock) -> AegisDecisionWorkflow:
    return AegisDecisionWorkflow(
        repository=repository,
        clock=clock,
        catalog=load_model_catalog(RUNTIME_CATALOG_PATH),
        source=FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS),
        token=TokenIdentity(
            name="AEGIS",
            symbol="AEGIS",
            decimals=6,
            address=TOKEN_ADDRESS,
            chain_id=84532,
        ),
    )


@pytest.mark.asyncio
async def test_a_real_mongo_payment_attempt_survives_a_reopened_store(mongo_uri) -> None:
    database = f"pbl_aegis_test_{uuid.uuid4().hex}"
    clock = FrozenClock()
    cipher = LocalEnvelopeCipher(master_key=b"m" * 32)
    repository = _repository(mongo_uri, database)
    cleanup: AsyncMongoClient = AsyncMongoClient(mongo_uri)
    try:
        await repository.ensure_indexes()
        purchases = PurchaseService(
            repository=repository,
            cipher=cipher,
            domains=DomainRegistry([AiInferenceDomainModule(default_max_output_tokens=1024)]),
            clock=clock,
        )
        created = await purchases.create(
            owner_address=OWNER,
            domain_id="ai_inference",
            raw_request={
                "requestSchema": "aegis-aa-v1",
                "prompt": PROMPT,
                "allowed_providers": ["openai"],
            },
            budget_units=50_000,
            policy={"scoringPolicyVersion": "aa-three-factor-v1"},
        )
        purchase_id = created.purchase_id
        decision = await _workflow(repository, clock).decide(purchase_id=purchase_id)

        await repository.bind_buyer_wallet(
            owner_address=OWNER,
            buyer_wallet_address=BUYER_WALLET,
            bound_at=clock.now(),
        )
        payments = PaymentService(repository=repository, clock=clock)
        await payments.configure_wallet_policy(
            WalletPolicy(
                buyer_wallet_address=BUYER_WALLET,
                policy_date=clock.now().date().isoformat(),
                token=TOKEN_ADDRESS,
                per_transaction_limit_units=100_000,
                daily_limit_units=1_000_000,
            )
        )
        aegis = AegisPaymentService(repository=repository, clock=clock)
        terms, intent = await aegis.reserve(purchase_id)
        assert terms.amount_units == decision.amount_units

        # Concurrent reservations of one purchase against the real store.
        again = await asyncio.gather(
            *(aegis.reserve(purchase_id) for _ in range(4)), return_exceptions=True
        )
        assert not [item for item in again if isinstance(item, BaseException)]
        assert {item[1].authorization_nonce for item in again} == {
            intent.authorization_nonce
        }

        await payments.authorize(
            purchase_id=purchase_id,
            authorization_hash="0x" + "ab" * 32,
            signature="0x" + "cd" * 65,
        )
        settled = await aegis.settle(
            purchase_id=purchase_id,
            facilitator_transaction="x402mock:" + "9" * 32,
            facilitator_network="eip155:84532",
            facilitator_payer=BUYER_WALLET,
            facilitator_amount=str(terms.amount_units),
        )
        assert settled.state is PaymentIntentState.SETTLED
    finally:
        await repository.close()

    # A brand new client and repository: nothing below can come from process memory.
    reopened = _repository(mongo_uri, database)
    try:
        events = await reopened.list_events(purchase_id)
        types = [event.type for event in events]
        assert types.count(EventType.PAYMENT_INTENT_CLAIMED) == 1
        assert types.count(EventType.PAYMENT_SETTLED) == 1
        stored = await reopened.get_payment_intent(purchase_id)
        assert stored is not None
        assert stored.state is PaymentIntentState.SETTLED
        assert stored.transaction_hash is None
        settlement = next(
            event for event in events if event.type == EventType.PAYMENT_SETTLED
        )
        assert settlement.payload["verificationBasis"] == "facilitator_response"
        assert settlement.payload["executionMode"] == "mock"
        assert settlement.payload["facilitatorResponse"]["amount"] == str(terms.amount_units)

        # A second attempt after the reopen is refused, not re-paid.
        reopened_payments = AegisPaymentService(repository=reopened, clock=FrozenClock())
        with pytest.raises(PaymentConflictError):
            await reopened_payments.fail(purchase_id=purchase_id, reason="late refusal")

        # And the audit re-classifies the stored original request from the same store.
        report = await AuditService(
            repository=reopened,
            clock=FrozenClock(),
            original_requests=StoredRequestClassifier(store=reopened, cipher=cipher),
        ).audit(purchase_id)
        rules = {finding.rule_id for finding in report.findings}
        assert "AUD-AA-PRIORITY-CLASSIFICATION-UNVERIFIABLE" not in rules
        assert "AUD-AA-PRIORITY-ORIGINAL-MISMATCH" not in rules
        assert "AUD-AA-PRIORITY-ORIGINAL-UNAVAILABLE" not in rules
    finally:
        await reopened.close()
        # Only the throwaway database this test created is removed.
        await cleanup.drop_database(database)
        await cleanup.close()
