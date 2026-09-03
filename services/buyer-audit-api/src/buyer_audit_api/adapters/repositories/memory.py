from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from typing import Literal

from buyer_audit_api.core.errors import (
    EvidenceIntegrityError,
    EvidenceTransitionError,
    PaymentConflictError,
    PaymentPolicyError,
)
from buyer_audit_api.core.events import create_event, verify_event_chain
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceHead,
    JsonObject,
    SensitivePayload,
    WalletBinding,
)
from buyer_audit_api.core.payment import PaymentIntent, PaymentIntentState, WalletPolicy
from buyer_audit_api.core.seller_execution import SellerExecution, SellerExecutionState


def _same_payment_binding(left: PaymentIntent, right: PaymentIntent) -> bool:
    return (
        left.purchase_id,
        left.buyer_wallet_address.lower(),
        left.policy_date,
        left.quote_id,
        left.decision_event_hash,
        left.amount_units,
        left.token.lower(),
        left.pay_to.lower(),
        left.permit2_nonce,
        left.transfer_method,
        left.authorization_nonce,
    ) == (
        right.purchase_id,
        right.buyer_wallet_address.lower(),
        right.policy_date,
        right.quote_id,
        right.decision_event_hash,
        right.amount_units,
        right.token.lower(),
        right.pay_to.lower(),
        right.permit2_nonce,
        right.transfer_method,
        right.authorization_nonce,
    )


