from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from buyer_audit_api.core.errors import (
    PaymentConflictError,
    PaymentEvidenceError,
    PaymentPolicyError,
)
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.hashing import sha256_bytes
from buyer_audit_api.core.models import (
    TERMINAL_PAYMENT_EVENT_TYPES,
    EventType,
    EvidenceEvent,
    EvidenceHead,
    EvidenceSource,
    JsonObject,
    ScenarioMetadata,
    TransactionRef,
    WalletBinding,
)
from buyer_audit_api.core.ports import Clock

_AUXILIARY_EVENT_TYPES = {
    EventType.DELIVERY_STAGED,
    EventType.SENSITIVE_PAYLOAD_ACCESSED,
    EventType.CORRECTION_RECORDED,
    EventType.PAYMENT_RECONCILIATION_CHECKED,
    EventType.PAYMENT_ATTEMPT_REJECTED,
}


class PaymentIntentState(StrEnum):
    CLAIMED = "CLAIMED"
    AUTHORIZED = "AUTHORIZED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    MISMATCH_CONFIRMED = "MISMATCH_CONFIRMED"
    RECONCILED_NO_TRANSFER = "RECONCILED_NO_TRANSFER"


TERMINAL_PAYMENT_INTENT_STATES: frozenset[PaymentIntentState] = frozenset(
    {
        PaymentIntentState.SETTLED,
        PaymentIntentState.FAILED,
        PaymentIntentState.MISMATCH_CONFIRMED,
        PaymentIntentState.RECONCILED_NO_TRANSFER,
    }
)

ReservationAction = Literal["hold", "settle", "release", "replace_with_actual"]


class ReconciliationVerifierOutcome(StrEnum):
    """Bounded reconciliation outcomes produced by an independent proof verifier."""

    RECEIPT_NOT_FOUND = "RECEIPT_NOT_FOUND"
    RECEIPT_PENDING_FINALITY = "RECEIPT_PENDING_FINALITY"
    RECEIPT_HASH_MISMATCH = "RECEIPT_HASH_MISMATCH"
    AMBIGUOUS_TRANSFER_EVIDENCE = "AMBIGUOUS_TRANSFER_EVIDENCE"
    SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER = "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER"
    AUTHORIZATION_UNUSED_AFTER_EXPIRY = "AUTHORIZATION_UNUSED_AFTER_EXPIRY"
    MISMATCHED_TRANSFER_CONFIRMED = "MISMATCHED_TRANSFER_CONFIRMED"
    RECEIPT_REVERTED = "RECEIPT_REVERTED"
    EXACT_TRANSFER_CONFIRMED = "EXACT_TRANSFER_CONFIRMED"


NO_TRANSFER_VERIFIER_OUTCOMES: frozenset[ReconciliationVerifierOutcome] = frozenset(
    {
        ReconciliationVerifierOutcome.SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER,
        ReconciliationVerifierOutcome.AUTHORIZATION_UNUSED_AFTER_EXPIRY,
    }
)


@dataclass(frozen=True, slots=True)
class ReconciliationPolicy:
    """Bounded retry/finality policy; a momentary absence never closes a payment."""

    minimum_attempts: int = 3
    minimum_finality_confirmations: int = 2

    def __post_init__(self) -> None:
        if self.minimum_attempts < 1 or self.minimum_finality_confirmations < 1:
            raise ValueError("reconciliation policy bounds must be positive")


def terminal_outcome_key(purchase_id: str) -> str:
    """One terminal outcome per purchase, shared by all four terminal event types."""
    return f"terminal:{purchase_id}"


@dataclass(frozen=True, slots=True)
class WalletPolicy:
    buyer_wallet_address: str
    policy_date: str
    token: str
    per_transaction_limit_units: int
    daily_limit_units: int
    spent_units: int = 0
    reserved_units: int = 0

    def __post_init__(self) -> None:
        values = (
            self.per_transaction_limit_units,
            self.daily_limit_units,
            self.spent_units,
            self.reserved_units,
        )
        if any(isinstance(value, bool) or value < 0 for value in values):
            raise ValueError("wallet policy units must be non-negative integers")
        if self.per_transaction_limit_units > self.daily_limit_units:
            raise ValueError("per-transaction limit exceeds daily limit")
        # A confirmed mismatch can push actual spend past the configured daily limit.
        # That recorded fact must stay readable; the claim guard rejects further payments.


@dataclass(frozen=True, slots=True)
class PaymentQuoteView:
    quote_id: str
    seller_agent_id: str
    erc8004_agent_id: str
    provider_id: str
    model_id: str
    model_version: str
    amount_units: int
    token: str
    pay_to: str
    expires_at: datetime
    signer_address: str
    chain_id: int
    verifying_contract: str


@dataclass(frozen=True, slots=True)
class PaymentView:
    purchase_id: str
    owner_address: str
    buyer_wallet_address: str
    budget_units: int
    request_policy: JsonObject
    decision_event_hash: str
    quote: PaymentQuoteView
    event_count: int
    head_event_hash: str


@dataclass(frozen=True, slots=True)
class ActualTransfer:
    """A verified buyer outflow, whether authoritative on-chain or attested synthetic."""

    amount_units: int
    token: str
    from_address: str
    to_address: str

    def __post_init__(self) -> None:
        if isinstance(self.amount_units, bool) or self.amount_units <= 0:
            raise ValueError("an actual transfer must move a positive amount")
        for field, value in (
            ("token", self.token),
            ("from", self.from_address),
            ("to", self.to_address),
        ):
            if not value.strip():
                raise ValueError(f"actual transfer {field} is required")

    def to_payload(self) -> JsonObject:
        return {
            "amountUnits": self.amount_units,
            "from": self.from_address.lower(),
            "to": self.to_address.lower(),
            "token": self.token.lower(),
        }


