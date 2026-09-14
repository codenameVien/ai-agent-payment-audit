from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from buyer_audit_api.core.aa_audit import is_aa_policy_request, recalculate_decision
from buyer_audit_api.core.aa_policy import (
    AA_REQUEST_SCHEMA_VERSION,
    AA_SCORING_POLICY_VERSION,
)
from buyer_audit_api.core.documents import verify_immutable_document
from buyer_audit_api.core.errors import (
    EvidenceImmutabilityError,
    EvidenceIntegrityError,
    EvidenceTransitionError,
)
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.hashing import sha256_json
from buyer_audit_api.core.models import (
    AUXILIARY_EVENT_TYPES,
    TERMINAL_PAYMENT_EVENT_TYPES,
    EventType,
    EvidenceEvent,
    EvidenceHead,
    ImmutableDocument,
    JsonObject,
)
from buyer_audit_api.core.ports import Clock
from buyer_audit_api.core.request_classification import (
    OriginalClassification,
    StoredRequestClassifier,
)

RULESET_VERSION = "phase6.rules.v1"
LEGACY_RULESET_VERSION = "legacy.pre-phase6"

#: Per-purchase singleton events in the `aa-three-factor-v1` lifecycle.
_AA_SINGLETON_EVENT_TYPES: tuple[EventType, ...] = (
    EventType.AA_SNAPSHOT_RECORDED,
    EventType.DECIDED,
)

