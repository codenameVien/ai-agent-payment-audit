from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

JsonObject = dict[str, Any]


class EventType(StrEnum):
    REQUESTED = "REQUESTED"
    QUOTED = "QUOTED"
    DECIDED = "DECIDED"
    PAYMENT_INTENT_CLAIMED = "PAYMENT_INTENT_CLAIMED"
    PAYMENT_AUTHORIZED = "PAYMENT_AUTHORIZED"
    PAYMENT_SETTLED = "PAYMENT_SETTLED"
    PAYMENT_RECONCILIATION_REQUIRED = "PAYMENT_RECONCILIATION_REQUIRED"
    PAYMENT_SUBMISSION_IDENTIFIED = "PAYMENT_SUBMISSION_IDENTIFIED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    DELIVERY_STAGED = "DELIVERY_STAGED"
    DELIVERED = "DELIVERED"
    AUDITED = "AUDITED"
    REPUTATION_RECORDED = "REPUTATION_RECORDED"
    EVIDENCE_ANCHORED = "EVIDENCE_ANCHORED"
    SENSITIVE_PAYLOAD_ACCESSED = "SENSITIVE_PAYLOAD_ACCESSED"
    CORRECTION_RECORDED = "CORRECTION_RECORDED"


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    event_id: str
    purchase_id: str
    sequence: int
    type: EventType
    occurred_at: datetime
    actor: JsonObject
    payload: JsonObject
    payload_hash: str
    previous_event_hash: str | None
    event_hash: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceHead:
    purchase_id: str
    event_count: int
    head_event_hash: str


@dataclass(frozen=True, slots=True)
class SensitivePayload:
    payload_id: str
    purchase_id: str
    kind: str
    algorithm: str
    ciphertext: str
    nonce: str
    encrypted_dek: str
    dek_nonce: str
    key_version: str
    content_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SiweChallenge:
    owner_address: str
    nonce: str
    message: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    owner_address: str
    nonce: str


@dataclass(frozen=True, slots=True)
class WalletBinding:
    owner_address: str
    buyer_wallet_address: str
    bound_at: datetime


@dataclass(frozen=True, slots=True)
class PurchaseCreated:
    purchase_id: str
    event: EvidenceEvent
    sensitive_payload_id: str