@dataclass(frozen=True, slots=True)
class ReconciliationCheck:
    """One append-only bounded reconciliation attempt; it never changes payment state."""

    attempt_number: int
    checked_at: datetime
    checked_chain_id: int
    submission_ref: str
    verifier_outcome: ReconciliationVerifierOutcome
    finality_confirmations: int
    proof_ref: str
    evidence_source: EvidenceSource
    receipt_status: int | None = None
    block_number: int | None = None
    authorization_state: str | None = None
    scenario: ScenarioMetadata | None = None

    def __post_init__(self) -> None:
        if isinstance(self.attempt_number, bool) or self.attempt_number < 1:
            raise ValueError("reconciliation attempt numbers start at 1")
        if self.finality_confirmations < 0:
            raise ValueError("finality confirmations must be non-negative")
        if self.checked_at.tzinfo is None:
            raise ValueError("reconciliation check time must include a timezone")
        if not self.submission_ref.strip() or not self.proof_ref.strip():
            raise ValueError("reconciliation check requires submission and proof references")

    def to_payload(self) -> JsonObject:
        payload: JsonObject = {
            "attemptNumber": self.attempt_number,
            "checkedAt": self.checked_at.isoformat(),
            "checkedChainId": self.checked_chain_id,
            "evidenceSource": self.evidence_source.value,
            "finalityConfirmations": self.finality_confirmations,
            "proofRef": self.proof_ref,
            "submissionRef": self.submission_ref,
            "verifierOutcome": self.verifier_outcome.value,
        }
        if self.receipt_status is not None:
            payload["receiptStatus"] = self.receipt_status
        if self.block_number is not None:
            payload["blockNumber"] = self.block_number
        if self.authorization_state is not None:
            payload["authorizationState"] = self.authorization_state
        if self.scenario is not None:
            payload["scenario"] = self.scenario.to_payload()
        return payload


@dataclass(frozen=True, slots=True)
class ConfirmedMismatchProof:
    """Proof that an actual outflow happened but disagreed with the signed quote."""

    actual_transfer: ActualTransfer
    transaction_ref: TransactionRef
    proof_ref: str
    evidence_source: EvidenceSource
    scenario: ScenarioMetadata | None = None

    def __post_init__(self) -> None:
        if not self.proof_ref.strip():
            raise ValueError("a confirmed mismatch requires a proof reference")
        if (
            self.evidence_source is EvidenceSource.SYNTHETIC_LOCAL
        ) is not (self.transaction_ref.kind == "LOCAL"):
            raise ValueError("mismatch proof source and transaction variant disagree")


@dataclass(frozen=True, slots=True)
class NoTransferProof:
    """Proof that bounded reconciliation ended with no transfer at all."""

    reason_code: str
    checked_chain_id: int
    attempt_count: int
    first_checked_at: datetime
    last_checked_at: datetime
    authorization_nonce_hash: str
    finality_evidence: JsonObject
    proof_ref: str
    evidence_source: EvidenceSource
    submission_ref: str | None = None
    scenario: ScenarioMetadata | None = None

    def __post_init__(self) -> None:
        if not self.reason_code.strip() or not self.proof_ref.strip():
            raise ValueError("a no-transfer proof requires a reason code and proof reference")
        if isinstance(self.attempt_count, bool) or self.attempt_count < 1:
            raise ValueError("a no-transfer proof requires at least one bounded check")
        if self.last_checked_at < self.first_checked_at:
            raise ValueError("no-transfer check window is reversed")
        if not self.authorization_nonce_hash.startswith("sha256:"):
            raise ValueError("authorization nonce must be recorded as a hash")


@dataclass(frozen=True, slots=True)
class ConfirmedOutflow:
    """Immutable record of an actual outflow that a wallet policy cannot express."""

    terminal_outcome_key: str
    purchase_id: str
    buyer_wallet: str
    policy_date: str
    token: str
    amount_units: int
    recipient: str
    transaction_ref: TransactionRef
    proof_ref: str

@dataclass(frozen=True, slots=True)
class PaymentIntent:
    purchase_id: str
    buyer_wallet_address: str
    policy_date: str
    quote_id: str
    decision_event_hash: str
    amount_units: int
    token: str
    pay_to: str
    permit2_nonce: str | None
    state: PaymentIntentState
    claimed_at: datetime
    transfer_method: Literal["permit2", "eip3009"] = "eip3009"
    authorization_nonce: str | None = None
    decision_authorization_hash: str | None = None
    decision_authorization_signature: str | None = None
    authorized_at: datetime | None = None
    reconciliation_reason: str | None = None
    transaction_hash: str | None = None
    block_number: int | None = None
    transfer_log_index: int | None = None
    settlement_verified_at: datetime | None = None
    failure_reason: str | None = None
    failed_at: datetime | None = None
    reconciliation_attempt_count: int = 0
    reconciliation_first_checked_at: datetime | None = None
    reconciliation_last_checked_at: datetime | None = None
    reconciliation_last_outcome: ReconciliationVerifierOutcome | None = None
    actual_transfer: ActualTransfer | None = None
    mismatched_fields: tuple[str, ...] = ()
    terminal_outcome_key: str | None = None
    terminal_proof_ref: str | None = None
    terminal_evidence_source: EvidenceSource | None = None
    local_transaction_id: str | None = None
    no_transfer_reason_code: str | None = None


