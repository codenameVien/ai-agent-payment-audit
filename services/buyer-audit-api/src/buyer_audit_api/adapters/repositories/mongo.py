from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
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
    PaymentEvidenceError,
    PaymentPolicyError,
)
from buyer_audit_api.core.events import create_event, verify_event_chain
from buyer_audit_api.core.models import (
    TERMINAL_PAYMENT_EVENT_TYPES,
    EventType,
    EvidenceEvent,
    EvidenceHead,
    EvidenceSource,
    JsonObject,
    SensitivePayload,
    TransactionRef,
    WalletBinding,
    transaction_ref_from_payload,
)
from buyer_audit_api.core.payment import (
    ActualTransfer,
    ConfirmedOutflow,
    PaymentIntent,
    PaymentIntentState,
    ReconciliationVerifierOutcome,
    ReservationAction,
    WalletPolicy,
)
from buyer_audit_api.core.reputation import (
    ConfirmedFeedbackProof,
    DecisionKind,
    FreshnessStatus,
    OutboxStatus,
    PublishIdentity,
    RawFeedbackEvent,
    ReputationDecision,
    ReputationPublishJob,
    ReputationQueryScope,
    ReputationReason,
    ReputationSnapshot,
    assert_prepared_commitment,
    assert_proof_matches_job,
    proof_from_payload,
    publication_conflict_payload,
    reputation_recorded_payload,
    submission_identity,
)
from buyer_audit_api.core.seller_execution import SellerExecution, SellerExecutionState

# M2: the per-purchase singleton unique indexes this packet introduces. One declaration
# feeds both the read-only collision preflight and index creation, so coverage cannot drift.
_PHASE6_SINGLETON_INDEXES: tuple[tuple[EventType, str], ...] = (
    (EventType.PAYMENT_MISMATCH_CONFIRMED, "unique_payment_mismatch_per_purchase"),
    (
        EventType.PAYMENT_RECONCILED_NO_TRANSFER,
        "unique_payment_no_transfer_per_purchase",
    ),
    (EventType.REPUTATION_DECIDED, "unique_reputation_decision_per_purchase"),
)

# Singleton indexes that already existed before this packet; unchanged behaviour.
_EXISTING_SINGLETON_INDEXES: tuple[tuple[EventType, str], ...] = (
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
)


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
        "transferMethod": intent.transfer_method,
        "authorizationNonce": intent.authorization_nonce,
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
        "reconciliation": {
            "attemptCount": intent.reconciliation_attempt_count,
            "firstCheckedAt": intent.reconciliation_first_checked_at,
            "lastCheckedAt": intent.reconciliation_last_checked_at,
            "lastOutcome": (
                intent.reconciliation_last_outcome.value
                if intent.reconciliation_last_outcome is not None
                else None
            ),
        },
        "actualTransfer": (
            intent.actual_transfer.to_payload() if intent.actual_transfer is not None else None
        ),
        "mismatchedFields": list(intent.mismatched_fields),
        "terminalOutcomeKey": intent.terminal_outcome_key,
        "terminalProofRef": intent.terminal_proof_ref,
        "terminalProofFingerprint": intent.terminal_proof_fingerprint,
        "terminalEvidenceSource": (
            intent.terminal_evidence_source.value
            if intent.terminal_evidence_source is not None
            else None
        ),
        "localTransactionId": intent.local_transaction_id,
        "noTransferReasonCode": intent.no_transfer_reason_code,
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
        permit2_nonce=cast(str | None, document.get("permit2Nonce")),
        state=PaymentIntentState(str(document["state"])),
        claimed_at=cast(datetime, document["claimedAt"]),
        transfer_method=cast(
            Literal["permit2", "eip3009"], document.get("transferMethod", "permit2")
        ),
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
        transaction_hash=cast(str | None, document.get("transactionHash")),
        block_number=cast(int | None, document.get("blockNumber")),
        transfer_log_index=cast(int | None, document.get("transferLogIndex")),
        settlement_verified_at=cast(
            datetime | None, document.get("settlementVerifiedAt")
        ),
        failure_reason=cast(str | None, document.get("failureReason")),
        failed_at=cast(datetime | None, document.get("failedAt")),
        reconciliation_attempt_count=int(_reconciliation(document).get("attemptCount", 0) or 0),
        reconciliation_first_checked_at=cast(
            datetime | None, _reconciliation(document).get("firstCheckedAt")
        ),
        reconciliation_last_checked_at=cast(
            datetime | None, _reconciliation(document).get("lastCheckedAt")
        ),
        reconciliation_last_outcome=(
            ReconciliationVerifierOutcome(str(_reconciliation(document)["lastOutcome"]))
            if _reconciliation(document).get("lastOutcome") is not None
            else None
        ),
        actual_transfer=_actual_transfer_from_document(document.get("actualTransfer")),
        mismatched_fields=tuple(
            str(item) for item in (document.get("mismatchedFields") or [])
        ),
        terminal_outcome_key=cast(str | None, document.get("terminalOutcomeKey")),
        terminal_proof_ref=cast(str | None, document.get("terminalProofRef")),
        terminal_proof_fingerprint=cast(
            str | None, document.get("terminalProofFingerprint")
        ),
        terminal_evidence_source=(
            EvidenceSource(str(document["terminalEvidenceSource"]))
            if document.get("terminalEvidenceSource") is not None
            else None
        ),
        local_transaction_id=cast(str | None, document.get("localTransactionId")),
        no_transfer_reason_code=cast(str | None, document.get("noTransferReasonCode")),
    )


def _reconciliation(document: dict[str, Any]) -> dict[str, Any]:
    raw = document.get("reconciliation")
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _actual_transfer_from_document(value: object) -> ActualTransfer | None:
    if not isinstance(value, dict):
        return None
    return ActualTransfer(
        amount_units=int(value["amountUnits"]),
        token=str(value["token"]),
        from_address=str(value["from"]),
        to_address=str(value["to"]),
    )


