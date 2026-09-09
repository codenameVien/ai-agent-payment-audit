"""Payment reservation and Facilitator-based settlement for `aa-three-factor-v1`.

The Evidence API is the only owner of these records. The Provider Gateway and the
payment execution module never reach MongoDB and never state an amount: they read the
fixed decision, and every mutation below re-derives the amount, the token, the recipient
and the terms binding from the stored decision evidence before anything is written.

What this module deliberately does not do:

* it never verifies a chain receipt, a `Transfer` log or an `AuthorizationUsed` log. The
  new runtime records `verificationBasis=facilitator_response`, which is exactly what a
  Facilitator answer proves and no more;
* Mock records never write an EVM transaction hash. A `live` Facilitator may return the
  Base Sepolia transaction hash it submitted; that value is recorded as the
  Facilitator-provided settlement reference, not as application-side chain verification;
* it never re-opens a purchase for a fresh payment. One `purchaseId` holds at most one
  payment intent, one ERC-3009 authorization nonce and one terminal outcome.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace

from buyer_audit_api.core.aa_audit import is_aa_policy_request
from buyer_audit_api.core.aa_policy import AA_SCORING_POLICY_VERSION, terms_binding_hash
from buyer_audit_api.core.errors import (
    PaymentConflictError,
    PaymentEvidenceError,
    PaymentPolicyError,
)
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import (
    TERMINAL_PAYMENT_EVENT_TYPES,
    EventType,
    EvidenceEvent,
    EvidenceSource,
    JsonObject,
)
from buyer_audit_api.core.payment import (
    TERMINAL_PAYMENT_INTENT_STATES,
    PaymentIntent,
    PaymentIntentState,
    PaymentRepository,
    terminal_outcome_key,
)
from buyer_audit_api.core.ports import Clock

#: The settlement evidence of the new runtime is only as strong as the Facilitator
#: answer it came from. Storing the basis keeps that limit inside the record itself.
FACILITATOR_VERIFICATION_BASIS = "facilitator_response"
#: Mock execution is never relabelled as a real transfer, in evidence or in the UI.
MOCK_EXECUTION_MODE = "mock"
EXECUTION_MODES = frozenset({MOCK_EXECUTION_MODE, "live"})


def facilitator_response_payload(
    *,
    success: bool,
    network: str,
    transaction: str,
    payer: str | None,
    amount: str | None,
) -> JsonObject:
    """The external answer exactly as it arrived, including what it failed to say.

    Absent fields stay `null` instead of being filled in from what we expected, because a
    Facilitator that did not name a payer or an amount did not verify one.
    """
    return {
        "amount": amount,
        "network": network,
        "payer": payer,
        "success": success,
        "transaction": transaction,
    }


def caip2_network(chain_id: int) -> str:
    """The CAIP-2 identifier of the chain the decision priced this purchase against."""
    return f"eip155:{chain_id}"


@dataclass(frozen=True, slots=True)
class AegisPaymentTerms:
    """The immutable terms a 402 challenge and an ERC-3009 signature must reproduce."""

    purchase_id: str
    owner_address: str
    budget_units: int
    max_transaction_units: int | None
    decision_event_hash: str
    snapshot_hash: str
    terms_binding_hash: str
    amount_units: int
    recipient: str
    provider_id: str
    provider_model_id: str
    model_version: str
    token: JsonObject
    event_count: int
    head_event_hash: str

    @property
    def chain_id(self) -> int:
        value = self.token.get("chainId")
        if isinstance(value, bool) or not isinstance(value, int):
            raise PaymentEvidenceError("decision token chainId is malformed")
        return value

    @property
    def network(self) -> str:
        """The only network a settlement of these terms may claim."""
        return caip2_network(self.chain_id)

    def to_payload(self) -> JsonObject:
        return {
            "amountUnits": self.amount_units,
            "budgetUnits": self.budget_units,
            "decisionEventHash": self.decision_event_hash,
            "modelVersion": self.model_version,
            "providerId": self.provider_id,
            "providerModelId": self.provider_model_id,
            "purchaseId": self.purchase_id,
            "recipient": self.recipient,
            "scoringPolicyVersion": AA_SCORING_POLICY_VERSION,
            "snapshotHash": self.snapshot_hash,
            "termsBindingHash": self.terms_binding_hash,
            "token": self.token,
        }


def _string(payload: Mapping[str, object], key: str, *, field: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PaymentEvidenceError(f"{field} is malformed")
    return value.strip()


def _units(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PaymentEvidenceError(f"{field} is malformed")
    return value


def _mapping(value: object, *, field: str) -> JsonObject:
    if not isinstance(value, dict):
        raise PaymentEvidenceError(f"{field} is malformed")
    return dict(value)


def read_terms(events: list[EvidenceEvent]) -> AegisPaymentTerms:
    """Re-derive the payment terms from the stored decision, never from a request body.

    The winner's recipient lives on the priced candidate rather than on the winner entry,
    so it is looked up by the candidate key the decision itself recorded. The terms hash
    is then recomputed from those fields and compared with the stored one: a decision
    whose binding was edited after capture can never authorize a payment.
    """
    if not events or events[0].type != EventType.REQUESTED:
        raise PaymentEvidenceError("purchase evidence is missing")
    requested = events[0]
    if not is_aa_policy_request(requested.payload):
        raise PaymentEvidenceError("purchase is not an aegis-aa-v1 request")
    decided = next((event for event in events if event.type == EventType.DECIDED), None)
    if decided is None:
        raise PaymentEvidenceError("purchase has no recorded decision")
    if decided.payload.get("scoringPolicyVersion") != AA_SCORING_POLICY_VERSION:
        raise PaymentEvidenceError("decision is not aa-three-factor-v1")
    snapshot = next(
        (event for event in events if event.type == EventType.AA_SNAPSHOT_RECORDED), None
    )
    if snapshot is None:
        raise PaymentEvidenceError("decision has no AA snapshot evidence")
    if decided.payload.get("snapshotHash") != snapshot.payload.get("snapshotHash"):
        raise PaymentEvidenceError("decision cites a different AA snapshot")

    winner = _mapping(decided.payload.get("winner"), field="decision winner")
    candidate_key = _string(winner, "candidateKey", field="decision winner candidate key")
    candidates = decided.payload.get("candidates")
    if not isinstance(candidates, list):
        raise PaymentEvidenceError("decision candidates are malformed")
    matches = [
        item
        for item in candidates
        if isinstance(item, dict) and item.get("candidateKey") == candidate_key
    ]
    if len(matches) != 1:
        raise PaymentEvidenceError("decision winner is not a unique priced candidate")
    priced = matches[0]

    amount_units = _units(decided.payload.get("amountUnits"), field="decision amount")
    if amount_units == 0:
        raise PaymentPolicyError("zero_payment_amount_unsupported")
    for source, field in ((winner, "decision winner amount"), (priced, "candidate amount")):
        if _units(source.get("amountUnits"), field=field) != amount_units:
            raise PaymentEvidenceError("decision amount disagrees with its winner")
    provider_id = _string(winner, "providerId", field="decision winner provider")
    provider_model_id = _string(winner, "providerModelId", field="decision winner model")
    model_version = _string(winner, "modelVersion", field="decision winner model version")
    if (
        priced.get("providerId") != provider_id
        or priced.get("providerModelId") != provider_model_id
        or priced.get("modelVersion") != model_version
    ):
        raise PaymentEvidenceError("decision winner identity disagrees with its candidate")
    recipient = _string(priced, "recipient", field="decision winner recipient").lower()
    token = _mapping(decided.payload.get("token"), field="decision token identity")
    snapshot_hash = _string(decided.payload, "snapshotHash", field="decision snapshot hash")
    stored_binding = _string(
        decided.payload, "termsBindingHash", field="decision terms binding"
    )
    recomputed = terms_binding_hash(
        purchase_id=decided.purchase_id,
        snapshot_hash=snapshot_hash,
        provider_id=provider_id,
        provider_model_id=provider_model_id,
        model_version=model_version,
        amount_units=amount_units,
        recipient=recipient,
        token=token,
    )
    if recomputed != stored_binding:
        raise PaymentEvidenceError("decision terms binding does not match its own terms")

    policy = requested.payload.get("policy")
    raw_limit = policy.get("maxTransactionUnits") if isinstance(policy, dict) else None
    return AegisPaymentTerms(
        purchase_id=decided.purchase_id,
        owner_address=_string(requested.actor, "id", field="owner address").lower(),
        budget_units=_units(requested.payload.get("budgetUnits"), field="request budget"),
        max_transaction_units=(
            None if raw_limit is None else _units(raw_limit, field="request transaction limit")
        ),
        decision_event_hash=decided.event_hash,
        snapshot_hash=snapshot_hash,
        terms_binding_hash=stored_binding,
        amount_units=amount_units,
        recipient=recipient,
        provider_id=provider_id,
        provider_model_id=provider_model_id,
        model_version=model_version,
        token=token,
        event_count=len(events),
        head_event_hash=events[-1].event_hash,
    )


class AegisPaymentService:
    """Reserve one attempt per purchase and record the Facilitator outcome."""

    def __init__(
        self,
        *,
        repository: PaymentRepository,
        clock: Clock,
        execution_mode: str = MOCK_EXECUTION_MODE,
    ) -> None:
        """`execution_mode` is server configuration, never a request field.

        A caller that could label its own attempt `live` could make mock evidence read as
        a real transfer, so the mode is fixed here and written into every record.
        """
        normalized = execution_mode.strip().lower()
        if normalized not in EXECUTION_MODES:
            raise ValueError("execution mode must be mock or live")
        self._repository = repository
        self._clock = clock
        self._execution_mode = normalized

    @property
    def execution_mode(self) -> str:
        return self._execution_mode

    async def _verified_events(self, purchase_id: str) -> list[EvidenceEvent]:
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
        return events

    async def terms(self, purchase_id: str) -> AegisPaymentTerms:
        return read_terms(await self._verified_events(purchase_id))

    async def reserve(self, purchase_id: str) -> tuple[AegisPaymentTerms, PaymentIntent]:
        """Claim the single payment attempt of this purchase, or return the existing one.

        The claim is one atomic unit with the budget reservation and the append-only
        `PAYMENT_INTENT_CLAIMED` event, so concurrent callers and a restarted executor all
        converge on one intent, one amount and one ERC-3009 nonce.
        """
        events = await self._verified_events(purchase_id)
        terms = read_terms(events)
        existing = await self._repository.get_payment_intent(purchase_id)
        if existing is not None:
            if (
                existing.quote_id != terms.terms_binding_hash
                or existing.decision_event_hash != terms.decision_event_hash
                or existing.amount_units != terms.amount_units
                or existing.token != str(terms.token.get("address", "")).lower()
                or existing.pay_to != terms.recipient
            ):
                raise PaymentConflictError("payment intent immutable binding changed")
            return terms, existing
        if terms.amount_units > terms.budget_units:
            raise PaymentPolicyError("selected model exceeds the request budget")
        if (
            terms.max_transaction_units is not None
            and terms.amount_units > terms.max_transaction_units
        ):
            raise PaymentPolicyError("selected model exceeds the request transaction limit")
        binding = await self._repository.get_wallet_binding(terms.owner_address)
        if binding is None:
            raise PaymentPolicyError("buyer wallet is not bound")
        token_address = str(terms.token.get("address", "")).lower()
        if not token_address.startswith("0x"):
            raise PaymentEvidenceError("decision token address is malformed")
        now = self._clock.now()
        intent = PaymentIntent(
            purchase_id=purchase_id,
            buyer_wallet_address=binding.buyer_wallet_address.lower(),
            policy_date=now.date().isoformat(),
            # The new runtime has no negotiated quote. This identity is the fixed terms
            # binding of the decision, and it is recorded under that name in evidence.
            quote_id=terms.terms_binding_hash,
            decision_event_hash=terms.decision_event_hash,
            amount_units=terms.amount_units,
            token=token_address,
            pay_to=terms.recipient,
            permit2_nonce=None,
            state=PaymentIntentState.CLAIMED,
            claimed_at=now,
            transfer_method="eip3009",
            authorization_nonce="0x" + secrets.token_hex(32),
        )
        claimed = await self._repository.claim_payment_intent(
            intent=intent,
            occurred_at=now,
            actor={"id": "payment-executor", "type": "service"},
            payload={
                "amountUnits": intent.amount_units,
                "authorizationNonce": intent.authorization_nonce,
                "buyerWalletAddress": intent.buyer_wallet_address,
                "decisionEventHash": intent.decision_event_hash,
                "modelVersion": terms.model_version,
                "payTo": intent.pay_to,
                "providerId": terms.provider_id,
                "providerModelId": terms.provider_model_id,
                "quoteId": intent.quote_id,
                "scoringPolicyVersion": AA_SCORING_POLICY_VERSION,
                "snapshotHash": terms.snapshot_hash,
                "termsBindingHash": terms.terms_binding_hash,
                "token": intent.token,
                "transferMethod": intent.transfer_method,
            },
            evidence_refs=(terms.decision_event_hash, terms.terms_binding_hash),
            expected_event_count=terms.event_count,
            expected_head_event_hash=terms.head_event_hash,
        )
        return terms, claimed

    async def _transition_context(
        self, purchase_id: str
    ) -> tuple[AegisPaymentTerms, PaymentIntent, list[EvidenceEvent]]:
        events = await self._verified_events(purchase_id)
        terms = read_terms(events)
        intent = await self._repository.get_payment_intent(purchase_id)
        if intent is None:
            raise PaymentEvidenceError("payment intent is missing")
        if intent.quote_id != terms.terms_binding_hash:
            raise PaymentConflictError("payment intent is bound to different terms")
        return terms, intent, events

    async def settle(
        self,
        *,
        purchase_id: str,
        facilitator_transaction: str,
        facilitator_network: str,
        facilitator_payer: str,
        facilitator_amount: str | None = None,
    ) -> PaymentIntent:
        """Record the Facilitator's success answer as the payment status basis.

        A settled purchase is terminal: a repeated identical answer is idempotent and a
        different answer is a conflict, so a delivery failure after settlement can never
        produce a second payment.
        """
        reference = facilitator_transaction.strip()
        if not reference:
            raise PaymentEvidenceError("facilitator settlement reference is required")
        if reference.startswith("0x") and self._execution_mode == MOCK_EXECUTION_MODE:
            # Mock Facilitators must not create a record that resembles a live Base
            # Sepolia payment. In live mode the external Facilitator's transaction hash
            # is its settlement reference; the application still does not query a
            # receipt, Transfer, or AuthorizationUsed event to validate it.
            raise PaymentEvidenceError(
                "mock facilitator settlement reference must not claim an EVM transaction hash"
            )
        payer = facilitator_payer.strip().lower()
        terms, intent, events = await self._transition_context(purchase_id)
        network = facilitator_network.strip()
        if network != terms.network:
            # A settlement on another chain is not this purchase's settlement, however
            # successful the Facilitator claims it was.
            raise PaymentEvidenceError(
                f"facilitator network {network!r} is not the decided network {terms.network!r}"
            )
        if payer != intent.buyer_wallet_address:
            raise PaymentEvidenceError("facilitator payer is not the bound buyer wallet")
        if facilitator_amount is not None and facilitator_amount != str(intent.amount_units):
            raise PaymentEvidenceError(
                f"facilitator settled {facilitator_amount}, not the reserved "
                f"{intent.amount_units}"
            )
        outcome_key = terminal_outcome_key(purchase_id)
        payload: JsonObject = {
            "amountUnits": intent.amount_units,
            "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
            "executionMode": self._execution_mode,
            "facilitatorNetwork": network,
            "facilitatorPayer": payer,
            # The original answer is preserved beside the derived fields, so an audit can
            # always see what the Facilitator actually said rather than our reading of it.
            "facilitatorResponse": facilitator_response_payload(
                success=True,
                network=network,
                transaction=reference,
                payer=payer,
                amount=facilitator_amount,
            ),
            "settlementReference": reference,
            "terminalOutcomeKey": outcome_key,
            "termsBindingHash": terms.terms_binding_hash,
            "to": intent.pay_to,
            "token": intent.token,
            "verificationBasis": FACILITATOR_VERIFICATION_BASIS,
        }
        if intent.state is PaymentIntentState.SETTLED:
            settled = next(
                (event for event in events if event.type == EventType.PAYMENT_SETTLED), None
            )
            if settled is not None and settled.payload == payload:
                return intent
            raise PaymentConflictError("payment has a different settlement proof")
        if intent.state in TERMINAL_PAYMENT_INTENT_STATES:
            raise PaymentConflictError("payment already has a different terminal outcome")
        if intent.state is not PaymentIntentState.AUTHORIZED:
            raise PaymentConflictError("payment cannot settle from current state")
        now = self._clock.now()
        return await self._repository.transition_payment_intent(
            next_intent=replace(
                intent,
                state=PaymentIntentState.SETTLED,
                settlement_verified_at=now,
                terminal_outcome_key=outcome_key,
                terminal_proof_ref=reference,
                terminal_evidence_source=EvidenceSource.SYNTHETIC_LOCAL,
            ),
            expected_states=(PaymentIntentState.AUTHORIZED,),
            event_type=EventType.PAYMENT_SETTLED,
            occurred_at=now,
            actor={"id": "x402-facilitator", "type": "service"},
            payload=payload,
            evidence_refs=(
                intent.decision_authorization_hash or terms.terms_binding_hash,
                terms.terms_binding_hash,
            ),
            expected_event_count=len(events),
            expected_head_event_hash=events[-1].event_hash,
            reservation_action="settle",
        )

    async def fail(self, *, purchase_id: str, reason: str) -> PaymentIntent:
        """Record a rejected attempt and release the reservation, once and for all."""
        return await self._fail(purchase_id=purchase_id, reason=reason)

    async def record_ambiguous_settlement(
        self,
        *,
        purchase_id: str,
        mismatch_reason: str,
        success: bool,
        network: str,
        transaction: str,
        payer: str | None,
        amount: str | None,
    ) -> PaymentIntent:
        """Park an attempt whose Facilitator answer contradicts the terms it answers.

        A success that names the wrong amount, network or payer - or names no payer at
        all - is not evidence that nothing happened. Money may well have moved, so this is
        neither a settlement nor a failure: the reservation is **held**, the purchase
        leaves the authorized state only towards reconciliation, and the original answer is
        stored verbatim next to the reason it was refused. Recording it as a failure would
        release the budget for a payment that may already have been made.
        """
        reason = mismatch_reason.strip()
        if not reason:
            raise PaymentEvidenceError("a mismatch reason is required")
        terms, intent, events = await self._transition_context(purchase_id)
        response = facilitator_response_payload(
            success=success,
            network=network.strip(),
            transaction=transaction.strip(),
            payer=None if payer is None else payer.strip().lower(),
            amount=None if amount is None else amount.strip(),
        )
        payload: JsonObject = {
            "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
            "executionMode": self._execution_mode,
            "facilitatorResponse": response,
            "mismatchReason": reason,
            "reason": reason,
            "termsBindingHash": terms.terms_binding_hash,
            "verificationBasis": FACILITATOR_VERIFICATION_BASIS,
        }
        if intent.state is PaymentIntentState.RECONCILIATION_REQUIRED:
            recorded = next(
                (
                    event
                    for event in events
                    if event.type == EventType.PAYMENT_RECONCILIATION_REQUIRED
                ),
                None,
            )
            if recorded is not None and recorded.payload == payload:
                return intent
            raise PaymentConflictError("payment is already parked with a different answer")
        if intent.state in TERMINAL_PAYMENT_INTENT_STATES:
            raise PaymentConflictError("payment already has a terminal outcome")
        if intent.state is not PaymentIntentState.AUTHORIZED:
            raise PaymentConflictError("payment cannot enter reconciliation from this state")
        return await self._repository.transition_payment_intent(
            next_intent=replace(
                intent,
                state=PaymentIntentState.RECONCILIATION_REQUIRED,
                reconciliation_reason=reason,
            ),
            expected_states=(PaymentIntentState.AUTHORIZED,),
            event_type=EventType.PAYMENT_RECONCILIATION_REQUIRED,
            occurred_at=self._clock.now(),
            actor={"id": "payment-executor", "type": "service"},
            payload=payload,
            evidence_refs=(terms.decision_event_hash, terms.terms_binding_hash),
            expected_event_count=len(events),
            expected_head_event_hash=events[-1].event_hash,
            # The budget stays reserved: an ambiguous attempt must not free it for another.
            reservation_action="hold",
        )

    async def _fail(self, *, purchase_id: str, reason: str) -> PaymentIntent:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise PaymentEvidenceError("failure reason is required")
        terms, intent, events = await self._transition_context(purchase_id)
        outcome_key = terminal_outcome_key(purchase_id)
        payload: JsonObject = {
            "evidenceSource": EvidenceSource.SYNTHETIC_LOCAL.value,
            "executionMode": self._execution_mode,
            "reason": normalized_reason,
            "terminalOutcomeKey": outcome_key,
            "termsBindingHash": terms.terms_binding_hash,
            "verificationBasis": FACILITATOR_VERIFICATION_BASIS,
        }
        if intent.state is PaymentIntentState.FAILED:
            failed = next(
                (event for event in events if event.type == EventType.PAYMENT_FAILED), None
            )
            if failed is not None and failed.payload == payload:
                return intent
            raise PaymentConflictError("payment has a different failure reason")
        if intent.state in TERMINAL_PAYMENT_INTENT_STATES:
            raise PaymentConflictError("payment already has a different terminal outcome")
        if intent.state not in (PaymentIntentState.CLAIMED, PaymentIntentState.AUTHORIZED):
            raise PaymentConflictError("payment cannot fail from current state")
        now = self._clock.now()
        return await self._repository.transition_payment_intent(
            next_intent=replace(
                intent,
                state=PaymentIntentState.FAILED,
                failure_reason=normalized_reason,
                failed_at=now,
                terminal_outcome_key=outcome_key,
                terminal_proof_ref=None,
                terminal_evidence_source=EvidenceSource.SYNTHETIC_LOCAL,
            ),
            expected_states=(PaymentIntentState.CLAIMED, PaymentIntentState.AUTHORIZED),
            event_type=EventType.PAYMENT_FAILED,
            occurred_at=now,
            actor={"id": "payment-executor", "type": "service"},
            payload=payload,
            evidence_refs=(terms.decision_event_hash, terms.terms_binding_hash),
            expected_event_count=len(events),
            expected_head_event_hash=events[-1].event_hash,
            reservation_action="release",
        )

    async def delivery_context(
        self, purchase_id: str
    ) -> tuple[AegisPaymentTerms, PaymentIntent, list[EvidenceEvent]]:
        """The settled context a delivery record must match, or an explicit refusal."""
        try:
            terms, intent, events = await self._transition_context(purchase_id)
        except PaymentEvidenceError as exc:
            if "payment intent is missing" not in str(exc):
                raise
            # The purchase exists; it simply has nothing settled to deliver against.
            raise PaymentConflictError("delivery requires a settled payment") from exc
        if intent.state is not PaymentIntentState.SETTLED:
            raise PaymentConflictError("delivery requires a settled payment")
        return terms, intent, events


def has_terminal_payment(events: list[EvidenceEvent]) -> bool:
    return any(event.type in TERMINAL_PAYMENT_EVENT_TYPES for event in events)