class PaymentRepository(Protocol):
    async def list_events(self, purchase_id: str) -> list[EvidenceEvent]: ...

    async def get_event_head(self, purchase_id: str) -> EvidenceHead | None: ...

    async def get_wallet_binding(self, owner_address: str) -> WalletBinding | None: ...

    async def put_wallet_policy(self, policy: WalletPolicy) -> None: ...

    async def get_wallet_policy(
        self, *, buyer_wallet_address: str, policy_date: str, token: str | None = None
    ) -> WalletPolicy | None: ...

    async def get_latest_wallet_policy(
        self, buyer_wallet_address: str, *, max_policy_date: str | None = None
    ) -> WalletPolicy | None: ...

    async def get_payment_intent(self, purchase_id: str) -> PaymentIntent | None: ...

    async def claim_payment_intent(
        self,
        *,
        intent: PaymentIntent,
        occurred_at: datetime,
        actor: JsonObject,
        payload: JsonObject,
        evidence_refs: tuple[str, ...],
        expected_event_count: int,
        expected_head_event_hash: str,
    ) -> PaymentIntent: ...

    async def transition_payment_intent(
        self,
        *,
        next_intent: PaymentIntent,
        expected_states: tuple[PaymentIntentState, ...],
        event_type: EventType,
        occurred_at: datetime,
        actor: JsonObject,
        payload: JsonObject,
        evidence_refs: tuple[str, ...],
        expected_event_count: int,
        expected_head_event_hash: str,
        reservation_action: ReservationAction,
        confirmed_outflow: ConfirmedOutflow | None = None,
    ) -> PaymentIntent: ...

    async def list_confirmed_outflows(self, purchase_id: str) -> list[ConfirmedOutflow]: ...


def _required_dict(value: object, *, field: str) -> JsonObject:
    if not isinstance(value, dict):
        raise PaymentEvidenceError(f"{field} is malformed")
    return value


def _required_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaymentEvidenceError(f"{field} is malformed")
    return value.strip()


