from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal, cast

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError
from pymongo.server_api import ServerApi

from buyer_audit_api.core.errors import (
    DuplicateEventError,
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
    JsonObject,
    SensitivePayload,
    WalletBinding,
)
from buyer_audit_api.core.payment import PaymentIntent, PaymentIntentState, WalletPolicy
from buyer_audit_api.core.seller_execution import SellerExecution, SellerExecutionState


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
        "permit2Nonce": intent.permit2_nonce,
        "state": intent.state.value,
        "claimedAt": intent.claimed_at,
        "decisionAuthorizationHash": intent.decision_authorization_hash,
        "decisionAuthorizationSignature": intent.decision_authorization_signature,
        "authorizedAt": intent.authorized_at,
        "reconciliationReason": intent.reconciliation_reason,
        "transactionHash": intent.transaction_hash,
        "blockNumber": intent.block_number,
        "transferLogIndex": intent.transfer_log_index,
        "settlementVerifiedAt": intent.settlement_verified_at,
        "failureReason": intent.failure_reason,
        "failedAt": intent.failed_at,
    }


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
        permit2_nonce=str(document["permit2Nonce"]),
        state=PaymentIntentState(str(document["state"])),
        claimed_at=cast(datetime, document["claimedAt"]),
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
        transaction_hash=cast(str | None, document.get("transactionHash")),
        block_number=cast(int | None, document.get("blockNumber")),
        transfer_log_index=cast(int | None, document.get("transferLogIndex")),
        settlement_verified_at=cast(
            datetime | None, document.get("settlementVerifiedAt")
        ),
        failure_reason=cast(str | None, document.get("failureReason")),
        failed_at=cast(datetime | None, document.get("failedAt")),
    )


def _seller_execution_document(execution: SellerExecution) -> dict[str, Any]:
    return {
        "purchaseId": execution.purchase_id,
        "quoteId": execution.quote_id,
        "sellerAgentId": execution.seller_agent_id,
        "promptHash": execution.prompt_hash,
        "paymentProofHash": execution.payment_proof_hash,
        "promptPayloadId": execution.prompt_payload_id,
        "state": execution.state.value,
        "claimedAt": execution.claimed_at,
        "authorizationPayloadId": execution.authorization_payload_id,
        "settlementPayloadId": execution.settlement_payload_id,
        "providerAttemptId": execution.provider_attempt_id,
        "providerAttemptToken": execution.provider_attempt_token,
        "resultPayloadId": execution.result_payload_id,
    }


def _seller_execution_from_document(document: dict[str, Any]) -> SellerExecution:
    return SellerExecution(
        purchase_id=str(document["purchaseId"]),
        quote_id=str(document["quoteId"]),
        seller_agent_id=str(document["sellerAgentId"]),
        prompt_hash=str(document["promptHash"]),
        payment_proof_hash=str(document["paymentProofHash"]),
        prompt_payload_id=str(document["promptPayloadId"]),
        state=SellerExecutionState(str(document["state"])),
        claimed_at=cast(datetime, document["claimedAt"]),
        authorization_payload_id=cast(
            str | None, document.get("authorizationPayloadId")
        ),
        settlement_payload_id=cast(str | None, document.get("settlementPayloadId")),
        provider_attempt_id=cast(str | None, document.get("providerAttemptId")),
        provider_attempt_token=cast(str | None, document.get("providerAttemptToken")),
        result_payload_id=cast(str | None, document.get("resultPayloadId")),
    )


