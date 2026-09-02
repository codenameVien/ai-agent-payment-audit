from __future__ import annotations

import uuid
from datetime import datetime

from buyer_audit_api.core.errors import EvidenceIntegrityError
from buyer_audit_api.core.hashing import build_event_hash, sha256_json
from buyer_audit_api.core.models import EventType, EvidenceEvent, JsonObject


def create_event(
    *,
    purchase_id: str,
    sequence: int,
    event_type: EventType,
    occurred_at: datetime,
    actor: JsonObject,
    payload: JsonObject,
    previous_event_hash: str | None,
    evidence_refs: tuple[str, ...],
) -> EvidenceEvent:
    payload_hash = sha256_json(payload)
    event_hash = build_event_hash(
        purchase_id=purchase_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=occurred_at,
        actor=actor,
        previous_event_hash=previous_event_hash,
        payload_hash=payload_hash,
        evidence_refs=evidence_refs,
    )
    return EvidenceEvent(
        event_id=str(uuid.uuid4()),
        purchase_id=purchase_id,
        sequence=sequence,
        type=event_type,
        occurred_at=occurred_at,
        actor=actor,
        payload=payload,
        payload_hash=payload_hash,
        previous_event_hash=previous_event_hash,
        event_hash=event_hash,
        evidence_refs=evidence_refs,
    )


def verify_event_chain(
    events: list[EvidenceEvent],
    *,
    expected_event_count: int | None = None,
    expected_head_event_hash: str | None = None,
) -> None:
    previous_hash: str | None = None
    for expected_sequence, event in enumerate(events, start=1):
        if event.sequence != expected_sequence:
            raise EvidenceIntegrityError("event sequence gap or reordering")
        if event.previous_event_hash != previous_hash:
            raise EvidenceIntegrityError("previous event hash mismatch")
        expected_payload_hash = sha256_json(event.payload)
        if event.payload_hash != expected_payload_hash:
            raise EvidenceIntegrityError("payload hash mismatch")
        expected_event_hash = build_event_hash(
            purchase_id=event.purchase_id,
            sequence=event.sequence,
            event_type=event.type,
            occurred_at=event.occurred_at,
            actor=event.actor,
            previous_event_hash=event.previous_event_hash,
            payload_hash=event.payload_hash,
            evidence_refs=event.evidence_refs,
        )
        if event.event_hash != expected_event_hash:
            raise EvidenceIntegrityError("event hash mismatch")
        previous_hash = event.event_hash
    if expected_event_count is not None and len(events) != expected_event_count:
        raise EvidenceIntegrityError("event count does not match immutable head")
    if expected_head_event_hash is not None:
        actual_head = events[-1].event_hash if events else None
        if actual_head != expected_head_event_hash:
            raise EvidenceIntegrityError("terminal event hash does not match immutable head")