def _confirmed_outflow_document(outflow: ConfirmedOutflow) -> dict[str, Any]:
    return {
        "amountUnits": outflow.amount_units,
        "buyerWallet": outflow.buyer_wallet,
        "policyDate": outflow.policy_date,
        "proofRef": outflow.proof_ref,
        "purchaseId": outflow.purchase_id,
        "recipient": outflow.recipient,
        "terminalOutcomeKey": outflow.terminal_outcome_key,
        "token": outflow.token,
        "transactionRef": outflow.transaction_ref.to_payload(),
    }


def _stored_datetime(value: object) -> datetime:
    """BSON round-trips datetimes; anything else in a stored field is malformed."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    raise EvidenceIntegrityError("stored timestamp is malformed")


def _confirmed_outflow_from_document(document: dict[str, Any]) -> ConfirmedOutflow:
    return ConfirmedOutflow(
        terminal_outcome_key=str(document["terminalOutcomeKey"]),
        purchase_id=str(document["purchaseId"]),
        buyer_wallet=str(document["buyerWallet"]),
        policy_date=str(document["policyDate"]),
        token=str(document["token"]),
        amount_units=int(document["amountUnits"]),
        recipient=str(document["recipient"]),
        transaction_ref=transaction_ref_from_payload(document["transactionRef"]),
        proof_ref=str(document["proofRef"]),
    )


_CLAIMABLE_STATUSES: tuple[OutboxStatus, ...] = (
    OutboxStatus.PENDING,
    OutboxStatus.LEASED,
    OutboxStatus.PREPARED,
    OutboxStatus.SUBMITTED_UNKNOWN,
)


def _outbox_document(job: ReputationPublishJob) -> dict[str, Any]:
    return {
        "attemptCount": job.attempt_count,
        "blockNumber": job.block_number,
        "chainId": job.identity.chain_id,
        "createdAt": job.created_at,
        "decision": job.decision.to_payload(),
        "evidenceSource": (
            None if job.evidence_source is None else job.evidence_source.value
        ),
        "clientAddress": job.client_address,
        "confirmedProof": (
            None if job.confirmed_proof is None else job.confirmed_proof.to_payload()
        ),
        "feedbackHash": job.feedback_hash,
        "feedbackUri": job.feedback_uri,
        "jobId": job.job_id,
        "leaseExpiresAt": job.lease_expires_at,
        "logIndex": job.log_index,
        "payloadFingerprint": job.payload_fingerprint,
        "publishIdentity": job.identity.to_payload(),
        "publishIdentityHash": job.identity_hash,
        "purchaseId": job.identity.purchase_id,
        "receiptProofRef": job.receipt_proof_ref,
        "registryAddress": job.identity.registry_address,
        "sellerAgentId": job.identity.seller_agent_id,
        "status": job.status.value,
        "transactionRef": (
            None if job.transaction_ref is None else job.transaction_ref.to_payload()
        ),
        "updatedAt": job.updated_at,
        "workerId": job.worker_id,
    }


def _outbox_from_document(document: dict[str, Any]) -> ReputationPublishJob:
    identity = PublishIdentity.from_payload(document["publishIdentity"])
    decision_payload = dict(document["decision"])
    raw_value = decision_payload.get("value")
    decision = ReputationDecision(
        kind=DecisionKind(str(decision_payload["decision"])),
        value=None if raw_value is None else int(raw_value),
        reason_codes=tuple(
            ReputationReason(str(item)) for item in decision_payload["reasonCodes"]
        ),
        audit_bundle_hash=str(decision_payload["auditBundleHash"]),
        ruleset_version=str(decision_payload["rulesetVersion"]),
        seller_agent_id=str(decision_payload["sellerAgentId"]),
        erc8004_agent_id=str(decision_payload["erc8004AgentId"]),
    )
    raw_reference = document.get("transactionRef")
    raw_source = document.get("evidenceSource")
    raw_proof = document.get("confirmedProof")
    return ReputationPublishJob(
        job_id=str(document["jobId"]),
        identity=identity,
        identity_hash=str(document["publishIdentityHash"]),
        payload_fingerprint=str(document["payloadFingerprint"]),
        decision=decision,
        status=OutboxStatus(str(document["status"])),
        created_at=_stored_datetime(document["createdAt"]),
        updated_at=_stored_datetime(document["updatedAt"]),
        attempt_count=int(document.get("attemptCount", 0)),
        worker_id=cast(str | None, document.get("workerId")),
        lease_expires_at=cast(datetime | None, document.get("leaseExpiresAt")),
        feedback_hash=cast(str | None, document.get("feedbackHash")),
        client_address=cast(str | None, document.get("clientAddress")),
        feedback_uri=cast(str | None, document.get("feedbackUri")),
        transaction_ref=(
            None if raw_reference is None else transaction_ref_from_payload(raw_reference)
        ),
        receipt_proof_ref=cast(str | None, document.get("receiptProofRef")),
        block_number=cast(int | None, document.get("blockNumber")),
        log_index=cast(int | None, document.get("logIndex")),
        evidence_source=(
            None if raw_source is None else EvidenceSource(str(raw_source))
        ),
        confirmed_proof=(
            None if raw_proof is None else proof_from_payload(raw_proof)
        ),
    )


def _snapshot_document(snapshot: ReputationSnapshot) -> dict[str, Any]:
    payload = snapshot.to_payload()
    return {
        **payload,
        "queriedAt": snapshot.queried_at,
        "snapshotHash": snapshot.snapshot_hash,
    }


def _snapshot_from_document(document: dict[str, Any]) -> ReputationSnapshot:
    scope_payload = dict(document["scope"])
    scope = ReputationQueryScope(
        chain_id=int(scope_payload["chainId"]),
        registry_address=str(scope_payload["registryAddress"]),
        erc8004_agent_id=str(scope_payload["erc8004AgentId"]),
        trusted_clients=tuple(str(item) for item in scope_payload["trustedClients"]),
        from_block=int(scope_payload["fromBlock"]),
        to_block=int(scope_payload["toBlock"]),
        tag1=str(scope_payload["tag1"]),
        tag2=str(scope_payload["tag2"]),
    )
    freshness = dict(document["freshness"])
    return ReputationSnapshot(
        snapshot_id=str(document["snapshotId"]),
        seller_agent_id=str(document["sellerAgentId"]),
        scope=scope,
        queried_at=_stored_datetime(document["queriedAt"]),
        raw_values=tuple(
            RawFeedbackEvent(
                value=int(item["value"]),
                value_decimals=int(item["valueDecimals"]),
                client_address=str(item["clientAddress"]),
                block_number=int(item["blockNumber"]),
                log_index=int(item["logIndex"]),
                transaction_ref=transaction_ref_from_payload(item["transactionRef"]),
                tag1=str(item["tag1"]),
                tag2=str(item["tag2"]),
            )
            for item in document["rawValues"]
        ),
        derived_score=float(document["derivedScore"]),
        freshness_status=FreshnessStatus(str(freshness["status"])),
        evidence_source=EvidenceSource(str(document["evidenceSource"])),
        aggregation_method=str(document["aggregationMethod"]),
        freshness_age_seconds=cast(int | None, freshness.get("ageSeconds")),
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
        and left.permit2_nonce == right.permit2_nonce
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
        self._wallet_policies = self._database["walletPolicies"]
        self._payment_intents = self._database["paymentIntents"]
        self._seller_executions = self._database["sellerExecutions"]
        self._confirmed_outflows = self._database["confirmedOutflows"]
        self._reputation_outbox = self._database["reputationOutbox"]
        self._reputation_snapshots = self._database["reputationSnapshots"]

    async def phase6_index_collision_report(self) -> list[dict[str, Any]]:
        """Read-only duplicate report covering every unique index this packet introduces.

        It aggregates existing documents that already carry the new fields and reports
        every key that would violate a new unique index, including the two per-purchase
        terminal singletons. It never writes, backfills or deletes.
        """
        specs: list[tuple[str, Any, dict[str, Any], list[str]]] = [
            (
                "unique_phase6_terminal_outcome",
                self._events,
                {
                    "type": {"$in": [item.value for item in TERMINAL_PAYMENT_EVENT_TYPES]},
                    "payload.terminalOutcomeKey": {"$type": "string"},
                },
                ["payload.terminalOutcomeKey"],
            ),
            (
                "unique_phase6_reconciliation_attempt",
                self._events,
                {
                    "type": EventType.PAYMENT_RECONCILIATION_CHECKED.value,
                    "payload.attemptNumber": {"$type": "number"},
                },
                ["purchaseId", "type", "payload.attemptNumber"],
            ),
            (
                "unique_phase6_rejected_attempt",
                self._events,
                {
                    "type": EventType.PAYMENT_ATTEMPT_REJECTED.value,
                    "payload.attemptId": {"$type": "string"},
                },
                ["purchaseId", "type", "payload.attemptId"],
            ),
            (
                "unique_confirmed_outflow_terminal",
                self._confirmed_outflows,
                {"terminalOutcomeKey": {"$type": "string"}},
                ["terminalOutcomeKey"],
            ),
            (
                "unique_confirmed_outflow_transaction",
                self._confirmed_outflows,
                {
                    "transactionRef.kind": "EVM",
                    "transactionRef.hash": {"$type": "string"},
                },
                ["transactionRef.hash"],
            ),
            (
                "unique_confirmed_outflow_transaction_local",
                self._confirmed_outflows,
                {
                    "transactionRef.kind": "LOCAL",
                    "transactionRef.id": {"$type": "string"},
                },
                ["transactionRef.runId", "transactionRef.id"],
            ),
            (
                "unique_reputation_publish_identity",
                self._reputation_outbox,
                {"publishIdentityHash": {"$type": "string"}},
                ["publishIdentityHash"],
            ),
            (
                "unique_reputation_evm_transaction",
                self._reputation_outbox,
                {
                    "transactionRef.kind": "EVM",
                    "transactionRef.hash": {"$type": "string"},
                },
                ["transactionRef.hash"],
            ),
            (
                "unique_reputation_local_transaction",
                self._reputation_outbox,
                {
                    "transactionRef.kind": "LOCAL",
                    "transactionRef.id": {"$type": "string"},
                },
                ["transactionRef.runId", "transactionRef.id"],
            ),
            (
                "unique_reputation_snapshot_query",
                self._reputation_snapshots,
                {"queryFingerprint": {"$type": "string"}},
                ["queryFingerprint", "scope.toBlock"],
            ),
            (
                "unique_reputation_job_id",
                self._reputation_outbox,
                {"jobId": {"$type": "string"}},
                ["jobId"],
            ),
            (
                "unique_reputation_snapshot",
                self._reputation_snapshots,
                {"snapshotId": {"$type": "string"}},
                ["snapshotId"],
            ),
        ]
        specs.extend(
            (name, self._events, {"type": event_type.value}, ["purchaseId"])
            for event_type, name in _PHASE6_SINGLETON_INDEXES
        )
        report: list[dict[str, Any]] = []
        for name, collection, match, keys in specs:
            group_id = {
                key.replace(".", "_"): f"${key}" for key in keys
            }
            cursor = await collection.aggregate(
                [
                    {"$match": match},
                    {"$group": {"_id": group_id, "count": {"$sum": 1}}},
                    {"$match": {"count": {"$gt": 1}}},
                    {"$sort": {"count": DESCENDING}},
                    {"$limit": 32},
                ]
            )
            async for document in cursor:
                report.append(
                    {
                        "index": name,
                        "key": document["_id"],
                        "count": int(document["count"]),
                    }
                )
        return report

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
        for event_type, name in _EXISTING_SINGLETON_INDEXES:
            await self._events.create_index(
                [("purchaseId", ASCENDING)],
                unique=True,
                name=name,
                partialFilterExpression={"type": event_type.value},
            )
        # M2 / design 18.15.7: the read-only preflight runs before creating ANY unique index
        # this packet introduces, including the two per-purchase terminal singletons below.
        # Historical rows are never modified, backfilled or deleted to make them fit.
        collisions = await self.phase6_index_collision_report()
        if collisions:
            raise EvidenceIntegrityError(
                "Phase 6 unique index preflight found existing collisions: "
                + "; ".join(
                    f"{item['index']} {item['key']} x{item['count']}" for item in collisions
                )
            )
        for event_type, name in _PHASE6_SINGLETON_INDEXES:
            await self._events.create_index(
                [("purchaseId", ASCENDING)],
                unique=True,
                name=name,
                partialFilterExpression={"type": event_type.value},
            )
        await self._events.create_index(
            [("payload.terminalOutcomeKey", ASCENDING)],
            unique=True,
            name="unique_phase6_terminal_outcome",
            partialFilterExpression={
                "type": {"$in": [item.value for item in TERMINAL_PAYMENT_EVENT_TYPES]},
                "payload.terminalOutcomeKey": {"$type": "string"},
            },
        )
        await self._events.create_index(
            [
                ("purchaseId", ASCENDING),
                ("type", ASCENDING),
                ("payload.attemptNumber", ASCENDING),
            ],
            unique=True,
            name="unique_phase6_reconciliation_attempt",
            partialFilterExpression={
                "type": EventType.PAYMENT_RECONCILIATION_CHECKED.value,
                "payload.attemptNumber": {"$type": "number"},
            },
        )
        await self._events.create_index(
            [
                ("purchaseId", ASCENDING),
                ("type", ASCENDING),
                ("payload.attemptId", ASCENDING),
            ],
            unique=True,
            name="unique_phase6_rejected_attempt",
            partialFilterExpression={
                "type": EventType.PAYMENT_ATTEMPT_REJECTED.value,
                "payload.attemptId": {"$type": "string"},
            },
        )
        await self._confirmed_outflows.create_index(
            [("terminalOutcomeKey", ASCENDING)],
            unique=True,
            name="unique_confirmed_outflow_terminal",
        )
        await self._confirmed_outflows.create_index(
            [("transactionRef.hash", ASCENDING)],
            unique=True,
            name="unique_confirmed_outflow_transaction",
            partialFilterExpression={
                "transactionRef.kind": "EVM",
                "transactionRef.hash": {"$type": "string"},
            },
        )
        await self._confirmed_outflows.create_index(
            [("transactionRef.runId", ASCENDING), ("transactionRef.id", ASCENDING)],
            unique=True,
            name="unique_confirmed_outflow_transaction_local",
            partialFilterExpression={
                "transactionRef.kind": "LOCAL",
                "transactionRef.id": {"$type": "string"},
            },
        )
        await self._confirmed_outflows.create_index(
            [("purchaseId", ASCENDING)],
            name="confirmed_outflow_purchase",
        )
        await self._reputation_outbox.create_index(
            [("publishIdentityHash", ASCENDING)],
            unique=True,
            name="unique_reputation_publish_identity",
        )
        await self._reputation_outbox.create_index(
            [("jobId", ASCENDING)],
            unique=True,
            name="unique_reputation_job_id",
        )
        await self._reputation_outbox.create_index(
            [("transactionRef.hash", ASCENDING)],
            unique=True,
            name="unique_reputation_evm_transaction",
            partialFilterExpression={
                "transactionRef.kind": "EVM",
                "transactionRef.hash": {"$type": "string"},
            },
        )
        await self._reputation_outbox.create_index(
            [("transactionRef.runId", ASCENDING), ("transactionRef.id", ASCENDING)],
            unique=True,
            name="unique_reputation_local_transaction",
            partialFilterExpression={
                "transactionRef.kind": "LOCAL",
                "transactionRef.id": {"$type": "string"},
            },
        )
        await self._reputation_outbox.create_index(
            [("status", ASCENDING), ("leaseExpiresAt", ASCENDING)],
            name="reputation_publish_lease",
        )
        await self._reputation_snapshots.create_index(
            [("snapshotId", ASCENDING)],
            unique=True,
            name="unique_reputation_snapshot",
        )
        await self._reputation_snapshots.create_index(
            [("queryFingerprint", ASCENDING), ("scope.toBlock", ASCENDING)],
            unique=True,
            name="unique_reputation_snapshot_query",
        )
        await self._reputation_snapshots.create_index(
            [("sellerAgentId", ASCENDING), ("queriedAt", DESCENDING)],
            name="reputation_snapshot_agent",
        )
        await self._heads.create_index(
            [("purchaseId", ASCENDING), ("eventCount", ASCENDING)], unique=True
        )
        await self._heads.create_index([("purchaseId", ASCENDING), ("eventCount", DESCENDING)])
        await self._sensitive.create_index([("payloadId", ASCENDING)], unique=True)
        await self._sensitive.create_index([("purchaseId", ASCENDING), ("kind", ASCENDING)])
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
        confirmed_outflow: ConfirmedOutflow | None = None,
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
                    same_state_submission_binding = (
                        event_type == EventType.PAYMENT_SUBMISSION_IDENTIFIED
                        and existing.state == PaymentIntentState.RECONCILIATION_REQUIRED
                        and normalized_next.state
                        == PaymentIntentState.RECONCILIATION_REQUIRED
                        and existing.transaction_hash is None
                        and normalized_next.transaction_hash is not None
                    )
                    same_state_reconciliation_check = (
                        event_type == EventType.PAYMENT_RECONCILIATION_CHECKED
                        and existing.state == PaymentIntentState.RECONCILIATION_REQUIRED
                        and normalized_next.state
                        == PaymentIntentState.RECONCILIATION_REQUIRED
                        and normalized_next.reconciliation_attempt_count
                        == existing.reconciliation_attempt_count + 1
                    )
                    if existing.state == normalized_next.state and not (
                        same_state_submission_binding or same_state_reconciliation_check
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
                        "token": existing.token,
                    }
                    policy_update: dict[str, Any] | None = None
                    if reservation_action in ("settle", "release", "replace_with_actual"):
                        policy_filter["reservedUnits"] = {"$gte": existing.amount_units}
                        increments = {"reservedUnits": -existing.amount_units}
                        if reservation_action == "settle":
                            increments["spentUnits"] = existing.amount_units
                        elif reservation_action == "replace_with_actual":
                            if (
                                confirmed_outflow is None
                                or confirmed_outflow.token != existing.token
                            ):
                                raise PaymentConflictError(
                                    "replace_with_actual requires a same-token confirmed outflow"
                                )
                            increments["spentUnits"] = confirmed_outflow.amount_units
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
                            "reconciliation.attemptCount": (
                                existing.reconciliation_attempt_count
                                if isinstance(existing_document.get("reconciliation"), dict)
                                else {"$exists": False}
                            ),
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
                    if confirmed_outflow is not None:
                        await self._confirmed_outflows.insert_one(
                            _confirmed_outflow_document(confirmed_outflow),
                            session=active_session,
                        )
                    return normalized_next

                try:
                    return await session.with_transaction(transition_once)
                except DuplicateKeyError as exc:
                    index_name = str((exc.details or {}).get("errmsg", ""))
                    if event_type == EventType.PAYMENT_SETTLED:
                        raise PaymentConflictError(
                            "settlement transaction is already bound"
                        ) from exc
                    if (
                        "unique_confirmed_outflow_transaction" in index_name
                        and confirmed_outflow is not None
                        and await self._transaction_ref_belongs_elsewhere(confirmed_outflow)
                    ):
                        raise PaymentConflictError(
                            "confirmed outflow transaction is already recorded"
                        ) from exc
                    # Same-purchase Phase 6 unique indexes are the second guard behind the
                    # payment-intent CAS. A loser must re-read so an identical proof still
                    # returns the committed result and a different proof still conflicts.
                    await asyncio.sleep(min(0.001 * (2**attempt), 0.025))
                    continue
        raise PaymentConflictError("payment transition contention exceeded retries")

    async def _transaction_ref_belongs_elsewhere(self, outflow: ConfirmedOutflow) -> bool:
        """True when another purchase already recorded this exact transaction reference."""
        reference = outflow.transaction_ref
        query: dict[str, Any] = (
            {"transactionRef.kind": "EVM", "transactionRef.hash": reference.hash}
            if reference.kind == "EVM"
            else {
                "transactionRef.kind": "LOCAL",
                "transactionRef.runId": reference.run_id,
                "transactionRef.id": reference.id,
            }
        )
        document = await self._confirmed_outflows.find_one(query, {"purchaseId": 1})
        return document is not None and str(document["purchaseId"]) != outflow.purchase_id

    async def list_confirmed_outflows(self, purchase_id: str) -> list[ConfirmedOutflow]:
        cursor = self._confirmed_outflows.find({"purchaseId": purchase_id}).sort(
            "terminalOutcomeKey", ASCENDING
        )
        return [_confirmed_outflow_from_document(document) async for document in cursor]

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

    # ---- Reputation outbox -------------------------------------------------------

    async def get_job(self, job_id: str) -> ReputationPublishJob | None:
        document = await self._reputation_outbox.find_one({"jobId": job_id})
        return None if document is None else _outbox_from_document(document)

    async def get_by_identity(self, identity_hash: str) -> ReputationPublishJob | None:
        document = await self._reputation_outbox.find_one(
            {"publishIdentityHash": identity_hash}
        )
        return None if document is None else _outbox_from_document(document)

    async def claim_job(
        self, *, worker_id: str, lease_until: datetime, now: datetime
    ) -> ReputationPublishJob | None:
        """Atomic lease acquisition: the CAS is the claim, not an in-process lock."""
        document = await self._reputation_outbox.find_one_and_update(
            {
                "status": {"$in": [item.value for item in _CLAIMABLE_STATUSES]},
                "$or": [
                    {"leaseExpiresAt": None},
                    {"leaseExpiresAt": {"$lte": now}},
                ],
            },
            [
                {
                    "$set": {
                        "status": {
                            "$cond": [
                                {"$eq": ["$status", OutboxStatus.PENDING.value]},
                                OutboxStatus.LEASED.value,
                                "$status",
                            ]
                        },
                        "workerId": worker_id,
                        "leaseExpiresAt": lease_until,
                        "attemptCount": {"$add": ["$attemptCount", 1]},
                        "updatedAt": now,
                    }
                }
            ],
            sort=[("createdAt", ASCENDING), ("jobId", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return None if document is None else _outbox_from_document(document)

    async def _transition_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        payload_fingerprint: str,
        allowed: frozenset[OutboxStatus],
        now: datetime,
        updates: dict[str, Any],
        transaction_ref: TransactionRef | None,
    ) -> ReputationPublishJob:
        """One compare-and-set over status, lease owner and immutable fingerprint."""
        existing = await self._reputation_outbox.find_one({"jobId": job_id})
        if existing is None:
            raise PaymentEvidenceError("reputation job not found")
        job = _outbox_from_document(existing)
        if job.payload_fingerprint != payload_fingerprint:
            raise PaymentConflictError("reputation job has a different payload fingerprint")
        if job.status not in allowed:
            raise PaymentConflictError(f"reputation job cannot move from {job.status.value}")
        if job.worker_id != worker_id or not job.lease_held_at(now):
            raise PaymentConflictError("reputation job lease is not held by this worker")
        if transaction_ref is not None and job.transaction_ref is not None:
            if submission_identity(job.transaction_ref) != submission_identity(
                transaction_ref
            ):
                raise PaymentConflictError(
                    "reputation job is already bound to another submitted transaction"
                )
        try:
            document = await self._reputation_outbox.find_one_and_update(
                {
                    "jobId": job_id,
                    "status": job.status.value,
                    "workerId": worker_id,
                    "payloadFingerprint": payload_fingerprint,
                },
                {"$set": {**updates, "updatedAt": now}},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise PaymentConflictError(
                "reputation transaction reference is already recorded"
            ) from exc
        if document is None:
            raise PaymentConflictError("reputation job changed before the transition")
        return _outbox_from_document(document)

    async def mark_prepared(
        self,
        *,
        job_id: str,
        worker_id: str,
        payload_fingerprint: str,
        transaction_ref: TransactionRef | None,
        feedback_hash: str,
        client_address: str,
        feedback_uri: str,
        now: datetime,
    ) -> ReputationPublishJob:
        existing = await self._reputation_outbox.find_one({"jobId": job_id})
        if existing is not None:
            assert_prepared_commitment(
                job=_outbox_from_document(existing),
                feedback_hash=feedback_hash,
                client_address=client_address,
                feedback_uri=feedback_uri,
            )
        return await self._transition_job(
            job_id=job_id,
            worker_id=worker_id,
            payload_fingerprint=payload_fingerprint,
            allowed=frozenset({OutboxStatus.LEASED, OutboxStatus.PREPARED}),
            now=now,
            updates={
                "status": OutboxStatus.PREPARED.value,
                "feedbackHash": feedback_hash,
                "clientAddress": client_address,
                "feedbackUri": feedback_uri,
                **(
                    {}
                    if transaction_ref is None
                    else {"transactionRef": transaction_ref.to_payload()}
                ),
            },
            transaction_ref=transaction_ref,
        )

    async def mark_submitted_unknown(
        self,
        *,
        job_id: str,
        worker_id: str,
        payload_fingerprint: str,
        transaction_ref: TransactionRef | None,
        reason: str,
        now: datetime,
    ) -> ReputationPublishJob:
        return await self._transition_job(
            job_id=job_id,
            worker_id=worker_id,
            payload_fingerprint=payload_fingerprint,
            allowed=frozenset(
                {OutboxStatus.PREPARED, OutboxStatus.SUBMITTED_UNKNOWN}
            ),
            now=now,
            updates={
                "status": OutboxStatus.SUBMITTED_UNKNOWN.value,
                "unknownReason": reason,
                **(
                    {}
                    if transaction_ref is None
                    else {"transactionRef": transaction_ref.to_payload()}
                ),
            },
            transaction_ref=transaction_ref,
        )

    async def mark_confirmed(
        self,
        *,
        job_id: str,
        worker_id: str,
        payload_fingerprint: str,
        proof: ConfirmedFeedbackProof,
        now: datetime,
    ) -> tuple[EvidenceEvent, ReputationPublishJob]:
        """One Mongo transaction: `REPUTATION_RECORDED` append plus `CONFIRMED` job.

        Either the purchase chain carries the publication and the job is confirmed, or
        neither happened. There is no ordering in which one survives without the other.
        """
        existing = await self._reputation_outbox.find_one({"jobId": job_id})
        if existing is None:
            raise PaymentEvidenceError("reputation job not found")
        job = _outbox_from_document(existing)
        if job.payload_fingerprint != payload_fingerprint:
            raise PaymentConflictError("reputation job has a different payload fingerprint")
        if job.status is OutboxStatus.CONFIRMED:
            stored = job.confirmed_proof
            if stored is not None and stored.proof_hash == proof.proof_hash:
                return await self._recorded_event(job), job
            raise PaymentConflictError("reputation job has a different confirmation")
        if job.is_terminal:
            raise PaymentConflictError(f"reputation job is already {job.status.value}")
        if job.worker_id != worker_id or not job.lease_held_at(now):
            raise PaymentConflictError("reputation job lease is not held by this worker")
        assert_proof_matches_job(job=job, proof=proof)
        purchase_id = job.identity.purchase_id
        payload = reputation_recorded_payload(job=job, proof=proof)
        updates: dict[str, Any] = {
            "status": OutboxStatus.CONFIRMED.value,
            "transactionRef": proof.transaction_ref.to_payload(),
            "receiptProofRef": proof.receipt_proof_ref,
            "feedbackHash": proof.feedback_hash,
            "clientAddress": proof.client_address,
            "feedbackUri": proof.feedback_uri,
            "blockNumber": proof.block_number,
            "logIndex": proof.log_index,
            "evidenceSource": proof.transaction_ref.evidence_source.value,
            "confirmedProof": proof.to_payload(),
            "leaseExpiresAt": None,
            "updatedAt": now,
        }
        return await self._atomic_outbox_append(
            purchase_id=purchase_id,
            job=job,
            event_type=EventType.REPUTATION_RECORDED,
            actor={"id": "erc8004-reputation-writer", "type": "service"},
            payload=payload,
            evidence_refs=(
                job.decision.audit_bundle_hash,
                job.identity_hash,
                proof.receipt_proof_ref,
            ),
            now=now,
            filter_document={
                "jobId": job_id,
                "status": job.status.value,
                "workerId": worker_id,
                "payloadFingerprint": payload_fingerprint,
            },
            updates=updates,
        )

    async def _recorded_event(self, job: ReputationPublishJob) -> EvidenceEvent:
        document = await self._events.find_one(
            {
                "purchaseId": job.identity.purchase_id,
                "type": EventType.REPUTATION_RECORDED.value,
                "payload.publishIdentityHash": job.identity_hash,
            },
            sort=[("sequence", DESCENDING)],
        )
        if document is None:
            raise EvidenceIntegrityError(
                "a confirmed job has no append-only reputation record"
            )
        return _event_from_document(document)

    async def _atomic_outbox_append(
        self,
        *,
        purchase_id: str,
        job: ReputationPublishJob,
        event_type: EventType,
        actor: JsonObject,
        payload: JsonObject,
        evidence_refs: tuple[str, ...],
        now: datetime,
        filter_document: dict[str, Any],
        updates: dict[str, Any],
    ) -> tuple[EvidenceEvent, ReputationPublishJob]:
        """Appends one purchase event and moves the job in a single transaction."""
        async with self._client.start_session() as session:
            for attempt in range(64):

                async def append_once(
                    active_session: AsyncClientSession,
                ) -> tuple[EvidenceEvent, ReputationPublishJob]:
                    documents, _ = await self._load_verified_chain(
                        purchase_id=purchase_id, session=active_session
                    )
                    event = self._next_event(
                        purchase_id=purchase_id,
                        event_documents=documents,
                        event_type=event_type,
                        occurred_at=now,
                        actor=actor,
                        payload=payload,
                        evidence_refs=evidence_refs,
                    )
                    await self._events.insert_one(
                        _event_document(event), session=active_session
                    )
                    await self._insert_event_head(
                        purchase_id=purchase_id,
                        event=event,
                        anchored_at=now,
                        session=active_session,
                    )
                    moved = await self._reputation_outbox.find_one_and_update(
                        filter_document,
                        {"$set": updates},
                        return_document=ReturnDocument.AFTER,
                        session=active_session,
                    )
                    if moved is None:
                        raise PaymentConflictError(
                            "reputation job changed before the transition"
                        )
                    return event, _outbox_from_document(moved)

                try:
                    return await session.with_transaction(append_once)
                except DuplicateKeyError as exc:
                    index_name = str((exc.details or {}).get("errmsg", ""))
                    if "unique_reputation_evm_transaction" in index_name or (
                        "unique_reputation_local_transaction" in index_name
                    ):
                        raise PaymentConflictError(
                            "reputation transaction reference is already recorded"
                        ) from exc
                    await asyncio.sleep(min(0.001 * (2**attempt), 0.025))
                    continue
        raise DuplicateEventError(
            "reputation append contention exceeded bounded retries"
        )

    async def record_conflict(
        self,
        *,
        identity_hash: str,
        requested_fingerprint: str,
        reason_code: str,
        now: datetime,
    ) -> tuple[EvidenceEvent, ReputationPublishJob]:
        """One Mongo transaction: `CONFLICT` plus its append-only conflict finding."""
        existing = await self._reputation_outbox.find_one(
            {"publishIdentityHash": identity_hash}
        )
        if existing is None:
            raise PaymentEvidenceError("reputation job not found")
        job = _outbox_from_document(existing)
        if job.status is OutboxStatus.CONFIRMED:
            raise PaymentConflictError(
                "a confirmed publication cannot be turned into a conflict"
            )
        payload = publication_conflict_payload(
            identity_hash=identity_hash,
            existing_fingerprint=job.payload_fingerprint,
            requested_fingerprint=requested_fingerprint,
            reason_code=reason_code,
        )
        recorded = await self._events.find_one(
            {
                "purchaseId": job.identity.purchase_id,
                "type": EventType.REPUTATION_PUBLICATION_CONFLICT.value,
                "payload.publishIdentityHash": identity_hash,
                "payload.requestedFingerprint": requested_fingerprint,
                "payload.reasonCode": reason_code,
            },
            sort=[("sequence", DESCENDING)],
        )
        if recorded is not None:
            return _event_from_document(recorded), job
        return await self._atomic_outbox_append(
            purchase_id=job.identity.purchase_id,
            job=job,
            event_type=EventType.REPUTATION_PUBLICATION_CONFLICT,
            actor={"id": "reputation-outbox", "type": "service"},
            payload=payload,
            evidence_refs=(identity_hash, job.decision.audit_bundle_hash),
            now=now,
            filter_document={
                "publishIdentityHash": identity_hash,
                "status": {"$ne": OutboxStatus.CONFIRMED.value},
            },
            updates={
                "status": OutboxStatus.CONFLICT.value,
                "conflictReasonCode": reason_code,
                "requestedFingerprint": requested_fingerprint,
                "leaseExpiresAt": None,
                "updatedAt": now,
            },
        )

    async def list_recoverable_jobs(
        self, *, now: datetime
    ) -> list[ReputationPublishJob]:
        cursor = self._reputation_outbox.find(
            {
                "status": {"$in": [item.value for item in _CLAIMABLE_STATUSES]},
                "$or": [
                    {"leaseExpiresAt": None},
                    {"leaseExpiresAt": {"$lte": now}},
                ],
            }
        ).sort([("createdAt", ASCENDING), ("jobId", ASCENDING)])
        return [_outbox_from_document(document) async for document in cursor]

    # ---- Reputation snapshots ----------------------------------------------------

    async def put_snapshot(self, snapshot: ReputationSnapshot) -> ReputationSnapshot:
        existing = await self._reputation_snapshots.find_one(
            {"snapshotId": snapshot.snapshot_id}
        )
        if existing is not None:
            stored = _snapshot_from_document(existing)
            if stored.snapshot_hash != snapshot.snapshot_hash:
                raise PaymentConflictError("a reputation snapshot is immutable once stored")
            return stored
        try:
            await self._reputation_snapshots.insert_one(_snapshot_document(snapshot))
        except DuplicateKeyError as exc:
            duplicate = await self._reputation_snapshots.find_one(
                {
                    "queryFingerprint": snapshot.scope.query_fingerprint,
                    "scope.toBlock": snapshot.scope.to_block,
                }
            )
            if duplicate is None:
                raise
            stored = _snapshot_from_document(duplicate)
            if stored.snapshot_hash != snapshot.snapshot_hash:
                raise PaymentConflictError(
                    "another snapshot already answers this query at this block"
                ) from exc
            return stored
        return snapshot

    async def get_snapshot(self, snapshot_id: str) -> ReputationSnapshot | None:
        document = await self._reputation_snapshots.find_one({"snapshotId": snapshot_id})
        return None if document is None else _snapshot_from_document(document)

    async def latest_snapshot(
        self, *, seller_agent_id: str, query_fingerprint: str
    ) -> ReputationSnapshot | None:
        cursor = (
            self._reputation_snapshots.find(
                {
                    "sellerAgentId": seller_agent_id,
                    "queryFingerprint": query_fingerprint,
                }
            )
            .sort([("queriedAt", DESCENDING), ("snapshotId", DESCENDING)])
            .limit(1)
        )
        async for document in cursor:
            return _snapshot_from_document(document)
        return None

    # ---- Atomic terminal finalize ------------------------------------------------

    async def finalize_atomic(
        self,
        *,
        purchase_id: str,
        audit_payload: JsonObject | None,
        audit_evidence_refs: tuple[str, ...],
        decision_payload: JsonObject,
        decision_evidence_refs: tuple[str, ...],
        occurred_at: datetime,
        expected_event_count: int,
        expected_head_event_hash: str,
        identity: PublishIdentity,
        identity_hash: str,
        payload_fingerprint: str,
        decision: ReputationDecision,
        status: OutboxStatus,
    ) -> tuple[EvidenceEvent | None, EvidenceEvent, ReputationPublishJob]:
        """One Mongo transaction: audit, decision and the unique outbox job.

        A restart or a second worker either sees the whole unit or none of it, so a
        purchase can never end up with a decision but no job, or two jobs for one identity.
        """
        actor: JsonObject = {"id": "terminal-audit-coordinator", "type": "service"}
        job_id = f"repjob:{identity_hash.split(':')[-1][:32]}"
        job = ReputationPublishJob(
            job_id=job_id,
            identity=identity,
            identity_hash=identity_hash,
            payload_fingerprint=payload_fingerprint,
            decision=decision,
            status=status,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        async with self._client.start_session() as session:
            for attempt in range(64):

                async def finalize_once(
                    active_session: AsyncClientSession,
                ) -> tuple[EvidenceEvent | None, EvidenceEvent]:
                    documents, events = await self._load_verified_chain(
                        purchase_id=purchase_id,
                        session=active_session,
                        expected_event_count=expected_event_count,
                        expected_head_event_hash=expected_head_event_hash,
                    )
                    if any(
                        event.type == EventType.REPUTATION_DECIDED for event in events
                    ):
                        raise PaymentConflictError(
                            "purchase already has a reputation decision"
                        )
                    has_audit = any(event.type == EventType.AUDITED for event in events)
                    if audit_payload is not None and has_audit:
                        raise PaymentConflictError("purchase already has a terminal audit")
                    if audit_payload is None and not has_audit:
                        raise PaymentEvidenceError(
                            "a decision-only finalize requires a persisted terminal audit"
                        )
                    appended: EvidenceEvent | None = None
                    if audit_payload is not None:
                        appended = self._next_event(
                            purchase_id=purchase_id,
                            event_documents=documents,
                            event_type=EventType.AUDITED,
                            occurred_at=occurred_at,
                            actor=actor,
                            payload=audit_payload,
                            evidence_refs=audit_evidence_refs,
                        )
                        await self._events.insert_one(
                            _event_document(appended), session=active_session
                        )
                        await self._insert_event_head(
                            purchase_id=purchase_id,
                            event=appended,
                            anchored_at=occurred_at,
                            session=active_session,
                        )
                        documents = [*documents, _event_document(appended)]
                    decided = self._next_event(
                        purchase_id=purchase_id,
                        event_documents=documents,
                        event_type=EventType.REPUTATION_DECIDED,
                        occurred_at=occurred_at,
                        actor=actor,
                        payload=decision_payload,
                        evidence_refs=decision_evidence_refs,
                    )
                    await self._events.insert_one(
                        _event_document(decided), session=active_session
                    )
                    await self._insert_event_head(
                        purchase_id=purchase_id,
                        event=decided,
                        anchored_at=occurred_at,
                        session=active_session,
                    )
                    await self._reputation_outbox.insert_one(
                        _outbox_document(job), session=active_session
                    )
                    return appended, decided

                try:
                    audit_event, decision_event = await session.with_transaction(
                        finalize_once
                    )
                except DuplicateKeyError as exc:
                    index_name = str((exc.details or {}).get("errmsg", ""))
                    if "unique_reputation_publish_identity" in index_name:
                        existing = await self.get_by_identity(identity_hash)
                        if (
                            existing is not None
                            and existing.payload_fingerprint != payload_fingerprint
                        ):
                            raise PaymentConflictError(
                                "publish identity already has a different fingerprint"
                            ) from exc
                        raise PaymentConflictError(
                            "publish identity already has a job"
                        ) from exc
                    if "unique_reputation_decision_per_purchase" in index_name:
                        raise PaymentConflictError(
                            "purchase already has a reputation decision"
                        ) from exc
                    if "unique_audit_per_purchase" in index_name:
                        raise PaymentConflictError(
                            "purchase already has a terminal audit"
                        ) from exc
                    await asyncio.sleep(min(0.001 * (2**attempt), 0.025))
                    continue
                return audit_event, decision_event, job
        raise DuplicateEventError("terminal finalize contention exceeded bounded retries")
