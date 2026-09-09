from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pymongo import AsyncMongoClient

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.mongo import MongoEvidenceRepository
from buyer_audit_api.core.audit import AuditService, AuditSeverity
from buyer_audit_api.core.documents import verify_immutable_document
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.errors import EvidenceImmutabilityError
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.domains.ai_inference import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_CATALOG_PATH,
    FIXTURE_PAGE_PATHS,
    load_model_catalog,
)
from buyer_audit_api.domains.ai_inference.aa_models import AaSnapshot, TokenIdentity
from buyer_audit_api.domains.ai_inference.aa_workflow import (
    AegisDecisionWorkflow,
    AegisEvidenceReader,
)

pytestmark = pytest.mark.mongo

OWNER = "0x0000000000000000000000000000000000000001"
PROMPT = "x" * 100


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 9, 3, 0, tzinfo=UTC)


@pytest.fixture
def mongo_uri() -> str:
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI is not configured")
    return uri


@pytest.mark.asyncio
async def test_real_mongo_aa_snapshot_round_trip_and_immutability(mongo_uri) -> None:
    # A dedicated throwaway database: no existing record is read, written or cleaned up.
    database = f"pbl_aegis_test_{uuid.uuid4().hex}"
    repository = MongoEvidenceRepository(uri=mongo_uri, database=database)
    cleanup: AsyncMongoClient = AsyncMongoClient(mongo_uri)
    clock = FrozenClock()
    try:
        await repository.ensure_indexes()
        indexes = await cleanup[database]["purchaseEvents"].index_information()
        assert "unique_aa_snapshot_per_purchase" in indexes

        purchases = PurchaseService(
            repository=repository,
            cipher=LocalEnvelopeCipher(master_key=b"p" * 32),
            domains=DomainRegistry(
                [AiInferenceDomainModule(default_max_output_tokens=1024)]
            ),
            clock=clock,
        )
        created = await purchases.create(
            owner_address=OWNER,
            domain_id="ai_inference",
            raw_request={"requestSchema": "aegis-aa-v1", "prompt": PROMPT},
            budget_units=5_000,
            policy={"scoringPolicyVersion": "aa-three-factor-v1"},
        )
        workflow = AegisDecisionWorkflow(
            repository=repository,
            clock=clock,
            catalog=load_model_catalog(FIXTURE_CATALOG_PATH),
            source=FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS),
            token=TokenIdentity(
                name="AEGIS",
                symbol="AEGIS",
                decimals=6,
                address="0x0000000000000000000000000000000000000000",
                chain_id=84532,
            ),
        )
        decision = await workflow.decide(purchase_id=created.purchase_id)

        stored = await repository.get_document(decision.snapshot_id)
        assert stored is not None
        verify_immutable_document(stored)
        assert stored.content_hash == decision.snapshot_hash
        # Decimal source values survive a BSON round trip as exact decimal text.
        restored = AaSnapshot.from_payload(
            {**stored.payload, "snapshotId": stored.document_id}
        )
        assert [str(model.input_price_per_million) for model in restored.models] == [
            "0.15",
            "0.8",
            "0.3",
        ]
        assert restored.raw_pages == tuple(
            path.read_text(encoding="utf-8") for path in FIXTURE_PAGE_PATHS
        )

        # Re-storing the identical body is idempotent; a different body is refused.
        assert (await repository.put_document(stored)).content_hash == stored.content_hash
        with pytest.raises(EvidenceImmutabilityError):
            await repository.put_document(
                replace(stored, payload={**stored.payload, "totalModelCount": 99})
            )
        unchanged = await repository.get_document(decision.snapshot_id)
        assert unchanged is not None
        assert unchanged.payload["totalModelCount"] == 4

        events = await repository.list_events(created.purchase_id)
        assert [event.type for event in events] == [
            EventType.REQUESTED,
            EventType.AA_SNAPSHOT_RECORDED,
            EventType.DECIDED,
        ]
        # The audit loads the immutable body back out of Mongo, recomputes the decision
        # from it, and finds nothing to contradict.
        report = await AuditService(repository=repository, clock=clock).audit(
            created.purchase_id
        )
        assert [finding.rule_id for finding in report.findings] == ["AUD-PAYMENT-PENDING"]
        assert report.severity is AuditSeverity.CAUTION

        bundle = await AegisEvidenceReader(repository=repository).decision_evidence(
            created.purchase_id
        )
        assert bundle["decision"]["amountUnits"] == decision.amount_units
    finally:
        await cleanup.drop_database(database)
        await cleanup.close()
        await repository.close()
