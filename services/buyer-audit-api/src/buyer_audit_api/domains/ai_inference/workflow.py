from __future__ import annotations

from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import EventType, EvidenceEvent
from buyer_audit_api.core.ports import Clock, EvidenceRepository
from buyer_audit_api.domains.ai_inference.models import (
    BenchmarkSnapshot,
    Candidate,
    NormalizedAiRequest,
    SelectionDecision,
    SellerIdentityEvidence,
    SellerQuote,
)
from buyer_audit_api.domains.ai_inference.ports import (
    BenchmarkProvider,
    SelectionExplanationPort,
    SellerIdentityVerifier,
    SellerQuoteClient,
    SellerQuoteVerifier,
)
from buyer_audit_api.domains.ai_inference.selection import SelectionEngine

_AUXILIARY_EVENT_TYPES = {
    EventType.SENSITIVE_PAYLOAD_ACCESSED,
    EventType.CORRECTION_RECORDED,
}


def _validate_quote_binding(purchase_id: str, quote: SellerQuote) -> None:
    if quote.purchase_id != purchase_id:
        raise ValueError("quote purchase binding mismatch")
    if not quote.signature.startswith("0x"):
        raise ValueError("quote signature must be hex encoded")
    if not quote.signer_address.startswith("0x"):
        raise ValueError("quote signer must be an address")


class DeterministicExplanationAdapter:
    async def explain(self, *, deterministic_explanation: str) -> str:
        return deterministic_explanation


