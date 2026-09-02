from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import rfc8785

from buyer_audit_api.core.models import EventType, JsonObject


def utc_iso(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return rfc8785.dumps(value)


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def build_event_hash(
    *,
    purchase_id: str,
    sequence: int,
    event_type: EventType,
    occurred_at: datetime,
    actor: JsonObject,
    previous_event_hash: str | None,
    payload_hash: str,
    evidence_refs: tuple[str, ...],
) -> str:
    envelope = {
        "actor": actor,
        "evidenceRefs": list(evidence_refs),
        "occurredAt": utc_iso(occurred_at),
        "payloadHash": payload_hash,
        "previousEventHash": previous_event_hash,
        "purchaseId": purchase_id,
        "sequence": sequence,
        "type": event_type.value,
    }
    return sha256_json(envelope)