def _required_units(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PaymentEvidenceError(f"{field} is malformed")
    return value


def _required_datetime(value: object, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PaymentEvidenceError(f"{field} is malformed") from exc
    else:
        raise PaymentEvidenceError(f"{field} is malformed")
    if parsed.tzinfo is None:
        raise PaymentEvidenceError(f"{field} must include a timezone")
    return parsed


def _normalized_transfer(transfer: ActualTransfer) -> ActualTransfer:
    return ActualTransfer(
        amount_units=transfer.amount_units,
        token=transfer.token.lower(),
        from_address=transfer.from_address.lower(),
        to_address=transfer.to_address.lower(),
    )


def _local_transaction_id(reference: TransactionRef) -> str | None:
    return reference.id if reference.kind == "LOCAL" else None


def mismatched_quote_fields(
    intent: PaymentIntent, actual: ActualTransfer
) -> tuple[str, ...]:
    """Exact quote fields contradicted by a verified actual transfer, in canonical order."""
    mismatched: list[str] = []
    if actual.amount_units != intent.amount_units:
        mismatched.append("amount")
    if actual.token.lower() != intent.token:
        mismatched.append("token")
    if actual.to_address.lower() != intent.pay_to:
        mismatched.append("recipient")
    return tuple(mismatched)


class PaymentService:
    def __init__(
        self,
        *,
        repository: PaymentRepository,
        clock: Clock,
        reconciliation_policy: ReconciliationPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._reconciliation_policy = reconciliation_policy or ReconciliationPolicy()

    async def configure_wallet_policy(self, policy: WalletPolicy) -> None:
        await self._repository.put_wallet_policy(policy)

    async def get_wallet_policy(
        self, *, buyer_wallet_address: str, policy_date: str, token: str | None = None
    ) -> WalletPolicy | None:
        return await self._repository.get_wallet_policy(
            buyer_wallet_address=buyer_wallet_address,
            policy_date=policy_date,
            token=token,
        )

    async def get_payment_intent(self, purchase_id: str) -> PaymentIntent | None:
        return await self._repository.get_payment_intent(purchase_id)

    async def get_latest_wallet_policy(self, buyer_wallet_address: str) -> WalletPolicy | None:
        return await self._repository.get_latest_wallet_policy(
            buyer_wallet_address,
            max_policy_date=self._clock.now().date().isoformat(),
        )

    async def _transition_context(self, purchase_id: str) -> tuple[PaymentIntent, int, str]:
        intent = await self._repository.get_payment_intent(purchase_id)
        if intent is None:
            raise PaymentEvidenceError("payment intent is missing")
        events = await self._repository.list_events(purchase_id)
        head = await self._repository.get_event_head(purchase_id)
        if not events or head is None:
            raise PaymentEvidenceError("payment evidence is missing")
        verify_event_chain(
            events,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )
        return intent, head.event_count, head.head_event_hash

    async def _verified_context(
        self, purchase_id: str
    ) -> tuple[PaymentIntent, list[EvidenceEvent], int, str]:
        intent = await self._repository.get_payment_intent(purchase_id)
        if intent is None:
            raise PaymentEvidenceError("payment intent is missing")
        events = await self._repository.list_events(purchase_id)
        head = await self._repository.get_event_head(purchase_id)
        if not events or head is None:
            raise PaymentEvidenceError("payment evidence is missing")
        verify_event_chain(
            events,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )
        return intent, events, head.event_count, head.head_event_hash

    async def authorize(
        self,
        *,
        purchase_id: str,
        authorization_hash: str,
        signature: str,
    ) -> PaymentIntent:
        if not authorization_hash or not signature:
            raise PaymentEvidenceError("decision authorization is incomplete")
        intent, event_count, head_hash = await self._transition_context(purchase_id)
        if intent.state == PaymentIntentState.AUTHORIZED:
            if (
                intent.decision_authorization_hash == authorization_hash
                and intent.decision_authorization_signature == signature
            ):
                return intent
            raise PaymentConflictError("payment has a different authorization")
        if intent.state != PaymentIntentState.CLAIMED:
            raise PaymentConflictError("payment cannot be authorized from current state")
        now = self._clock.now()
        next_intent = replace(
            intent,
            state=PaymentIntentState.AUTHORIZED,
            decision_authorization_hash=authorization_hash,
            decision_authorization_signature=signature,
            authorized_at=now,
        )
        return await self._repository.transition_payment_intent(
            next_intent=next_intent,
            expected_states=(PaymentIntentState.CLAIMED,),
            event_type=EventType.PAYMENT_AUTHORIZED,
            occurred_at=now,
            actor={"id": "payment-executor", "type": "service"},
            payload={
                "authorizationHash": authorization_hash,
                "authorizationNonce": intent.authorization_nonce,
                "decisionEventHash": intent.decision_event_hash,
                "quoteId": intent.quote_id,
                "signatureHash": sha256_bytes(signature.encode("utf-8")),
            },
            evidence_refs=(intent.decision_event_hash, authorization_hash),
            expected_event_count=event_count,
            expected_head_event_hash=head_hash,
            reservation_action="hold",
        )

    async def require_reconciliation(
        self,
        *,
        purchase_id: str,
        reason: str,
        transaction_hash: str | None = None,
    ) -> PaymentIntent:
        if not reason.strip():
            raise PaymentEvidenceError("reconciliation reason is required")
        normalized_transaction_hash = (
            transaction_hash.lower() if transaction_hash is not None else None
        )
        if normalized_transaction_hash is not None and (
            not normalized_transaction_hash.startswith("0x")
            or len(normalized_transaction_hash) != 66
        ):
            raise PaymentEvidenceError("transaction hash is malformed")
        intent, event_count, head_hash = await self._transition_context(purchase_id)
        normalized_reason = reason.strip()
        if intent.state == PaymentIntentState.RECONCILIATION_REQUIRED:
            if intent.transaction_hash == normalized_transaction_hash:
                return intent
            raise PaymentConflictError("payment has a different reconciliation reason")
        if intent.state != PaymentIntentState.AUTHORIZED:
            raise PaymentConflictError("payment cannot enter reconciliation")
        now = self._clock.now()
        return await self._repository.transition_payment_intent(
            next_intent=replace(
                intent,
                state=PaymentIntentState.RECONCILIATION_REQUIRED,
                reconciliation_reason=normalized_reason,
                transaction_hash=normalized_transaction_hash,
            ),
            expected_states=(PaymentIntentState.AUTHORIZED,),
            event_type=EventType.PAYMENT_RECONCILIATION_REQUIRED,
            occurred_at=now,
            actor={"id": "payment-executor", "type": "service"},
            payload={
                "authorizationHash": intent.decision_authorization_hash,
                "reason": normalized_reason,
                "transactionHash": normalized_transaction_hash,
            },
            evidence_refs=(intent.decision_authorization_hash or "",),
            expected_event_count=event_count,
            expected_head_event_hash=head_hash,
            reservation_action="hold",
        )

    async def bind_reconciliation_transaction(
        self,
        *,
        purchase_id: str,
        transaction_hash: str,
    ) -> PaymentIntent:
        normalized_transaction_hash = transaction_hash.lower()
        if (
            not normalized_transaction_hash.startswith("0x")
            or len(normalized_transaction_hash) != 66
        ):
            raise PaymentEvidenceError("transaction hash is malformed")
        intent, event_count, head_hash = await self._transition_context(purchase_id)
        if intent.state != PaymentIntentState.RECONCILIATION_REQUIRED:
            raise PaymentConflictError("payment is not awaiting reconciliation")
        if intent.transaction_hash == normalized_transaction_hash:
            return intent
        if intent.transaction_hash is not None:
            raise PaymentConflictError("payment already has a different transaction")
        now = self._clock.now()
        return await self._repository.transition_payment_intent(
            next_intent=replace(intent, transaction_hash=normalized_transaction_hash),
            expected_states=(PaymentIntentState.RECONCILIATION_REQUIRED,),
            event_type=EventType.PAYMENT_SUBMISSION_IDENTIFIED,
            occurred_at=now,
            actor={"id": "seller-execution-recovery", "type": "service"},
            payload={
                "authorizationHash": intent.decision_authorization_hash,
                "transactionHash": normalized_transaction_hash,
            },
            evidence_refs=(
                intent.decision_authorization_hash or intent.quote_id,
                normalized_transaction_hash,
            ),
            expected_event_count=event_count,
            expected_head_event_hash=head_hash,
            reservation_action="hold",
        )

    async def settle(
        self,
        *,
        purchase_id: str,
        transaction_hash: str,
        block_number: int,
        transfer_log_index: int,
        receipt_status: int,
        token: str,
        from_address: str,
        to_address: str,
        amount_units: int,
    ) -> PaymentIntent:
        intent, event_count, head_hash = await self._transition_context(purchase_id)
        normalized_proof = (
            transaction_hash.lower(),
            block_number,
            transfer_log_index,
        )
        if intent.state == PaymentIntentState.SETTLED:
            if (
                intent.transaction_hash,
                intent.block_number,
                intent.transfer_log_index,
            ) == normalized_proof:
                return intent
            raise PaymentConflictError("payment has a different settlement proof")
        if intent.state in TERMINAL_PAYMENT_INTENT_STATES:
            raise PaymentConflictError("payment already has a different terminal outcome")
        if intent.state != PaymentIntentState.RECONCILIATION_REQUIRED:
            raise PaymentConflictError("payment cannot settle from current state")
        if intent.transaction_hash != transaction_hash.lower():
            raise PaymentConflictError("settlement receipt is not bound to submitted payment")
        if receipt_status != 1:
            raise PaymentEvidenceError("receipt status is not successful")
        if block_number < 0 or transfer_log_index < 0:
            raise PaymentEvidenceError("receipt position is malformed")
        if token.lower() != intent.token:
            raise PaymentEvidenceError("receipt token contract mismatch")
        if from_address.lower() != intent.buyer_wallet_address:
            raise PaymentEvidenceError("receipt transfer sender mismatch")
        if to_address.lower() != intent.pay_to:
            raise PaymentEvidenceError("receipt transfer recipient mismatch")
        if amount_units != intent.amount_units:
            raise PaymentEvidenceError("receipt transfer amount mismatch")
        if not transaction_hash.startswith("0x") or len(transaction_hash) != 66:
            raise PaymentEvidenceError("transaction hash is malformed")
        now = self._clock.now()
        outcome_key = terminal_outcome_key(purchase_id)
        proof_ref = sha256_bytes(
            f"{transaction_hash.lower()}:{block_number}:{transfer_log_index}".encode()
        )
        next_intent = replace(
            intent,
            state=PaymentIntentState.SETTLED,
            transaction_hash=transaction_hash.lower(),
            block_number=block_number,
            transfer_log_index=transfer_log_index,
            settlement_verified_at=now,
            terminal_outcome_key=outcome_key,
            terminal_proof_ref=proof_ref,
            terminal_evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
        )
        return await self._repository.transition_payment_intent(
            next_intent=next_intent,
            expected_states=(PaymentIntentState.RECONCILIATION_REQUIRED,),
            event_type=EventType.PAYMENT_SETTLED,
            occurred_at=now,
            actor={"id": "independent-receipt-verifier", "type": "service"},
            payload={
                "amountUnits": amount_units,
                "blockNumber": block_number,
                "from": from_address.lower(),
                "receiptStatus": receipt_status,
                "terminalOutcomeKey": outcome_key,
                "to": to_address.lower(),
                "token": token.lower(),
                "transactionHash": transaction_hash.lower(),
                "transferLogIndex": transfer_log_index,
            },
            evidence_refs=(
                intent.decision_authorization_hash or "",
                transaction_hash.lower(),
            ),
            expected_event_count=event_count,
            expected_head_event_hash=head_hash,
            reservation_action="settle",
        )

    async def fail_confirmed(
        self,
        *,
        purchase_id: str,
        reason: str,
        transaction_hash: str,
        block_number: int,
        receipt_status: int,
    ) -> PaymentIntent:
        normalized_transaction_hash = transaction_hash.lower()
        if (
            not normalized_transaction_hash.startswith("0x")
            or len(normalized_transaction_hash) != 66
        ):
            raise PaymentEvidenceError("transaction hash is malformed")
        if block_number < 0 or receipt_status != 0:
            raise PaymentEvidenceError("failure requires a confirmed reverted receipt")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise PaymentEvidenceError("failure reason is required")
        intent, event_count, head_hash = await self._transition_context(purchase_id)
        if intent.state == PaymentIntentState.FAILED:
            if (
                intent.failure_reason == normalized_reason
                and intent.transaction_hash == normalized_transaction_hash
                and intent.block_number == block_number
            ):
                return intent
            raise PaymentConflictError("payment has a different failure reason")
        if intent.state in TERMINAL_PAYMENT_INTENT_STATES:
            raise PaymentConflictError("payment already has a different terminal outcome")
        if intent.state != PaymentIntentState.RECONCILIATION_REQUIRED:
            raise PaymentConflictError("payment cannot fail from current state")
        if intent.transaction_hash != normalized_transaction_hash:
            raise PaymentConflictError("reverted receipt is not bound to submitted payment")
        now = self._clock.now()
        outcome_key = terminal_outcome_key(purchase_id)
        proof_ref = sha256_bytes(
            f"{normalized_transaction_hash}:{block_number}:reverted".encode()
        )
        return await self._repository.transition_payment_intent(
            next_intent=replace(
                intent,
                state=PaymentIntentState.FAILED,
                failure_reason=normalized_reason,
                transaction_hash=normalized_transaction_hash,
                block_number=block_number,
                failed_at=now,
                terminal_outcome_key=outcome_key,
                terminal_proof_ref=proof_ref,
                terminal_evidence_source=EvidenceSource.BASE_SEPOLIA_VERIFIED,
            ),
            expected_states=(PaymentIntentState.RECONCILIATION_REQUIRED,),
            event_type=EventType.PAYMENT_FAILED,
            occurred_at=now,
            actor={"id": "independent-receipt-verifier", "type": "service"},
            payload={
                "blockNumber": block_number,
                "reason": normalized_reason,
                "receiptStatus": receipt_status,
                "terminalOutcomeKey": outcome_key,
                "transactionHash": normalized_transaction_hash,
            },
            evidence_refs=(
                intent.decision_authorization_hash or intent.quote_id,
                normalized_transaction_hash,
            ),
            expected_event_count=event_count,
            expected_head_event_hash=head_hash,
            reservation_action="release",
        )

    @staticmethod
    def _reject_legacy_permit2(intent: PaymentIntent) -> None:
        """Historical Permit2 intents stay readable but never reconcile again."""
        if intent.transfer_method != "eip3009":
            raise PaymentConflictError(
                "legacy Permit2 payment intents are read-only and cannot be reconciled"
            )

    async def record_reconciliation_check(
        self,
        *,
        purchase_id: str,
        check: ReconciliationCheck,
    ) -> PaymentIntent:
        """Append one bounded reconciliation attempt without changing payment state."""
        intent, events, event_count, head_hash = await self._verified_context(purchase_id)
        self._reject_legacy_permit2(intent)
        if intent.state in TERMINAL_PAYMENT_INTENT_STATES:
            raise PaymentConflictError("payment already has a terminal outcome")
        if intent.state != PaymentIntentState.RECONCILIATION_REQUIRED:
            raise PaymentConflictError("payment is not awaiting reconciliation")
        payload = check.to_payload()
        recorded = [
            event
            for event in events
            if event.type == EventType.PAYMENT_RECONCILIATION_CHECKED
            and event.payload.get("attemptNumber") == check.attempt_number
        ]
        if recorded:
            if len(recorded) == 1 and recorded[0].payload == payload:
                return intent
            raise PaymentConflictError("reconciliation attempt has different evidence")
        if check.attempt_number != intent.reconciliation_attempt_count + 1:
            raise PaymentEvidenceError("reconciliation attempt numbers must be consecutive")
        return await self._repository.transition_payment_intent(
            next_intent=replace(
                intent,
                reconciliation_attempt_count=check.attempt_number,
                reconciliation_first_checked_at=(
                    intent.reconciliation_first_checked_at or check.checked_at
                ),
                reconciliation_last_checked_at=check.checked_at,
                reconciliation_last_outcome=check.verifier_outcome,
            ),
            expected_states=(PaymentIntentState.RECONCILIATION_REQUIRED,),
            event_type=EventType.PAYMENT_RECONCILIATION_CHECKED,
            occurred_at=check.checked_at,
            actor={"id": "independent-receipt-verifier", "type": "service"},
            payload=payload,
            evidence_refs=(check.proof_ref, check.submission_ref),
            expected_event_count=event_count,
            expected_head_event_hash=head_hash,
            reservation_action="hold",
        )

    async def confirm_mismatch(
        self,
        *,
        purchase_id: str,
        proof: ConfirmedMismatchProof,
    ) -> PaymentIntent:
        """Record a confirmed real outflow that disagreed with the signed quote."""
        intent, _events, event_count, head_hash = await self._verified_context(purchase_id)
        self._reject_legacy_permit2(intent)
        actual = proof.actual_transfer
        mismatched = mismatched_quote_fields(intent, actual)
        if intent.state == PaymentIntentState.MISMATCH_CONFIRMED:
            if (
                intent.terminal_proof_ref == proof.proof_ref
                and intent.actual_transfer == _normalized_transfer(actual)
            ):
                return intent
            raise PaymentConflictError("payment has a different mismatch proof")
        if intent.state in TERMINAL_PAYMENT_INTENT_STATES:
            raise PaymentConflictError("payment already has a different terminal outcome")
        if intent.state != PaymentIntentState.RECONCILIATION_REQUIRED:
            raise PaymentConflictError("payment cannot confirm a mismatch from current state")
        if actual.from_address.lower() != intent.buyer_wallet_address:
            raise PaymentEvidenceError("mismatch proof is not a buyer outflow")
        if not mismatched:
            raise PaymentEvidenceError("proof matches the signed quote and is not a mismatch")
        now = self._clock.now()
        outcome_key = terminal_outcome_key(purchase_id)
        same_token = actual.token.lower() == intent.token
        confirmed_outflow = ConfirmedOutflow(
            terminal_outcome_key=outcome_key,
            purchase_id=purchase_id,
            buyer_wallet=intent.buyer_wallet_address,
            policy_date=intent.policy_date,
            token=actual.token.lower(),
            amount_units=actual.amount_units,
            recipient=actual.to_address.lower(),
            transaction_ref=proof.transaction_ref,
            proof_ref=proof.proof_ref,
        )
        payload: JsonObject = {
            "actualTransfer": actual.to_payload(),
            "evidenceSource": proof.evidence_source.value,
            "mismatchedFields": list(mismatched),
            "proofRef": proof.proof_ref,
            "quoteBinding": {
                "amountUnits": intent.amount_units,
                "payTo": intent.pay_to,
                "token": intent.token,
            },
            "reconciliationAttempts": intent.reconciliation_attempt_count,
            "terminalOutcomeKey": outcome_key,
            "transactionRef": proof.transaction_ref.to_payload(),
        }
        if proof.scenario is not None:
            payload["scenario"] = proof.scenario.to_payload()
        return await self._repository.transition_payment_intent(
            next_intent=replace(
                intent,
                state=PaymentIntentState.MISMATCH_CONFIRMED,
                actual_transfer=_normalized_transfer(actual),
                mismatched_fields=mismatched,
                terminal_outcome_key=outcome_key,
                terminal_proof_ref=proof.proof_ref,
                terminal_evidence_source=proof.evidence_source,
                local_transaction_id=_local_transaction_id(proof.transaction_ref),
            ),
            expected_states=(PaymentIntentState.RECONCILIATION_REQUIRED,),
            event_type=EventType.PAYMENT_MISMATCH_CONFIRMED,
            occurred_at=now,
            actor={"id": "independent-receipt-verifier", "type": "service"},
            payload=payload,
            evidence_refs=(
                intent.decision_authorization_hash or intent.quote_id,
                proof.proof_ref,
            ),
            expected_event_count=event_count,
            expected_head_event_hash=head_hash,
            reservation_action="replace_with_actual" if same_token else "release",
            confirmed_outflow=confirmed_outflow,
        )

    async def reconcile_no_transfer(
        self,
        *,
        purchase_id: str,
        proof: NoTransferProof,
    ) -> PaymentIntent:
        """Close a payment as terminal no-transfer once bounded checks are exhausted."""
        intent, events, event_count, head_hash = await self._verified_context(purchase_id)
        self._reject_legacy_permit2(intent)
        if intent.state == PaymentIntentState.RECONCILED_NO_TRANSFER:
            if intent.terminal_proof_ref == proof.proof_ref:
                return intent
            raise PaymentConflictError("payment has a different no-transfer proof")
        if intent.state in TERMINAL_PAYMENT_INTENT_STATES:
            raise PaymentConflictError("payment already has a different terminal outcome")
        if intent.state != PaymentIntentState.RECONCILIATION_REQUIRED:
            raise PaymentConflictError("payment cannot reconcile from current state")
        if not any(
            event.type == EventType.PAYMENT_RECONCILIATION_REQUIRED for event in events
        ):
            raise PaymentEvidenceError("no-transfer requires prior reconciliation evidence")
        policy = self._reconciliation_policy
        if proof.attempt_count != intent.reconciliation_attempt_count:
            raise PaymentEvidenceError("no-transfer proof attempt count is not recorded")
        if intent.reconciliation_attempt_count < policy.minimum_attempts:
            raise PaymentEvidenceError("bounded reconciliation retries are not exhausted")
        if intent.reconciliation_last_outcome not in NO_TRANSFER_VERIFIER_OUTCOMES:
            raise PaymentEvidenceError(
                "a momentary missing receipt cannot confirm a terminal no-transfer"
            )
        confirmations = proof.finality_evidence.get("confirmations")
        if (
            not isinstance(confirmations, int)
            or isinstance(confirmations, bool)
            or confirmations < policy.minimum_finality_confirmations
        ):
            raise PaymentEvidenceError("no-transfer proof lacks configured finality evidence")
        now = self._clock.now()
        outcome_key = terminal_outcome_key(purchase_id)
        payload: JsonObject = {
            "attemptCount": proof.attempt_count,
            "authorizationNonceHash": proof.authorization_nonce_hash,
            "checkedChainId": proof.checked_chain_id,
            "evidenceSource": proof.evidence_source.value,
            "finalityEvidence": proof.finality_evidence,
            "firstCheckedAt": proof.first_checked_at.isoformat(),
            "lastCheckedAt": proof.last_checked_at.isoformat(),
            "proofRef": proof.proof_ref,
            "reasonCode": proof.reason_code,
            "submissionRef": proof.submission_ref,
            "terminalOutcomeKey": outcome_key,
        }
        if proof.scenario is not None:
            payload["scenario"] = proof.scenario.to_payload()
        return await self._repository.transition_payment_intent(
            next_intent=replace(
                intent,
                state=PaymentIntentState.RECONCILED_NO_TRANSFER,
                no_transfer_reason_code=proof.reason_code,
                terminal_outcome_key=outcome_key,
                terminal_proof_ref=proof.proof_ref,
                terminal_evidence_source=proof.evidence_source,
            ),
            expected_states=(PaymentIntentState.RECONCILIATION_REQUIRED,),
            event_type=EventType.PAYMENT_RECONCILED_NO_TRANSFER,
            occurred_at=now,
            actor={"id": "payment-reconciliation-closer", "type": "service"},
            payload=payload,
            evidence_refs=(
                intent.decision_authorization_hash or intent.quote_id,
                proof.proof_ref,
            ),
            expected_event_count=event_count,
            expected_head_event_hash=head_hash,
            reservation_action="release",
        )

    async def list_confirmed_outflows(self, purchase_id: str) -> list[ConfirmedOutflow]:
        return await self._repository.list_confirmed_outflows(purchase_id)

    async def load_payment_view(self, purchase_id: str) -> PaymentView:
        events = await self._repository.list_events(purchase_id)
        head = await self._repository.get_event_head(purchase_id)
        if not events or head is None:
            raise PaymentEvidenceError("purchase evidence is missing")
        verify_event_chain(
            events,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )
        if any(event.purchase_id != purchase_id for event in events):
            raise PaymentEvidenceError("cross-purchase evidence in chain")
        business_events = [event for event in events if event.type not in _AUXILIARY_EVENT_TYPES]
        business_types = [event.type for event in business_events]
        prefix = [EventType.REQUESTED, EventType.QUOTED, EventType.DECIDED]
        claimed_type = EventType.PAYMENT_INTENT_CLAIMED
        authorized_type = EventType.PAYMENT_AUTHORIZED
        required_type = EventType.PAYMENT_RECONCILIATION_REQUIRED
        submitted_type = EventType.PAYMENT_SUBMISSION_IDENTIFIED
        allowed_payment_prefixes: tuple[list[EventType], ...] = (
            [],
            [claimed_type],
            [claimed_type, EventType.PAYMENT_FAILED],
            [claimed_type, authorized_type],
            [claimed_type, authorized_type, EventType.PAYMENT_SETTLED],
            [claimed_type, authorized_type, EventType.PAYMENT_FAILED],
            [claimed_type, authorized_type, required_type],
            [claimed_type, authorized_type, required_type, submitted_type],
            *(
                [claimed_type, authorized_type, required_type, terminal]
                for terminal in TERMINAL_PAYMENT_EVENT_TYPES
            ),
            *(
                [claimed_type, authorized_type, required_type, submitted_type, terminal]
                for terminal in TERMINAL_PAYMENT_EVENT_TYPES
            ),
        )
        tail = business_types[3:]
        post_payment_types = {
            EventType.DELIVERED,
            EventType.AUDITED,
            EventType.REPUTATION_RECORDED,
            EventType.EVIDENCE_ANCHORED,
        }
        lifecycle_valid = any(
            tail[: len(payment_prefix)] == payment_prefix
            and (
                len(tail) == len(payment_prefix)
                or (
                    bool(payment_prefix)
                    and payment_prefix[-1] in TERMINAL_PAYMENT_EVENT_TYPES
                )
            )
            and all(item in post_payment_types for item in tail[len(payment_prefix) :])
            and len(tail[len(payment_prefix) :]) == len(set(tail[len(payment_prefix) :]))
            for payment_prefix in allowed_payment_prefixes
        )
        if business_types[:3] != prefix or not lifecycle_valid:
            raise PaymentEvidenceError("purchase payment lifecycle is malformed")

        requested, quoted, decided = business_events[:3]
        owner_address = _required_string(requested.actor.get("id"), field="owner address")
        binding = await self._repository.get_wallet_binding(owner_address)
        if binding is None:
            raise PaymentPolicyError("buyer wallet is not bound")
        budget_units = _required_units(requested.payload.get("budgetUnits"), field="request budget")
        request_policy = _required_dict(requested.payload.get("policy"), field="request policy")
        winner = _required_dict(decided.payload.get("winner"), field="decision winner")
        winner_quote_id = _required_string(winner.get("quote_id"), field="decision quote id")
        raw_quotes = quoted.payload.get("signedQuotes")
        if not isinstance(raw_quotes, list):
            raise PaymentEvidenceError("signed quotes are malformed")
        matching_quotes = [
            quote
            for quote in raw_quotes
            if isinstance(quote, dict) and quote.get("quote_id") == winner_quote_id
        ]
        if len(matching_quotes) != 1:
            raise PaymentEvidenceError("decision quote binding is ambiguous")
        raw_quote = matching_quotes[0]
        if raw_quote.get("purchase_id") != purchase_id:
            raise PaymentEvidenceError("quote purchase binding mismatch")
        identity_evidence = quoted.payload.get("quoteIdentityEvidence")
        if not isinstance(identity_evidence, list):
            raise PaymentEvidenceError("quote identity evidence is malformed")
        identity_matches = [
            item
            for item in identity_evidence
            if isinstance(item, dict)
            and item.get("quoteId") == winner_quote_id
            and item.get("identityVerified") is True
        ]
        if len(identity_matches) != 1:
            raise PaymentEvidenceError("seller identity is not verified")
        identity_match = identity_matches[0]
        if raw_quote.get("available") is not True:
            raise PaymentEvidenceError("selected quote is unavailable")

        quote = PaymentQuoteView(
            quote_id=winner_quote_id,
            seller_agent_id=_required_string(
                raw_quote.get("seller_agent_id"), field="seller agent id"
            ),
            erc8004_agent_id=_required_string(
                identity_match.get("erc8004AgentId"), field="ERC-8004 agent id"
            ),
            provider_id=_required_string(
                raw_quote.get("provider_id"), field="quote provider id"
            ),
            model_id=_required_string(raw_quote.get("model_id"), field="quote model id"),
            model_version=_required_string(
                raw_quote.get("model_version"), field="quote model version"
            ),
            amount_units=_required_units(raw_quote.get("amount_units"), field="quote amount"),
            token=_required_string(raw_quote.get("token"), field="quote token").lower(),
            pay_to=_required_string(raw_quote.get("pay_to"), field="quote recipient").lower(),
            expires_at=_required_datetime(raw_quote.get("expires_at"), field="quote expiry"),
            signer_address=_required_string(
                raw_quote.get("signer_address"), field="quote signer"
            ).lower(),
            chain_id=_required_units(raw_quote.get("chain_id"), field="quote chain"),
            verifying_contract=_required_string(
                raw_quote.get("verifying_contract"), field="quote contract"
            ).lower(),
        )
        return PaymentView(
            purchase_id=purchase_id,
            owner_address=owner_address.lower(),
            buyer_wallet_address=binding.buyer_wallet_address.lower(),
            budget_units=budget_units,
            request_policy=request_policy,
            decision_event_hash=decided.event_hash,
            quote=quote,
            event_count=len(events),
            head_event_hash=events[-1].event_hash,
        )

    async def claim(self, purchase_id: str) -> PaymentIntent:
        view = await self.load_payment_view(purchase_id)
        existing = await self._repository.get_payment_intent(purchase_id)
        if existing is not None:
            immutable_binding = (
                existing.purchase_id,
                existing.buyer_wallet_address,
                existing.quote_id,
                existing.decision_event_hash,
                existing.amount_units,
                existing.token,
                existing.pay_to,
            )
            current_binding = (
                purchase_id,
                view.buyer_wallet_address,
                view.quote.quote_id,
                view.decision_event_hash,
                view.quote.amount_units,
                view.quote.token,
                view.quote.pay_to,
            )
            if immutable_binding != current_binding:
                raise PaymentConflictError("payment intent immutable binding changed")
            return existing
        now = self._clock.now()
        if now >= view.quote.expires_at:
            raise PaymentPolicyError("selected quote is expired")
        if view.quote.amount_units > view.budget_units:
            raise PaymentPolicyError("selected quote exceeds request budget")
        request_limit = view.request_policy.get("maxTransactionUnits")
        if request_limit is not None:
            limit = _required_units(request_limit, field="request transaction limit")
            if view.quote.amount_units > limit:
                raise PaymentPolicyError("selected quote exceeds request transaction limit")
        intent = PaymentIntent(
            purchase_id=purchase_id,
            buyer_wallet_address=view.buyer_wallet_address,
            policy_date=now.date().isoformat(),
            quote_id=view.quote.quote_id,
            decision_event_hash=view.decision_event_hash,
            amount_units=view.quote.amount_units,
            token=view.quote.token,
            pay_to=view.quote.pay_to,
            permit2_nonce=None,
            state=PaymentIntentState.CLAIMED,
            claimed_at=now,
            transfer_method="eip3009",
            authorization_nonce="0x" + secrets.token_hex(32),
        )
        return await self._repository.claim_payment_intent(
            intent=intent,
            occurred_at=now,
            actor={"id": "payment-executor", "type": "service"},
            payload={
                "amountUnits": intent.amount_units,
                "buyerWalletAddress": intent.buyer_wallet_address,
                "decisionEventHash": intent.decision_event_hash,
                "payTo": intent.pay_to,
                "transferMethod": intent.transfer_method,
                "authorizationNonce": intent.authorization_nonce,
                "quoteId": intent.quote_id,
                "token": intent.token,
            },
            evidence_refs=(intent.decision_event_hash, intent.quote_id),
            expected_event_count=view.event_count,
            expected_head_event_hash=view.head_event_hash,
        )
