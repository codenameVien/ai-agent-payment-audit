"""Canonical Phase 6 purchase projection.

This module is pure: it derives orthogonal `lifecycleStatus`, `paymentStatus`,
`auditStatus` and `evidenceSource` axes from an already verified append-only event
list plus an optional payment intent. It performs no I/O and never appends, so every
read path can use it without touching the evidence chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from buyer_audit_api.core.errors import EvidenceIntegrityError
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceSource,
    EvmTransactionRef,
    JsonObject,
    LocalTransactionRef,
    ScenarioMetadata,
    TransactionRef,
    transaction_ref_from_payload,
)
from buyer_audit_api.core.payment import PaymentIntent, PaymentIntentState


class PaymentStatus(StrEnum):
    PAYMENT_NOT_STARTED = "PAYMENT_NOT_STARTED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_CONFIRMATION_UNKNOWN = "PAYMENT_CONFIRMATION_UNKNOWN"
    PAYMENT_SETTLED = "PAYMENT_SETTLED"
    PAYMENT_MISMATCH_CONFIRMED = "PAYMENT_MISMATCH_CONFIRMED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    RECONCILED_NO_TRANSFER = "RECONCILED_NO_TRANSFER"


class AuditStatus(StrEnum):
    PENDING_AUDIT = "PENDING_AUDIT"
    AUDITED_NORMAL = "AUDITED_NORMAL"
    AUDITED_WARNING = "AUDITED_WARNING"
    AUDITED_RISK = "AUDITED_RISK"


TERMINAL_PAYMENT_STATUS: dict[EventType, PaymentStatus] = {
    EventType.PAYMENT_MISMATCH_CONFIRMED: PaymentStatus.PAYMENT_MISMATCH_CONFIRMED,
    EventType.PAYMENT_RECONCILED_NO_TRANSFER: PaymentStatus.RECONCILED_NO_TRANSFER,
    EventType.PAYMENT_FAILED: PaymentStatus.PAYMENT_FAILED,
    EventType.PAYMENT_SETTLED: PaymentStatus.PAYMENT_SETTLED,
}

TERMINAL_INTENT_STATUS: dict[PaymentIntentState, PaymentStatus] = {
    PaymentIntentState.SETTLED: PaymentStatus.PAYMENT_SETTLED,
    PaymentIntentState.FAILED: PaymentStatus.PAYMENT_FAILED,
    PaymentIntentState.MISMATCH_CONFIRMED: PaymentStatus.PAYMENT_MISMATCH_CONFIRMED,
    PaymentIntentState.RECONCILED_NO_TRANSFER: PaymentStatus.RECONCILED_NO_TRANSFER,
}

_AUDIT_STATUS_BY_VERDICT: dict[str, AuditStatus] = {
    "NORMAL": AuditStatus.AUDITED_NORMAL,
    "CAUTION": AuditStatus.AUDITED_WARNING,
    "RISK": AuditStatus.AUDITED_RISK,
}

_NON_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        EventType.SENSITIVE_PAYLOAD_ACCESSED,
        EventType.CORRECTION_RECORDED,
        EventType.EVIDENCE_ANCHORED,
        EventType.PAYMENT_RECONCILIATION_CHECKED,
        EventType.PAYMENT_ATTEMPT_REJECTED,
    }
)

_UNKNOWN_PAYMENT_EVENT_TYPES = frozenset(
    {
        EventType.PAYMENT_RECONCILIATION_REQUIRED,
        EventType.PAYMENT_SUBMISSION_IDENTIFIED,
        EventType.PAYMENT_RECONCILIATION_CHECKED,
    }
)

_PENDING_PAYMENT_EVENT_TYPES = frozenset(
    {
        EventType.PAYMENT_INTENT_CLAIMED,
        EventType.PAYMENT_AUTHORIZED,
        EventType.PAYMENT_ATTEMPT_REJECTED,
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
    scenario: ScenarioMetadata | None
    amount_units: int | None
    token: str | None
    mismatched_fields: tuple[str, ...]
    audit_severity: str | None
    finding_count: int
    audit_covers_head: bool
    event_count: int
    head_event_hash: str

    @property
    def legacy_transaction_hash(self) -> str | None:
        """Compatibility field: only a settled EVM proof may fill the legacy hash."""
        if self.payment_status is not PaymentStatus.PAYMENT_SETTLED:
            return None
        if isinstance(self.transaction_ref, EvmTransactionRef):
            return self.transaction_ref.hash
        return None


def _scenario_metadata(events: list[EvidenceEvent]) -> ScenarioMetadata | None:
    found: set[ScenarioMetadata] = set()
    for event in events:
        raw = event.payload.get("scenario")
        if raw is None:
            continue
        try:
            found.add(ScenarioMetadata.from_payload(raw))
        except ValueError as exc:
            raise EvidenceIntegrityError("scenario metadata is malformed") from exc
    if not found:
        return None
    if len(found) > 1:
        raise EvidenceIntegrityError("purchase mixes more than one scenario run")
    return found.pop()


def _is_legacy_permit2(events: list[EvidenceEvent]) -> bool:
    claimed = next(
        (event for event in events if event.type == EventType.PAYMENT_INTENT_CLAIMED),
        None,
    )
    if claimed is None:
        return False
    return (
        claimed.payload.get("transferMethod") == "permit2"
        or claimed.payload.get("permit2Nonce") is not None
    )


def _terminal_events(events: list[EvidenceEvent]) -> list[EvidenceEvent]:
    return [event for event in events if event.type in TERMINAL_PAYMENT_STATUS]


def _terminal_transaction_ref(
    terminal: EvidenceEvent | None,
    *,
    resolved_source: EvidenceSource | None,
) -> TransactionRef | None:
    if terminal is None:
        return None
    raw_ref = terminal.payload.get("transactionRef")
    if raw_ref is not None:
        try:
            return transaction_ref_from_payload(raw_ref)
        except ValueError as exc:
            raise EvidenceIntegrityError("terminal transaction reference is malformed") from exc
    legacy_hash = terminal.payload.get("transactionHash")
    if not isinstance(legacy_hash, str) or not legacy_hash:
        return None
    if resolved_source is EvidenceSource.SYNTHETIC_LOCAL:
        raise EvidenceIntegrityError("synthetic evidence cannot carry a legacy transaction hash")
    try:
        return EvmTransactionRef(
            hash=legacy_hash.lower(),
            evidence_source=resolved_source or EvidenceSource.BASE_SEPOLIA_VERIFIED,
            block_number=(
                int(terminal.payload["blockNumber"])
                if isinstance(terminal.payload.get("blockNumber"), int)
                else None
            ),
            log_index=(
                int(terminal.payload["transferLogIndex"])
                if isinstance(terminal.payload.get("transferLogIndex"), int)
                else None
            ),
        )
    except ValueError as exc:
        raise EvidenceIntegrityError("terminal transaction hash is malformed") from exc


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


def _resolve_evidence_source(
    events: list[EvidenceEvent],
    *,
    scenario: ScenarioMetadata | None,
    terminal: EvidenceEvent | None,
) -> EvidenceSource | None:
    sources: set[EvidenceSource] = set()
    if scenario is not None:
        sources.add(EvidenceSource.SYNTHETIC_LOCAL)
    for event in events:
        sources |= _payload_sources(event)
    if _is_legacy_permit2(events):
        sources.add(EvidenceSource.HISTORICAL_ON_CHAIN)
    if (
        terminal is not None
        and not _payload_sources(terminal)
        and isinstance(terminal.payload.get("transactionHash"), str)
    ):
        # A legacy terminal proof declares no source; its transfer method decides.
        sources.add(
            EvidenceSource.HISTORICAL_ON_CHAIN
            if _is_legacy_permit2(events)
            else EvidenceSource.BASE_SEPOLIA_VERIFIED
        )
    if EvidenceSource.SYNTHETIC_LOCAL in sources and len(sources) > 1:
        raise EvidenceIntegrityError("purchase mixes synthetic and on-chain evidence sources")
    if len(sources) > 1:
        raise EvidenceIntegrityError("purchase declares contradictory evidence sources")
    return next(iter(sources)) if sources else None


def _payment_status(
    events: list[EvidenceEvent],
    terminal: EvidenceEvent | None,
    payment_intent: PaymentIntent | None,
) -> PaymentStatus:
    if terminal is not None:
        status = TERMINAL_PAYMENT_STATUS[terminal.type]
        if payment_intent is not None:
            intent_status = TERMINAL_INTENT_STATUS.get(payment_intent.state)
            if intent_status is not None and intent_status is not status:
                raise EvidenceIntegrityError(
                    "payment intent terminal state contradicts terminal evidence"
                )
        return status
    if payment_intent is not None and payment_intent.state in TERMINAL_INTENT_STATUS:
        raise EvidenceIntegrityError("terminal payment intent has no terminal evidence")
    present = {event.type for event in events}
    if present & _UNKNOWN_PAYMENT_EVENT_TYPES:
        return PaymentStatus.PAYMENT_CONFIRMATION_UNKNOWN
    if payment_intent is not None and payment_intent.transaction_hash is not None:
        return PaymentStatus.PAYMENT_CONFIRMATION_UNKNOWN
    if present & _PENDING_PAYMENT_EVENT_TYPES:
        return PaymentStatus.PAYMENT_PENDING
    return PaymentStatus.PAYMENT_NOT_STARTED


def _audit_projection(
    events: list[EvidenceEvent],
) -> tuple[AuditStatus, str | None, int, bool]:
    audited = [event for event in events if event.type == EventType.AUDITED]
    if not audited:
        return AuditStatus.PENDING_AUDIT, None, 0, False
    if len(audited) > 1:
        raise EvidenceIntegrityError("multiple persisted audit reports exist")
    audited_event = audited[0]
    payload = audited_event.payload
    verdict = str(payload.get("severity", ""))
    status = _AUDIT_STATUS_BY_VERDICT.get(verdict)
    if status is None:
        raise EvidenceIntegrityError("persisted audit verdict is malformed")
    findings = payload.get("findings")
    # H4: a read model must disclose when evidence advanced past the audited head.
    covers_head = audited_event.sequence == len(events)
    return (
        status,
        verdict,
        len(findings) if isinstance(findings, list) else 0,
        covers_head,
    )


class PurchaseProjectionService:
    """Pure projection over verified events; never performs I/O or appends evidence."""

    def project(
        self,
        events: list[EvidenceEvent],
        payment_intent: PaymentIntent | None = None,
    ) -> PurchaseProjection:
        if not events:
            raise EvidenceIntegrityError("purchase evidence is missing")
        purchase_ids = {event.purchase_id for event in events}
        if len(purchase_ids) != 1:
            raise EvidenceIntegrityError("cross-purchase evidence in chain")
        purchase_id = events[0].purchase_id
        if payment_intent is not None and payment_intent.purchase_id != purchase_id:
            raise EvidenceIntegrityError("payment intent belongs to another purchase")

        terminals = _terminal_events(events)
        if len({event.type for event in terminals}) > 1:
            raise EvidenceIntegrityError("conflicting terminal payment evidence")
        terminal = terminals[0] if terminals else None

        scenario = _scenario_metadata(events)
        evidence_source = _resolve_evidence_source(
            events, scenario=scenario, terminal=terminal
        )
        transaction_ref = _terminal_transaction_ref(terminal, resolved_source=evidence_source)
        if transaction_ref is not None:
            # H1: the resolved source and the terminal reference must be the same fact.
            if (
                evidence_source is not None
                and transaction_ref.evidence_source is not evidence_source
            ):
                raise EvidenceIntegrityError(
                    "terminal transaction reference contradicts the evidence source"
                )
            if isinstance(transaction_ref, LocalTransactionRef) and (
                scenario is None or transaction_ref.run_id != scenario.run_id
            ):
                raise EvidenceIntegrityError(
                    "local transaction reference belongs to another run"
                )

        payment_status = _payment_status(events, terminal, payment_intent)
        audit_status, audit_severity, finding_count, audit_covers_head = _audit_projection(
            events
        )

        requested = events[0]
        lifecycle = [event for event in events if event.type not in _NON_LIFECYCLE_EVENT_TYPES]
        claimed = next(
            (event for event in events if event.type == EventType.PAYMENT_INTENT_CLAIMED),
            None,
        )
        raw_mismatched = (
            terminal.payload.get("mismatchedFields")
            if terminal is not None and terminal.type == EventType.PAYMENT_MISMATCH_CONFIRMED
            else None
        )
        mismatched_fields = (
            tuple(str(item) for item in raw_mismatched)
            if isinstance(raw_mismatched, list)
            else ()
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
            scenario=scenario,
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
            mismatched_fields=mismatched_fields,
            audit_severity=audit_severity,
            finding_count=finding_count,
            audit_covers_head=audit_covers_head,
            event_count=len(events),
            head_event_hash=events[-1].event_hash,
        )
