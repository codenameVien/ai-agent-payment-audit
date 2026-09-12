from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal

JsonObject = dict[str, Any]

BASE_SEPOLIA_CHAIN_ID = 84532
EVM_TRANSACTION_HASH_PATTERN = re.compile(r"^0x[0-9a-f]{64}$")
SCENARIO_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
LOCAL_TRANSACTION_ID_PATTERN = re.compile(
    r"^localtx:(?P<run_id>[0-9a-f]{32}):(payment|feedback):[0-9]{6}$"
)


class EventType(StrEnum):
    REQUESTED = "REQUESTED"
    QUOTED = "QUOTED"
    AA_SNAPSHOT_RECORDED = "AA_SNAPSHOT_RECORDED"
    DECIDED = "DECIDED"
    PAYMENT_INTENT_CLAIMED = "PAYMENT_INTENT_CLAIMED"
    PAYMENT_AUTHORIZED = "PAYMENT_AUTHORIZED"
    PAYMENT_SETTLED = "PAYMENT_SETTLED"
    PAYMENT_RECONCILIATION_REQUIRED = "PAYMENT_RECONCILIATION_REQUIRED"
    PAYMENT_SUBMISSION_IDENTIFIED = "PAYMENT_SUBMISSION_IDENTIFIED"
    PAYMENT_RECONCILIATION_CHECKED = "PAYMENT_RECONCILIATION_CHECKED"
    PAYMENT_MISMATCH_CONFIRMED = "PAYMENT_MISMATCH_CONFIRMED"
    PAYMENT_RECONCILED_NO_TRANSFER = "PAYMENT_RECONCILED_NO_TRANSFER"
    PAYMENT_ATTEMPT_REJECTED = "PAYMENT_ATTEMPT_REJECTED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    DELIVERY_STAGED = "DELIVERY_STAGED"
    DELIVERED = "DELIVERED"
    AUDITED = "AUDITED"
    REPUTATION_RECORDED = "REPUTATION_RECORDED"
    REPUTATION_DECIDED = "REPUTATION_DECIDED"
    REPUTATION_PUBLICATION_CONFLICT = "REPUTATION_PUBLICATION_CONFLICT"
    EVIDENCE_ANCHORED = "EVIDENCE_ANCHORED"
    SENSITIVE_PAYLOAD_ACCESSED = "SENSITIVE_PAYLOAD_ACCESSED"
    CORRECTION_RECORDED = "CORRECTION_RECORDED"
    # Auxiliary evidence is intentionally separate from deterministic lifecycle facts.
    # It may advise a reviewer but can never authorize or reject a payment.
    OBSERVER_COMPLETED = "OBSERVER_COMPLETED"
    OBSERVER_UNAVAILABLE = "OBSERVER_UNAVAILABLE"
    CHECKPOINT_TARGET_SELECTED = "CHECKPOINT_TARGET_SELECTED"
    CHECKPOINT_RECORDED = "CHECKPOINT_RECORDED"


AUXILIARY_EVENT_TYPES: tuple[EventType, ...] = (
    EventType.OBSERVER_COMPLETED,
    EventType.OBSERVER_UNAVAILABLE,
    EventType.CHECKPOINT_TARGET_SELECTED,
    EventType.CHECKPOINT_RECORDED,
)


TERMINAL_PAYMENT_EVENT_TYPES: tuple[EventType, ...] = (
    EventType.PAYMENT_SETTLED,
    EventType.PAYMENT_FAILED,
    EventType.PAYMENT_MISMATCH_CONFIRMED,
    EventType.PAYMENT_RECONCILED_NO_TRANSFER,
)


class EvidenceSource(StrEnum):
    BASE_SEPOLIA_VERIFIED = "BASE_SEPOLIA_VERIFIED"
    HISTORICAL_ON_CHAIN = "HISTORICAL_ON_CHAIN"
    SYNTHETIC_LOCAL = "SYNTHETIC_LOCAL"


@dataclass(frozen=True, slots=True)
class ScenarioMetadata:
    """Attested local-scenario provenance carried inside synthetic event payloads."""

    run_id: str
    scenario_id: str
    catalog_version: str
    catalog_hash: str

    def __post_init__(self) -> None:
        if SCENARIO_RUN_ID_PATTERN.fullmatch(self.run_id) is None:
            raise ValueError("scenario runId must be 32 lowercase hex characters")
        for field, value in (
            ("scenarioId", self.scenario_id),
            ("catalogVersion", self.catalog_version),
            ("catalogHash", self.catalog_hash),
        ):
            if not value.strip():
                raise ValueError(f"scenario {field} is required")

    def to_payload(self) -> JsonObject:
        return {
            "catalogHash": self.catalog_hash,
            "catalogVersion": self.catalog_version,
            "runId": self.run_id,
            "scenarioId": self.scenario_id,
        }

    @staticmethod
    def from_payload(value: object) -> ScenarioMetadata:
        if not isinstance(value, dict):
            raise ValueError("scenario metadata is malformed")
        return ScenarioMetadata(
            run_id=str(value.get("runId", "")),
            scenario_id=str(value.get("scenarioId", "")),
            catalog_version=str(value.get("catalogVersion", "")),
            catalog_hash=str(value.get("catalogHash", "")),
        )


