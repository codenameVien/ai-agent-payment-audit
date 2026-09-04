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