class AuditSeverity(StrEnum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    RISK = "RISK"


class AuditAuthority(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    SEMANTIC_ADVISORY = "SEMANTIC_ADVISORY"


class AddFinding(Protocol):
    """The collector a rule block appends one structured outcome through."""

    def __call__(
        self,
        rule_id: str,
        severity: AuditSeverity,
        title: str,
        detail: str,
        evidence_refs: tuple[str, ...],
        *,
        expected: JsonObject | None = None,
        observed: JsonObject | None = None,
        mismatched_fields: tuple[str, ...] = (),
    ) -> None: ...


_LEGACY_AUTHORITY: dict[str, AuditAuthority] = {
    "deterministic": AuditAuthority.DETERMINISTIC,
    "semantic": AuditAuthority.SEMANTIC_ADVISORY,
}


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """One structured rule outcome. `code` stays available as a legacy alias of `rule_id`."""

    rule_id: str
    severity: AuditSeverity
    title: str
    detail: str
    evidence_refs: tuple[str, ...] = ()
    authority: AuditAuthority = AuditAuthority.DETERMINISTIC
    ruleset_version: str = RULESET_VERSION
    expected: JsonObject | None = None
    observed: JsonObject | None = None
    mismatched_fields: tuple[str, ...] = ()

    @property
    def code(self) -> str:
        return self.rule_id


@dataclass(frozen=True, slots=True)
class AuditReport:
    report_id: str
    purchase_id: str
    severity: AuditSeverity
    findings: tuple[AuditFinding, ...]
    evidence_head_event_hash: str
    audit_bundle_hash: str
    ruleset_version: str = RULESET_VERSION


@dataclass(frozen=True, slots=True)
class AuditDraft:
    """Pure evaluation result. Only a coordinator may persist it as an `AUDITED` event."""

    purchase_id: str
    severity: AuditSeverity
    findings: tuple[AuditFinding, ...]
    evidence_head_event_hash: str
    audit_bundle_hash: str
    report_id: str
    ruleset_version: str
    final_eligible: bool
    finding_payloads: list[JsonObject] = field(default_factory=list)

    def as_report(self) -> AuditReport:
        return AuditReport(
            report_id=self.report_id,
            purchase_id=self.purchase_id,
            severity=self.severity,
            findings=self.findings,
            evidence_head_event_hash=self.evidence_head_event_hash,
            audit_bundle_hash=self.audit_bundle_hash,
            ruleset_version=self.ruleset_version,
        )


class AuditRepository(Protocol):
    async def list_events(self, purchase_id: str) -> list[EvidenceEvent]: ...

    async def get_event_head(self, purchase_id: str) -> EvidenceHead | None: ...

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
    ) -> EvidenceEvent: ...

    async def get_document(self, document_id: str) -> ImmutableDocument | None:
        """The immutable body a snapshot event references, for independent recomputation."""
        ...


class SemanticAuditAdvisor(Protocol):
    async def advise(
        self, *, purchase_id: str, public_events: tuple[EvidenceEvent, ...]
    ) -> tuple[AuditFinding, ...]: ...


class NoopSemanticAuditAdvisor:
    async def advise(
        self, *, purchase_id: str, public_events: tuple[EvidenceEvent, ...]
    ) -> tuple[AuditFinding, ...]:
        del purchase_id, public_events
        return ()


def finding_payload(finding: AuditFinding) -> JsonObject:
    payload: JsonObject = {
        "authority": finding.authority.value,
        "code": finding.rule_id,
        "detail": finding.detail,
        "evidenceRefs": list(finding.evidence_refs),
        "ruleId": finding.rule_id,
        "rulesetVersion": finding.ruleset_version,
        "severity": finding.severity.value,
        "title": finding.title,
    }
    if finding.expected is not None:
        payload["expected"] = finding.expected
    if finding.observed is not None:
        payload["observed"] = finding.observed
    if finding.mismatched_fields:
        payload["mismatchedFields"] = list(finding.mismatched_fields)
    return payload


def _authority_from_payload(value: object) -> AuditAuthority:
    raw = str(value) if value is not None else "deterministic"
    legacy = _LEGACY_AUTHORITY.get(raw)
    if legacy is not None:
        return legacy
    try:
        return AuditAuthority(raw)
    except ValueError as exc:
        raise EvidenceIntegrityError("stored audit finding authority is malformed") from exc


def finding_from_payload(item: JsonObject) -> AuditFinding:
    """Read legacy and Phase 6 findings without rewriting stored documents."""
    rule_id = item.get("ruleId") or item.get("code")
    if not isinstance(rule_id, str) or not rule_id:
        raise EvidenceIntegrityError("stored audit finding is malformed")
    expected = item.get("expected")
    observed = item.get("observed")
    mismatched = item.get("mismatchedFields")
    try:
        severity = AuditSeverity(str(item["severity"]))
    except (KeyError, ValueError) as exc:
        raise EvidenceIntegrityError("stored audit finding severity is malformed") from exc
    return AuditFinding(
        rule_id=rule_id,
        severity=severity,
        title=str(item.get("title", "")),
        detail=str(item.get("detail", "")),
        evidence_refs=tuple(str(ref) for ref in item.get("evidenceRefs", [])),
        authority=_authority_from_payload(item.get("authority")),
        ruleset_version=str(item.get("rulesetVersion", LEGACY_RULESET_VERSION)),
        expected=dict(expected) if isinstance(expected, dict) else None,
        observed=dict(observed) if isinstance(observed, dict) else None,
        mismatched_fields=(
            tuple(str(name) for name in mismatched) if isinstance(mismatched, list) else ()
        ),
    )


def combine_severity(findings: tuple[AuditFinding, ...]) -> AuditSeverity:
    """Deterministic rules own the verdict; semantic advisories cap out at a warning."""
    deterministic = [item for item in findings if item.authority is AuditAuthority.DETERMINISTIC]
    if any(item.severity is AuditSeverity.RISK for item in deterministic):
        return AuditSeverity.RISK
    if findings:
        return AuditSeverity.CAUTION
    return AuditSeverity.NORMAL


def _report_from_event(event: EvidenceEvent) -> AuditReport:
    findings_raw = event.payload.get("findings")
    if not isinstance(findings_raw, list):
        raise EvidenceIntegrityError("stored audit findings are malformed")
    parsed = [item for item in findings_raw if isinstance(item, dict)]
    if len(parsed) != len(findings_raw):
        raise EvidenceIntegrityError("stored audit finding is malformed")
    findings = tuple(finding_from_payload(item) for item in parsed)
    try:
        return AuditReport(
            report_id=str(event.payload["reportId"]),
            purchase_id=event.purchase_id,
            severity=AuditSeverity(str(event.payload["severity"])),
            findings=findings,
            evidence_head_event_hash=str(event.payload["evidenceHeadEventHash"]),
            audit_bundle_hash=str(event.payload["auditBundleHash"]),
            ruleset_version=str(event.payload.get("rulesetVersion", LEGACY_RULESET_VERSION)),
        )
    except (KeyError, ValueError) as exc:
        raise EvidenceIntegrityError("stored audit report is malformed") from exc


@dataclass(frozen=True, slots=True)
class PersistedAudit:
    """A persisted audit with the position it actually describes."""

    report: AuditReport
    audited_sequence: int
    audited_head_event_hash: str | None
    covers_head: bool


def require_current_audit(persisted: PersistedAudit) -> None:
    """P6-AC-03.5: a persisted audit is current truth only for the same head and ruleset.

    Both conditions demand an explicit correction/re-audit policy rather than a silent
    stale answer. Any lifecycle event appended after `AUDITED` changes the head, so a
    purchase that has progressed past its audit is re-audited through that explicit policy
    instead of being served a report that no longer describes its evidence. Advisory
    observer and checkpoint records are the only events that may follow it.
    """
    if not persisted.covers_head:
        raise EvidenceIntegrityError(
            "evidence head advanced after the terminal audit; "
            "an explicit correction or re-audit policy is required"
        )
    if persisted.report.ruleset_version != RULESET_VERSION:
        raise EvidenceIntegrityError(
            "persisted audit was produced by ruleset "
            f"{persisted.report.ruleset_version!r}, not {RULESET_VERSION!r}; "
            "an explicit re-audit policy is required"
        )


class AuditReportReader:
    """Parses persisted audit evidence. It never evaluates rules and never writes."""

    def persisted(self, events: list[EvidenceEvent]) -> AuditReport | None:
        found = self.persisted_audit(events)
        return None if found is None else found.report

    def current(self, events: list[EvidenceEvent]) -> AuditReport | None:
        """The persisted audit, but only when it is still the truth for this evidence."""
        persisted = self.persisted_audit(events)
        if persisted is None:
            return None
        require_current_audit(persisted)
        return persisted.report

    def persisted_audit(self, events: list[EvidenceEvent]) -> PersistedAudit | None:
        audited = [event for event in events if event.type == EventType.AUDITED]
        if not audited:
            return None
        if len(audited) != 1:
            raise EvidenceIntegrityError("multiple audit reports exist")
        audited_event = audited[0]
        report = _report_from_event(audited_event)
        index = events.index(audited_event)
        audited_head = events[index - 1].event_hash if index > 0 else None
        if report.evidence_head_event_hash != audited_head:
            raise EvidenceIntegrityError(
                "stored audit does not describe the head it was appended to"
            )
        return PersistedAudit(
            report=report,
            audited_sequence=audited_event.sequence,
            audited_head_event_hash=audited_head,
            # Advisory observer/checkpoint records do not change the deterministic
            # facts audited above. Any other later lifecycle event still demands a
            # deliberate re-audit policy.
            covers_head=all(event.type in AUXILIARY_EVENT_TYPES for event in events[index + 1 :]),
        )


class AuditEvaluator:
    """Pure deterministic rule evaluation over verified events; performs no I/O."""

    def evaluate(
        self,
        events: list[EvidenceEvent],
        ruleset_version: str = RULESET_VERSION,
        *,
        referenced_documents: Mapping[str, JsonObject] | None = None,
    ) -> AuditDraft:
        if not events:
            raise EvidenceIntegrityError("purchase evidence is missing")
        purchase_id = events[0].purchase_id
        findings = self.deterministic_findings(
            events,
            ruleset_version=ruleset_version,
            referenced_documents=referenced_documents,
        )
        return self.draft(
            purchase_id=purchase_id,
            events=events,
            findings=findings,
            ruleset_version=ruleset_version,
        )

    def draft(
        self,
        *,
        purchase_id: str,
        events: list[EvidenceEvent],
        findings: tuple[AuditFinding, ...],
        ruleset_version: str = RULESET_VERSION,
    ) -> AuditDraft:
        head_event_hash = events[-1].event_hash
        severity = combine_severity(findings)
        finding_payloads = [finding_payload(item) for item in findings]
        bundle = {
            "eventHashes": [event.event_hash for event in events],
            "evidenceHeadEventHash": head_event_hash,
            "findings": finding_payloads,
            "purchaseId": purchase_id,
            "severity": severity.value,
        }
        audit_bundle_hash = sha256_json(bundle)
        report_digest = sha256_json({"purchaseId": purchase_id, "bundle": audit_bundle_hash}).split(
            ":", 1
        )[1]
        return AuditDraft(
            purchase_id=purchase_id,
            severity=severity,
            findings=findings,
            evidence_head_event_hash=head_event_hash,
            audit_bundle_hash=audit_bundle_hash,
            report_id=f"audit:{report_digest}",
            ruleset_version=ruleset_version,
            final_eligible=is_final_eligible(events),
            finding_payloads=finding_payloads,
        )

    @staticmethod
    def deterministic_findings(
        events: list[EvidenceEvent],
        *,
        ruleset_version: str = RULESET_VERSION,
        referenced_documents: Mapping[str, JsonObject] | None = None,
        original_classification: OriginalClassification | None = None,
    ) -> tuple[AuditFinding, ...]:
        by_type = {event.type: event for event in events}
        findings: list[AuditFinding] = []

        def add(
            rule_id: str,
            severity: AuditSeverity,
            title: str,
            detail: str,
            evidence_refs: tuple[str, ...],
            *,
            expected: JsonObject | None = None,
            observed: JsonObject | None = None,
            mismatched_fields: tuple[str, ...] = (),
        ) -> None:
            findings.append(
                AuditFinding(
                    rule_id=rule_id,
                    severity=severity,
                    title=title,
                    detail=detail,
                    evidence_refs=evidence_refs,
                    authority=AuditAuthority.DETERMINISTIC,
                    ruleset_version=ruleset_version,
                    expected=expected,
                    observed=observed,
                    mismatched_fields=mismatched_fields,
                )
            )

        for event_type in (
            EventType.PAYMENT_INTENT_CLAIMED,
            EventType.PAYMENT_AUTHORIZED,
            EventType.PAYMENT_SETTLED,
            EventType.PAYMENT_FAILED,
        ):
            duplicates = [event for event in events if event.type == event_type]
            if len(duplicates) > 1:
                add(
                    "AUD-DUPLICATE-PAYMENT-EVENT",
                    AuditSeverity.RISK,
                    "중복 결제 수명주기 이벤트가 발견됐습니다",
                    f"{event_type.value} 이벤트가 한 구매에 여러 번 기록됐습니다.",
                    tuple(event.event_hash for event in duplicates),
                    expected={"eventType": event_type.value, "count": 1},
                    observed={"eventType": event_type.value, "count": len(duplicates)},
                )

        requested = by_type.get(EventType.REQUESTED)
        if requested is not None and is_aa_policy_request(requested.payload):
            _aa_deterministic_findings(
                events=events,
                requested=requested,
                add=add,
                referenced_documents=referenced_documents or {},
                original_classification=original_classification,
            )
            return tuple(findings)

        # Only an `aa-three-factor-v1` purchase carries the evidence these rules recompute
        # from. Anything else is reported as unjudgeable instead of being judged by a rule
        # written for another policy.
        add(
            "AUD-EVIDENCE-INCOMPLETE",
            AuditSeverity.RISK,
            "구매 판단 증거가 불완전합니다",
            "aegis-aa-v1 요청과 AA snapshot·결정 증거로 판단할 수 있는 구매가 아닙니다.",
            tuple(event.event_hash for event in events),
            expected={
                "requestSchemaVersion": AA_REQUEST_SCHEMA_VERSION,
                "events": ["REQUESTED", "AA_SNAPSHOT_RECORDED", "DECIDED"],
            },
            observed={"events": sorted({event.type.value for event in events})},
        )
        return tuple(findings)


def _aa_deterministic_findings(
    *,
    events: list[EvidenceEvent],
    requested: EvidenceEvent,
    add: AddFinding,
    referenced_documents: Mapping[str, JsonObject],
    original_classification: OriginalClassification | None = None,
) -> None:
    """Judge an `aa-three-factor-v1` purchase by recomputing its own decision.

    The stored snapshot binding, amounts, filters, scores and rank are all derived again
    from the preserved AA source values; nothing the buyer wrote is accepted as proof of
    itself.
    """
    by_type = {event.type: event for event in events}
    for event_type in _AA_SINGLETON_EVENT_TYPES:
        duplicates = [event for event in events if event.type == event_type]
        if len(duplicates) > 1:
            add(
                "AUD-AA-DUPLICATE-DECISION-EVENT",
                AuditSeverity.RISK,
                "구매 판단 이벤트가 중복 기록됐습니다",
                f"{event_type.value} 이벤트가 한 구매에 여러 번 기록됐습니다.",
                tuple(event.event_hash for event in duplicates),
                expected={"eventType": event_type.value, "count": 1},
                observed={"eventType": event_type.value, "count": len(duplicates)},
            )
    snapshot = by_type.get(EventType.AA_SNAPSHOT_RECORDED)
    decided = by_type.get(EventType.DECIDED)
    if snapshot is None or decided is None:
        add(
            "AUD-AA-EVIDENCE-INCOMPLETE",
            AuditSeverity.RISK,
            "AA 근거 또는 결정 증거가 없습니다",
            "요청·AA snapshot·결정 중 하나 이상이 기록되지 않았습니다.",
            tuple(event.event_hash for event in events),
            expected={"events": ["REQUESTED", "AA_SNAPSHOT_RECORDED", "DECIDED"]},
            observed={"events": sorted({event.type.value for event in events})},
        )
        return
    if decided.payload.get("scoringPolicyVersion") != AA_SCORING_POLICY_VERSION:
        add(
            "AUD-AA-POLICY-VERSION-MISMATCH",
            AuditSeverity.RISK,
            "결정에 기록된 정책 버전이 요청 정책과 다릅니다",
            "aegis-aa-v1 요청은 aa-three-factor-v1 정책으로만 결정될 수 있습니다.",
            (requested.event_hash, decided.event_hash),
            expected={"scoringPolicyVersion": AA_SCORING_POLICY_VERSION},
            observed={"scoringPolicyVersion": decided.payload.get("scoringPolicyVersion")},
            mismatched_fields=("scoringPolicyVersion",),
        )
        return
    refs = (requested.event_hash, snapshot.event_hash, decided.event_hash)
    snapshot_id = snapshot.payload.get("snapshotId")
    snapshot_body = (
        referenced_documents.get(str(snapshot_id)) if isinstance(snapshot_id, str) else None
    )
    if snapshot_body is None:
        # Without the immutable body there is no independent input to recompute from, so
        # the decision is reported as unverified rather than quietly accepted. The cheap
        # evidence-local checks below still run.
        add(
            "AUD-AA-SNAPSHOT-BODY-UNAVAILABLE",
            AuditSeverity.RISK,
            "AA snapshot 원본을 읽지 못해 결정을 재계산할 수 없습니다",
            "결정이 참조하는 immutable snapshot 문서를 찾지 못했습니다.",
            refs,
            expected={"snapshotDocument": snapshot_id},
            observed={"snapshotDocument": None},
        )
    else:
        for issue in recalculate_decision(
            requested_payload=requested.payload,
            snapshot_payload={
                **snapshot_body,
                "snapshotHash": snapshot.payload.get("snapshotHash"),
            },
            decided_payload=decided.payload,
            original_classification=original_classification,
        ):
            add(
                issue.rule_id,
                AuditSeverity.RISK if issue.is_contradiction else AuditSeverity.CAUTION,
                (
                    "저장된 결정이 정책 재계산과 다릅니다"
                    if issue.is_contradiction
                    else "재계산으로 확인할 수 없는 범위가 있습니다"
                ),
                issue.detail,
                refs,
                expected=issue.expected,
                observed=issue.observed,
                mismatched_fields=issue.mismatched_fields,
            )
    budget = requested.payload.get("budgetUnits")
    amount = decided.payload.get("amountUnits")
    if isinstance(budget, int) and isinstance(amount, int) and amount > budget:
        add(
            "AUD-BUDGET-EXCEEDED",
            AuditSeverity.RISK,
            "결제 금액이 요청 예산을 초과했습니다",
            "선택된 모델의 선결제 금액이 사용자가 승인한 예산보다 큽니다.",
            (requested.event_hash, decided.event_hash),
            expected={"maxAmountUnits": budget},
            observed={"amountUnits": amount},
            mismatched_fields=("amount",),
        )
    explanation = decided.payload.get("generatedExplanation")
    winner = decided.payload.get("winner")
    priority = decided.payload.get("priority")
    tokens = (
        str(priority.get("effectivePriority")) if isinstance(priority, dict) else "",
        str(winner.get("providerId")) if isinstance(winner, dict) else "",
        str(winner.get("providerModelId")) if isinstance(winner, dict) else "",
    )
    if not isinstance(explanation, str) or any(
        not token or token not in explanation for token in tokens
    ):
        add(
            "AUD-EXPLANATION-SELECTION-MISMATCH",
            AuditSeverity.RISK,
            "구매 에이전트 설명과 실제 선택이 다릅니다",
            "설명에 적용된 우선순위와 선택된 제공자·모델이 모두 반영되지 않았습니다.",
            (decided.event_hash,),
            expected={"explanationMentions": list(tokens)},
            observed={"explanationPresent": isinstance(explanation, str)},
        )
    if by_type.get(EventType.PAYMENT_SETTLED) is None and not (
        {event.type for event in events} & set(TERMINAL_PAYMENT_EVENT_TYPES)
    ):
        add(
            "AUD-PAYMENT-PENDING",
            AuditSeverity.CAUTION,
            "결제가 아직 정산되지 않았습니다",
            "결정은 고정됐지만 Facilitator 정산 증거가 아직 없습니다.",
            (decided.event_hash,),
            expected={"settled": True},
            observed={"settled": False},
        )


def is_final_eligible(events: list[EvidenceEvent]) -> bool:
    """Terminal audit is only allowed after terminal payment/delivery evidence exists."""
    event_types = {event.type for event in events}
    if EventType.PAYMENT_FAILED in event_types:
        return True
    return EventType.PAYMENT_SETTLED in event_types and EventType.DELIVERED in event_types


class AuditService:
    def __init__(
        self,
        *,
        repository: AuditRepository,
        clock: Clock,
        semantic_advisor: SemanticAuditAdvisor | None = None,
        original_requests: StoredRequestClassifier | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._semantic = semantic_advisor or NoopSemanticAuditAdvisor()
        # Optional on purpose: without it the audit keeps AEGIS-01's honest "not
        # re-derivable" report instead of silently claiming a verification it did not do.
        self._original_requests = original_requests
        self._evaluator = AuditEvaluator()
        self._reader = AuditReportReader()

    async def _referenced_documents(self, events: list[EvidenceEvent]) -> dict[str, JsonObject]:
        """Load the immutable bodies the evidence points at, verifying each on the way.

        A body whose stored hash does not describe it is dropped, so the recalculation
        reports the decision as unverified instead of trusting a rewritten document.
        """
        documents: dict[str, JsonObject] = {}
        for event in events:
            if event.type is not EventType.AA_SNAPSHOT_RECORDED:
                continue
            document_id = event.payload.get("snapshotId")
            if not isinstance(document_id, str):
                continue
            stored = await self._repository.get_document(document_id)
            if stored is None or stored.purchase_id != event.purchase_id:
                continue
            try:
                verify_immutable_document(stored)
            except EvidenceImmutabilityError:
                continue
            if stored.content_hash != event.payload.get("snapshotHash"):
                continue
            documents[document_id] = {
                **stored.payload,
                "snapshotId": stored.document_id,
            }
        return documents

    async def _original_classification(
        self, events: list[EvidenceEvent]
    ) -> OriginalClassification | None:
        """Re-classify the stored original request, for `aa-three-factor-v1` purchases only.

        The result carries policy names and dictionary terms, never the prompt, so nothing
        that reaches a finding, the semantic advisor or a log line contains user text.
        """
        if self._original_requests is None or not events:
            return None
        requested = events[0]
        if requested.type is not EventType.REQUESTED:
            return None
        if not is_aa_policy_request(requested.payload):
            return None
        return await self._original_requests.classify(
            purchase_id=requested.purchase_id,
            requested_payload=requested.payload,
        )

    async def audit(self, purchase_id: str) -> AuditReport:
        events = await self._repository.list_events(purchase_id)
        head = await self._repository.get_event_head(purchase_id)
        if not events or head is None:
            raise EvidenceIntegrityError("purchase evidence is missing")
        verify_event_chain(
            events,
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )
        current = self._reader.current(events)
        if current is not None:
            return current

        referenced = await self._referenced_documents(events)
        original = await self._original_classification(events)
        deterministic = self._evaluator.deterministic_findings(
            events,
            referenced_documents=referenced,
            original_classification=original,
        )
        semantic = await self._semantic.advise(
            purchase_id=purchase_id,
            public_events=tuple(events),
        )
        for item in semantic:
            if (
                item.authority is not AuditAuthority.SEMANTIC_ADVISORY
                or item.severity is AuditSeverity.NORMAL
            ):
                raise ValueError("semantic advisor may only add caution/risk advisories")
        draft = self._evaluator.draft(
            purchase_id=purchase_id,
            events=events,
            findings=deterministic + tuple(semantic),
        )
        if not draft.final_eligible:
            return draft.as_report()
        payload: JsonObject = {
            "auditBundleHash": draft.audit_bundle_hash,
            "evidenceHeadEventHash": draft.evidence_head_event_hash,
            "findings": draft.finding_payloads,
            "reportId": draft.report_id,
            "rulesetVersion": draft.ruleset_version,
            "severity": draft.severity.value,
        }
        try:
            event = await self._repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.AUDITED,
                occurred_at=self._clock.now(),
                actor={"id": "deterministic-audit-engine", "type": "service"},
                payload=payload,
                evidence_refs=(draft.evidence_head_event_hash, draft.audit_bundle_hash),
                expected_event_count=head.event_count,
                expected_head_event_hash=head.head_event_hash,
            )
        except EvidenceTransitionError:
            # H4: the loser of an append race must apply exactly the same currency rules as
            # the normal path. A winner's audit plus any later event is not a current report.
            raced = await self._repository.list_events(purchase_id)
            raced_head = await self._repository.get_event_head(purchase_id)
            if not raced or raced_head is None:
                raise
            verify_event_chain(
                raced,
                expected_event_count=raced_head.event_count,
                expected_head_event_hash=raced_head.head_event_hash,
            )
            report = self._reader.current(raced)
            if report is not None:
                return report
            raise
        return _report_from_event(event)
