"""Canonical purchase projection.

This module is pure: it derives orthogonal `lifecycleStatus`, `paymentStatus`,
`auditStatus` and `evidenceSource` axes from an already verified append-only event
list. It performs no I/O and never appends, so every read path can use it without
touching the evidence chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from buyer_audit_api.core.audit import AuditReportReader
from buyer_audit_api.core.errors import EvidenceIntegrityError
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceSource,
    JsonObject,
    TransactionRef,
    transaction_ref_from_payload,
)


class PaymentStatus(StrEnum):
    PAYMENT_NOT_STARTED = "PAYMENT_NOT_STARTED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_CONFIRMATION_UNKNOWN = "PAYMENT_CONFIRMATION_UNKNOWN"
    PAYMENT_SETTLED = "PAYMENT_SETTLED"
    PAYMENT_FAILED = "PAYMENT_FAILED"


class AuditStatus(StrEnum):
    PENDING_AUDIT = "PENDING_AUDIT"
    AUDITED_NORMAL = "AUDITED_NORMAL"
    AUDITED_WARNING = "AUDITED_WARNING"
    AUDITED_RISK = "AUDITED_RISK"


TERMINAL_PAYMENT_STATUS: dict[EventType, PaymentStatus] = {
    EventType.PAYMENT_FAILED: PaymentStatus.PAYMENT_FAILED,
    EventType.PAYMENT_SETTLED: PaymentStatus.PAYMENT_SETTLED,
}

_AUDIT_STATUS_BY_VERDICT: dict[str, AuditStatus] = {
    "NORMAL": AuditStatus.AUDITED_NORMAL,
    "CAUTION": AuditStatus.AUDITED_WARNING,
    "RISK": AuditStatus.AUDITED_RISK,
}

# H4: one pure parser for persisted audit evidence, shared with every read path.
_AUDIT_READER = AuditReportReader()

_NON_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        EventType.SENSITIVE_PAYLOAD_ACCESSED,
        EventType.CORRECTION_RECORDED,
    }
)

_UNKNOWN_PAYMENT_EVENT_TYPES = frozenset({EventType.PAYMENT_RECONCILIATION_REQUIRED})

_PENDING_PAYMENT_EVENT_TYPES = frozenset(
    {
        EventType.PAYMENT_INTENT_CLAIMED,
        EventType.PAYMENT_AUTHORIZED,
    }
)


@dataclass(frozen=True, slots=True)
class PurchaseProjection:
    purchase_id: str
    created_at: datetime
    domain: str
    request_summary: JsonObject
    lifecycle_status: str
    payment_status: PaymentStatus
    audit_status: AuditStatus
    evidence_source: EvidenceSource | None
    transaction_ref: TransactionRef | None
    amount_units: int | None
    token: str | None
    audit_severity: str | None
    finding_count: int
    audit_covers_head: bool
    event_count: int
    head_event_hash: str


def _terminal_events(events: list[EvidenceEvent]) -> list[EvidenceEvent]:
    return [event for event in events if event.type in TERMINAL_PAYMENT_STATUS]


def _terminal_transaction_ref(terminal: EvidenceEvent | None) -> TransactionRef | None:
    """Only a validated `transactionRef` union is exposed; raw hash strings never are."""
    if terminal is None:
        return None
    raw_ref = terminal.payload.get("transactionRef")
    if raw_ref is None:
        return None
    try:
        return transaction_ref_from_payload(raw_ref)
    except ValueError as exc:
        raise EvidenceIntegrityError("terminal transaction reference is malformed") from exc


def _payload_sources(event: EvidenceEvent) -> set[EvidenceSource]:
    """Every evidence source declared by an event, including its nested transaction ref."""
    sources: set[EvidenceSource] = set()
    raw = event.payload.get("evidenceSource")
    if isinstance(raw, str):
        try:
            sources.add(EvidenceSource(raw))
        except ValueError as exc:
            raise EvidenceIntegrityError(
                f"unknown evidence source on {event.type.value}"
            ) from exc
    nested = event.payload.get("transactionRef")
    if nested is not None:
        try:
            sources.add(transaction_ref_from_payload(nested).evidence_source)
        except ValueError as exc:
            raise EvidenceIntegrityError("nested transaction reference is malformed") from exc
    return sources


def _resolve_evidence_source(events: list[EvidenceEvent]) -> EvidenceSource | None:
    sources: set[EvidenceSource] = set()
    for event in events:
        sources |= _payload_sources(event)
    if EvidenceSource.SYNTHETIC_LOCAL in sources and len(sources) > 1:
        raise EvidenceIntegrityError("purchase mixes synthetic and on-chain evidence sources")
    if len(sources) > 1:
        raise EvidenceIntegrityError("purchase declares contradictory evidence sources")
    return next(iter(sources)) if sources else None


def _payment_status(events: list[EvidenceEvent], terminal: EvidenceEvent | None) -> PaymentStatus:
    if terminal is not None:
        return TERMINAL_PAYMENT_STATUS[terminal.type]
    present = {event.type for event in events}
    if present & _UNKNOWN_PAYMENT_EVENT_TYPES:
        return PaymentStatus.PAYMENT_CONFIRMATION_UNKNOWN
    if present & _PENDING_PAYMENT_EVENT_TYPES:
        return PaymentStatus.PAYMENT_PENDING
    return PaymentStatus.PAYMENT_NOT_STARTED


def _audit_projection(
    events: list[EvidenceEvent],
) -> tuple[AuditStatus, str | None, int, bool]:
    """H4: the read model rejects exactly the audit evidence the reader rejects.

    Position and payload are validated by the shared `AuditReportReader`, so a summary can
    never present an audit whose `evidenceHeadEventHash` does not describe its predecessor.
    """
    persisted = _AUDIT_READER.persisted_audit(events)
    if persisted is None:
        return AuditStatus.PENDING_AUDIT, None, 0, False
    verdict = persisted.report.severity.value
    status = _AUDIT_STATUS_BY_VERDICT.get(verdict)
    if status is None:
        raise EvidenceIntegrityError("persisted audit verdict is malformed")
    return (
        status,
        verdict,
        len(persisted.report.findings),
        # H4: a read model must disclose when evidence advanced past the audited head.
        persisted.covers_head,
    )


class PurchaseProjectionService:
    """Pure projection over verified events; never performs I/O or appends evidence."""

    def project(self, events: list[EvidenceEvent]) -> PurchaseProjection:
        if not events:
            raise EvidenceIntegrityError("purchase evidence is missing")
        purchase_ids = {event.purchase_id for event in events}
        if len(purchase_ids) != 1:
            raise EvidenceIntegrityError("cross-purchase evidence in chain")
        purchase_id = events[0].purchase_id

        terminals = _terminal_events(events)
        if len({event.type for event in terminals}) > 1:
            raise EvidenceIntegrityError("conflicting terminal payment evidence")
        terminal = terminals[0] if terminals else None

        evidence_source = _resolve_evidence_source(events)
        transaction_ref = _terminal_transaction_ref(terminal)
        if (
            transaction_ref is not None
            and evidence_source is not None
            and transaction_ref.evidence_source is not evidence_source
        ):
            # H1: the resolved source and the terminal reference must be the same fact.
            raise EvidenceIntegrityError(
                "terminal transaction reference contradicts the evidence source"
            )

        payment_status = _payment_status(events, terminal)
        audit_status, audit_severity, finding_count, audit_covers_head = _audit_projection(
            events
        )

        requested = events[0]
        lifecycle = [event for event in events if event.type not in _NON_LIFECYCLE_EVENT_TYPES]
        claimed = next(
            (event for event in events if event.type == EventType.PAYMENT_INTENT_CLAIMED),
            None,
        )
        return PurchaseProjection(
            purchase_id=purchase_id,
            created_at=requested.occurred_at,
            domain=str(requested.payload.get("domain", "unknown")),
            request_summary=(
                dict(requested.payload["normalizedRequest"])
                if isinstance(requested.payload.get("normalizedRequest"), dict)
                else {}
            ),
            lifecycle_status=(lifecycle[-1] if lifecycle else events[-1]).type.value,
            payment_status=payment_status,
            audit_status=audit_status,
            evidence_source=evidence_source,
            transaction_ref=transaction_ref,
            amount_units=(
                int(claimed.payload["amountUnits"])
                if claimed is not None and isinstance(claimed.payload.get("amountUnits"), int)
                else None
            ),
            token=(
                str(claimed.payload["token"])
                if claimed is not None and isinstance(claimed.payload.get("token"), str)
                else None
            ),
            audit_severity=audit_severity,
            finding_count=finding_count,
            audit_covers_head=audit_covers_head,
            event_count=len(events),
            head_event_hash=events[-1].event_hash,
        )
