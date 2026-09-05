"""Pure reputation core for the Phase 6 terminal-audit feedback loop.

This module holds no I/O. It owns four things:

* the durable publish identity and the immutable payload fingerprint that make one
  purchase's external feedback effect at most one (`P6-AC-05.3`),
* the deterministic `PUBLISH(100) | PUBLISH(0) | DEFER` mapping over persisted terminal
  audit evidence (`P6-AC-05.2`),
* the outbox job state machine that survives restarts (`P6-AC-05.4`),
* the reputation query snapshot, its provenance and the neutral-mean aggregation used as a
  bounded selection signal (`P6-AC-06.1`, `P6-AC-06.3`).

Nothing here decides *when* to run; `core/terminal.py` owns orchestration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from buyer_audit_api.core.audit import (
    RULESET_VERSION,
    AuditAuthority,
    AuditReport,
    AuditSeverity,
)
from buyer_audit_api.core.errors import PaymentConflictError, PaymentEvidenceError
from buyer_audit_api.core.hashing import sha256_json
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceSource,
    EvmTransactionRef,
    JsonObject,
    TransactionRef,
    transaction_ref_from_payload,
)
from buyer_audit_api.core.payment import PaymentIntentState
from buyer_audit_api.core.projections import PaymentStatus

# ERC-8004 feedback tags. One declaration; every publisher, query and verifier uses these.
FEEDBACK_TAG1 = "pbl-audit"
FEEDBACK_TAG2 = "payment-outcome"


# The canonical Base Sepolia reputation registry. This is a protocol address, not a
# per-deployment setting, and it matches the Payment Executor's compiled-in default.
BASE_SEPOLIA_REPUTATION_REGISTRY = "0x8004b663056a597dffe9eccc1965a193b7388713"
AGGREGATION_METHOD = "ARITHMETIC_MEAN_V1"
NEUTRAL_SCORE = 50.0
PUBLISH_SUCCESS_VALUE = 100
PUBLISH_FAILURE_VALUE = 0

# P6-AC-06.3: a snapshot older than this is still usable but must be labelled STALE.
DEFAULT_FRESHNESS_WINDOW = timedelta(hours=24)

# `P6-AC-06.1` and requirements 12.10: the trusted feedback-client allow-list is
# **deployment configuration**, and an unknown client is never counted. This module
# only names the config key; the composition root parses it and no wallet address is
# ever compiled in. An absent or empty declaration is a valid fail-closed production
# state: no client is trusted, no query is issued, and every candidate scores neutral.
FEEDBACK_CLIENTS_ENV = "PBL_AUDIT_FEEDBACK_CLIENTS"
FEEDBACK_CLIENT_PATTERN = re.compile(r"^0x[0-9a-f]{40}$")

# Design 18.9: the query is a bounded window ending at the chain head, never an
# unbounded scan and never the genesis block alone.
REPUTATION_LOOKBACK_BLOCKS = 10_000
MAX_REPUTATION_LOOKBACK_BLOCKS = 100_000

# The only evidence sources a reputation query may carry. A synthetic or historical
# label can never enter a Base Sepolia query answer.
QUERYABLE_EVIDENCE_SOURCES: frozenset[EvidenceSource] = frozenset(
    {EvidenceSource.BASE_SEPOLIA_VERIFIED}
)


class DecisionKind(StrEnum):
    PUBLISH = "PUBLISH"
    DEFER = "DEFER"


class ReputationReason(StrEnum):
    """Objective reason codes. They name the evidence, never a semantic opinion."""

    SELLER_DELIVERED_AS_QUOTED = "SELLER_DELIVERED_AS_QUOTED"
    BUYER_ATTRIBUTED_FINDINGS_ONLY = "BUYER_ATTRIBUTED_FINDINGS_ONLY"
    PAYMENT_MISMATCH_CONFIRMED = "PAYMENT_MISMATCH_CONFIRMED"
    PAYMENT_RECONCILED_NO_TRANSFER = "PAYMENT_RECONCILED_NO_TRANSFER"
    SELLER_ATTRIBUTED_PAYMENT_FAILURE = "SELLER_ATTRIBUTED_PAYMENT_FAILURE"
    DELIVERY_INTEGRITY_FAILURE = "DELIVERY_INTEGRITY_FAILURE"
    PAYMENT_CONFIRMATION_UNKNOWN = "PAYMENT_CONFIRMATION_UNKNOWN"
    ATTRIBUTION_INSUFFICIENT = "ATTRIBUTION_INSUFFICIENT"
    SELLER_IDENTITY_UNKNOWN = "SELLER_IDENTITY_UNKNOWN"
    CONFLICTING_PROOF = "CONFLICTING_PROOF"
    AUDIT_NOT_TERMINAL = "AUDIT_NOT_TERMINAL"


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    NO_EVIDENCE = "NO_EVIDENCE"


class OutboxStatus(StrEnum):
    """Durable job state. `LEASED` is the only operational-only transition."""

    PENDING = "PENDING"
    LEASED = "LEASED"
    PREPARED = "PREPARED"
    SUBMITTED_UNKNOWN = "SUBMITTED_UNKNOWN"
    CONFIRMED = "CONFIRMED"
    DEFERRED = "DEFERRED"
    CONFLICT = "CONFLICT"


TERMINAL_OUTBOX_STATUSES: frozenset[OutboxStatus] = frozenset(
    {OutboxStatus.CONFIRMED, OutboxStatus.DEFERRED, OutboxStatus.CONFLICT}
)

# A job in one of these states may already have produced an external effect, so recovery
# must look for existing feedback before it ever considers submitting again.
SUBMITTED_OUTBOX_STATUSES: frozenset[OutboxStatus] = frozenset(
    {OutboxStatus.PREPARED, OutboxStatus.SUBMITTED_UNKNOWN}
)

# Deterministic rules whose evidence is attributable to the seller, not the buyer's
# selection or explanation.
_SELLER_ATTRIBUTED_RULES: frozenset[str] = frozenset(
    {
        "AUD-QUOTE-PAYMENT-MISMATCH",
        "AUD-FACILITATOR-SUCCESS-WITHOUT-TRANSFER",
        "AUD-PAYMENT-RECONCILED-NO-TRANSFER",
        "AUD-PAYMENT-FAILED",
        "AUD-DELIVERY-SELECTION-MISMATCH",
        "AUD-DELIVERY-MISSING",
        "AUD-DELIVERY-HASH-MISSING",
    }
)

# Delivery integrity is seller-attributable on its own: the paid-for result never arrived
# intact, whatever the payment evidence says.
_DELIVERY_INTEGRITY_RULES: frozenset[str] = frozenset(
    {
        "AUD-DELIVERY-SELECTION-MISMATCH",
        "AUD-DELIVERY-MISSING",
        "AUD-DELIVERY-HASH-MISSING",
    }
)


# Terminal intent states and the event-derived status that must accompany them. A
# disagreement means the mutable document and the append-only evidence tell different
# stories, which is conflicting proof rather than a publishable outcome.
_STATUS_FOR_STATE: dict[PaymentIntentState, PaymentStatus] = {
    PaymentIntentState.SETTLED: PaymentStatus.PAYMENT_SETTLED,
    PaymentIntentState.FAILED: PaymentStatus.PAYMENT_FAILED,
    PaymentIntentState.MISMATCH_CONFIRMED: PaymentStatus.PAYMENT_MISMATCH_CONFIRMED,
    PaymentIntentState.RECONCILED_NO_TRANSFER: PaymentStatus.RECONCILED_NO_TRANSFER,
}


@dataclass(frozen=True, slots=True)
class PublishIdentity:
    """`P6-AC-05.3`: the durable identity of one purchase's external feedback effect."""

    chain_id: int
    registry_address: str
    purchase_id: str
    seller_agent_id: str
    tag1: str = FEEDBACK_TAG1
    tag2: str = FEEDBACK_TAG2

    def __post_init__(self) -> None:
        if self.chain_id <= 0:
            raise ValueError("publish identity chainId must be positive")
        for name, value in (
            ("registryAddress", self.registry_address),
            ("purchaseId", self.purchase_id),
            ("sellerAgentId", self.seller_agent_id),
            ("tag1", self.tag1),
            ("tag2", self.tag2),
        ):
            if not value.strip():
                raise ValueError(f"publish identity {name} is required")
        if self.registry_address != self.registry_address.lower():
            raise ValueError("publish identity registryAddress must be lowercase")

    def to_payload(self) -> JsonObject:
        return {
            "chainId": self.chain_id,
            "purchaseId": self.purchase_id,
            "registryAddress": self.registry_address,
            "sellerAgentId": self.seller_agent_id,
            "tag1": self.tag1,
            "tag2": self.tag2,
        }

    @property
    def identity_hash(self) -> str:
        return sha256_json(self.to_payload())

    @staticmethod
    def from_payload(value: object) -> PublishIdentity:
        if not isinstance(value, dict):
            raise ValueError("publish identity is malformed")
        try:
            return PublishIdentity(
                chain_id=int(value["chainId"]),
                registry_address=str(value["registryAddress"]),
                purchase_id=str(value["purchaseId"]),
                seller_agent_id=str(value["sellerAgentId"]),
                tag1=str(value["tag1"]),
                tag2=str(value["tag2"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("publish identity is malformed") from exc


@dataclass(frozen=True, slots=True)
class ReputationDecision:
    """A typed `PUBLISH(value, reasonCodes)` or `DEFER(reasonCode)` over audit evidence."""

    kind: DecisionKind
    reason_codes: tuple[ReputationReason, ...]
    audit_bundle_hash: str
    ruleset_version: str
    seller_agent_id: str
    erc8004_agent_id: str
    value: int | None = None

    def __post_init__(self) -> None:
        if not self.reason_codes:
            raise ValueError("a reputation decision requires at least one reason code")
        if self.kind is DecisionKind.PUBLISH:
            if self.value not in (PUBLISH_SUCCESS_VALUE, PUBLISH_FAILURE_VALUE):
                raise ValueError("a published objective value is exactly 100 or 0")
        elif self.value is not None:
            raise ValueError("a deferred decision carries no value")
        if not self.audit_bundle_hash.strip():
            raise ValueError("a reputation decision requires the audit bundle hash")
        if not self.ruleset_version.strip():
            raise ValueError("a reputation decision requires the ruleset version")

    @property
    def publishes(self) -> bool:
        return self.kind is DecisionKind.PUBLISH

    def to_payload(self) -> JsonObject:
        payload: JsonObject = {
            "auditBundleHash": self.audit_bundle_hash,
            "decision": self.kind.value,
            "erc8004AgentId": self.erc8004_agent_id,
            "reasonCodes": [item.value for item in self.reason_codes],
            "rulesetVersion": self.ruleset_version,
            "sellerAgentId": self.seller_agent_id,
        }
        if self.value is not None:
            payload["value"] = self.value
        return payload

    def payload_fingerprint(self, identity: PublishIdentity) -> str:
        """`P6-AC-05.3`: identity, agent, bundle, ruleset, value and reasons are immutable.

        The same identity arriving with any different fingerprint is a conflict, never a
        second submission.
        """
        return sha256_json(
            {
                "decision": self.to_payload(),
                "publishIdentity": identity.to_payload(),
                "publishIdentityHash": identity.identity_hash,
            }
        )


@dataclass(frozen=True, slots=True)
class SellerAttribution:
    """The seller-side facts a decision is allowed to look at."""

    seller_agent_id: str
    erc8004_agent_id: str
    identity_verified: bool
    delivered_as_quoted: bool

    def __post_init__(self) -> None:
        if not self.seller_agent_id.strip() or not self.erc8004_agent_id.strip():
            raise ValueError("seller attribution requires both agent identifiers")


class ReputationDecisionPolicy:
    """Deterministic `P6-AC-05.2` mapping. It reads persisted evidence only."""

    def decide(
        self,
        *,
        audit: AuditReport,
        payment_status: PaymentStatus,
        payment_state: PaymentIntentState | None,
        attribution: SellerAttribution,
        conflicting_proof: bool = False,
    ) -> ReputationDecision:
        contradicts = payment_state is not None and (
            _STATUS_FOR_STATE.get(payment_state, payment_status) is not payment_status
        )
        deferral = self._deferral_reason(
            audit=audit,
            payment_status=payment_status,
            attribution=attribution,
            conflicting_proof=conflicting_proof or contradicts,
        )
        if deferral is not None:
            return self._defer(deferral, audit=audit, attribution=attribution)

        failure_reasons = self._failure_reasons(
            audit=audit, payment_status=payment_status
        )
        if failure_reasons:
            return ReputationDecision(
                kind=DecisionKind.PUBLISH,
                value=PUBLISH_FAILURE_VALUE,
                reason_codes=failure_reasons,
                audit_bundle_hash=audit.audit_bundle_hash,
                ruleset_version=audit.ruleset_version,
                seller_agent_id=attribution.seller_agent_id,
                erc8004_agent_id=attribution.erc8004_agent_id,
            )

        if not attribution.delivered_as_quoted:
            return self._defer(
                ReputationReason.ATTRIBUTION_INSUFFICIENT,
                audit=audit,
                attribution=attribution,
            )

        # Only buyer selection / explanation findings remain, and semantic advisories never
        # lower a seller score, so the seller kept its side of the signed quote.
        reasons: list[ReputationReason] = [ReputationReason.SELLER_DELIVERED_AS_QUOTED]
        if audit.findings:
            reasons.append(ReputationReason.BUYER_ATTRIBUTED_FINDINGS_ONLY)
        return ReputationDecision(
            kind=DecisionKind.PUBLISH,
            value=PUBLISH_SUCCESS_VALUE,
            reason_codes=tuple(reasons),
            audit_bundle_hash=audit.audit_bundle_hash,
            ruleset_version=audit.ruleset_version,
            seller_agent_id=attribution.seller_agent_id,
            erc8004_agent_id=attribution.erc8004_agent_id,
        )

    def _deferral_reason(
        self,
        *,
        audit: AuditReport,
        payment_status: PaymentStatus,
        attribution: SellerAttribution,
        conflicting_proof: bool,
    ) -> ReputationReason | None:
        if conflicting_proof:
            return ReputationReason.CONFLICTING_PROOF
        if payment_status is PaymentStatus.PAYMENT_CONFIRMATION_UNKNOWN:
            return ReputationReason.PAYMENT_CONFIRMATION_UNKNOWN
        if payment_status in (
            PaymentStatus.PAYMENT_NOT_STARTED,
            PaymentStatus.PAYMENT_PENDING,
        ):
            return ReputationReason.AUDIT_NOT_TERMINAL
        if not attribution.identity_verified:
            return ReputationReason.SELLER_IDENTITY_UNKNOWN
        if audit.ruleset_version != RULESET_VERSION:
            return ReputationReason.AUDIT_NOT_TERMINAL
        return None

    def _failure_reasons(
        self,
        *,
        audit: AuditReport,
        payment_status: PaymentStatus,
    ) -> tuple[ReputationReason, ...]:
        """Keyed off the append-only projection, never off the mutable intent document.

        The payment status is derived from the event chain, so a purchase whose intent
        document is missing or lagging still yields the same objective reason codes.
        """
        reasons: list[ReputationReason] = []
        status_reason = {
            PaymentStatus.PAYMENT_MISMATCH_CONFIRMED: (
                ReputationReason.PAYMENT_MISMATCH_CONFIRMED
            ),
            PaymentStatus.RECONCILED_NO_TRANSFER: (
                ReputationReason.PAYMENT_RECONCILED_NO_TRANSFER
            ),
            PaymentStatus.PAYMENT_FAILED: (
                ReputationReason.SELLER_ATTRIBUTED_PAYMENT_FAILURE
            ),
        }.get(payment_status)
        if status_reason is not None:
            reasons.append(status_reason)
        deterministic = {
            finding.rule_id
            for finding in audit.findings
            if finding.authority is AuditAuthority.DETERMINISTIC
        }
        if deterministic & _DELIVERY_INTEGRITY_RULES:
            reasons.append(ReputationReason.DELIVERY_INTEGRITY_FAILURE)
        if not reasons and deterministic & _SELLER_ATTRIBUTED_RULES:
            reasons.append(ReputationReason.SELLER_ATTRIBUTED_PAYMENT_FAILURE)
        # Deduplicate while keeping the deterministic order the reasons were derived in.
        return tuple(dict.fromkeys(reasons))

    def _defer(
        self,
        reason: ReputationReason,
        *,
        audit: AuditReport,
        attribution: SellerAttribution,
    ) -> ReputationDecision:
        return ReputationDecision(
            kind=DecisionKind.DEFER,
            reason_codes=(reason,),
            audit_bundle_hash=audit.audit_bundle_hash,
            ruleset_version=audit.ruleset_version,
            seller_agent_id=attribution.seller_agent_id,
            erc8004_agent_id=attribution.erc8004_agent_id,
        )


def semantic_only(audit: AuditReport) -> bool:
    """True when every finding is a semantic advisory, which never lowers a seller score."""
    return bool(audit.findings) and all(
        finding.authority is AuditAuthority.SEMANTIC_ADVISORY
        for finding in audit.findings
    )


def audit_severity_allows_publish(audit: AuditReport) -> bool:
    """`P6-AC-03.6`/`P6-AC-05.2`: a semantic-only warning cannot make a seller score 0."""
    return audit.severity is not AuditSeverity.RISK or not semantic_only(audit)


@dataclass(frozen=True, slots=True)
class ReputationPublishJob:
    """One durable outbox job. `status` is operational; the evidence is in the chain."""

    job_id: str
    identity: PublishIdentity
    identity_hash: str
    payload_fingerprint: str
    decision: ReputationDecision
    status: OutboxStatus
    created_at: datetime
    updated_at: datetime
    attempt_count: int = 0
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    feedback_hash: str | None = None
    client_address: str | None = None
    feedback_uri: str | None = None
    transaction_ref: TransactionRef | None = None
    receipt_proof_ref: str | None = None
    block_number: int | None = None
    log_index: int | None = None
    evidence_source: EvidenceSource | None = None
    confirmed_proof: ConfirmedFeedbackProof | None = None

    def __post_init__(self) -> None:
        if self.identity_hash != self.identity.identity_hash:
            raise ValueError("outbox job identity hash does not describe its identity")
        if self.payload_fingerprint != self.decision.payload_fingerprint(self.identity):
            raise ValueError("outbox job fingerprint does not describe its decision")
        if self.attempt_count < 0:
            raise ValueError("outbox attempt count cannot be negative")
        if self.client_address is not None and (
            self.client_address != self.client_address.lower()
        ):
            raise ValueError("outbox job clientAddress must be lowercase")
        if self.status is OutboxStatus.PREPARED and (
            self.feedback_hash is None
            or self.client_address is None
            or self.feedback_uri is None
        ):
            # PREPARED is the durable record of an *intent to submit*. An EVM transaction
            # hash only exists after the submission, so what is frozen here is the full
            # payload commitment - hash, client and feedback URI - and the reference is
            # bound exactly once later.
            raise ValueError(
                "a prepared job requires the committed feedback hash, client and URI"
            )
        if self.status is OutboxStatus.SUBMITTED_UNKNOWN and self.feedback_hash is None:
            # A submission whose outcome is unknown still names what was committed. The
            # transaction reference stays absent when the broadcast can not be named:
            # that is the fail-closed reconciliation state, never a licence to resubmit.
            raise ValueError(
                "an unknown submission requires the committed feedback hash"
            )
        if self.status is OutboxStatus.CONFIRMED:
            if (
                self.receipt_proof_ref is None
                or self.transaction_ref is None
                or self.confirmed_proof is None
                or self.block_number is None
                or self.log_index is None
                or self.evidence_source is None
            ):
                raise ValueError(
                    "a confirmed job requires its full receipt and feedback-event proof"
                )
            if self.confirmed_proof.receipt_proof_ref != self.receipt_proof_ref:
                raise ValueError("a confirmed job must keep the proof it accepted")

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_OUTBOX_STATUSES

    def lease_held_at(self, now: datetime) -> bool:
        return self.lease_expires_at is not None and self.lease_expires_at > now


@dataclass(frozen=True, slots=True)
class RawFeedbackEvent:
    """One trusted on-chain feedback event, kept exactly as it was read."""

    value: int
    value_decimals: int
    client_address: str
    block_number: int
    log_index: int
    transaction_ref: TransactionRef
    tag1: str = FEEDBACK_TAG1
    tag2: str = FEEDBACK_TAG2

    def __post_init__(self) -> None:
        if self.value_decimals < 0:
            raise ValueError("feedback valueDecimals cannot be negative")
        if self.block_number < 0 or self.log_index < 0:
            raise ValueError("feedback coordinates cannot be negative")
        if self.client_address != self.client_address.lower():
            raise ValueError("feedback clientAddress must be lowercase")

    @property
    def normalized_value(self) -> float:
        """The 0..100 score this event carries once its decimals are applied."""
        return float(self.value) / float(10**self.value_decimals)

    def to_payload(self) -> JsonObject:
        return {
            "blockNumber": self.block_number,
            "clientAddress": self.client_address,
            "logIndex": self.log_index,
            "tag1": self.tag1,
            "tag2": self.tag2,
            "transactionRef": self.transaction_ref.to_payload(),
            "value": self.value,
            "valueDecimals": self.value_decimals,
        }


@dataclass(frozen=True, slots=True)
class ReputationQueryScope:
    """Exactly what was asked of the registry, so a snapshot can be re-derived."""

    chain_id: int
    registry_address: str
    erc8004_agent_id: str
    trusted_clients: tuple[str, ...]
    from_block: int
    to_block: int
    tag1: str = FEEDBACK_TAG1
    tag2: str = FEEDBACK_TAG2

    def __post_init__(self) -> None:
        if not self.trusted_clients:
            raise ValueError("a reputation query requires a trusted client allow-list")
        if any(client != client.lower() for client in self.trusted_clients):
            raise ValueError("trusted clients must be lowercase")
        if self.from_block < 0 or self.to_block < self.from_block:
            raise ValueError("reputation query block range is malformed")
        if self.registry_address != self.registry_address.lower():
            raise ValueError("reputation query registryAddress must be lowercase")

    def to_payload(self) -> JsonObject:
        return {
            "chainId": self.chain_id,
            "erc8004AgentId": self.erc8004_agent_id,
            "fromBlock": self.from_block,
            "registryAddress": self.registry_address,
            "tag1": self.tag1,
            "tag2": self.tag2,
            "toBlock": self.to_block,
            "trustedClients": list(self.trusted_clients),
        }

    @property
    def query_fingerprint(self) -> str:
        """Identifies the question, not the window or the answer.

        `fromBlock`/`toBlock` are excluded because the window is derived from the chain
        head: the same question asked twice differs only by how far the chain has moved.
        Together with `toBlock` this is the unique key of one stored answer, and it is
        what a fallback compares so a snapshot of another agent, registry, chain, tag
        pair or client allow-list can never be reused.
        """
        return sha256_json(
            {
                key: value
                for key, value in self.to_payload().items()
                if key not in ("fromBlock", "toBlock")
            }
        )

    @property
    def window_size(self) -> int:
        return self.to_block - self.from_block + 1

    def contains_block(self, block_number: int) -> bool:
        return self.from_block <= block_number <= self.to_block

    def resolved_for_lookback(self, lookback_blocks: int) -> bool:
        """Design 18.9: a bounded window that actually ends at a resolved chain head."""
        if lookback_blocks < 1 or self.to_block < 1:
            return False
        return (
            self.window_size <= lookback_blocks
            and self.from_block == max(0, self.to_block - lookback_blocks + 1)
        )


@dataclass(frozen=True, slots=True)
class ReputationSnapshot:
    """`P6-AC-06.1`: an immutable answer plus the provenance to audit it."""

    snapshot_id: str
    seller_agent_id: str
    scope: ReputationQueryScope
    queried_at: datetime
    raw_values: tuple[RawFeedbackEvent, ...]
    derived_score: float
    freshness_status: FreshnessStatus
    evidence_source: EvidenceSource
    aggregation_method: str = AGGREGATION_METHOD
    freshness_age_seconds: int | None = None

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip():
            raise ValueError("a reputation snapshot requires an id")
        if not 0.0 <= self.derived_score <= 100.0:
            raise ValueError("derived reputation score must be within 0..100")
        if self.raw_values and self.freshness_status is FreshnessStatus.NO_EVIDENCE:
            raise ValueError("a snapshot with events cannot claim NO_EVIDENCE")
        if not self.raw_values and self.freshness_status is not FreshnessStatus.NO_EVIDENCE:
            raise ValueError("a snapshot without events must be NO_EVIDENCE")
        if any(
            event.client_address not in self.scope.trusted_clients
            for event in self.raw_values
        ):
            raise ValueError("a snapshot event came from an untrusted client")
        if any(
            event.tag1 != self.scope.tag1 or event.tag2 != self.scope.tag2
            for event in self.raw_values
        ):
            raise ValueError("a snapshot event does not match the queried tags")
        if self.evidence_source not in QUERYABLE_EVIDENCE_SOURCES:
            raise ValueError("a reputation snapshot is only a verified Base answer")
        for event in self.raw_values:
            if not self.scope.contains_block(event.block_number):
                raise ValueError("a snapshot event lies outside the queried block range")
            reference = event.transaction_ref
            if reference.evidence_source is not self.evidence_source:
                raise ValueError("a snapshot event carries a different evidence source")
            if not isinstance(reference, EvmTransactionRef):
                raise ValueError("a verified snapshot event requires a chain reference")
            if reference.chain_id != self.scope.chain_id:
                raise ValueError("a snapshot event was read from another chain")
            if (
                reference.block_number is not None
                and reference.block_number != event.block_number
            ):
                raise ValueError("a snapshot event contradicts its transaction block")
            if reference.log_index is not None and reference.log_index != event.log_index:
                raise ValueError("a snapshot event contradicts its transaction log index")

    @property
    def event_count(self) -> int:
        return len(self.raw_values)

    def to_payload(self) -> JsonObject:
        payload: JsonObject = {
            "aggregationMethod": self.aggregation_method,
            "derivedScore": self.derived_score,
            "eventCount": self.event_count,
            "evidenceSource": self.evidence_source.value,
            "freshness": {"status": self.freshness_status.value},
            "queriedAt": self.queried_at.isoformat(),
            "queryFingerprint": self.scope.query_fingerprint,
            "rawValues": [event.to_payload() for event in self.raw_values],
            "scope": self.scope.to_payload(),
            "sellerAgentId": self.seller_agent_id,
            "snapshotId": self.snapshot_id,
        }
        if self.freshness_age_seconds is not None:
            payload["freshness"]["ageSeconds"] = self.freshness_age_seconds
        return payload

    @property
    def snapshot_hash(self) -> str:
        return sha256_json(self.to_payload())


class ReputationAggregationPolicy:
    """`P6-AC-06.3`: decimals-normalized arithmetic mean, or an explicit neutral 50."""

    def __init__(self, *, freshness_window: timedelta = DEFAULT_FRESHNESS_WINDOW) -> None:
        self._freshness_window = freshness_window

    def aggregate(
        self,
        *,
        snapshot_id: str,
        seller_agent_id: str,
        scope: ReputationQueryScope,
        queried_at: datetime,
        events: tuple[RawFeedbackEvent, ...],
        evidence_source: EvidenceSource,
        now: datetime | None = None,
    ) -> ReputationSnapshot:
        matching = tuple(
            event
            for event in events
            if event.client_address in scope.trusted_clients
            and event.tag1 == scope.tag1
            and event.tag2 == scope.tag2
        )
        if not matching:
            return ReputationSnapshot(
                snapshot_id=snapshot_id,
                seller_agent_id=seller_agent_id,
                scope=scope,
                queried_at=queried_at,
                raw_values=(),
                derived_score=NEUTRAL_SCORE,
                freshness_status=FreshnessStatus.NO_EVIDENCE,
                evidence_source=evidence_source,
            )
        normalized = [event.normalized_value for event in matching]
        if any(value < 0.0 or value > 100.0 for value in normalized):
            raise PaymentEvidenceError("a normalized feedback value is outside 0..100")
        derived = round(sum(normalized) / len(normalized), 6)
        age = None if now is None else int(max(timedelta(), now - queried_at).total_seconds())
        status = FreshnessStatus.FRESH
        if now is not None and now - queried_at > self._freshness_window:
            status = FreshnessStatus.STALE
        return ReputationSnapshot(
            snapshot_id=snapshot_id,
            seller_agent_id=seller_agent_id,
            scope=scope,
            queried_at=queried_at,
            raw_values=matching,
            derived_score=derived,
            freshness_status=status,
            evidence_source=evidence_source,
            freshness_age_seconds=age,
        )

    def stale_or_neutral(
        self,
        *,
        snapshot: ReputationSnapshot | None,
        now: datetime,
        snapshot_id: str,
        seller_agent_id: str,
        scope: ReputationQueryScope,
        evidence_source: EvidenceSource,
    ) -> ReputationSnapshot:
        """Design 18.9: a failed query reuses a fresh-enough confirmed snapshot as STALE.

        Reuse requires the stored snapshot to answer **exactly this question**: the same
        seller agent and the same query fingerprint - chain, registry, ERC-8004 agent,
        trusted-client allow-list and tag pair. A snapshot of another agent or another
        allow-list is not this agent's reputation, so it is `NO_EVIDENCE/50`. Anything
        older than the window is also `NO_EVIDENCE/50`. A query failure never becomes a
        favourable score.
        """
        reusable = (
            snapshot is not None
            and snapshot.seller_agent_id == seller_agent_id
            and snapshot.scope.query_fingerprint == scope.query_fingerprint
            and now - snapshot.queried_at <= self._freshness_window
        )
        if snapshot is not None and reusable:
            return ReputationSnapshot(
                snapshot_id=snapshot.snapshot_id,
                seller_agent_id=snapshot.seller_agent_id,
                scope=snapshot.scope,
                queried_at=snapshot.queried_at,
                raw_values=snapshot.raw_values,
                derived_score=snapshot.derived_score,
                freshness_status=(
                    FreshnessStatus.NO_EVIDENCE
                    if not snapshot.raw_values
                    else FreshnessStatus.STALE
                ),
                evidence_source=snapshot.evidence_source,
                freshness_age_seconds=int(
                    max(timedelta(), now - snapshot.queried_at).total_seconds()
                ),
            )
        return ReputationSnapshot(
            snapshot_id=snapshot_id,
            seller_agent_id=seller_agent_id,
            scope=scope,
            queried_at=now,
            raw_values=(),
            derived_score=NEUTRAL_SCORE,
            freshness_status=FreshnessStatus.NO_EVIDENCE,
            evidence_source=evidence_source,
        )


@dataclass(frozen=True, slots=True)
class ConfirmedFeedbackProof:
    """`P6-AC-05.5`: a receipt *and* a matching feedback event, never a bare tx hash."""

    transaction_ref: TransactionRef
    receipt_proof_ref: str
    registry_address: str
    client_address: str
    erc8004_agent_id: str
    value: int
    value_decimals: int
    feedback_hash: str
    block_number: int
    log_index: int
    tag1: str = FEEDBACK_TAG1
    tag2: str = FEEDBACK_TAG2
    feedback_uri: str = ""

    def __post_init__(self) -> None:
        if not self.receipt_proof_ref.strip():
            raise ValueError("a confirmed feedback proof requires a receipt proof reference")
        if not self.feedback_hash.strip():
            raise ValueError("a confirmed feedback proof requires the feedback hash")
        if self.block_number < 0 or self.log_index < 0:
            raise ValueError("confirmed feedback coordinates cannot be negative")
        if self.value not in (PUBLISH_SUCCESS_VALUE, PUBLISH_FAILURE_VALUE):
            raise ValueError("confirmed objective feedback is exactly 100 or 0")
        if self.value_decimals != 0:
            raise ValueError("objective feedback valueDecimals must be 0")
        if self.client_address != self.client_address.lower():
            raise ValueError("confirmed feedback clientAddress must be lowercase")
        if self.registry_address != self.registry_address.lower():
            raise ValueError("confirmed feedback registryAddress must be lowercase")
        if not self.registry_address.strip():
            raise ValueError("a confirmed feedback proof requires the registry it read")
        reference = self.transaction_ref
        if isinstance(reference, EvmTransactionRef):
            # The decoded event coordinates and the reference that carries them are one
            # observation. Two different answers in one proof is not a proof.
            if (
                reference.block_number is not None
                and reference.block_number != self.block_number
            ):
                raise ValueError("confirmed feedback contradicts its transaction block")
            if reference.log_index is not None and reference.log_index != self.log_index:
                raise ValueError("confirmed feedback contradicts its transaction log index")

    def to_payload(self) -> JsonObject:
        return {
            "blockNumber": self.block_number,
            "clientAddress": self.client_address,
            "erc8004AgentId": self.erc8004_agent_id,
            "feedbackHash": self.feedback_hash,
            "feedbackUri": self.feedback_uri,
            "logIndex": self.log_index,
            "receiptProofRef": self.receipt_proof_ref,
            "registryAddress": self.registry_address,
            "tag1": self.tag1,
            "tag2": self.tag2,
            "transactionRef": self.transaction_ref.to_payload(),
            "value": self.value,
            "valueDecimals": self.value_decimals,
        }

    @property
    def proof_hash(self) -> str:
        return sha256_json(self.to_payload())


def submission_identity(reference: TransactionRef) -> tuple[str, ...]:
    """The part of a reference that a submission irrevocably commits to.

    Confirmation legitimately discovers `blockNumber`/`logIndex`, so those are excluded.
    The evidence source and chain are **not** discovered: they are chosen when the
    submission is made, so reclassifying a Base submission as historical - or moving it
    to another chain - is a different submission, never the same one confirmed.
    """
    if isinstance(reference, EvmTransactionRef):
        return (
            "EVM",
            reference.hash,
            reference.evidence_source.value,
            str(reference.chain_id),
        )
    return ("LOCAL", f"{reference.run_id}:{reference.id}", reference.evidence_source.value)


def assert_prepared_commitment(
    *,
    job: ReputationPublishJob,
    feedback_hash: str,
    client_address: str,
    feedback_uri: str,
) -> None:
    """`P6-AC-05.4`: what `PREPARED` freezes can never be re-prepared differently.

    The commitment is the feedback hash, the client that will sign it and the feedback
    URI, which must be the decided audit bundle. A second prepare with any other value
    is a conflict, not a new attempt.
    """
    if client_address != client_address.lower():
        raise PaymentEvidenceError("prepared clientAddress must be lowercase")
    if feedback_uri != job.decision.audit_bundle_hash:
        raise PaymentEvidenceError(
            "a prepared submission must commit to the decided audit bundle"
        )
    if job.status is not OutboxStatus.PREPARED:
        return
    for name, existing, requested in (
        ("feedback hash", job.feedback_hash, feedback_hash),
        ("client", job.client_address, client_address),
        ("feedback URI", job.feedback_uri, feedback_uri),
    ):
        if existing is not None and existing != requested:
            raise PaymentConflictError(
                f"reputation job is already prepared with another {name}"
            )


def assert_proof_matches_job(
    *, job: ReputationPublishJob, proof: ConfirmedFeedbackProof
) -> None:
    """`P6-AC-05.5`: the proof must match the entire commitment this job already made.

    Identity, fingerprint-bound decision, registry, chain, evidence source, prepared
    feedback hash, prepared client, the audit-bundle feedback URI, the bound submission
    reference and any already-known event coordinates all have to agree. Anything less
    lets a second, differently-shaped external effect be recorded as this job's outcome.
    """
    if proof.erc8004_agent_id != job.decision.erc8004_agent_id:
        raise PaymentEvidenceError("confirmed feedback belongs to another agent")
    if proof.tag1 != job.identity.tag1 or proof.tag2 != job.identity.tag2:
        raise PaymentEvidenceError("confirmed feedback carries different tags")
    if job.decision.value is None or proof.value != job.decision.value:
        raise PaymentEvidenceError("confirmed feedback value is not the decided value")
    if proof.registry_address != job.identity.registry_address:
        raise PaymentEvidenceError("confirmed feedback was read from another registry")
    if proof.feedback_uri != job.decision.audit_bundle_hash:
        raise PaymentEvidenceError(
            "confirmed feedback does not commit to this audit bundle"
        )
    reference = proof.transaction_ref
    if isinstance(reference, EvmTransactionRef):
        if reference.chain_id != job.identity.chain_id:
            raise PaymentEvidenceError("confirmed feedback is on another chain")
        if reference.evidence_source not in QUERYABLE_EVIDENCE_SOURCES | {
            EvidenceSource.HISTORICAL_ON_CHAIN
        }:
            raise PaymentEvidenceError("confirmed feedback carries an unusable source")
    if job.feedback_hash is not None and proof.feedback_hash != job.feedback_hash:
        raise PaymentEvidenceError(
            "confirmed feedback is not the payload this job prepared"
        )
    if job.client_address is not None and proof.client_address != job.client_address:
        raise PaymentEvidenceError("confirmed feedback was written by another client")
    if job.feedback_uri is not None and proof.feedback_uri != job.feedback_uri:
        raise PaymentEvidenceError("confirmed feedback changed the prepared feedback URI")
    if job.transaction_ref is not None and (
        submission_identity(job.transaction_ref) != submission_identity(proof.transaction_ref)
    ):
        raise PaymentEvidenceError(
            "confirmed feedback is not the submitted transaction of this job"
        )
    if job.block_number is not None and proof.block_number != job.block_number:
        raise PaymentEvidenceError("confirmed feedback moved to another block")
    if job.log_index is not None and proof.log_index != job.log_index:
        raise PaymentEvidenceError("confirmed feedback moved to another log index")


def reputation_recorded_payload(
    *, job: ReputationPublishJob, proof: ConfirmedFeedbackProof
) -> JsonObject:
    """Design 18.6.1: the extended `REPUTATION_RECORDED` payload.

    The legacy keys stay exactly where the existing parser reads them, so historical
    rows and new rows are read by one reader without a backfill. `transactionHash` is
    present only for a chain reference: a synthetic run has no chain hash and must never
    be rendered as one.
    """
    payload: JsonObject = {
        "blockNumber": proof.block_number,
        "chainId": job.identity.chain_id,
        "clientAddress": proof.client_address,
        "erc8004AgentId": proof.erc8004_agent_id,
        "evidenceSource": proof.transaction_ref.evidence_source.value,
        "feedbackHash": proof.feedback_hash,
        "feedbackUri": proof.feedback_uri,
        "logIndex": proof.log_index,
        "objectiveValue": proof.value,
        "publishIdentityHash": job.identity_hash,
        "receiptProofRef": proof.receipt_proof_ref,
        "registryAddress": proof.registry_address,
        "tag1": proof.tag1,
        "tag2": proof.tag2,
        "transactionRef": proof.transaction_ref.to_payload(),
        "value": proof.value,
        "valueDecimals": proof.value_decimals,
    }
    reference = proof.transaction_ref
    if isinstance(reference, EvmTransactionRef):
        payload["transactionHash"] = reference.hash
    return payload


def publication_conflict_payload(
    *,
    identity_hash: str,
    existing_fingerprint: str,
    requested_fingerprint: str,
    reason_code: str,
) -> JsonObject:
    """Design 18.6.1: the append-only `REPUTATION_PUBLICATION_CONFLICT` payload."""
    if not reason_code.strip():
        raise ValueError("a publication conflict requires a reason code")
    return {
        "existingFingerprint": existing_fingerprint,
        "publishIdentityHash": identity_hash,
        "reasonCode": reason_code,
        "requestedFingerprint": requested_fingerprint,
    }


def conflict_is_identical(payload: JsonObject, candidate: JsonObject) -> bool:
    """Same identity, same requested fingerprint, same reason: one conflict, recorded once."""
    keys = ("publishIdentityHash", "requestedFingerprint", "reasonCode")
    return all(payload.get(key) == candidate.get(key) for key in keys)


def proof_from_payload(value: object) -> ConfirmedFeedbackProof:
    """Rebuilds a stored proof. It never writes and never invents a missing field."""
    if not isinstance(value, dict):
        raise ValueError("stored confirmed feedback proof is malformed")
    try:
        return ConfirmedFeedbackProof(
            transaction_ref=transaction_ref_from_payload(value["transactionRef"]),
            receipt_proof_ref=str(value["receiptProofRef"]),
            registry_address=str(value["registryAddress"]),
            client_address=str(value["clientAddress"]),
            erc8004_agent_id=str(value["erc8004AgentId"]),
            value=int(value["value"]),
            value_decimals=int(value["valueDecimals"]),
            feedback_hash=str(value["feedbackHash"]),
            block_number=int(value["blockNumber"]),
            log_index=int(value["logIndex"]),
            tag1=str(value["tag1"]),
            tag2=str(value["tag2"]),
            feedback_uri=str(value["feedbackUri"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("stored confirmed feedback proof is malformed") from exc


@dataclass(frozen=True, slots=True)
class TerminalReputationState:
    """What the read models need to explain a purchase's reputation position."""

    decision: ReputationDecision | None = None
    identity: PublishIdentity | None = None
    job_status: OutboxStatus | None = None
    conflicts: tuple[JsonObject, ...] = field(default_factory=tuple)


def decision_from_event(event: EvidenceEvent) -> ReputationDecision:
    """Parses a persisted `REPUTATION_DECIDED` payload. It never writes."""
    if event.type is not EventType.REPUTATION_DECIDED:
        raise ValueError("event is not a reputation decision")
    payload = event.payload
    try:
        kind = DecisionKind(str(payload["decision"]))
        reasons = tuple(
            ReputationReason(str(item)) for item in list(payload["reasonCodes"])
        )
        raw_value = payload.get("value")
        return ReputationDecision(
            kind=kind,
            value=None if raw_value is None else int(raw_value),
            reason_codes=reasons,
            audit_bundle_hash=str(payload["auditBundleHash"]),
            ruleset_version=str(payload["rulesetVersion"]),
            seller_agent_id=str(payload["sellerAgentId"]),
            erc8004_agent_id=str(payload["erc8004AgentId"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("stored reputation decision is malformed") from exc
