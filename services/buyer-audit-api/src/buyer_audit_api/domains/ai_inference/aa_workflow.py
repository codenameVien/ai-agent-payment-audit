"""The `aa-three-factor-v1` decision workflow.

Event order is REQUESTED, AA_SNAPSHOT_RECORDED, DECIDED. The snapshot body itself lives
in an immutable content-addressed document owned by the Evidence API; the event carries
only its id, its hash, its provenance and the numbers needed to recognise it, so the
captured evidence can never be edited under a decision that already cites it.

Both appends are guarded by the expected head, so a concurrent retry loses the race
instead of writing a second snapshot or a second decision for one purchase.
"""

from __future__ import annotations

from collections.abc import Mapping

from buyer_audit_api.core.aa_policy import AA_SCORING_POLICY_VERSION
from buyer_audit_api.core.documents import verify_immutable_document
from buyer_audit_api.core.errors import (
    EvidenceIntegrityError,
    ExternalEvidenceError,
    SelectionAbortedError,
)
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.models import EventType, EvidenceEvent, JsonObject
from buyer_audit_api.core.ports import Clock, EvidenceRepository
from buyer_audit_api.domains.ai_inference.aa_catalog import build_aa_snapshot, validate_capture
from buyer_audit_api.domains.ai_inference.aa_models import (
    AA_ATTRIBUTION_URL,
    AA_COMPLETION_BENCHMARK_NOTE,
    AaSelectionDecision,
    AaSnapshot,
    ModelCatalog,
    TokenIdentity,
)
from buyer_audit_api.domains.ai_inference.aa_ports import (
    AaCaptureSource,
    DecisionExplanationPort,
)
from buyer_audit_api.domains.ai_inference.aa_request import AegisNormalizedRequest
from buyer_audit_api.domains.ai_inference.aa_selection import select

#: Events that may appear anywhere without changing the business lifecycle position.
_AUXILIARY_EVENT_TYPES = frozenset(
    {EventType.SENSITIVE_PAYLOAD_ACCESSED, EventType.CORRECTION_RECORDED}
)


class DeterministicDecisionExplanation:
    """Default explanation port: the deterministic sentence, unchanged."""

    async def explain(self, *, deterministic_explanation: str) -> str:
        return deterministic_explanation


