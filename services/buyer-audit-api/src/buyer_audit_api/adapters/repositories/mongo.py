from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal, cast

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError
from pymongo.server_api import ServerApi

from buyer_audit_api.core.documents import verify_immutable_document
from buyer_audit_api.core.errors import (
    DuplicateEventError,
    EvidenceImmutabilityError,
    EvidenceIntegrityError,
    EvidenceTransitionError,
    PaymentConflictError,
    PaymentPolicyError,
)
from buyer_audit_api.core.events import create_event, verify_event_chain
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceHead,
    EvidenceSource,
    ImmutableDocument,
    JsonObject,
    SensitivePayload,
    WalletBinding,
)
from buyer_audit_api.core.payment import (
    PaymentIntent,
    PaymentIntentState,
    ReservationAction,
    WalletPolicy,
)

# Per-purchase singleton events, each guarded by a partial unique index on `purchaseId`.
_SINGLETON_INDEXES: tuple[tuple[EventType, str], ...] = (
    (EventType.PAYMENT_AUTHORIZED, "unique_payment_authorized_per_purchase"),
    (
        EventType.PAYMENT_RECONCILIATION_REQUIRED,
        "unique_payment_reconciliation_per_purchase",
    ),
    (EventType.PAYMENT_FAILED, "unique_payment_failed_per_purchase"),
    (EventType.DELIVERED, "unique_delivery_per_purchase"),
    (EventType.AUDITED, "unique_audit_per_purchase"),
)

#: One terminal payment outcome per purchase, across every terminal event type.
_TERMINAL_OUTCOME_INDEX_NAME = "unique_phase6_terminal_outcome"
#: Frozen on purpose. Existing databases already hold this index, and MongoDB refuses to
#: create an index under an existing name with a different partial filter, so the list is
#: kept byte-for-byte as earlier releases created it - including two terminal types that
#: can no longer be written. Never derive it from `TERMINAL_PAYMENT_EVENT_TYPES`.
_TERMINAL_OUTCOME_INDEX_FILTER: dict[str, Any] = {
    "type": {
        "$in": [
            "PAYMENT_SETTLED",
            "PAYMENT_FAILED",
            "PAYMENT_MISMATCH_CONFIRMED",
            "PAYMENT_RECONCILED_NO_TRANSFER",
        ]
    },
    "payload.terminalOutcomeKey": {"$type": "string"},
}


def _event_document(event: EvidenceEvent) -> dict[str, Any]:
    return {
        "actor": event.actor,
        "eventHash": event.event_hash,
        "eventId": event.event_id,
        "evidenceRefs": list(event.evidence_refs),
        "occurredAt": event.occurred_at,
        "payload": event.payload,
        "payloadHash": event.payload_hash,
        "previousEventHash": event.previous_event_hash,
        "purchaseId": event.purchase_id,
        "sequence": event.sequence,
        "type": event.type.value,
    }


def _event_from_document(document: dict[str, Any]) -> EvidenceEvent:
    return EvidenceEvent(
        event_id=str(document["eventId"]),
        purchase_id=str(document["purchaseId"]),
        sequence=int(document["sequence"]),
        type=EventType(str(document["type"])),
        occurred_at=cast(datetime, document["occurredAt"]),
        actor=cast(JsonObject, document["actor"]),
        payload=cast(JsonObject, document["payload"]),
        payload_hash=str(document["payloadHash"]),
        previous_event_hash=cast(str | None, document.get("previousEventHash")),
        event_hash=str(document["eventHash"]),
        evidence_refs=tuple(str(item) for item in document.get("evidenceRefs", [])),
    )


def _sensitive_document(payload: SensitivePayload) -> dict[str, Any]:
    return {
        "algorithm": payload.algorithm,
        "ciphertext": payload.ciphertext,
        "contentHash": payload.content_hash,
        "createdAt": payload.created_at,
        "dekNonce": payload.dek_nonce,
        "encryptedDek": payload.encrypted_dek,
        "keyVersion": payload.key_version,
        "kind": payload.kind,
        "nonce": payload.nonce,
        "payloadId": payload.payload_id,
        "purchaseId": payload.purchase_id,
    }