def _same_payment_binding(left: PaymentIntent, right: PaymentIntent) -> bool:
    return (
        left.purchase_id == right.purchase_id
        and left.buyer_wallet_address == right.buyer_wallet_address
        and left.policy_date == right.policy_date
        and left.quote_id == right.quote_id
        and left.decision_event_hash == right.decision_event_hash
        and left.amount_units == right.amount_units
        and left.token == right.token
        and left.pay_to == right.pay_to
        and left.permit2_nonce == right.permit2_nonce
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
        self._wallet_policies = self._database["walletPolicies"]
        self._payment_intents = self._database["paymentIntents"]
        self._seller_executions = self._database["sellerExecutions"]

    async def ensure_indexes(self) -> None:
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
            name="unique_quoted_per_purchase",
            partialFilterExpression={"type": EventType.QUOTED.value},
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
            name="unique_payment_intent_per_purchase",
            partialFilterExpression={"type": EventType.PAYMENT_INTENT_CLAIMED.value},
        )
        await self._events.create_index(
            [("purchaseId", ASCENDING)],
            unique=True,
            name="unique_payment_settled_per_purchase",
            partialFilterExpression={"type": EventType.PAYMENT_SETTLED.value},
        )
        await self._events.create_index(
            [("payload.transactionHash", ASCENDING)],
            unique=True,
            name="unique_settlement_transaction_hash",
            partialFilterExpression={
                "type": EventType.PAYMENT_SETTLED.value,
                "payload.transactionHash": {"$type": "string"},
            },
        )
        for event_type, name in (
            (EventType.PAYMENT_AUTHORIZED, "unique_payment_authorized_per_purchase"),
            (
                EventType.PAYMENT_RECONCILIATION_REQUIRED,
                "unique_payment_reconciliation_per_purchase",
            ),
            (EventType.PAYMENT_FAILED, "unique_payment_failed_per_purchase"),
            (EventType.EVIDENCE_ANCHORED, "unique_evidence_anchor_per_purchase"),
            (EventType.REPUTATION_RECORDED, "unique_reputation_per_purchase"),
            (EventType.DELIVERED, "unique_delivery_per_purchase"),
            (EventType.AUDITED, "unique_audit_per_purchase"),
        ):
            await self._events.create_index(
                [("purchaseId", ASCENDING)],
                unique=True,
                name=name,
                partialFilterExpression={"type": event_type.value},
            )
        await self._heads.create_index(
            [("purchaseId", ASCENDING), ("eventCount", ASCENDING)], unique=True
        )
        await self._heads.create_index([("purchaseId", ASCENDING), ("eventCount", DESCENDING)])
        await self._sensitive.create_index([("payloadId", ASCENDING)], unique=True)
        await self._sensitive.create_index([("purchaseId", ASCENDING), ("kind", ASCENDING)])
        await self._wallet_policies.create_index(
            [("buyerWalletAddress", ASCENDING), ("policyDate", ASCENDING)],
            unique=True,
            name="unique_wallet_policy_day",
        )
        await self._payment_intents.create_index(
            [("purchaseId", ASCENDING)],
            unique=True,
            name="unique_payment_intent_document",
        )
        await self._seller_executions.create_index(
            [("purchaseId", ASCENDING)],
            unique=True,
            name="unique_seller_execution_purchase",
        )

    async def close(self) -> None:
        await self._client.close()

    async def issue_siwe_nonce(
        self,
        *,
        owner_address: str,
        nonce_hash: str,
        expires_at: datetime,
    ) -> None:
        await self._users.update_one(
            {"ownerAddress": owner_address.lower()},
            {
                "$set": {
                    "ownerAddress": owner_address.lower(),
                    "siweNonceConsumedAt": None,
                    "siweNonceExpiresAt": expires_at,
                    "siweNonceHash": nonce_hash,
                }
            },
            upsert=True,
        )

    async def consume_siwe_nonce(
        self,
        *,
        owner_address: str,
        nonce_hash: str,
        consumed_at: datetime,
    ) -> bool:
        result = await self._users.find_one_and_update(
            {
                "ownerAddress": owner_address.lower(),
                "siweNonceConsumedAt": None,
                "siweNonceExpiresAt": {"$gt": consumed_at},
                "siweNonceHash": nonce_hash,
            },
            {"$set": {"siweNonceConsumedAt": consumed_at}},
            return_document=ReturnDocument.AFTER,
        )
        return result is not None

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
        self, *, buyer_wallet_address: str, policy_date: str
    ) -> WalletPolicy | None:
        document = await self._wallet_policies.find_one(
            {
                "buyerWalletAddress": buyer_wallet_address.lower(),
                "policyDate": policy_date,
            }
        )
        return _wallet_policy_from_document(document) if document is not None else None

    async def get_latest_wallet_policy(
        self, buyer_wallet_address: str
    ) -> WalletPolicy | None:
        document = await self._wallet_policies.find_one(
            {"buyerWalletAddress": buyer_wallet_address.lower()},
            sort=[("policyDate", DESCENDING)],
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
                            not _same_payment_binding(existing, normalized_intent)
                            or len(claim_events) != 1
                            or claim_events[0].payload.get("quoteId") != existing.quote_id
                            or claim_events[0].payload.get("decisionEventHash")
                            != existing.decision_event_hash
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
        reservation_action: Literal["hold", "settle", "release"],
    ) -> PaymentIntent:
        normalized_next = replace(
            next_intent,
            buyer_wallet_address=next_intent.buyer_wallet_address.lower(),
            token=next_intent.token.lower(),
            pay_to=next_intent.pay_to.lower(),
            transaction_hash=(
                next_intent.transaction_hash.lower()
                if next_intent.transaction_hash is not None
                else None
            ),
        )
        immutable_fields = (
            "buyer_wallet_address",
            "policy_date",
            "quote_id",
            "decision_event_hash",
            "amount_units",
            "token",
            "pay_to",
            "permit2_nonce",
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
                    same_state_submission_binding = (
                        event_type == EventType.PAYMENT_SUBMISSION_IDENTIFIED
                        and existing.state == PaymentIntentState.RECONCILIATION_REQUIRED
                        and normalized_next.state
                        == PaymentIntentState.RECONCILIATION_REQUIRED
                        and existing.transaction_hash is None
                        and normalized_next.transaction_hash is not None
                    )
                    if (
                        existing.state == normalized_next.state
                        and not same_state_submission_binding
                    ):
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
                            "settlement transaction is already bound"
                        ) from exc
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

    async def claim_seller_execution(
        self, execution: SellerExecution
    ) -> SellerExecution:
        try:
            await self._seller_executions.insert_one(
                _seller_execution_document(execution)
            )
            return execution
        except DuplicateKeyError:
            existing = await self._seller_executions.find_one(
                {"purchaseId": execution.purchase_id}
            )
            if existing is None:
                raise
            return _seller_execution_from_document(existing)

    async def transition_seller_execution(
        self,
        *,
        purchase_id: str,
        expected_state: SellerExecutionState,
        next_execution: SellerExecution,
    ) -> SellerExecution:
        document = await self._seller_executions.find_one_and_update(
            {"purchaseId": purchase_id, "state": expected_state.value},
            {"$set": _seller_execution_document(next_execution)},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            document = await self._seller_executions.find_one(
                {"purchaseId": purchase_id}
            )
        if document is None:
            raise ValueError("seller execution is missing")
        return _seller_execution_from_document(document)

    async def get_seller_execution(self, purchase_id: str) -> SellerExecution | None:
        document = await self._seller_executions.find_one({"purchaseId": purchase_id})
        return _seller_execution_from_document(document) if document else None
