from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceHead,
    JsonObject,
    SensitivePayload,
    TransactionRef,
    VerifiedIdentity,
    WalletBinding,
)
from buyer_audit_api.core.seller_execution import SellerExecution, SellerExecutionState

if TYPE_CHECKING:  # avoid a runtime cycle with the payment and projection cores
    from buyer_audit_api.core.payment import (
        ConfirmedMismatchProof,
        ConfirmedOutflow,
        NoTransferProof,
        PaymentIntent,
        ReconciliationCheck,
    )
    from buyer_audit_api.core.projections import PurchaseProjection
    from buyer_audit_api.core.reputation import (
        ConfirmedFeedbackProof,
        OutboxStatus,
        PublishIdentity,
        ReputationDecision,
        ReputationPublishJob,
        ReputationSnapshot,
    )


class EvidenceReadPort(Protocol):
    """Read-only evidence access for query paths; it exposes no append operation."""

    async def list_events(self, purchase_id: str) -> list[EvidenceEvent]: ...

    async def get_event_head(self, purchase_id: str) -> EvidenceHead | None: ...

    async def list_owner_purchase_ids(self, owner_address: str) -> list[str]: ...


class PurchaseProjectionPort(Protocol):
    """Pure projection contract used by every read model."""

    def project(
        self,
        events: list[EvidenceEvent],
        payment_intent: PaymentIntent | None = None,
    ) -> PurchaseProjection: ...


class TerminalPaymentPort(Protocol):
    """Atomic append-only terminal payment operations owned by the Evidence API."""

    async def record_reconciliation_check(
        self, *, purchase_id: str, check: ReconciliationCheck
    ) -> PaymentIntent: ...

    async def confirm_mismatch(
        self, *, purchase_id: str, proof: ConfirmedMismatchProof
    ) -> PaymentIntent: ...

    async def reconcile_no_transfer(
        self, *, purchase_id: str, proof: NoTransferProof
    ) -> PaymentIntent: ...

    async def list_confirmed_outflows(
        self, purchase_id: str
    ) -> list[ConfirmedOutflow]: ...


class ReputationOutboxPort(Protocol):
    """Durable publish-job state. `P6-AC-05.3`: one identity, at most one effect.

    Every transition is a compare-and-set against the persisted status, lease owner and
    immutable payload fingerprint, so a restart or a second worker cannot double-publish.
    """

    async def get_job(self, job_id: str) -> ReputationPublishJob | None: ...

    async def get_by_identity(
        self, identity_hash: str
    ) -> ReputationPublishJob | None: ...

    async def claim_job(
        self, *, worker_id: str, lease_until: datetime, now: datetime
    ) -> ReputationPublishJob | None: ...

    async def mark_prepared(
        self,
        *,
        job_id: str,
        worker_id: str,
        payload_fingerprint: str,
        transaction_ref: TransactionRef | None,
        feedback_hash: str,
        now: datetime,
    ) -> ReputationPublishJob: ...

    async def mark_submitted_unknown(
        self,
        *,
        job_id: str,
        worker_id: str,
        payload_fingerprint: str,
        transaction_ref: TransactionRef,
        reason: str,
        now: datetime,
    ) -> ReputationPublishJob: ...

    async def mark_confirmed(
        self,
        *,
        job_id: str,
        worker_id: str,
        payload_fingerprint: str,
        proof: ConfirmedFeedbackProof,
        now: datetime,
    ) -> ReputationPublishJob: ...

    async def record_conflict(
        self,
        *,
        identity_hash: str,
        requested_fingerprint: str,
        reason_code: str,
        now: datetime,
    ) -> ReputationPublishJob: ...

    async def list_recoverable_jobs(
        self, *, now: datetime
    ) -> list[ReputationPublishJob]: ...


class ReputationSnapshotPort(Protocol):
    """Immutable ERC-8004 query provenance (`P6-AC-06.1`); snapshots are never rewritten."""

    async def put_snapshot(self, snapshot: ReputationSnapshot) -> ReputationSnapshot: ...

    async def get_snapshot(self, snapshot_id: str) -> ReputationSnapshot | None: ...

    async def latest_snapshot(
        self, seller_agent_id: str
    ) -> ReputationSnapshot | None: ...


class TerminalOrchestrationPort(Protocol):
    """One atomic unit: verified read, `AUDITED`, `REPUTATION_DECIDED` and the outbox job."""

    async def list_events(self, purchase_id: str) -> list[EvidenceEvent]: ...

    async def get_event_head(self, purchase_id: str) -> EvidenceHead | None: ...

    async def get_payment_intent(self, purchase_id: str) -> PaymentIntent | None: ...

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
    ) -> tuple[EvidenceEvent | None, EvidenceEvent, ReputationPublishJob]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class DomainModule(Protocol):
    @property
    def domain_id(self) -> str: ...

    def normalize_request(self, payload: Mapping[str, Any]) -> JsonObject: ...


class EvidenceRepository(Protocol):
    async def ensure_indexes(self) -> None: ...

    async def close(self) -> None: ...

    async def issue_siwe_nonce(
        self,
        *,
        owner_address: str,
        nonce_hash: str,
        expires_at: datetime,
    ) -> None: ...

    async def consume_siwe_nonce(
        self,
        *,
        owner_address: str,
        nonce_hash: str,
        consumed_at: datetime,
    ) -> bool: ...

    async def bind_buyer_wallet(
        self,
        *,
        owner_address: str,
        buyer_wallet_address: str,
        bound_at: datetime,
    ) -> WalletBinding: ...

    async def get_wallet_binding(self, owner_address: str) -> WalletBinding | None: ...

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
    ) -> EvidenceEvent: ...

    async def list_events(self, purchase_id: str) -> list[EvidenceEvent]: ...

    async def list_owner_purchase_ids(self, owner_address: str) -> list[str]: ...

    async def get_event_head(self, purchase_id: str) -> EvidenceHead | None: ...

    async def store_sensitive_payload(self, payload: SensitivePayload) -> None: ...

    async def get_sensitive_payload(self, payload_id: str) -> SensitivePayload | None: ...

    async def claim_seller_execution(
        self, execution: SellerExecution
    ) -> SellerExecution: ...

    async def transition_seller_execution(
        self,
        *,
        purchase_id: str,
        expected_state: SellerExecutionState,
        next_execution: SellerExecution,
    ) -> SellerExecution: ...

    async def get_seller_execution(
        self, purchase_id: str
    ) -> SellerExecution | None: ...


class PayloadCipher(Protocol):
    def encrypt_json(
        self,
        *,
        purchase_id: str,
        kind: str,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> SensitivePayload: ...

    def decrypt_json(self, payload: SensitivePayload) -> JsonObject: ...


class SiweSignatureVerifier(Protocol):
    def verify(
        self,
        *,
        message: str,
        signature: str,
        expected_domain: str,
        expected_uri: str,
        expected_chain_id: int,
        now: datetime,
    ) -> VerifiedIdentity: ...