def _sensitive_from_document(document: dict[str, Any]) -> SensitivePayload:
    return SensitivePayload(
        payload_id=str(document["payloadId"]),
        purchase_id=str(document["purchaseId"]),
        kind=str(document["kind"]),
        algorithm=str(document["algorithm"]),
        ciphertext=str(document["ciphertext"]),
        nonce=str(document["nonce"]),
        encrypted_dek=str(document["encryptedDek"]),
        dek_nonce=str(document["dekNonce"]),
        key_version=str(document["keyVersion"]),
        content_hash=str(document["contentHash"]),
        created_at=cast(datetime, document["createdAt"]),
    )


def _immutable_document(document: ImmutableDocument) -> dict[str, Any]:
    return {
        "contentHash": document.content_hash,
        "createdAt": document.created_at,
        "documentId": document.document_id,
        "kind": document.kind,
        "payload": document.payload,
        "purchaseId": document.purchase_id,
    }


def _immutable_from_document(document: dict[str, Any]) -> ImmutableDocument:
    payload = document["payload"]
    if not isinstance(payload, dict):
        raise EvidenceIntegrityError("stored immutable document payload is malformed")
    return ImmutableDocument(
        document_id=str(document["documentId"]),
        purchase_id=str(document["purchaseId"]),
        kind=str(document["kind"]),
        content_hash=str(document["contentHash"]),
        created_at=cast(datetime, document["createdAt"]),
        payload=cast(JsonObject, payload),
    )


def _wallet_policy_document(policy: WalletPolicy) -> dict[str, Any]:
    return {
        "buyerWalletAddress": policy.buyer_wallet_address,
        "policyDate": policy.policy_date,
        "token": policy.token,
        "perTransactionLimitUnits": policy.per_transaction_limit_units,
        "dailyLimitUnits": policy.daily_limit_units,
        "spentUnits": policy.spent_units,
        "reservedUnits": policy.reserved_units,
    }


def _wallet_policy_from_document(document: dict[str, Any]) -> WalletPolicy:
    return WalletPolicy(
        buyer_wallet_address=str(document["buyerWalletAddress"]),
        policy_date=str(document["policyDate"]),
        token=str(document["token"]),
        per_transaction_limit_units=int(document["perTransactionLimitUnits"]),
        daily_limit_units=int(document["dailyLimitUnits"]),
        spent_units=int(document["spentUnits"]),
        reserved_units=int(document["reservedUnits"]),
    )


def _payment_intent_document(intent: PaymentIntent) -> dict[str, Any]:
    return {
        "purchaseId": intent.purchase_id,
        "buyerWalletAddress": intent.buyer_wallet_address,
        "policyDate": intent.policy_date,
        "quoteId": intent.quote_id,
        "decisionEventHash": intent.decision_event_hash,
        "amountUnits": intent.amount_units,
        "token": intent.token,
        "payTo": intent.pay_to,
        "transferMethod": intent.transfer_method,
        "authorizationNonce": intent.authorization_nonce,
        "state": intent.state.value,
        "claimedAt": intent.claimed_at,
        "decisionAuthorizationHash": intent.decision_authorization_hash,
        "decisionAuthorizationSignature": intent.decision_authorization_signature,
        "authorizedAt": intent.authorized_at,
        "reconciliationReason": intent.reconciliation_reason,
        "settlementVerifiedAt": intent.settlement_verified_at,
        "failureReason": intent.failure_reason,
        "failedAt": intent.failed_at,
        "terminalOutcomeKey": intent.terminal_outcome_key,
        "terminalProofRef": intent.terminal_proof_ref,
        "terminalEvidenceSource": (
            intent.terminal_evidence_source.value
            if intent.terminal_evidence_source is not None
            else None
        ),
    }


def _transfer_method(document: dict[str, Any]) -> Literal["eip3009"]:
    """An AEGIS intent is always an ERC-3009 authorization; anything else fails closed."""
    if document.get("transferMethod") != "eip3009":
        raise EvidenceIntegrityError("stored payment intent is not an ERC-3009 authorization")
    return "eip3009"