class AegisDecisionWorkflow:
    """Captures the AA snapshot and records the fixed decision for one purchase."""

    def __init__(
        self,
        *,
        repository: EvidenceRepository,
        clock: Clock,
        catalog: ModelCatalog,
        source: AaCaptureSource,
        token: TokenIdentity,
        explanation: DecisionExplanationPort | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._catalog = catalog
        self._source = source
        self._token = token
        self._explanation = explanation or DeterministicDecisionExplanation()

    @property
    def catalog(self) -> ModelCatalog:
        return self._catalog

    async def _verified_events(self, purchase_id: str) -> list[EvidenceEvent]:
        events = await self._repository.list_events(purchase_id)
        head = await self._repository.get_event_head(purchase_id)
        if head is None:
            raise EvidenceIntegrityError("missing immutable evidence head")
        verify_event_chain(
            events,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )
        if any(event.purchase_id != purchase_id for event in events):
            raise EvidenceIntegrityError("cross-purchase evidence in chain")
        return events

    @staticmethod
    def _lifecycle(events: list[EvidenceEvent]) -> list[EventType]:
        return [event.type for event in events if event.type not in _AUXILIARY_EVENT_TYPES]

    @staticmethod
    def requested_context(
        events: list[EvidenceEvent],
    ) -> tuple[AegisNormalizedRequest, int]:
        """Read the persisted request; a stored value is never re-normalized here."""
        if not events or events[0].type != EventType.REQUESTED:
            raise EvidenceIntegrityError("exactly one leading REQUESTED event is required")
        payload = events[0].payload
        raw_request = payload.get("normalizedRequest")
        raw_budget = payload.get("budgetUnits")
        if not isinstance(raw_request, dict):
            raise EvidenceIntegrityError("persisted REQUESTED normalized request is malformed")
        if not isinstance(raw_budget, int) or isinstance(raw_budget, bool) or raw_budget < 0:
            raise EvidenceIntegrityError("persisted REQUESTED budget is malformed")
        try:
            request = AegisNormalizedRequest.model_validate(raw_request)
        except ValueError as exc:
            raise EvidenceIntegrityError(
                "persisted request is not an aegis-aa-v1 normalization"
            ) from exc
        return request, raw_budget

    async def _capture_snapshot(
        self,
        *,
        purchase_id: str,
        events: list[EvidenceEvent],
    ) -> None:
        pages = await self._source.fetch_pages()
        capture = validate_capture(pages, field_paths=self._source.field_paths)
        snapshot, document = build_aa_snapshot(
            purchase_id=purchase_id,
            catalog=self._catalog,
            capture=capture,
            mode=self._source.mode,
            fetched_at=self._clock.now(),
            field_paths=self._source.field_paths,
            source_url=self._source.source_url,
        )
        stored = await self._repository.put_document(document)
        await self._repository.append_event(
            purchase_id=purchase_id,
            event_type=EventType.AA_SNAPSHOT_RECORDED,
            occurred_at=snapshot.fetched_at,
            actor={"id": "buyer-orchestrator", "type": "service"},
            payload={
                "attributionUrl": AA_ATTRIBUTION_URL,
                "catalogProvenance": [item.value for item in snapshot.catalog_provenance],
                "catalogVersion": snapshot.catalog_version,
                "completionBenchmark": AA_COMPLETION_BENCHMARK_NOTE,
                "fieldPaths": self._source.field_paths.to_payload(),
                "mode": snapshot.mode.value,
                "pageCount": snapshot.page_count,
                "rawResponseHash": snapshot.raw_response_hash,
                "snapshotHash": stored.content_hash,
                "snapshotId": stored.document_id,
                "sourceUrl": snapshot.source_url,
                "totalModelCount": snapshot.total_model_count,
            },
            evidence_refs=(stored.document_id,),
            expected_event_count=len(events),
            expected_head_event_hash=events[-1].event_hash,
        )

    async def load_snapshot(self, event: EvidenceEvent) -> tuple[AaSnapshot, str]:
        """Re-read the immutable body a stored snapshot event points at."""
        snapshot_id = event.payload.get("snapshotId")
        snapshot_hash = event.payload.get("snapshotHash")
        if not isinstance(snapshot_id, str) or not isinstance(snapshot_hash, str):
            raise EvidenceIntegrityError("stored AA snapshot reference is malformed")
        document = await self._repository.get_document(snapshot_id)
        if document is None:
            raise EvidenceIntegrityError(f"AA snapshot document {snapshot_id} is missing")
        verify_immutable_document(document)
        if document.content_hash != snapshot_hash:
            raise EvidenceIntegrityError("stored AA snapshot hash does not match its document")
        if document.purchase_id != event.purchase_id:
            raise EvidenceIntegrityError("AA snapshot document belongs to another purchase")
        snapshot = AaSnapshot.from_payload(
            {**document.payload, "snapshotId": document.document_id}
        )
        return snapshot, document.content_hash

    async def decide(self, *, purchase_id: str) -> AaSelectionDecision:
        """Idempotent per purchase: an existing decision is returned, never replaced."""
        events = await self._verified_events(purchase_id)
        request, budget_units = self.requested_context(events)
        lifecycle = self._lifecycle(events)
        if lifecycle == [EventType.REQUESTED]:
            await self._capture_snapshot(purchase_id=purchase_id, events=events)
            events = await self._verified_events(purchase_id)
            lifecycle = self._lifecycle(events)
        if lifecycle != [EventType.REQUESTED, EventType.AA_SNAPSHOT_RECORDED]:
            raise EvidenceIntegrityError(
                "purchase is not positioned for an aa-three-factor-v1 decision"
            )
        snapshot_event = events[-1]
        snapshot, snapshot_hash = await self.load_snapshot(snapshot_event)
        decision = select(
            purchase_id=purchase_id,
            catalog=self._catalog,
            snapshot=snapshot,
            snapshot_hash=snapshot_hash,
            request=request,
            budget_units=budget_units,
            token=self._token,
        )
        payload: JsonObject = decision.to_payload()
        payload["generatedExplanation"] = await self._explanation.explain(
            deterministic_explanation=decision.explanation
        )
        await self._repository.append_event(
            purchase_id=purchase_id,
            event_type=EventType.DECIDED,
            occurred_at=self._clock.now(),
            actor={"id": "buyer-agent", "type": "agent"},
            payload=payload,
            evidence_refs=(decision.snapshot_id, decision.terms_binding_hash),
            expected_event_count=len(events),
            expected_head_event_hash=events[-1].event_hash,
        )
        return decision


class AegisEvidenceReader:
    """Read-only access to the fixed decision, for the gateway and the signing module.

    They must never reach MongoDB directly and must never trust a client-supplied amount,
    so this returns the stored decision together with the snapshot body it was computed
    from and the terms binding a 402 challenge has to reproduce.
    """

    def __init__(self, *, repository: EvidenceRepository) -> None:
        self._repository = repository

    async def decision_evidence(self, purchase_id: str) -> JsonObject:
        events = await self._repository.list_events(purchase_id)
        head = await self._repository.get_event_head(purchase_id)
        if head is None:
            raise EvidenceIntegrityError("missing immutable evidence head")
        verify_event_chain(
            events,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )
        decided = next(
            (event for event in events if event.type == EventType.DECIDED), None
        )
        if decided is None:
            raise SelectionAbortedError("this purchase has no recorded decision")
        if decided.payload.get("scoringPolicyVersion") != AA_SCORING_POLICY_VERSION:
            raise SelectionAbortedError(
                "this purchase was decided under a superseded scoring policy"
            )
        snapshot_event = next(
            (event for event in events if event.type == EventType.AA_SNAPSHOT_RECORDED),
            None,
        )
        if snapshot_event is None:
            raise EvidenceIntegrityError("decision has no AA snapshot evidence")
        snapshot_id = snapshot_event.payload.get("snapshotId")
        if not isinstance(snapshot_id, str):
            raise EvidenceIntegrityError("stored AA snapshot reference is malformed")
        document = await self._repository.get_document(snapshot_id)
        if document is None:
            raise EvidenceIntegrityError(f"AA snapshot document {snapshot_id} is missing")
        verify_immutable_document(document)
        if decided.payload.get("snapshotHash") != document.content_hash:
            raise EvidenceIntegrityError("decision cites a different AA snapshot body")
        return {
            "decision": dict(decided.payload),
            "decisionEventHash": decided.event_hash,
            "purchaseId": purchase_id,
            "snapshot": {**document.payload, "snapshotId": document.document_id},
            "snapshotEventHash": snapshot_event.event_hash,
            "snapshotHash": document.content_hash,
        }


def decision_from_event_payload(payload: Mapping[str, object]) -> JsonObject:
    """Narrow a stored DECIDED payload to the new policy, or raise."""
    if payload.get("scoringPolicyVersion") != AA_SCORING_POLICY_VERSION:
        raise ExternalEvidenceError("stored decision is not aa-three-factor-v1")
    return dict(payload)
