"""`MongoEvidenceRepository` guards against a real MongoDB.

Ported from the superseded repository tests: rollback when the immutable head write
fails, BSON millisecond round trips, wallet binding uniqueness, the per-token wallet
policy index, the atomic reservation and authorization (now driven by the AEGIS
reservation), hash-chain tamper detection and ciphertext-only storage.

It also pins the index set: an existing database already holds every index earlier
releases created, so `ensure_indexes` must succeed there without changing any of them.

Every test uses its own throwaway database and drops only that database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.mongo import MongoEvidenceRepository
from buyer_audit_api.core.aegis_payment import AegisPaymentService
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.errors import EvidenceIntegrityError
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.payment import PaymentIntentState, PaymentService, WalletPolicy
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.domains.ai_inference import AiInferenceDomainModule
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_CATALOG_PATH,
    FIXTURE_PAGE_PATHS,
    load_model_catalog,
)
from buyer_audit_api.domains.ai_inference.aa_models import TokenIdentity
from buyer_audit_api.domains.ai_inference.aa_workflow import AegisDecisionWorkflow

pytestmark = pytest.mark.mongo

OWNER = "0x0000000000000000000000000000000000000001"
BUYER = "0x0000000000000000000000000000000000000002"
TOKEN = "0x0000000000000000000000000000000000000000"
PROMPT = "x" * 100


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 9, 3, 0, tzinfo=UTC)


class FailingHeadMongoRepository(MongoEvidenceRepository):
    async def _insert_event_head(self, **kwargs: Any) -> None:
        del kwargs
        raise RuntimeError("injected head write failure")


@pytest.fixture
def mongo_uri() -> str:
    uri = os.getenv("TEST_MONGODB_URI")
    if not uri:
        pytest.skip("TEST_MONGODB_URI is not configured")
    return uri


@dataclass
class Store:
    database: str
    repository: MongoEvidenceRepository
    raw: AsyncMongoClient[dict[str, Any]]


@pytest.fixture
async def store(mongo_uri: str) -> AsyncIterator[Store]:
    database = f"pbl_aegis_repo_test_{uuid.uuid4().hex}"
    repository = MongoEvidenceRepository(uri=mongo_uri, database=database)
    raw: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(mongo_uri)
    try:
        yield Store(database=database, repository=repository, raw=raw)
    finally:
        await repository.close()
        await raw.drop_database(database)
        await raw.close()


async def _decided(repository: MongoEvidenceRepository, clock: FrozenClock) -> tuple[str, int]:
    purchases = PurchaseService(
        repository=repository,
        cipher=LocalEnvelopeCipher(master_key=b"m" * 32),
        domains=DomainRegistry([AiInferenceDomainModule(default_max_output_tokens=1024)]),
        clock=clock,
    )
    created = await purchases.create(
        owner_address=OWNER,
        domain_id="ai_inference",
        raw_request={"requestSchema": "aegis-aa-v1", "prompt": PROMPT},
        budget_units=5_000,
        policy={"scoringPolicyVersion": "aa-three-factor-v1"},
    )
    decision = await AegisDecisionWorkflow(
        repository=repository,
        clock=clock,
        catalog=load_model_catalog(FIXTURE_CATALOG_PATH),
        source=FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS),
        token=TokenIdentity(
            name="AEGIS", symbol="AEGIS", decimals=6, address=TOKEN, chain_id=84532
        ),
    ).decide(purchase_id=created.purchase_id)
    return created.purchase_id, decision.amount_units


def _converged(outcomes: list[Any]) -> list[Any]:
    """The successful results of a concurrent burst, after checking how the rest failed.

    The payment services read the event list and the immutable head as two separate
    reads, so a caller racing the winner's commit can see a torn head and is refused
    with an integrity error. That refusal is fail-closed - it writes nothing - and it is
    the only failure a loser may produce; the invariants are then checked on the store.
    """
    successes = [item for item in outcomes if not isinstance(item, BaseException)]
    failures = [item for item in outcomes if isinstance(item, BaseException)]
    assert successes, failures
    for failure in failures:
        assert isinstance(failure, EvidenceIntegrityError), repr(failure)
        assert "does not match immutable head" in str(failure), repr(failure)
    return successes


# --------------------------------------------------------------------------------------
# Append-only evidence: rollback, round trip, serialization and tamper detection
# --------------------------------------------------------------------------------------


async def test_a_failed_head_write_rolls_back_the_event_and_the_append_recovers(
    store: Store, mongo_uri: str
) -> None:
    clock = FrozenClock()
    failing = FailingHeadMongoRepository(uri=mongo_uri, database=store.database)
    try:
        await store.repository.ensure_indexes()
        with pytest.raises(RuntimeError, match="injected head write failure"):
            await failing.append_event(
                purchase_id="purchase-rollback",
                event_type=EventType.CORRECTION_RECORDED,
                occurred_at=clock.now(),
                actor={"id": "test", "type": "test"},
                payload={"mustRollback": True},
            )
    finally:
        await failing.close()
    database = store.raw[store.database]
    rolled_back = {"purchaseId": "purchase-rollback"}
    assert await database["purchaseEvents"].count_documents(rolled_back) == 0
    assert await database["evidenceHeads"].count_documents(rolled_back) == 0

    recovered = await store.repository.append_event(
        purchase_id="purchase-rollback",
        event_type=EventType.CORRECTION_RECORDED,
        occurred_at=clock.now(),
        actor={"id": "test", "type": "test"},
        payload={"retry": True},
    )
    head = await store.repository.get_event_head("purchase-rollback")
    assert recovered.sequence == 1
    assert head is not None
    assert (head.event_count, head.head_event_hash) == (1, recovered.event_hash)


async def test_event_time_round_trips_at_bson_millisecond_precision(store: Store) -> None:
    await store.repository.ensure_indexes()
    await store.repository.append_event(
        purchase_id="purchase-millisecond-round-trip",
        event_type=EventType.REQUESTED,
        occurred_at=datetime(2026, 9, 2, 12, 40, 21, 123456, tzinfo=UTC),
        actor={"id": "test", "type": "test"},
        payload={"value": "round-trip"},
    )
    events = await store.repository.list_events("purchase-millisecond-round-trip")
    assert events[0].occurred_at.microsecond == 123000
    verify_event_chain(events)


async def test_concurrent_appends_chain_and_tampering_is_detected(store: Store) -> None:
    clock = FrozenClock()
    await store.repository.ensure_indexes()

    async def append(index: int) -> None:
        await store.repository.append_event(
            purchase_id="purchase-mongo",
            event_type=EventType.CORRECTION_RECORDED,
            occurred_at=clock.now(),
            actor={"id": "test", "type": "test"},
            payload={"index": index},
        )

    await asyncio.gather(*(append(index) for index in range(12)))
    events = await store.repository.list_events("purchase-mongo")
    assert [event.sequence for event in events] == list(range(1, 13))
    assert all(
        current.previous_event_hash == previous.event_hash
        for previous, current in zip(events, events[1:], strict=False)
    )
    head = await store.repository.get_event_head("purchase-mongo")
    assert head is not None
    verify_event_chain(
        events,
        expected_event_count=head.event_count,
        expected_head_event_hash=head.head_event_hash,
    )

    database = store.raw[store.database]
    for index in range(3):
        await store.repository.append_event(
            purchase_id="purchase-corrupt-head",
            event_type=EventType.CORRECTION_RECORDED,
            occurred_at=clock.now(),
            actor={"id": "test", "type": "test"},
            payload={"index": index},
        )
    await database["evidenceHeads"].delete_one(
        {"purchaseId": "purchase-corrupt-head", "eventCount": 2}
    )
    with pytest.raises(EvidenceIntegrityError, match="counts diverged"):
        await store.repository.append_event(
            purchase_id="purchase-corrupt-head",
            event_type=EventType.CORRECTION_RECORDED,
            occurred_at=clock.now(),
            actor={"id": "test", "type": "test"},
            payload={"mustNotAppend": True},
        )
    assert (
        await database["purchaseEvents"].count_documents({"purchaseId": "purchase-corrupt-head"})
        == 3
    )
    assert (
        await database["evidenceHeads"].count_documents({"purchaseId": "purchase-corrupt-head"})
        == 2
    )

    await database["purchaseEvents"].delete_one(
        {"purchaseId": "purchase-mongo", "sequence": head.event_count}
    )
    truncated = await store.repository.list_events("purchase-mongo")
    with pytest.raises(EvidenceIntegrityError, match="event count"):
        verify_event_chain(
            truncated,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )


async def test_sensitive_payloads_are_stored_only_as_ciphertext(store: Store) -> None:
    await store.repository.ensure_indexes()
    cipher = LocalEnvelopeCipher(master_key=b"m" * 32)
    payload = cipher.encrypt_json(
        purchase_id="purchase-mongo",
        kind="request",
        payload={"prompt": "mongo-private-value"},
        created_at=FrozenClock().now(),
    )
    await store.repository.store_sensitive_payload(payload)

    raw = await store.raw[store.database]["sensitivePayloads"].find_one(
        {"payloadId": payload.payload_id}
    )
    assert raw is not None
    assert "mongo-private-value" not in str(raw)
    stored = await store.repository.get_sensitive_payload(payload.payload_id)
    assert stored is not None
    assert cipher.decrypt_json(stored)["prompt"] == "mongo-private-value"


# --------------------------------------------------------------------------------------
# Wallet binding and per-token wallet policy
# --------------------------------------------------------------------------------------


async def test_a_buyer_wallet_binds_to_exactly_one_owner(store: Store) -> None:
    clock = FrozenClock()
    await store.repository.ensure_indexes()
    binding = await store.repository.bind_buyer_wallet(
        owner_address="0xowner", buyer_wallet_address="0xbuyer", bound_at=clock.now()
    )
    assert binding.buyer_wallet_address == "0xbuyer"
    with pytest.raises(ValueError, match="already bound"):
        await store.repository.bind_buyer_wallet(
            owner_address="0xother", buyer_wallet_address="0xbuyer", bound_at=clock.now()
        )
    with pytest.raises(ValueError, match="already bound"):
        await store.repository.bind_buyer_wallet(
            owner_address="0xowner", buyer_wallet_address="0xreplacement", bound_at=clock.now()
        )


async def test_the_policy_index_is_per_token_and_an_active_policy_survives_a_race(
    store: Store,
) -> None:
    clock = FrozenClock()
    policies = store.raw[store.database]["walletPolicies"]
    # A database from before per-token policies holds the two-field unique index.
    await policies.create_index(
        [("buyerWalletAddress", 1), ("policyDate", 1)],
        unique=True,
        name="unique_wallet_policy_day",
    )
    await store.repository.ensure_indexes()
    assert (await policies.index_information())["unique_wallet_policy_day"]["key"] == [
        ("buyerWalletAddress", 1),
        ("policyDate", 1),
        ("token", 1),
    ]

    race_policy = WalletPolicy(
        buyer_wallet_address="0xbuyer-race",
        policy_date=clock.now().date().isoformat(),
        token="0xtoken-race",
        per_transaction_limit_units=200_000,
        daily_limit_units=1_000_000,
    )
    await store.repository.put_wallet_policy(race_policy)

    async def reserve_during_policy_retry() -> None:
        result = await policies.find_one_and_update(
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
        *(store.repository.put_wallet_policy(race_policy) for _ in range(24)),
        return_exceptions=True,
    )
    raced = await store.repository.get_wallet_policy(
        buyer_wallet_address=race_policy.buyer_wallet_address,
        policy_date=race_policy.policy_date,
        token=race_policy.token,
    )
    assert raced is not None
    # A reservation that landed during reconfiguration is never overwritten.
    assert raced.reserved_units == 100_000

    second_token = WalletPolicy(
        buyer_wallet_address=race_policy.buyer_wallet_address,
        policy_date=race_policy.policy_date,
        token="0xtoken-race-v2",
        per_transaction_limit_units=200_000,
        daily_limit_units=1_000_000,
    )
    await store.repository.put_wallet_policy(second_token)
    assert (
        await policies.count_documents(
            {
                "buyerWalletAddress": race_policy.buyer_wallet_address,
                "policyDate": race_policy.policy_date,
            }
        )
        == 2
    )
    assert await store.repository.get_wallet_policy(
        buyer_wallet_address=race_policy.buyer_wallet_address,
        policy_date=race_policy.policy_date,
        token=second_token.token,
    ) == second_token


# --------------------------------------------------------------------------------------
# The AEGIS reservation and authorization are atomic and serialized
# --------------------------------------------------------------------------------------


async def test_reserve_and_authorize_roll_back_atomically_and_serialize(
    store: Store, mongo_uri: str
) -> None:
    clock = FrozenClock()
    database = store.raw[store.database]
    repository = store.repository
    failing = FailingHeadMongoRepository(uri=mongo_uri, database=store.database)
    try:
        await repository.ensure_indexes()
        purchase_id, amount_units = await _decided(repository, clock)
        await repository.bind_buyer_wallet(
            owner_address=OWNER, buyer_wallet_address=BUYER, bound_at=clock.now()
        )
        await repository.put_wallet_policy(
            WalletPolicy(
                buyer_wallet_address=BUYER,
                policy_date=clock.now().date().isoformat(),
                token=TOKEN,
                per_transaction_limit_units=100_000,
                daily_limit_units=1_000_000,
            )
        )

        # A failed head write undoes the reservation, the intent and the event together.
        with pytest.raises(RuntimeError, match="injected head write failure"):
            await AegisPaymentService(repository=failing, clock=clock).reserve(purchase_id)
        raw_policy = await database["walletPolicies"].find_one(
            {"buyerWalletAddress": BUYER, "token": TOKEN}
        )
        assert raw_policy is not None
        assert raw_policy["reservedUnits"] == 0
        assert await database["paymentIntents"].count_documents({"purchaseId": purchase_id}) == 0
        assert await database["purchaseEvents"].count_documents({"purchaseId": purchase_id}) == 3
        assert await database["evidenceHeads"].count_documents({"purchaseId": purchase_id}) == 3

        aegis = AegisPaymentService(repository=repository, clock=clock)
        reservations = _converged(
            await asyncio.gather(
                *(aegis.reserve(purchase_id) for _ in range(12)), return_exceptions=True
            )
        )
        intents = [intent for _, intent in reservations]
        stored = await repository.get_payment_intent(purchase_id)
        assert stored is not None
        assert stored.state is PaymentIntentState.CLAIMED
        assert {intent.authorization_nonce for intent in intents} == {stored.authorization_nonce}
        assert all(intent.transfer_method == "eip3009" for intent in intents)
        # A later caller always converges on the one committed attempt.
        _, again = await aegis.reserve(purchase_id)
        assert again == stored
        assert await database["paymentIntents"].count_documents({"purchaseId": purchase_id}) == 1
        policy = await repository.get_wallet_policy(
            buyer_wallet_address=BUYER, policy_date=clock.now().date().isoformat(), token=TOKEN
        )
        assert policy is not None
        assert (policy.reserved_units, policy.spent_units) == (amount_units, 0)
        assert [event.type for event in await repository.list_events(purchase_id)] == [
            EventType.REQUESTED,
            EventType.AA_SNAPSHOT_RECORDED,
            EventType.DECIDED,
            EventType.PAYMENT_INTENT_CLAIMED,
        ]

        with pytest.raises(RuntimeError, match="injected head write failure"):
            await PaymentService(repository=failing, clock=clock).authorize(
                purchase_id=purchase_id, authorization_hash="0xauth", signature="0xsig"
            )
        rolled_back = await repository.get_payment_intent(purchase_id)
        assert rolled_back is not None
        assert rolled_back.state is PaymentIntentState.CLAIMED
        assert len(await repository.list_events(purchase_id)) == 4

        payments = PaymentService(repository=repository, clock=clock)
        authorized = _converged(
            await asyncio.gather(
                *(
                    payments.authorize(
                        purchase_id=purchase_id, authorization_hash="0xauth", signature="0xsig"
                    )
                    for _ in range(12)
                ),
                return_exceptions=True,
            )
        )
        assert all(item.state is PaymentIntentState.AUTHORIZED for item in authorized)
        assert [event.type for event in await repository.list_events(purchase_id)].count(
            EventType.PAYMENT_AUTHORIZED
        ) == 1

        settled = _converged(
            await asyncio.gather(
                *(
                    aegis.settle(
                        purchase_id=purchase_id,
                        facilitator_transaction="x402mock:" + "9" * 32,
                        facilitator_network="eip155:84532",
                        facilitator_payer=BUYER,
                    )
                    for _ in range(12)
                ),
                return_exceptions=True,
            )
        )
        assert all(item.state is PaymentIntentState.SETTLED for item in settled)
        policy = await repository.get_wallet_policy(
            buyer_wallet_address=BUYER, policy_date=clock.now().date().isoformat(), token=TOKEN
        )
        assert policy is not None
        # The reservation became exactly one spend of the decided amount.
        assert (policy.reserved_units, policy.spent_units) == (0, amount_units)
        assert [event.type for event in await repository.list_events(purchase_id)] == [
            EventType.REQUESTED,
            EventType.AA_SNAPSHOT_RECORDED,
            EventType.DECIDED,
            EventType.PAYMENT_INTENT_CLAIMED,
            EventType.PAYMENT_AUTHORIZED,
            EventType.PAYMENT_SETTLED,
        ]
    finally:
        await failing.close()


# --------------------------------------------------------------------------------------
# Index compatibility with databases created before the cleanup
# --------------------------------------------------------------------------------------

IndexSpec = tuple[str, str, list[tuple[str, int]], dict[str, Any]]


def _per_purchase(name: str, event_type: str) -> IndexSpec:
    return (
        "purchaseEvents",
        name,
        [("purchaseId", 1)],
        {"unique": True, "partialFilterExpression": {"type": event_type}},
    )


#: Indexes both main (25eaab3) and the cleaned-up code create, with main's exact specs.
#: Captured by running main's `ensure_indexes` against a scratch server.
KEPT_INDEXES: tuple[IndexSpec, ...] = (
    ("users", "ownerAddress_1", [("ownerAddress", 1)], {"unique": True}),
    (
        "users",
        "unique_buyer_wallet_binding",
        [("buyerWalletAddress", 1)],
        {"unique": True, "partialFilterExpression": {"buyerWalletAddress": {"$type": "string"}}},
    ),
    ("purchaseEvents", "eventId_1", [("eventId", 1)], {"unique": True}),
    (
        "purchaseEvents",
        "purchaseId_1_sequence_1",
        [("purchaseId", 1), ("sequence", 1)],
        {"unique": True},
    ),
    _per_purchase("unique_decided_per_purchase", "DECIDED"),
    _per_purchase("unique_aa_snapshot_per_purchase", "AA_SNAPSHOT_RECORDED"),
    _per_purchase("unique_payment_intent_per_purchase", "PAYMENT_INTENT_CLAIMED"),
    _per_purchase("unique_payment_settled_per_purchase", "PAYMENT_SETTLED"),
    _per_purchase("unique_payment_authorized_per_purchase", "PAYMENT_AUTHORIZED"),
    _per_purchase(
        "unique_payment_reconciliation_per_purchase", "PAYMENT_RECONCILIATION_REQUIRED"
    ),
    _per_purchase("unique_payment_failed_per_purchase", "PAYMENT_FAILED"),
    _per_purchase("unique_delivery_per_purchase", "DELIVERED"),
    _per_purchase("unique_audit_per_purchase", "AUDITED"),
    (
        "purchaseEvents",
        "unique_phase6_terminal_outcome",
        [("payload.terminalOutcomeKey", 1)],
        {
            "unique": True,
            # The frozen filter: the same four types, in the same order, as main.
            "partialFilterExpression": {
                "type": {
                    "$in": [
                        "PAYMENT_SETTLED",
                        "PAYMENT_FAILED",
                        "PAYMENT_MISMATCH_CONFIRMED",
                        "PAYMENT_RECONCILED_NO_TRANSFER",
                    ]
                },
                "payload.terminalOutcomeKey": {"$type": "string"},
            },
        },
    ),
    (
        "evidenceHeads",
        "purchaseId_1_eventCount_1",
        [("purchaseId", 1), ("eventCount", 1)],
        {"unique": True},
    ),
    ("evidenceHeads", "purchaseId_1_eventCount_-1", [("purchaseId", 1), ("eventCount", -1)], {}),
    ("sensitivePayloads", "payloadId_1", [("payloadId", 1)], {"unique": True}),
    ("sensitivePayloads", "purchaseId_1_kind_1", [("purchaseId", 1), ("kind", 1)], {}),
    ("immutableDocuments", "documentId_1", [("documentId", 1)], {"unique": True}),
    ("immutableDocuments", "purchaseId_1_kind_1", [("purchaseId", 1), ("kind", 1)], {}),
    (
        "walletPolicies",
        "unique_wallet_policy_day",
        [("buyerWalletAddress", 1), ("policyDate", 1), ("token", 1)],
        {"unique": True},
    ),
    ("paymentIntents", "unique_payment_intent_document", [("purchaseId", 1)], {"unique": True}),
)

#: Indexes only main created. Existing databases still hold them; the cleaned-up code
#: neither creates nor drops them. Same capture as above.
NO_LONGER_CREATED_INDEXES: tuple[IndexSpec, ...] = (
    _per_purchase("unique_quoted_per_purchase", "QUOTED"),
    (
        "purchaseEvents",
        "unique_settlement_transaction_hash",
        [("payload.transactionHash", 1)],
        {
            "unique": True,
            "partialFilterExpression": {
                "type": "PAYMENT_SETTLED",
                "payload.transactionHash": {"$type": "string"},
            },
        },
    ),
    _per_purchase("unique_evidence_anchor_per_purchase", "EVIDENCE_ANCHORED"),
    _per_purchase("unique_reputation_per_purchase", "REPUTATION_RECORDED"),
    _per_purchase("unique_payment_mismatch_per_purchase", "PAYMENT_MISMATCH_CONFIRMED"),
    _per_purchase("unique_payment_no_transfer_per_purchase", "PAYMENT_RECONCILED_NO_TRANSFER"),
    _per_purchase("unique_reputation_decision_per_purchase", "REPUTATION_DECIDED"),
    (
        "purchaseEvents",
        "unique_reputation_conflict_key",
        [("payload.conflictKey", 1)],
        {
            "unique": True,
            "partialFilterExpression": {
                "type": "REPUTATION_PUBLICATION_CONFLICT",
                "payload.conflictKey": {"$type": "string"},
            },
        },
    ),
    (
        "purchaseEvents",
        "unique_phase6_reconciliation_attempt",
        [("purchaseId", 1), ("type", 1), ("payload.attemptNumber", 1)],
        {
            "unique": True,
            "partialFilterExpression": {
                "type": "PAYMENT_RECONCILIATION_CHECKED",
                "payload.attemptNumber": {"$type": "number"},
            },
        },
    ),
    (
        "purchaseEvents",
        "unique_phase6_rejected_attempt",
        [("purchaseId", 1), ("type", 1), ("payload.attemptId", 1)],
        {
            "unique": True,
            "partialFilterExpression": {
                "type": "PAYMENT_ATTEMPT_REJECTED",
                "payload.attemptId": {"$type": "string"},
            },
        },
    ),
    (
        "confirmedOutflows",
        "unique_confirmed_outflow_terminal",
        [("terminalOutcomeKey", 1)],
        {"unique": True},
    ),
    (
        "confirmedOutflows",
        "unique_confirmed_outflow_transaction",
        [("transactionRef.hash", 1)],
        {
            "unique": True,
            "partialFilterExpression": {
                "transactionRef.kind": "EVM",
                "transactionRef.hash": {"$type": "string"},
            },
        },
    ),
    (
        "confirmedOutflows",
        "unique_confirmed_outflow_transaction_local",
        [("transactionRef.runId", 1), ("transactionRef.id", 1)],
        {
            "unique": True,
            "partialFilterExpression": {
                "transactionRef.kind": "LOCAL",
                "transactionRef.id": {"$type": "string"},
            },
        },
    ),
    ("confirmedOutflows", "confirmed_outflow_purchase", [("purchaseId", 1)], {}),
)

#: The complete index state main leaves in a database.
MAIN_ERA_INDEXES: tuple[IndexSpec, ...] = KEPT_INDEXES + NO_LONGER_CREATED_INDEXES


async def _index_state(
    raw: AsyncMongoClient[dict[str, Any]], database: str
) -> dict[str, dict[str, dict[str, Any]]]:
    state: dict[str, dict[str, dict[str, Any]]] = {}
    for collection in sorted(await raw[database].list_collection_names()):
        info = await raw[database][collection].index_information()
        state[collection] = {
            name: {key: value for key, value in spec.items() if key not in {"v", "ns"}}
            for name, spec in info.items()
            if name != "_id_"
        }
    return state


def _expected(indexes: tuple[IndexSpec, ...]) -> dict[str, dict[str, dict[str, Any]]]:
    state: dict[str, dict[str, dict[str, Any]]] = {}
    for collection, name, keys, options in indexes:
        state.setdefault(collection, {})[name] = {"key": keys, **options}
    return state


def test_the_main_era_fixture_is_complete_and_unambiguous() -> None:
    keys = [(collection, name) for collection, name, _, _ in MAIN_ERA_INDEXES]
    assert len(keys) == len(set(keys)) == 36
    kept = {name for _, name, _, _ in KEPT_INDEXES}
    assert kept.isdisjoint(name for _, name, _, _ in NO_LONGER_CREATED_INDEXES)


async def test_ensure_indexes_succeeds_on_a_database_holding_main_era_indexes(
    store: Store,
) -> None:
    database = store.raw[store.database]
    for collection, name, keys, options in MAIN_ERA_INDEXES:
        await database[collection].create_index(keys, name=name, **options)
    before = await _index_state(store.raw, store.database)
    assert before == _expected(MAIN_ERA_INDEXES)

    await store.repository.ensure_indexes()
    await store.repository.ensure_indexes()

    # Nothing was created, changed or dropped: every index the runtime needs already
    # exists with an identical spec, and the indexes it no longer creates stay in place.
    assert await _index_state(store.raw, store.database) == before


async def test_every_runtime_index_keeps_its_main_era_name_keys_and_options(
    store: Store,
) -> None:
    await store.repository.ensure_indexes()

    created = await _index_state(store.raw, store.database)

    # Exact equality: names, keys, uniqueness and every partial filter - including the
    # order of the frozen terminal `$in` list - are main's.
    assert created == _expected(KEPT_INDEXES)

    # The index still admits one terminal outcome per purchase, whatever its type.
    events = store.raw[store.database]["purchaseEvents"]
    base = {
        "actor": {"id": "test", "type": "test"},
        "evidenceRefs": [],
        "occurredAt": FrozenClock().now(),
        "payloadHash": "sha256:" + "cc" * 32,
        "previousEventHash": None,
        "sequence": 1,
    }
    await events.insert_one(
        {
            **base,
            "eventHash": "sha256:" + "01" * 32,
            "eventId": "terminal-a",
            "payload": {"terminalOutcomeKey": "terminal:shared"},
            "purchaseId": "purchase-a",
            "type": EventType.PAYMENT_SETTLED.value,
        }
    )
    with pytest.raises(DuplicateKeyError):
        await events.insert_one(
            {
                **base,
                "eventHash": "sha256:" + "02" * 32,
                "eventId": "terminal-b",
                "payload": {"terminalOutcomeKey": "terminal:shared"},
                "purchaseId": "purchase-b",
                "type": EventType.PAYMENT_FAILED.value,
            }
        )