def _payment_intent_from_document(document: dict[str, Any]) -> PaymentIntent:
    return PaymentIntent(
        purchase_id=str(document["purchaseId"]),
        buyer_wallet_address=str(document["buyerWalletAddress"]),
        policy_date=str(document["policyDate"]),
        quote_id=str(document["quoteId"]),
        decision_event_hash=str(document["decisionEventHash"]),
        amount_units=int(document["amountUnits"]),
        token=str(document["token"]),
        pay_to=str(document["payTo"]),
        state=PaymentIntentState(str(document["state"])),
        claimed_at=cast(datetime, document["claimedAt"]),
        transfer_method=_transfer_method(document),
        authorization_nonce=cast(str | None, document.get("authorizationNonce")),
        decision_authorization_hash=cast(
            str | None, document.get("decisionAuthorizationHash")
        ),
        decision_authorization_signature=cast(
            str | None, document.get("decisionAuthorizationSignature")
        ),
        authorized_at=cast(datetime | None, document.get("authorizedAt")),
        reconciliation_reason=cast(
            str | None, document.get("reconciliationReason")
        ),
        settlement_verified_at=cast(
            datetime | None, document.get("settlementVerifiedAt")
        ),
        failure_reason=cast(str | None, document.get("failureReason")),
        failed_at=cast(datetime | None, document.get("failedAt")),
        terminal_outcome_key=cast(str | None, document.get("terminalOutcomeKey")),
        terminal_proof_ref=cast(str | None, document.get("terminalProofRef")),
        terminal_evidence_source=(
            EvidenceSource(str(document["terminalEvidenceSource"]))
            if document.get("terminalEvidenceSource") is not None
            else None
        ),
    )


def _same_payment_claim_binding(left: PaymentIntent, right: PaymentIntent) -> bool:
    return (
        left.purchase_id == right.purchase_id
        and left.buyer_wallet_address == right.buyer_wallet_address
        and left.policy_date == right.policy_date
        and left.quote_id == right.quote_id
        and left.decision_event_hash == right.decision_event_hash
        and left.amount_units == right.amount_units
        and left.token == right.token
        and left.pay_to == right.pay_to
        and left.transfer_method == right.transfer_method
    )


