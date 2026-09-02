from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.models import EventType, PurchaseCreated
from buyer_audit_api.core.ports import Clock, EvidenceRepository, PayloadCipher


class PurchaseService:
    def __init__(
        self,
        *,
        repository: EvidenceRepository,
        cipher: PayloadCipher,
        domains: DomainRegistry,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._domains = domains
        self._clock = clock

    async def create(
        self,
        *,
        owner_address: str,
        domain_id: str,
        raw_request: Mapping[str, Any],
        budget_units: int,
        policy: Mapping[str, Any],
    ) -> PurchaseCreated:
        if budget_units < 0:
            raise ValueError("budget_units must be non-negative")
        domain = self._domains.require(domain_id)
        normalized = domain.normalize_request(raw_request)
        purchase_id = str(uuid.uuid4())
        now = self._clock.now()
        sensitive = self._cipher.encrypt_json(
            purchase_id=purchase_id,
            kind="request",
            payload=raw_request,
            created_at=now,
        )
        await self._repository.store_sensitive_payload(sensitive)
        event = await self._repository.append_event(
            purchase_id=purchase_id,
            event_type=EventType.REQUESTED,
            occurred_at=now,
            actor={"id": owner_address.lower(), "type": "user"},
            payload={
                "budgetUnits": budget_units,
                "domain": domain_id,
                "normalizedRequest": normalized,
                "policy": dict(policy),
                "rawRequestHash": sensitive.content_hash,
                "sensitivePayloadId": sensitive.payload_id,
            },
        )
        return PurchaseCreated(
            purchase_id=purchase_id,
            event=event,
            sensitive_payload_id=sensitive.payload_id,
        )