class AiInferenceDecisionWorkflow:
    def __init__(
        self,
        *,
        repository: EvidenceRepository,
        clock: Clock,
        benchmarks: BenchmarkProvider,
        quotes: SellerQuoteClient,
        quote_verifier: SellerQuoteVerifier,
        identity_verifier: SellerIdentityVerifier,
        explanation: SelectionExplanationPort,
        selection: SelectionEngine | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._benchmarks = benchmarks
        self._quotes = quotes
        self._quote_verifier = quote_verifier
        self._identity_verifier = identity_verifier
        self._explanation = explanation
        self._selection = selection or SelectionEngine()

    async def _load_verified_events(self, purchase_id: str) -> list[EvidenceEvent]:
        events = await self._repository.list_events(purchase_id)
        head = await self._repository.get_event_head(purchase_id)
        if head is None:
            raise ValueError("missing immutable evidence head")
        verify_event_chain(
            events,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )
        if any(event.purchase_id != purchase_id for event in events):
            raise ValueError("cross-purchase evidence in chain")
        return events

    @staticmethod
    def _requested_context(
        events: list[EvidenceEvent],
        *,
        require_pre_quote: bool,
    ) -> tuple[NormalizedAiRequest, int]:
        requested = [event for event in events if event.type == EventType.REQUESTED]
        if len(requested) != 1 or not events or events[0].type != EventType.REQUESTED:
            raise ValueError("exactly one leading REQUESTED event is required")
        business_projection = [
            event.type for event in events if event.type not in _AUXILIARY_EVENT_TYPES
        ]
        if require_pre_quote:
            if business_projection != [EventType.REQUESTED]:
                raise ValueError("purchase is not ready for quoting")
        elif business_projection != [EventType.REQUESTED, EventType.QUOTED]:
            raise ValueError("invalid REQUESTED to QUOTED lifecycle")
        payload = requested[0].payload
        raw_request = payload.get("normalizedRequest")
        raw_budget = payload.get("budgetUnits")
        raw_policy = payload.get("policy")
        if not isinstance(raw_request, dict):
            raise ValueError("persisted REQUESTED normalized request is malformed")
        if not isinstance(raw_budget, int) or isinstance(raw_budget, bool) or raw_budget < 0:
            raise ValueError("persisted REQUESTED budget is malformed")
        if not isinstance(raw_policy, dict):
            raise ValueError("persisted REQUESTED policy is malformed")
        return NormalizedAiRequest.model_validate(raw_request), raw_budget

    async def _load_persisted_candidates(
        self, purchase_id: str
    ) -> tuple[list[Candidate], int, str]:
        events = await self._load_verified_events(purchase_id)
        self._requested_context(events, require_pre_quote=False)
        quoted_events = [event for event in events if event.type == EventType.QUOTED]
        payload = quoted_events[0].payload
        raw_snapshots = payload.get("benchmarkSnapshots")
        raw_quotes = payload.get("signedQuotes")
        raw_identity = payload.get("quoteIdentityEvidence")
        if (
            not isinstance(raw_snapshots, list)
            or not isinstance(raw_quotes, list)
            or not isinstance(raw_identity, list)
        ):
            raise ValueError("persisted QUOTED evidence is malformed")
        snapshots = [BenchmarkSnapshot.model_validate(value) for value in raw_snapshots]
        identities = [SellerIdentityEvidence.model_validate(value) for value in raw_identity]
        identity_by_quote = {value.quote_id: value for value in identities}
        if len(identity_by_quote) != len(raw_identity):
            raise ValueError("persisted quote identity evidence is malformed")
        quotes = [
            SellerQuote.model_validate(
                {
                    **value,
                    "identity_verified": identity_by_quote[
                        str(value.get("quote_id"))
                    ].identity_verified,
                }
            )
            for value in raw_quotes
            if isinstance(value, dict)
        ]
        if len(quotes) != len(raw_quotes):
            raise ValueError("persisted signed quote evidence is malformed")
        candidates: list[Candidate] = []
        used_snapshot_ids: set[str] = set()
        for quote in quotes:
            _validate_quote_binding(purchase_id, quote)
            identity = identity_by_quote.get(quote.quote_id)
            if identity is None or (
                identity.erc8004_agent_id != quote.erc8004_agent_id
                or identity.signer_address.lower() != quote.signer_address.lower()
                or identity.agent_wallet.lower() != quote.signer_address.lower()
            ):
                raise ValueError("quote and ERC-8004 identity binding mismatch")
            if not self._quote_verifier.verify(quote):
                raise ValueError("invalid persisted seller quote signature")
            matches = [
                snapshot
                for snapshot in snapshots
                if (
                    snapshot.provider_id == quote.provider_id
                    and snapshot.model_id == quote.model_id
                    and snapshot.model_version == quote.model_version
                )
            ]
            if len(matches) != 1 or matches[0].snapshot_id in used_snapshot_ids:
                raise ValueError("quote and benchmark binding mismatch")
            used_snapshot_ids.add(matches[0].snapshot_id)
            candidates.append(Candidate(quote=quote, benchmark=matches[0]))
        if len(candidates) != len(snapshots):
            raise ValueError("quote and benchmark cardinality mismatch")
        return candidates, len(events), events[-1].event_hash

    async def decide(
        self,
        *,
        purchase_id: str,
        normalized_request: NormalizedAiRequest,
        budget_units: int,
    ) -> SelectionDecision:
        initial_events = await self._load_verified_events(purchase_id)
        business_projection = [
            event.type
            for event in initial_events
            if event.type not in _AUXILIARY_EVENT_TYPES
        ]
        if business_projection == [EventType.REQUESTED]:
            persisted_request, persisted_budget = self._requested_context(
                initial_events,
                require_pre_quote=True,
            )
        elif business_projection == [EventType.REQUESTED, EventType.QUOTED]:
            persisted_request, persisted_budget = self._requested_context(
                initial_events,
                require_pre_quote=False,
            )
        else:
            raise ValueError("purchase is not ready for quoting or decision")
        if normalized_request != persisted_request:
            raise ValueError("normalized request does not match persisted REQUESTED")
        if budget_units != persisted_budget:
            raise ValueError("budget does not match persisted REQUESTED")

        now = self._clock.now()
        if business_projection == [EventType.REQUESTED]:
            snapshots = await self._benchmarks.candidates(persisted_request)
            if not snapshots:
                raise ValueError("no benchmark snapshots")

            candidates: list[Candidate] = []
            identity_evidence: list[SellerIdentityEvidence] = []
            for snapshot in snapshots[:3]:
                quote = await self._quotes.quote(
                    purchase_id=purchase_id,
                    request=persisted_request,
                    benchmark=snapshot,
                )
                _validate_quote_binding(purchase_id, quote)
                if not self._quote_verifier.verify(quote):
                    raise ValueError("invalid seller quote signature")
                identity = await self._identity_verifier.verify(quote)
                if (
                    identity.quote_id != quote.quote_id
                    or identity.erc8004_agent_id != quote.erc8004_agent_id
                    or identity.signer_address.lower() != quote.signer_address.lower()
                    or identity.agent_wallet.lower() != quote.signer_address.lower()
                ):
                    raise ValueError("quote and ERC-8004 identity binding mismatch")
                quote = quote.model_copy(
                    update={"identity_verified": identity.identity_verified}
                )
                identity_evidence.append(identity)
                candidates.append(Candidate(quote=quote, benchmark=snapshot))

            await self._repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.QUOTED,
                occurred_at=now,
                actor={"id": "buyer-orchestrator", "type": "service"},
                payload={
                    "benchmarkSnapshots": [
                        candidate.benchmark.model_dump(mode="json")
                        for candidate in candidates
                    ],
                    "signedQuotes": [
                        candidate.quote.model_dump(
                            mode="json", exclude={"identity_verified"}
                        )
                        for candidate in candidates
                    ],
                    "quoteIdentityEvidence": [
                        evidence.model_dump(mode="json", by_alias=True)
                        for evidence in identity_evidence
                    ],
                },
                evidence_refs=tuple(
                    sorted(
                        [candidate.benchmark.snapshot_id for candidate in candidates]
                        + [candidate.quote.quote_id for candidate in candidates]
                    )
                ),
                expected_event_count=len(initial_events),
                expected_head_event_hash=initial_events[-1].event_hash,
            )
        candidates, quoted_event_count, quoted_head_hash = await self._load_persisted_candidates(
            purchase_id
        )
        decision = self._selection.decide(
            request=persisted_request,
            budget_units=persisted_budget,
            candidates=candidates,
            now=now,
        )
        generated_explanation = await self._explanation.explain(
            deterministic_explanation=decision.explanation
        )
        decision_payload = decision.model_dump(mode="json")
        decision_payload["generatedExplanation"] = generated_explanation
        await self._repository.append_event(
            purchase_id=purchase_id,
            event_type=EventType.DECIDED,
            occurred_at=now,
            actor={"id": "buyer-agent", "type": "agent"},
            payload=decision_payload,
            evidence_refs=(decision.winner.quote_id, *decision.benchmark_snapshot_ids),
            expected_event_count=quoted_event_count,
            expected_head_event_hash=quoted_head_hash,
        )
        return decision