class MongoEvidenceRepository:
    def __init__(self, *, uri: str, database: str) -> None:
        self._client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            uri,
            server_api=ServerApi("1"),
            tz_aware=True,
        )
        self._database = self._client[database]
        self._users = self._database["users"]
        self._events = self._database["purchaseEvents"]
        self._heads = self._database["evidenceHeads"]
        self._sensitive = self._database["sensitivePayloads"]
        self._documents = self._database["immutableDocuments"]
        self._wallet_policies = self._database["walletPolicies"]
        self._payment_intents = self._database["paymentIntents"]

    async def ensure_indexes(self) -> None:
        """Create the indexes the AEGIS runtime relies on; leave every other index alone.

        Every spec below is unchanged from earlier releases, so an existing database that
        already holds them - together with indexes earlier releases created for the
        superseded flows - starts without an index conflict, and those older indexes are
        left in place. The one exception is `unique_wallet_policy_day`: a database from
        before per-token wallet policies holds it on (buyerWalletAddress, policyDate)
        only, so an index under that name with different keys is dropped and recreated
        on (buyerWalletAddress, policyDate, token). That is the only index this method
        ever drops.
        """
        await self._client.admin.command({"ping": 1})
        await self._users.create_index([("ownerAddress", ASCENDING)], unique=True)
        await self._users.create_index(
            [("buyerWalletAddress", ASCENDING)],
            unique=True,
            name="unique_buyer_wallet_binding",
            partialFilterExpression={"buyerWalletAddress": {"$type": "string"}},
        )
        await self._events.create_index([("eventId", ASCENDING)], unique=True)
        await self._events.create_index(
            [("purchaseId", ASCENDING), ("sequence", ASCENDING)],
            unique=True,
        )
        await self._events.create_index(
            [("purchaseId", ASCENDING)],
            unique=True,
            name="unique_decided_per_purchase",
            partialFilterExpression={"type": EventType.DECIDED.value},
        )
        await self._events.create_index(
            [("purchaseId", ASCENDING)],
            unique=True,
            name="unique_aa_snapshot_per_purchase",
            partialFilterExpression={"type": EventType.AA_SNAPSHOT_RECORDED.value},
        )
        await self._events.create_index(
            [("purchaseId", ASCENDING)],
            unique=True,
            name="unique_payment_intent_per_purchase",
            partialFilterExpression={"type": EventType.PAYMENT_INTENT_CLAIMED.value},
        )
        await self._events.create_index(
            [("purchaseId", ASCENDING)],
            unique=True,
            name="unique_payment_settled_per_purchase",
            partialFilterExpression={"type": EventType.PAYMENT_SETTLED.value},
        )
        for event_type, name in _SINGLETON_INDEXES:
            await self._events.create_index(
                [("purchaseId", ASCENDING)],
                unique=True,
                name=name,
                partialFilterExpression={"type": event_type.value},
            )
        await self._events.create_index(
            [("payload.terminalOutcomeKey", ASCENDING)],
            unique=True,
            name=_TERMINAL_OUTCOME_INDEX_NAME,
            partialFilterExpression=_TERMINAL_OUTCOME_INDEX_FILTER,
        )
        await self._heads.create_index(
            [("purchaseId", ASCENDING), ("eventCount", ASCENDING)], unique=True
        )
        await self._heads.create_index([("purchaseId", ASCENDING), ("eventCount", DESCENDING)])
        await self._sensitive.create_index([("payloadId", ASCENDING)], unique=True)
        await self._sensitive.create_index([("purchaseId", ASCENDING), ("kind", ASCENDING)])
        await self._documents.create_index([("documentId", ASCENDING)], unique=True)
        await self._documents.create_index(
            [("purchaseId", ASCENDING), ("kind", ASCENDING)]
        )
        policy_index_name = "unique_wallet_policy_day"
        policy_index_keys = [
            ("buyerWalletAddress", ASCENDING),
            ("policyDate", ASCENDING),
            ("token", ASCENDING),
        ]
        policy_indexes = await self._wallet_policies.index_information()
        existing_policy_index = policy_indexes.get(policy_index_name)
        if (
            existing_policy_index is not None
            and existing_policy_index.get("key") != policy_index_keys
        ):
            await self._wallet_policies.drop_index(policy_index_name)
        await self._wallet_policies.create_index(
            policy_index_keys,
            unique=True,
            name=policy_index_name,
        )
        await self._payment_intents.create_index(
            [("purchaseId", ASCENDING)],
            unique=True,
            name="unique_payment_intent_document",
        )

    async def close(self) -> None:
        await self._client.close()

    async def bind_buyer_wallet(
        self,
        *,
        owner_address: str,
        buyer_wallet_address: str,
        bound_at: datetime,
    ) -> WalletBinding:
        normalized_owner = owner_address.lower()
        normalized_buyer = buyer_wallet_address.lower()
        try:
            await self._users.update_one(
                {
                    "ownerAddress": normalized_owner,
                    "$or": [
                        {"buyerWalletAddress": {"$exists": False}},
                        {"buyerWalletAddress": normalized_buyer},
                    ],
                },
                {
                    "$set": {
                        "buyerWalletAddress": normalized_buyer,
                        "buyerWalletBoundAt": bound_at,
                        "ownerAddress": normalized_owner,
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError as exc:
            raise ValueError("owner or buyer wallet is already bound") from exc
        return WalletBinding(normalized_owner, normalized_buyer, bound_at)

    async def get_wallet_binding(self, owner_address: str) -> WalletBinding | None:
        document = await self._users.find_one(
            {"ownerAddress": owner_address.lower()},
            {"_id": 0, "buyerWalletAddress": 1, "buyerWalletBoundAt": 1, "ownerAddress": 1},
        )
        if document is None or "buyerWalletAddress" not in document:
            return None
        return WalletBinding(
            owner_address=str(document["ownerAddress"]),
            buyer_wallet_address=str(document["buyerWalletAddress"]),
            bound_at=cast(datetime, document["buyerWalletBoundAt"]),
        )

    async def _insert_event_head(
        self,
        *,
        purchase_id: str,
        event: EvidenceEvent,
        anchored_at: datetime,
        session: AsyncClientSession,
    ) -> None:
        await self._heads.insert_one(
            {
                "purchaseId": purchase_id,
                "eventCount": event.sequence,
                "headEventHash": event.event_hash,
                "anchoredAt": anchored_at,
            },
            session=session,
        )

    async def _load_verified_chain(
        self,
        *,
        purchase_id: str,
        session: AsyncClientSession,
        expected_event_count: int | None = None,
        expected_head_event_hash: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[EvidenceEvent]]:
        event_documents = await (
            self._events.find({"purchaseId": purchase_id}, session=session)
            .sort("sequence", ASCENDING)
            .to_list(length=None)
        )
        head_documents = await (
            self._heads.find({"purchaseId": purchase_id}, session=session)
            .sort("eventCount", ASCENDING)
            .to_list(length=None)
        )
        events = [_event_from_document(document) for document in event_documents]
        if len(events) != len(head_documents):
            raise EvidenceIntegrityError("event and immutable head counts diverged")
        verify_event_chain(
            events,
            expected_event_count=len(head_documents),
            expected_head_event_hash=(
                str(head_documents[-1]["headEventHash"]) if head_documents else None
            ),
        )
        for event, head in zip(events, head_documents, strict=True):
            if (
                str(head["purchaseId"]) != purchase_id
                or int(head["eventCount"]) != event.sequence
                or str(head["headEventHash"]) != event.event_hash
            ):
                raise EvidenceIntegrityError("event and immutable head diverged")
        if expected_event_count is not None and len(events) != expected_event_count:
            raise EvidenceTransitionError("evidence head changed before append")
        actual_head_hash = events[-1].event_hash if events else None
        if (
            expected_head_event_hash is not None
            and actual_head_hash != expected_head_event_hash
        ):
            raise EvidenceTransitionError("evidence head changed before append")
        return event_documents, events

    @staticmethod
    def _next_event(
        *,
        purchase_id: str,
        event_documents: list[dict[str, Any]],
        event_type: EventType,
        occurred_at: datetime,
        actor: JsonObject,
        payload: JsonObject,
        evidence_refs: tuple[str, ...],
    ) -> EvidenceEvent:
        latest = event_documents[-1] if event_documents else None
        return create_event(
            purchase_id=purchase_id,
            sequence=int(latest["sequence"]) + 1 if latest else 1,
            event_type=event_type,
            occurred_at=occurred_at,
            actor=actor,
            payload=payload,
            previous_event_hash=str(latest["eventHash"]) if latest else None,
            evidence_refs=evidence_refs,
        )

    async def append_event(
        self,
        *,
        purchase_id: str,
        event_type: EventType,
        occurred_at: datetime,
        actor: JsonObject,
        payload: JsonObject,
        evidence_refs: tuple[str, ...] = (),
        expected_event_count: int | None = None,
        expected_head_event_hash: str | None = None,
    ) -> EvidenceEvent:
        async with self._client.start_session() as session:
            for attempt in range(64):

                async def append_once(active_session: AsyncClientSession) -> EvidenceEvent:
                    event_documents, _ = await self._load_verified_chain(
                        purchase_id=purchase_id,
                        session=active_session,
                        expected_event_count=expected_event_count,
                        expected_head_event_hash=expected_head_event_hash,
                    )
                    event = self._next_event(
                        purchase_id=purchase_id,
                        event_documents=event_documents,
                        event_type=event_type,
                        occurred_at=occurred_at,
                        actor=actor,
                        payload=payload,
                        evidence_refs=evidence_refs,
                    )
                    await self._events.insert_one(_event_document(event), session=active_session)
                    await self._insert_event_head(
                        purchase_id=purchase_id,
                        event=event,
                        anchored_at=occurred_at,
                        session=active_session,
                    )
                    return event

                try:
                    return await session.with_transaction(append_once)
                except DuplicateKeyError as exc:
                    key_pattern = (exc.details or {}).get("keyPattern", {})
                    if key_pattern == {"purchaseId": 1}:
                        raise DuplicateEventError(
                            f"duplicate singleton event: {event_type.value}"
                        ) from exc
                    await asyncio.sleep(min(0.001 * (2**attempt), 0.025))
                    continue
        raise DuplicateEventError("event append contention exceeded bounded retries")

    async def put_wallet_policy(self, policy: WalletPolicy) -> None:
        normalized = replace(
            policy,
            buyer_wallet_address=policy.buyer_wallet_address.lower(),
            token=policy.token.lower(),
        )
        key = {
            "buyerWalletAddress": normalized.buyer_wallet_address,
            "policyDate": normalized.policy_date,
            "token": normalized.token,
        }
        existing_document = await self._wallet_policies.find_one(key)
        if existing_document is not None:
            existing = _wallet_policy_from_document(existing_document)
            if existing.spent_units or existing.reserved_units:
                if existing != normalized:
                    raise PaymentConflictError("active wallet policy cannot be replaced")
                return
            result = await self._wallet_policies.replace_one(
                {
                    "_id": existing_document["_id"],
                    "spentUnits": existing.spent_units,
                    "reservedUnits": existing.reserved_units,
                },
                _wallet_policy_document(normalized),
            )
            if result.modified_count == 1 or existing == normalized:
                return
            concurrent = await self._wallet_policies.find_one(key)
            if concurrent is not None and _wallet_policy_from_document(concurrent) == normalized:
                return
            raise PaymentConflictError("wallet policy changed during configuration")
        try:
            await self._wallet_policies.insert_one(_wallet_policy_document(normalized))
        except DuplicateKeyError as exc:
            raise PaymentConflictError("wallet policy was configured concurrently") from exc

    async def get_wallet_policy(
        self, *, buyer_wallet_address: str, policy_date: str, token: str | None = None
    ) -> WalletPolicy | None:
        query = {
            "buyerWalletAddress": buyer_wallet_address.lower(),
            "policyDate": policy_date,
        }
        if token is not None:
            query["token"] = token.lower()
        document = await self._wallet_policies.find_one(query, sort=[("_id", DESCENDING)])
        return _wallet_policy_from_document(document) if document is not None else None

    async def get_latest_wallet_policy(
        self, buyer_wallet_address: str, *, max_policy_date: str | None = None
    ) -> WalletPolicy | None:
        query: dict[str, Any] = {
            "buyerWalletAddress": buyer_wallet_address.lower()
        }
        if max_policy_date is not None:
            query["policyDate"] = {"$lte": max_policy_date}
        document = await self._wallet_policies.find_one(
            query,
            sort=[("policyDate", DESCENDING), ("_id", DESCENDING)],
        )
        return _wallet_policy_from_document(document) if document is not None else None

    async def get_payment_intent(self, purchase_id: str) -> PaymentIntent | None:
        document = await self._payment_intents.find_one({"purchaseId": purchase_id})
        return _payment_intent_from_document(document) if document is not None else None

    async def claim_payment_intent(
        self,
        *,
        intent: PaymentIntent,
        occurred_at: datetime,
        actor: JsonObject,
        payload: JsonObject,
        evidence_refs: tuple[str, ...],
        expected_event_count: int,
        expected_head_event_hash: str,
    ) -> PaymentIntent:
        normalized_intent = replace(
            intent,
            buyer_wallet_address=intent.buyer_wallet_address.lower(),
            token=intent.token.lower(),
            pay_to=intent.pay_to.lower(),
            state=PaymentIntentState.CLAIMED,
        )
        async with self._client.start_session() as session:
            for attempt in range(64):

                async def claim_once(active_session: AsyncClientSession) -> PaymentIntent:
                    existing_document = await self._payment_intents.find_one(
                        {"purchaseId": normalized_intent.purchase_id},
                        session=active_session,
                    )
                    if existing_document is not None:
                        existing = _payment_intent_from_document(existing_document)
                        _, events = await self._load_verified_chain(
                            purchase_id=normalized_intent.purchase_id,
                            session=active_session,
                        )
                        claim_events = [
                            event
                            for event in events
                            if event.type == EventType.PAYMENT_INTENT_CLAIMED
                        ]
                        if (
                            not _same_payment_claim_binding(existing, normalized_intent)
                            or len(claim_events) != 1
                            or claim_events[0].payload.get("quoteId") != existing.quote_id
                            or claim_events[0].payload.get("decisionEventHash")
                            != existing.decision_event_hash
                            or claim_events[0].payload.get("transferMethod")
                            != existing.transfer_method
                            or claim_events[0].payload.get("authorizationNonce")
                            != existing.authorization_nonce
                        ):
                            raise PaymentConflictError(
                                "purchase payment intent has different immutable data"
                            )
                        return existing

                    event_documents, _ = await self._load_verified_chain(
                        purchase_id=normalized_intent.purchase_id,
                        session=active_session,
                        expected_event_count=expected_event_count,
                        expected_head_event_hash=expected_head_event_hash,
                    )
                    event = self._next_event(
                        purchase_id=normalized_intent.purchase_id,
                        event_documents=event_documents,
                        event_type=EventType.PAYMENT_INTENT_CLAIMED,
                        occurred_at=occurred_at,
                        actor=actor,
                        payload=payload,
                        evidence_refs=evidence_refs,
                    )
                    policy_document = await self._wallet_policies.find_one(
                        {
                            "buyerWalletAddress": normalized_intent.buyer_wallet_address,
                            "policyDate": normalized_intent.policy_date,
                            "token": normalized_intent.token,
                        },
                        session=active_session,
                    )
                    if policy_document is None:
                        raise PaymentPolicyError(
                            "wallet policy is not configured for today"
                        )
                    policy = _wallet_policy_from_document(policy_document)
                    if policy.token.lower() != normalized_intent.token:
                        raise PaymentPolicyError("wallet policy token mismatch")
                    if (
                        normalized_intent.amount_units
                        > policy.per_transaction_limit_units
                    ):
                        raise PaymentPolicyError(
                            "payment exceeds wallet transaction limit"
                        )
                    projected = (
                        policy.spent_units
                        + policy.reserved_units
                        + normalized_intent.amount_units
                    )
                    if projected > policy.daily_limit_units:
                        raise PaymentPolicyError("payment exceeds wallet daily limit")
                    updated_policy = await self._wallet_policies.find_one_and_update(
                        {
                            "_id": policy_document["_id"],
                            "token": normalized_intent.token,
                            "perTransactionLimitUnits": {
                                "$gte": normalized_intent.amount_units
                            },
                            "$expr": {
                                "$lte": [
                                    {
                                        "$add": [
                                            "$spentUnits",
                                            "$reservedUnits",
                                            normalized_intent.amount_units,
                                        ]
                                    },
                                    "$dailyLimitUnits",
                                ]
                            },
                        },
                        {"$inc": {"reservedUnits": normalized_intent.amount_units}},
                        return_document=ReturnDocument.AFTER,
                        session=active_session,
                    )
                    if updated_policy is None:
                        raise PaymentPolicyError("wallet policy changed during reservation")
                    await self._payment_intents.insert_one(
                        _payment_intent_document(normalized_intent),
                        session=active_session,
                    )
                    await self._events.insert_one(
                        _event_document(event), session=active_session
                    )
                    await self._insert_event_head(
                        purchase_id=normalized_intent.purchase_id,
                        event=event,
                        anchored_at=occurred_at,
                        session=active_session,
                    )
                    return normalized_intent

                try:
                    return await session.with_transaction(claim_once)
                except DuplicateKeyError:
                    await asyncio.sleep(min(0.001 * (2**attempt), 0.025))
                    continue
        raise PaymentConflictError("payment claim contention exceeded bounded retries")

    async def transition_payment_intent(
        self,
        *,
        next_intent: PaymentIntent,
        expected_states: tuple[PaymentIntentState, ...],
        event_type: EventType,
        occurred_at: datetime,
        actor: JsonObject,
        payload: JsonObject,
        evidence_refs: tuple[str, ...],
        expected_event_count: int,
        expected_head_event_hash: str,
        reservation_action: ReservationAction,
    ) -> PaymentIntent:
        normalized_next = replace(
            next_intent,
            buyer_wallet_address=next_intent.buyer_wallet_address.lower(),
            token=next_intent.token.lower(),
            pay_to=next_intent.pay_to.lower(),
        )
        immutable_fields = (
            "buyer_wallet_address",
            "policy_date",
            "quote_id",
            "decision_event_hash",
            "amount_units",
            "token",
            "pay_to",
            "transfer_method",
            "authorization_nonce",
            "claimed_at",
        )
        async with self._client.start_session() as session:
            for attempt in range(64):

                async def transition_once(
                    active_session: AsyncClientSession,
                ) -> PaymentIntent:
                    existing_document = await self._payment_intents.find_one(
                        {"purchaseId": normalized_next.purchase_id},
                        session=active_session,
                    )
                    if existing_document is None:
                        raise PaymentConflictError("payment intent is missing")
                    existing = _payment_intent_from_document(existing_document)
                    if existing.state == normalized_next.state:
                        _, events = await self._load_verified_chain(
                            purchase_id=normalized_next.purchase_id,
                            session=active_session,
                        )
                        matching = [event for event in events if event.type == event_type]
                        if existing == normalized_next and len(matching) == 1:
                            return existing
                        raise PaymentConflictError(
                            "payment transition has different data"
                        )
                    if existing.state not in expected_states:
                        raise PaymentConflictError(
                            "payment state changed before transition"
                        )
                    if any(
                        getattr(existing, field) != getattr(normalized_next, field)
                        for field in immutable_fields
                    ):
                        raise PaymentConflictError("payment immutable binding changed")
                    event_documents, _ = await self._load_verified_chain(
                        purchase_id=normalized_next.purchase_id,
                        session=active_session,
                        expected_event_count=expected_event_count,
                        expected_head_event_hash=expected_head_event_hash,
                    )
                    event = self._next_event(
                        purchase_id=normalized_next.purchase_id,
                        event_documents=event_documents,
                        event_type=event_type,
                        occurred_at=occurred_at,
                        actor=actor,
                        payload=payload,
                        evidence_refs=evidence_refs,
                    )
                    policy_filter: dict[str, Any] = {
                        "buyerWalletAddress": existing.buyer_wallet_address,
                        "policyDate": existing.policy_date,
                        "token": existing.token,
                    }
                    policy_update: dict[str, Any] | None = None
                    if reservation_action in ("settle", "release"):
                        policy_filter["reservedUnits"] = {"$gte": existing.amount_units}
                        increments = {"reservedUnits": -existing.amount_units}
                        if reservation_action == "settle":
                            increments["spentUnits"] = existing.amount_units
                        policy_update = {"$inc": increments}
                    if policy_update is not None:
                        updated_policy = await self._wallet_policies.find_one_and_update(
                            policy_filter,
                            policy_update,
                            return_document=ReturnDocument.AFTER,
                            session=active_session,
                        )
                        if updated_policy is None:
                            raise PaymentConflictError(
                                "reserved budget is insufficient"
                            )
                    replaced = await self._payment_intents.replace_one(
                        {
                            "_id": existing_document["_id"],
                            "state": existing.state.value,
                        },
                        _payment_intent_document(normalized_next),
                        session=active_session,
                    )
                    if replaced.modified_count != 1:
                        raise PaymentConflictError(
                            "payment state changed before transition"
                        )
                    await self._events.insert_one(
                        _event_document(event), session=active_session
                    )
                    await self._insert_event_head(
                        purchase_id=normalized_next.purchase_id,
                        event=event,
                        anchored_at=occurred_at,
                        session=active_session,
                    )
                    return normalized_next

                try:
                    return await session.with_transaction(transition_once)
                except DuplicateKeyError as exc:
                    if event_type == EventType.PAYMENT_SETTLED:
                        raise PaymentConflictError(
                            "settlement collides with this purchase's recorded settlement, "
                            "terminal outcome or event sequence"
                        ) from exc
                    # Per-purchase unique indexes are the second guard behind the
                    # payment-intent CAS. A loser must re-read so an identical transition
                    # still returns the committed result and a different one still conflicts.
                    await asyncio.sleep(min(0.001 * (2**attempt), 0.025))
                    continue
        raise PaymentConflictError("payment transition contention exceeded retries")

    async def list_events(self, purchase_id: str) -> list[EvidenceEvent]:
        cursor = self._events.find({"purchaseId": purchase_id}).sort("sequence", ASCENDING)
        return [_event_from_document(document) async for document in cursor]

    async def list_owner_purchase_ids(self, owner_address: str) -> list[str]:
        cursor = self._events.find(
            {
                "type": EventType.REQUESTED.value,
                "actor.id": owner_address.lower(),
            },
            {"purchaseId": 1, "occurredAt": 1},
        ).sort([("occurredAt", DESCENDING), ("purchaseId", DESCENDING)])
        return [str(document["purchaseId"]) async for document in cursor]

    async def get_event_head(self, purchase_id: str) -> EvidenceHead | None:
        cursor = (
            self._heads.find({"purchaseId": purchase_id}).sort("eventCount", DESCENDING).limit(1)
        )
        documents = await cursor.to_list(length=1)
        if not documents:
            return None
        document = documents[0]
        return EvidenceHead(
            purchase_id=str(document["purchaseId"]),
            event_count=int(document["eventCount"]),
            head_event_hash=str(document["headEventHash"]),
        )

    async def store_sensitive_payload(self, payload: SensitivePayload) -> None:
        await self._sensitive.insert_one(_sensitive_document(payload))

    async def get_sensitive_payload(self, payload_id: str) -> SensitivePayload | None:
        document = await self._sensitive.find_one({"payloadId": payload_id})
        return _sensitive_from_document(document) if document else None

    async def put_document(self, document: ImmutableDocument) -> ImmutableDocument:
        """Insert-only: an identical body is idempotent, a different one is a conflict."""
        verify_immutable_document(document)
        existing = await self._documents.find_one({"documentId": document.document_id})
        if existing is not None:
            stored = _immutable_from_document(existing)
            if stored.content_hash != document.content_hash:
                raise EvidenceImmutabilityError(
                    "an immutable document cannot be rewritten once stored"
                )
            return stored
        try:
            await self._documents.insert_one(_immutable_document(document))
        except DuplicateKeyError as exc:
            duplicate = await self._documents.find_one(
                {"documentId": document.document_id}
            )
            if duplicate is None:
                raise
            stored = _immutable_from_document(duplicate)
            if stored.content_hash != document.content_hash:
                raise EvidenceImmutabilityError(
                    "an immutable document cannot be rewritten once stored"
                ) from exc
            return stored
        return document

    async def get_document(self, document_id: str) -> ImmutableDocument | None:
        found = await self._documents.find_one({"documentId": document_id})
        return _immutable_from_document(found) if found else None
