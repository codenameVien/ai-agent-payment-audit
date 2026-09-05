"""Terminal audit orchestration: the only writer of `AUDITED + REPUTATION_DECIDED`.

Design 18.7/18.8. `AuditEvaluator` decides *what* the audit says and
`ReputationDecisionPolicy` decides *what* the feedback should be; this module decides
*when*, and makes the audit, the decision and the outbox job appear together or not at all
(`P6-AC-05.3`, `P6-NFR-IDEM-01`).

Everything here is atomic through `TerminalOrchestrationPort.finalize_atomic`, so a worker
race or a process restart cannot produce two decisions or two jobs for one purchase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from buyer_audit_api.core.audit import (
    RULESET_VERSION,
    AuditDraft,
    AuditEvaluator,
    AuditReport,
    AuditReportReader,
    is_final_eligible,
    require_current_audit,
)
from buyer_audit_api.core.errors import (
    EvidenceIntegrityError,
    EvidenceTransitionError,
    PaymentConflictError,
)
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import (
    EventType,
    EvidenceEvent,
    EvidenceSource,
    JsonObject,
)
from buyer_audit_api.core.payment import PaymentIntent, PaymentIntentState
from buyer_audit_api.core.ports import (
    Clock,
    ReputationOutboxPort,
    TerminalOrchestrationPort,
)
from buyer_audit_api.core.projections import PaymentStatus, PurchaseProjectionService
from buyer_audit_api.core.reputation import (
    DecisionKind,
    OutboxStatus,
    PublishIdentity,
    ReputationDecision,
    ReputationDecisionPolicy,
    ReputationPublishJob,
    ReputationReason,
    SellerAttribution,
    decision_from_event,
)

DEFAULT_LEASE = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class TerminalResult:
    """What one `finalize_if_eligible` call observed or created."""

    purchase_id: str
    audit: AuditReport | None
    decision: ReputationDecision | None
    job: ReputationPublishJob | None
    finalized: bool
    reason: str

    @property
    def created_job(self) -> bool:
        return self.finalized and self.job is not None


class TerminalAuditCoordinator:
    """Finalizes a terminal audit and its reputation decision in one atomic step."""

    def __init__(
        self,
        *,
        orchestration: TerminalOrchestrationPort,
        outbox: ReputationOutboxPort,
        clock: Clock,
        chain_id: int,
        registry_address: str,
        policy: ReputationDecisionPolicy | None = None,
        evaluator: AuditEvaluator | None = None,
        projections: PurchaseProjectionService | None = None,
    ) -> None:
        self._orchestration = orchestration
        self._outbox = outbox
        self._clock = clock
        self._chain_id = chain_id
        self._registry_address = registry_address.lower()
        self._policy = policy or ReputationDecisionPolicy()
        self._evaluator = evaluator or AuditEvaluator()
        self._reader = AuditReportReader()
        self._projections = projections or PurchaseProjectionService()

    async def finalize_if_eligible(self, purchase_id: str) -> TerminalResult:
        """Idempotent. Returns the existing decision when one is already persisted."""
        events, intent = await self._verified_context(purchase_id)

        existing_decision = self._persisted_decision(events)
        if existing_decision is not None:
            persisted = self._reader.persisted_audit(events)
            job = await self._outbox.get_by_identity(
                self._identity(purchase_id, existing_decision.seller_agent_id).identity_hash
            )
            return TerminalResult(
                purchase_id=purchase_id,
                audit=None if persisted is None else persisted.report,
                decision=existing_decision,
                job=job,
                finalized=False,
                reason="decision already persisted",
            )

        if not is_final_eligible(events):
            # P6-AC-03.4/P6-AC-05.1: a preview never produces a decision or a job.
            return TerminalResult(
                purchase_id=purchase_id,
                audit=None,
                decision=None,
                job=None,
                finalized=False,
                reason="purchase is not terminal",
            )

        # A purchase audited by the read-side `AuditService` has no decision yet. Reuse
        # that persisted audit instead of appending a second one; P6-AC-03.5 still applies,
        # so a stale or wrong-ruleset audit fails closed here.
        persisted = self._reader.persisted_audit(events)
        if persisted is not None:
            require_current_audit(persisted)
            audit_report = persisted.report
            draft = None
        else:
            draft = self._evaluator.evaluate(events)
            if not draft.final_eligible:
                return TerminalResult(
                    purchase_id=purchase_id,
                    audit=None,
                    decision=None,
                    job=None,
                    finalized=False,
                    reason="audit draft is not final eligible",
                )
            audit_report = draft.as_report()

        attribution = self._attribution(events)
        projection = self._projections.project(events, intent)
        decision = self._policy.decide(
            audit=audit_report,
            payment_status=projection.payment_status,
            payment_state=None if intent is None else intent.state,
            attribution=attribution,
            conflicting_proof=self._has_conflicting_proof(events),
        )
        identity = self._identity(purchase_id, decision.seller_agent_id)
        return await self._finalize(
            purchase_id=purchase_id,
            events=events,
            draft=draft,
            audit=audit_report,
            decision=decision,
            identity=identity,
        )

    async def recover_pending(self, purchase_ids: list[str]) -> list[TerminalResult]:
        """Design 18.7.5: only purchases with terminal evidence but no audit/decision.

        The sweep never re-audits a finalized purchase and never touches a job that is
        already terminal, so restarting it is free of external effects.
        """
        results: list[TerminalResult] = []
        for purchase_id in purchase_ids:
            try:
                results.append(await self.finalize_if_eligible(purchase_id))
            except (EvidenceIntegrityError, PaymentConflictError) as exc:
                results.append(
                    TerminalResult(
                        purchase_id=purchase_id,
                        audit=None,
                        decision=None,
                        job=None,
                        finalized=False,
                        reason=f"skipped: {exc}",
                    )
                )
        return results

    async def _finalize(
        self,
        *,
        purchase_id: str,
        events: list[EvidenceEvent],
        draft: AuditDraft | None,
        audit: AuditReport,
        decision: ReputationDecision,
        identity: PublishIdentity,
    ) -> TerminalResult:
        now = self._clock.now()
        fingerprint = decision.payload_fingerprint(identity)
        audit_payload: JsonObject | None = None
        if draft is not None:
            audit_payload = {
                "auditBundleHash": draft.audit_bundle_hash,
                "evidenceHeadEventHash": draft.evidence_head_event_hash,
                "findings": draft.finding_payloads,
                "reportId": draft.report_id,
                "rulesetVersion": draft.ruleset_version,
                "severity": draft.severity.value,
            }
        decision_payload: JsonObject = {
            **decision.to_payload(),
            "evidenceSource": EvidenceSource.BASE_SEPOLIA_VERIFIED.value,
            "payloadFingerprint": fingerprint,
            "publishIdentity": identity.to_payload(),
            "publishIdentityHash": identity.identity_hash,
            "tag1": identity.tag1,
            "tag2": identity.tag2,
        }
        try:
            audit_event, decision_event, job = await self._orchestration.finalize_atomic(
                purchase_id=purchase_id,
                audit_payload=audit_payload,
                audit_evidence_refs=(
                    audit.evidence_head_event_hash,
                    audit.audit_bundle_hash,
                ),
                decision_payload=decision_payload,
                decision_evidence_refs=(audit.audit_bundle_hash, identity.identity_hash),
                occurred_at=now,
                expected_event_count=len(events),
                expected_head_event_hash=events[-1].event_hash,
                identity=identity,
                identity_hash=identity.identity_hash,
                payload_fingerprint=fingerprint,
                decision=decision,
                status=(
                    OutboxStatus.PENDING
                    if decision.publishes
                    else OutboxStatus.DEFERRED
                ),
            )
        except EvidenceTransitionError:
            # Another worker won the head race. Re-read and answer with its result.
            raced_events, _ = await self._verified_context(purchase_id)
            raced_decision = self._persisted_decision(raced_events)
            if raced_decision is None:
                raise
            persisted = self._reader.persisted_audit(raced_events)
            return TerminalResult(
                purchase_id=purchase_id,
                audit=None if persisted is None else persisted.report,
                decision=raced_decision,
                job=await self._outbox.get_by_identity(identity.identity_hash),
                finalized=False,
                reason="lost the finalize race",
            )
        if audit_payload is not None and (
            audit_event is None or audit_event.type is not EventType.AUDITED
        ):
            raise EvidenceIntegrityError("finalize did not append the terminal audit")
        if decision_event.type is not EventType.REPUTATION_DECIDED:
            raise EvidenceIntegrityError("finalize did not append a reputation decision")
        return TerminalResult(
            purchase_id=purchase_id,
            audit=audit,
            decision=decision,
            job=job,
            finalized=True,
            reason=(
                "published decision enqueued"
                if decision.publishes
                else f"deferred: {decision.reason_codes[0].value}"
            ),
        )

    async def _verified_context(
        self, purchase_id: str
    ) -> tuple[list[EvidenceEvent], PaymentIntent | None]:
        events = await self._orchestration.list_events(purchase_id)
        head = await self._orchestration.get_event_head(purchase_id)
        if not events or head is None:
            raise EvidenceIntegrityError("purchase evidence is missing")
        verify_event_chain(
            events,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )
        intent = await self._orchestration.get_payment_intent(purchase_id)
        if intent is not None and intent.purchase_id != purchase_id:
            raise EvidenceIntegrityError("payment intent belongs to another purchase")
        return events, intent

    def _persisted_decision(self, events: list[EvidenceEvent]) -> ReputationDecision | None:
        decided = [
            event for event in events if event.type == EventType.REPUTATION_DECIDED
        ]
        if not decided:
            return None
        if len(decided) > 1:
            raise EvidenceIntegrityError("multiple reputation decisions exist")
        return decision_from_event(decided[0])

    def _identity(self, purchase_id: str, seller_agent_id: str) -> PublishIdentity:
        return PublishIdentity(
            chain_id=self._chain_id,
            registry_address=self._registry_address,
            purchase_id=purchase_id,
            seller_agent_id=seller_agent_id,
        )

    def _attribution(self, events: list[EvidenceEvent]) -> SellerAttribution:
        """Reads the seller identity from the persisted decision and delivery evidence."""
        winner = self._winning_quote(events)
        delivered = next(
            (event for event in events if event.type == EventType.DELIVERED), None
        )
        payment_terminal = next(
            (
                event
                for event in events
                if event.type
                in (
                    EventType.PAYMENT_MISMATCH_CONFIRMED,
                    EventType.PAYMENT_RECONCILED_NO_TRANSFER,
                    EventType.PAYMENT_FAILED,
                )
            ),
            None,
        )
        if winner is None:
            return SellerAttribution(
                seller_agent_id="unknown",
                erc8004_agent_id="0",
                identity_verified=False,
                delivered_as_quoted=False,
            )
        seller_agent_id, erc8004_agent_id, identity_verified = winner
        delivered_as_quoted = delivered is not None and payment_terminal is None
        if delivered is not None and payment_terminal is None:
            delivered_as_quoted = self._delivery_matches_quote(events, delivered)
        return SellerAttribution(
            seller_agent_id=seller_agent_id,
            erc8004_agent_id=erc8004_agent_id,
            identity_verified=identity_verified,
            delivered_as_quoted=delivered_as_quoted,
        )

    def _winning_quote(
        self, events: list[EvidenceEvent]
    ) -> tuple[str, str, bool] | None:
        decided = next(
            (event for event in events if event.type == EventType.DECIDED), None
        )
        quoted = next(
            (event for event in events if event.type == EventType.QUOTED), None
        )
        if decided is None or quoted is None:
            return None
        winner = decided.payload.get("winner")
        if not isinstance(winner, dict):
            return None
        winning_quote_id = str(winner.get("quote_id", ""))
        quotes = quoted.payload.get("signedQuotes")
        evidence = quoted.payload.get("quoteIdentityEvidence")
        if not isinstance(quotes, list):
            return None
        match = next(
            (
                item
                for item in quotes
                if isinstance(item, dict) and str(item.get("quote_id")) == winning_quote_id
            ),
            None,
        )
        if match is None:
            return None
        # The ERC-8004 agent ID is an *identity* fact, so it comes from the verified
        # `quoteIdentityEvidence` the buyer recorded at selection time - the same source
        # the read models report. A seller-supplied quote field is not identity evidence,
        # and reading it there made a purchase with verified identity defer as
        # `ATTRIBUTION_INSUFFICIENT`.
        identity_verified = False
        agent_id = "0"
        if isinstance(evidence, list):
            identity = next(
                (
                    item
                    for item in evidence
                    if isinstance(item, dict)
                    and str(item.get("quoteId")) == winning_quote_id
                ),
                None,
            )
            if identity is not None:
                identity_verified = identity.get("identityVerified") is True
                raw_agent_id = identity.get("erc8004AgentId")
                if raw_agent_id is not None:
                    agent_id = str(raw_agent_id)
        return (
            str(match.get("seller_agent_id", "")),
            agent_id,
            identity_verified,
        )

    def _delivery_matches_quote(
        self, events: list[EvidenceEvent], delivered: EvidenceEvent
    ) -> bool:
        """Compares the delivery against the **signed quote** the buyer selected.

        The signed quote is the contract the seller is judged against, and it is what the
        delivery endpoint already validates. The `DECIDED.winner` summary only has to
        name the winning quote, so requiring provider/model fields there made a correct
        delivery look unattributable.
        """
        quote = self._winning_signed_quote(events)
        if quote is None:
            return False
        payload = delivered.payload
        for delivered_key, quote_key in (
            ("providerId", "provider_id"),
            ("modelId", "model_id"),
            ("modelVersion", "model_version"),
        ):
            expected = quote.get(quote_key)
            if expected is None:
                continue
            if str(payload.get(delivered_key, "")) != str(expected):
                return False
        return True

    def _winning_signed_quote(
        self, events: list[EvidenceEvent]
    ) -> dict[str, object] | None:
        decided = next(
            (event for event in events if event.type == EventType.DECIDED), None
        )
        quoted = next(
            (event for event in events if event.type == EventType.QUOTED), None
        )
        if decided is None or quoted is None:
            return None
        winner = decided.payload.get("winner")
        quotes = quoted.payload.get("signedQuotes")
        if not isinstance(winner, dict) or not isinstance(quotes, list):
            return None
        winning_quote_id = str(winner.get("quote_id", ""))
        return next(
            (
                item
                for item in quotes
                if isinstance(item, dict)
                and str(item.get("quote_id")) == winning_quote_id
            ),
            None,
        )

    def _has_conflicting_proof(self, events: list[EvidenceEvent]) -> bool:
        """More than one terminal payment proof, or a recorded publication conflict."""
        terminals = [
            event
            for event in events
            if event.type
            in (
                EventType.PAYMENT_SETTLED,
                EventType.PAYMENT_FAILED,
                EventType.PAYMENT_MISMATCH_CONFIRMED,
                EventType.PAYMENT_RECONCILED_NO_TRANSFER,
            )
        ]
        if len({event.type for event in terminals}) > 1:
            return True
        return any(
            event.type == EventType.REPUTATION_PUBLICATION_CONFLICT for event in events
        )


def deferral_reason(decision: ReputationDecision) -> ReputationReason | None:
    return None if decision.kind is DecisionKind.PUBLISH else decision.reason_codes[0]


def payment_status_blocks_publish(status: PaymentStatus) -> bool:
    """`P6-AC-03.4`: unknown confirmation never publishes."""
    return status is PaymentStatus.PAYMENT_CONFIRMATION_UNKNOWN


def state_is_terminal(state: PaymentIntentState | None) -> bool:
    return state is not None and state in {
        PaymentIntentState.SETTLED,
        PaymentIntentState.FAILED,
        PaymentIntentState.MISMATCH_CONFIRMED,
        PaymentIntentState.RECONCILED_NO_TRANSFER,
    }


def audit_ruleset_is_current(audit: AuditReport) -> bool:
    return audit.ruleset_version == RULESET_VERSION
