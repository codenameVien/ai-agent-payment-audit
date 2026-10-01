from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceHead,
    ImmutableDocument,
    JsonObject,
    SensitivePayload,
    WalletBinding,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class DomainModule(Protocol):
    @property
    def domain_id(self) -> str: ...

    def normalize_request(self, payload: Mapping[str, Any]) -> JsonObject: ...


class EvidenceRepository(Protocol):
    async def ensure_indexes(self) -> None: ...

    async def close(self) -> None: ...

    async def bind_buyer_wallet(
        self,
        *,
        buyer_wallet_address: str,
        bound_at: datetime,
    ) -> WalletBinding: ...

    async def get_wallet_binding(self) -> WalletBinding | None: ...

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

    async def list_purchase_ids(self) -> list[str]: ...

    async def get_event_head(self, purchase_id: str) -> EvidenceHead | None: ...

    async def store_sensitive_payload(self, payload: SensitivePayload) -> None: ...

    async def get_sensitive_payload(self, payload_id: str) -> SensitivePayload | None: ...

    async def put_document(self, document: ImmutableDocument) -> ImmutableDocument: ...

    async def get_document(self, document_id: str) -> ImmutableDocument | None: ...