@dataclass(frozen=True, slots=True)
class EvmTransactionRef:
    """A transaction that is provable on an EVM chain by hash."""

    KIND: ClassVar[Literal["EVM"]] = "EVM"

    hash: str
    evidence_source: EvidenceSource = EvidenceSource.BASE_SEPOLIA_VERIFIED
    chain_id: int = BASE_SEPOLIA_CHAIN_ID
    block_number: int | None = None
    log_index: int | None = None

    def __post_init__(self) -> None:
        if EVM_TRANSACTION_HASH_PATTERN.fullmatch(self.hash) is None:
            raise ValueError("EVM transaction hash must match ^0x[0-9a-f]{64}$")
        if self.chain_id != BASE_SEPOLIA_CHAIN_ID:
            raise ValueError("EVM transaction reference must be on Base Sepolia")
        if self.evidence_source is EvidenceSource.SYNTHETIC_LOCAL:
            raise ValueError("synthetic evidence cannot use an EVM transaction reference")
        for field, value in (("blockNumber", self.block_number), ("logIndex", self.log_index)):
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError(f"EVM transaction {field} must be a non-negative integer")

    @property
    def kind(self) -> Literal["EVM"]:
        return self.KIND

    def to_payload(self) -> JsonObject:
        payload: JsonObject = {
            "chainId": self.chain_id,
            "evidenceSource": self.evidence_source.value,
            "hash": self.hash,
            "kind": self.KIND,
        }
        if self.block_number is not None:
            payload["blockNumber"] = self.block_number
        if self.log_index is not None:
            payload["logIndex"] = self.log_index
        return payload


@dataclass(frozen=True, slots=True)
class LocalTransactionRef:
    """A synthetic local-ledger transaction that must never be rendered as a chain hash."""

    KIND: ClassVar[Literal["LOCAL"]] = "LOCAL"

    id: str
    run_id: str
    evidence_source: EvidenceSource = EvidenceSource.SYNTHETIC_LOCAL

    def __post_init__(self) -> None:
        match = LOCAL_TRANSACTION_ID_PATTERN.fullmatch(self.id)
        if match is None:
            raise ValueError(
                "local transaction id must match ^localtx:[0-9a-f]{32}:(payment|feedback):[0-9]{6}$"
            )
        if match.group("run_id") != self.run_id:
            raise ValueError("local transaction id does not belong to this runId")
        if self.evidence_source is not EvidenceSource.SYNTHETIC_LOCAL:
            raise ValueError("local transaction references are always SYNTHETIC_LOCAL")

    @property
    def kind(self) -> Literal["LOCAL"]:
        return self.KIND

    def to_payload(self) -> JsonObject:
        return {
            "evidenceSource": self.evidence_source.value,
            "id": self.id,
            "kind": self.KIND,
            "runId": self.run_id,
        }


TransactionRef = EvmTransactionRef | LocalTransactionRef


def transaction_ref_from_payload(value: object) -> TransactionRef:
    """Parse the persisted discriminated union without promoting one variant into the other."""
    if not isinstance(value, dict):
        raise ValueError("transaction reference is malformed")
    kind = value.get("kind")
    if kind == EvmTransactionRef.KIND:
        block_number = value.get("blockNumber")
        log_index = value.get("logIndex")
        return EvmTransactionRef(
            hash=str(value.get("hash", "")),
            evidence_source=EvidenceSource(str(value.get("evidenceSource", ""))),
            chain_id=int(value.get("chainId", 0)),
            block_number=int(block_number) if isinstance(block_number, int) else None,
            log_index=int(log_index) if isinstance(log_index, int) else None,
        )
    if kind == LocalTransactionRef.KIND:
        return LocalTransactionRef(
            id=str(value.get("id", "")),
            run_id=str(value.get("runId", "")),
            evidence_source=EvidenceSource(
                str(value.get("evidenceSource", EvidenceSource.SYNTHETIC_LOCAL.value))
            ),
        )
    raise ValueError("transaction reference kind must be EVM or LOCAL")


def transaction_ref_from_fields(
    *,
    transaction_hash: str | None,
    local_transaction_id: str | None,
    run_id: str | None = None,
    evidence_source: EvidenceSource | None = None,
    block_number: int | None = None,
    log_index: int | None = None,
) -> TransactionRef:
    """Reject type confusion between the EVM hash field and the local identifier field."""
    if transaction_hash is not None and local_transaction_id is not None:
        raise ValueError("a payload cannot carry both transactionHash and localTransactionId")
    if transaction_hash is not None:
        if transaction_hash.startswith("localtx:"):
            raise ValueError("a local transaction id cannot be a transactionHash")
        return EvmTransactionRef(
            hash=transaction_hash,
            evidence_source=evidence_source or EvidenceSource.BASE_SEPOLIA_VERIFIED,
            block_number=block_number,
            log_index=log_index,
        )
    if local_transaction_id is not None:
        if local_transaction_id.startswith("0x"):
            raise ValueError("an EVM transaction hash cannot be a localTransactionId")
        if run_id is None:
            raise ValueError("a local transaction reference requires its runId")
        return LocalTransactionRef(id=local_transaction_id, run_id=run_id)
    raise ValueError("a transaction reference requires transactionHash or localTransactionId")


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
class ImmutableDocument:
    """An append-only side document the Evidence API owns and never rewrites.

    Purchase events stay small by referencing one of these by `document_id`; the document
    itself carries a bulky immutable body such as a captured external API snapshot. The
    identity is derived from the content, so storing the same body twice is idempotent
    and storing a different body under a known id is a conflict, never an overwrite.
    """

    document_id: str
    purchase_id: str
    kind: str
    content_hash: str
    created_at: datetime
    payload: JsonObject


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
