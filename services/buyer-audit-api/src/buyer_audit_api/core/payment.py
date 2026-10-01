"""Payment intent records, wallet budget policy and the authorize/reconcile transitions.

`aegis_payment.py` owns the AEGIS reservation and the Facilitator outcome; this module
holds the shared intent model, the repository contract both services write through, and
the two transitions the payment execution module drives directly: recording the signed
decision authorization and parking an attempt whose outcome is unknown.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from buyer_audit_api.core.errors import PaymentConflictError, PaymentEvidenceError
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.hashing import sha256_bytes
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceHead,
    EvidenceSource,
    JsonObject,
    WalletBinding,
)
from buyer_audit_api.core.ports import Clock


class PaymentIntentState(StrEnum):
    CLAIMED = "CLAIMED"
    AUTHORIZED = "AUTHORIZED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"


TERMINAL_PAYMENT_INTENT_STATES: frozenset[PaymentIntentState] = frozenset(
    {
        PaymentIntentState.SETTLED,
        PaymentIntentState.FAILED,
    }
)

ReservationAction = Literal["hold", "settle", "release"]


def terminal_outcome_key(purchase_id: str) -> str:
    """One terminal outcome per purchase, shared by every terminal event type."""
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
        # Recorded spend is a fact and must stay readable as stored; the claim guard,
        # not this constructor, is what rejects a payment above the daily limit.


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
    state: PaymentIntentState
    claimed_at: datetime
    #: AEGIS pays with an ERC-3009 authorization only.
    transfer_method: Literal["eip3009"] = "eip3009"
    authorization_nonce: str | None = None
    decision_authorization_hash: str | None = None
    decision_authorization_signature: str | None = None
    authorized_at: datetime | None = None
    reconciliation_reason: str | None = None
    settlement_verified_at: datetime | None = None
    failure_reason: str | None = None
    failed_at: datetime | None = None
    terminal_outcome_key: str | None = None
    terminal_proof_ref: str | None = None
    terminal_evidence_source: EvidenceSource | None = None


class PaymentRepository(Protocol):
    async def list_events(self, purchase_id: str) -> list[EvidenceEvent]: ...

    async def get_event_head(self, purchase_id: str) -> EvidenceHead | None: ...

    async def get_wallet_binding(self) -> WalletBinding | None: ...

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
    ) -> PaymentIntent: ...


class PaymentService:
    def __init__(self, *, repository: PaymentRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

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
    ) -> PaymentIntent:
        """Park an authorized attempt whose outcome is unknown, keeping its reservation.

        An attempt already waiting for reconciliation is returned unchanged, so a retry
        never opens a second attempt and never releases the budget it holds.
        """
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise PaymentEvidenceError("reconciliation reason is required")
        intent, event_count, head_hash = await self._transition_context(purchase_id)
        if intent.state == PaymentIntentState.RECONCILIATION_REQUIRED:
            return intent
        if intent.state != PaymentIntentState.AUTHORIZED:
            raise PaymentConflictError("payment cannot enter reconciliation")
        now = self._clock.now()
        return await self._repository.transition_payment_intent(
            next_intent=replace(
                intent,
                state=PaymentIntentState.RECONCILIATION_REQUIRED,
                reconciliation_reason=normalized_reason,
            ),
            expected_states=(PaymentIntentState.AUTHORIZED,),
            event_type=EventType.PAYMENT_RECONCILIATION_REQUIRED,
            occurred_at=now,
            actor={"id": "payment-executor", "type": "service"},
            payload={
                "authorizationHash": intent.decision_authorization_hash,
                "reason": normalized_reason,
            },
            evidence_refs=(intent.decision_authorization_hash or "",),
            expected_event_count=event_count,
            expected_head_event_hash=head_hash,
            reservation_action="hold",
        )