class InMemoryEvidenceRepository:
    def __init__(self) -> None:
        self._users: dict[str, dict[str, object]] = {}
        self._events: dict[str, list[EvidenceEvent]] = {}
        self._heads: dict[str, list[EvidenceHead]] = {}
        self._sensitive: dict[str, SensitivePayload] = {}
        self._wallet_policies: dict[tuple[str, str], WalletPolicy] = {}
        self._payment_intents: dict[str, PaymentIntent] = {}
        self._seller_executions: dict[str, SellerExecution] = {}
        self._lock = asyncio.Lock()

    async def ensure_indexes(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def issue_siwe_nonce(
        self,
        *,
        owner_address: str,
        nonce_hash: str,
        expires_at: datetime,
    ) -> None:
        async with self._lock:
            user = self._users.setdefault(owner_address.lower(), {})
            user.update(
                {
                    "nonceHash": nonce_hash,
                    "nonceExpiresAt": expires_at,
                    "nonceConsumedAt": None,
                }
            )

    async def consume_siwe_nonce(
        self,
        *,
        owner_address: str,
        nonce_hash: str,
        consumed_at: datetime,
    ) -> bool:
        async with self._lock:
            user = self._users.get(owner_address.lower())
            if user is None:
                return False
            if user.get("nonceHash") != nonce_hash or user.get("nonceConsumedAt") is not None:
                return False
            expires_at = user.get("nonceExpiresAt")
            if not isinstance(expires_at, datetime) or consumed_at >= expires_at:
                return False
            user["nonceConsumedAt"] = consumed_at
            return True

    async def bind_buyer_wallet(
        self,
        *,
        owner_address: str,
        buyer_wallet_address: str,
        bound_at: datetime,
    ) -> WalletBinding:
        async with self._lock:
            normalized_owner = owner_address.lower()
            normalized_buyer = buyer_wallet_address.lower()
            owner = self._users.get(normalized_owner)
            if owner is not None and "buyerWalletAddress" in owner:
                existing_buyer = str(owner["buyerWalletAddress"])
                if existing_buyer != normalized_buyer:
                    raise ValueError("owner already has a different buyer wallet")
                return WalletBinding(
                    normalized_owner,
                    existing_buyer,
                    owner["buyerWalletBoundAt"],  # type: ignore[arg-type]
                )
            for existing_owner, user in self._users.items():
                if (
                    existing_owner != normalized_owner
                    and user.get("buyerWalletAddress") == normalized_buyer
                ):
                    raise ValueError("buyer wallet is already bound")
            owner = self._users.setdefault(normalized_owner, {})
            owner.update(
                {
                    "buyerWalletAddress": normalized_buyer,
                    "buyerWalletBoundAt": bound_at,
                }
            )
            return WalletBinding(normalized_owner, normalized_buyer, bound_at)

    async def get_wallet_binding(self, owner_address: str) -> WalletBinding | None:
        async with self._lock:
            user = self._users.get(owner_address.lower())
            if user is None or "buyerWalletAddress" not in user:
                return None
            return WalletBinding(
                owner_address=owner_address.lower(),
                buyer_wallet_address=str(user["buyerWalletAddress"]),
                bound_at=user["buyerWalletBoundAt"],  # type: ignore[arg-type]
            )

    def _validate_chain_locked(
        self,
        *,
        purchase_id: str,
        expected_event_count: int | None = None,
        expected_head_event_hash: str | None = None,
    ) -> tuple[list[EvidenceEvent], list[EvidenceHead]]:
        current = self._events.setdefault(purchase_id, [])
        heads = self._heads.setdefault(purchase_id, [])
        if len(current) != len(heads):
            raise EvidenceIntegrityError("event and immutable head counts diverged")
        verify_event_chain(
            current,
            expected_event_count=len(heads),
            expected_head_event_hash=heads[-1].head_event_hash if heads else None,
        )
        for event, head in zip(current, heads, strict=True):
            if (
                head.purchase_id != purchase_id
                or head.event_count != event.sequence
                or head.head_event_hash != event.event_hash
            ):
                raise EvidenceIntegrityError("event and immutable head diverged")
        if expected_event_count is not None and len(current) != expected_event_count:
            raise EvidenceTransitionError("evidence head changed before append")
        actual_head_hash = current[-1].event_hash if current else None
        if (
            expected_head_event_hash is not None
            and actual_head_hash != expected_head_event_hash
        ):
            raise EvidenceTransitionError("evidence head changed before append")
        return current, heads

    def _create_event_locked(
        self,
        *,
        purchase_id: str,
        event_type: EventType,
        occurred_at: datetime,
        actor: JsonObject,
        payload: JsonObject,
        evidence_refs: tuple[str, ...],
        expected_event_count: int | None,
        expected_head_event_hash: str | None,
    ) -> tuple[EvidenceEvent, list[EvidenceEvent], list[EvidenceHead]]:
        current, heads = self._validate_chain_locked(
            purchase_id=purchase_id,
            expected_event_count=expected_event_count,
            expected_head_event_hash=expected_head_event_hash,
        )
        singleton_types = {
            EventType.QUOTED,
            EventType.DECIDED,
            EventType.PAYMENT_INTENT_CLAIMED,
            EventType.PAYMENT_AUTHORIZED,
            EventType.PAYMENT_RECONCILIATION_REQUIRED,
            EventType.PAYMENT_SETTLED,
            EventType.PAYMENT_FAILED,
            EventType.EVIDENCE_ANCHORED,
            EventType.REPUTATION_RECORDED,
            EventType.DELIVERED,
            EventType.AUDITED,
        }
        if event_type in singleton_types and any(event.type == event_type for event in current):
            raise EvidenceTransitionError(f"duplicate singleton event: {event_type.value}")
        previous_hash = current[-1].event_hash if current else None
        event = create_event(
            purchase_id=purchase_id,
            sequence=len(current) + 1,
            event_type=event_type,
            occurred_at=occurred_at,
            actor=actor,
            payload=payload,
            previous_event_hash=previous_hash,
            evidence_refs=evidence_refs,
        )
        return event, current, heads

    @staticmethod
    def _commit_event_locked(
        event: EvidenceEvent,
        current: list[EvidenceEvent],
        heads: list[EvidenceHead],
    ) -> None:
        current.append(event)
        heads.append(EvidenceHead(event.purchase_id, event.sequence, event.event_hash))

    async def append_event(
        self,
        *,
        purchase_id: str,
        event_type: EventType,
        occurred_at: datetime,
        actor: JsonObject,
        payload: JsonObject,
        evidence_refs: tuple[str, ...] = (),
        expected_event_count: int | None = None,
        expected_head_event_hash: str | None = None,
    ) -> EvidenceEvent:
        async with self._lock:
            event, current, heads = self._create_event_locked(
                purchase_id=purchase_id,
                event_type=event_type,
                occurred_at=occurred_at,
                actor=actor,
                payload=payload,
                evidence_refs=evidence_refs,
                expected_event_count=expected_event_count,
                expected_head_event_hash=expected_head_event_hash,
            )
            self._commit_event_locked(event, current, heads)
            return deepcopy(event)

    async def put_wallet_policy(self, policy: WalletPolicy) -> None:
        normalized = replace(
            policy,
            buyer_wallet_address=policy.buyer_wallet_address.lower(),
            token=policy.token.lower(),
        )
        key = (normalized.buyer_wallet_address, normalized.policy_date)
        async with self._lock:
            existing = self._wallet_policies.get(key)
            if existing is not None and (existing.spent_units or existing.reserved_units):
                if existing != normalized:
                    raise PaymentConflictError("active wallet policy cannot be replaced")
                return
            self._wallet_policies[key] = normalized

    async def get_wallet_policy(
        self, *, buyer_wallet_address: str, policy_date: str
    ) -> WalletPolicy | None:
        async with self._lock:
            policy = self._wallet_policies.get((buyer_wallet_address.lower(), policy_date))
            return deepcopy(policy) if policy is not None else None

    async def get_latest_wallet_policy(
        self, buyer_wallet_address: str
    ) -> WalletPolicy | None:
        normalized = buyer_wallet_address.lower()
        async with self._lock:
            candidates = [
                policy
                for (address, _), policy in self._wallet_policies.items()
                if address == normalized
            ]
            latest = max(candidates, key=lambda item: item.policy_date, default=None)
            return deepcopy(latest) if latest is not None else None

    async def get_payment_intent(self, purchase_id: str) -> PaymentIntent | None:
        async with self._lock:
            intent = self._payment_intents.get(purchase_id)
            return deepcopy(intent) if intent is not None else None

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
    ) -> PaymentIntent:
        async with self._lock:
            existing = self._payment_intents.get(intent.purchase_id)
            if existing is not None:
                current, _ = self._validate_chain_locked(purchase_id=intent.purchase_id)
                claim_events = [
                    event
                    for event in current
                    if event.type == EventType.PAYMENT_INTENT_CLAIMED
                ]
                if (
                    not _same_payment_binding(existing, intent)
                    or len(claim_events) != 1
                    or claim_events[0].payload.get("quoteId") != existing.quote_id
                    or claim_events[0].payload.get("decisionEventHash")
                    != existing.decision_event_hash
                ):
                    raise PaymentConflictError(
                        "purchase payment intent has different immutable data"
                    )
                return deepcopy(existing)

            event, current, heads = self._create_event_locked(
                purchase_id=intent.purchase_id,
                event_type=EventType.PAYMENT_INTENT_CLAIMED,
                occurred_at=occurred_at,
                actor=actor,
                payload=payload,
                evidence_refs=evidence_refs,
                expected_event_count=expected_event_count,
                expected_head_event_hash=expected_head_event_hash,
            )
            key = (intent.buyer_wallet_address.lower(), intent.policy_date)
            policy = self._wallet_policies.get(key)
            if policy is None:
                raise PaymentPolicyError("wallet policy is not configured for today")
            if policy.token.lower() != intent.token.lower():
                raise PaymentPolicyError("wallet policy token mismatch")
            if intent.amount_units > policy.per_transaction_limit_units:
                raise PaymentPolicyError("payment exceeds wallet transaction limit")
            projected = policy.spent_units + policy.reserved_units + intent.amount_units
            if projected > policy.daily_limit_units:
                raise PaymentPolicyError("payment exceeds wallet daily limit")
            normalized_intent = replace(
                intent,
                buyer_wallet_address=intent.buyer_wallet_address.lower(),
                token=intent.token.lower(),
                pay_to=intent.pay_to.lower(),
                state=PaymentIntentState.CLAIMED,
            )
            self._wallet_policies[key] = replace(
                policy,
                reserved_units=policy.reserved_units + intent.amount_units,
            )
            self._payment_intents[intent.purchase_id] = normalized_intent
            self._commit_event_locked(event, current, heads)
            return deepcopy(normalized_intent)

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
        reservation_action: Literal["hold", "settle", "release"],
    ) -> PaymentIntent:
        async with self._lock:
            existing = self._payment_intents.get(next_intent.purchase_id)
            if existing is None:
                raise PaymentConflictError("payment intent is missing")
            current, heads = self._validate_chain_locked(
                purchase_id=next_intent.purchase_id
            )
            same_state_submission_binding = (
                event_type == EventType.PAYMENT_SUBMISSION_IDENTIFIED
                and existing.state == PaymentIntentState.RECONCILIATION_REQUIRED
                and next_intent.state == PaymentIntentState.RECONCILIATION_REQUIRED
                and existing.transaction_hash is None
                and next_intent.transaction_hash is not None
            )
            if existing.state == next_intent.state and not same_state_submission_binding:
                matching = [event for event in current if event.type == event_type]
                if existing == next_intent and len(matching) == 1:
                    return deepcopy(existing)
                raise PaymentConflictError("payment transition has different data")
            if existing.state not in expected_states:
                raise PaymentConflictError("payment state changed before transition")
            if existing.purchase_id != next_intent.purchase_id:
                raise PaymentConflictError("payment purchase binding changed")
            immutable_fields = (
                "buyer_wallet_address",
                "policy_date",
                "quote_id",
                "decision_event_hash",
                "amount_units",
                "token",
                "pay_to",
                "permit2_nonce",
                "transfer_method",
                "authorization_nonce",
                "claimed_at",
            )
            if any(
                getattr(existing, field) != getattr(next_intent, field)
                for field in immutable_fields
            ):
                raise PaymentConflictError("payment immutable binding changed")
            event, current, heads = self._create_event_locked(
                purchase_id=next_intent.purchase_id,
                event_type=event_type,
                occurred_at=occurred_at,
                actor=actor,
                payload=payload,
                evidence_refs=evidence_refs,
                expected_event_count=expected_event_count,
                expected_head_event_hash=expected_head_event_hash,
            )
            key = (existing.buyer_wallet_address, existing.policy_date)
            policy = self._wallet_policies.get(key)
            if policy is None:
                raise PaymentPolicyError("wallet policy is missing during transition")
            if reservation_action in ("settle", "release"):
                if policy.reserved_units < existing.amount_units:
                    raise PaymentConflictError("reserved budget is insufficient")
                policy = replace(
                    policy,
                    reserved_units=policy.reserved_units - existing.amount_units,
                    spent_units=(
                        policy.spent_units + existing.amount_units
                        if reservation_action == "settle"
                        else policy.spent_units
                    ),
                )
            if event_type == EventType.PAYMENT_SETTLED:
                transaction_hash = payload.get("transactionHash")
                if any(
                    prior.payload.get("transactionHash") == transaction_hash
                    for events in self._events.values()
                    for prior in events
                    if prior.type == EventType.PAYMENT_SETTLED
                ):
                    raise PaymentConflictError("transaction hash is already settled")
            self._wallet_policies[key] = policy
            self._payment_intents[next_intent.purchase_id] = next_intent
            self._commit_event_locked(event, current, heads)
            return deepcopy(next_intent)

    async def list_events(self, purchase_id: str) -> list[EvidenceEvent]:
        async with self._lock:
            return deepcopy(self._events.get(purchase_id, []))

    async def list_owner_purchase_ids(self, owner_address: str) -> list[str]:
        normalized = owner_address.lower()
        async with self._lock:
            matches = [
                (events[0].occurred_at, purchase_id)
                for purchase_id, events in self._events.items()
                if events
                and events[0].type == EventType.REQUESTED
                and events[0].actor.get("id") == normalized
            ]
            return [
                purchase_id
                for _, purchase_id in sorted(matches, reverse=True)
            ]

    async def get_event_head(self, purchase_id: str) -> EvidenceHead | None:
        async with self._lock:
            heads = self._heads.get(purchase_id, [])
            return deepcopy(heads[-1]) if heads else None

    async def store_sensitive_payload(self, payload: SensitivePayload) -> None:
        async with self._lock:
            if payload.payload_id in self._sensitive:
                raise ValueError("duplicate payload_id")
            self._sensitive[payload.payload_id] = payload

    async def get_sensitive_payload(self, payload_id: str) -> SensitivePayload | None:
        async with self._lock:
            payload = self._sensitive.get(payload_id)
            return deepcopy(payload) if payload else None

    async def claim_seller_execution(
        self, execution: SellerExecution
    ) -> SellerExecution:
        async with self._lock:
            existing = self._seller_executions.get(execution.purchase_id)
            if existing is not None:
                return deepcopy(existing)
            self._seller_executions[execution.purchase_id] = execution
            return deepcopy(execution)

    async def transition_seller_execution(
        self,
        *,
        purchase_id: str,
        expected_state: SellerExecutionState,
        next_execution: SellerExecution,
    ) -> SellerExecution:
        async with self._lock:
            existing = self._seller_executions.get(purchase_id)
            if existing is None:
                raise ValueError("seller execution is missing")
            if existing.state != expected_state:
                return deepcopy(existing)
            self._seller_executions[purchase_id] = next_execution
            return deepcopy(next_execution)

    async def get_seller_execution(self, purchase_id: str) -> SellerExecution | None:
        async with self._lock:
            execution = self._seller_executions.get(purchase_id)
            return deepcopy(execution) if execution is not None else None
