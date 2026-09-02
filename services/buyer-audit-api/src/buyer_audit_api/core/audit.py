from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from buyer_audit_api.core.errors import EvidenceIntegrityError, EvidenceTransitionError
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.hashing import sha256_json
from buyer_audit_api.core.models import EventType, EvidenceEvent, EvidenceHead, JsonObject
from buyer_audit_api.core.ports import Clock


class AuditSeverity(StrEnum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    RISK = "RISK"


@dataclass(frozen=True, slots=True)
class AuditFinding:
    code: str
    severity: AuditSeverity
    title: str
    detail: str
    evidence_refs: tuple[str, ...]
    authority: str = "deterministic"


@dataclass(frozen=True, slots=True)
class AuditReport:
    report_id: str
    purchase_id: str
    severity: AuditSeverity
    findings: tuple[AuditFinding, ...]
    evidence_head_event_hash: str
    audit_bundle_hash: str


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


def _finding_payload(finding: AuditFinding) -> JsonObject:
    return {
        "authority": finding.authority,
        "code": finding.code,
        "detail": finding.detail,
        "evidenceRefs": list(finding.evidence_refs),
        "severity": finding.severity.value,
        "title": finding.title,
    }


def _report_from_event(event: EvidenceEvent) -> AuditReport:
    findings_raw = event.payload.get("findings")
    if not isinstance(findings_raw, list):
        raise EvidenceIntegrityError("stored audit findings are malformed")
    findings = tuple(
        AuditFinding(
            code=str(item["code"]),
            severity=AuditSeverity(str(item["severity"])),
            title=str(item["title"]),
            detail=str(item["detail"]),
            evidence_refs=tuple(str(ref) for ref in item.get("evidenceRefs", [])),
            authority=str(item.get("authority", "deterministic")),
        )
        for item in findings_raw
        if isinstance(item, dict)
    )
    if len(findings) != len(findings_raw):
        raise EvidenceIntegrityError("stored audit finding is malformed")
    return AuditReport(
        report_id=str(event.payload["reportId"]),
        purchase_id=event.purchase_id,
        severity=AuditSeverity(str(event.payload["severity"])),
        findings=findings,
        evidence_head_event_hash=str(event.payload["evidenceHeadEventHash"]),
        audit_bundle_hash=str(event.payload["auditBundleHash"]),
    )


class AuditService:
    def __init__(
        self,
        *,
        repository: AuditRepository,
        clock: Clock,
        semantic_advisor: SemanticAuditAdvisor | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._semantic = semantic_advisor or NoopSemanticAuditAdvisor()

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
        existing = [event for event in events if event.type == EventType.AUDITED]
        if existing:
            if len(existing) != 1:
                raise EvidenceIntegrityError("multiple audit reports exist")
            return _report_from_event(existing[0])

        findings = list(self._deterministic_findings(events))
        semantic = await self._semantic.advise(
            purchase_id=purchase_id,
            public_events=tuple(events),
        )
        for item in semantic:
            if item.authority != "semantic" or item.severity == AuditSeverity.NORMAL:
                raise ValueError("semantic advisor may only add caution/risk advisories")
            findings.append(item)
        severity = (
            AuditSeverity.RISK
            if any(item.severity == AuditSeverity.RISK for item in findings)
            else AuditSeverity.CAUTION
            if findings
            else AuditSeverity.NORMAL
        )
        finding_payloads = [_finding_payload(item) for item in findings]
        bundle = {
            "eventHashes": [event.event_hash for event in events],
            "evidenceHeadEventHash": head.head_event_hash,
            "findings": finding_payloads,
            "purchaseId": purchase_id,
            "severity": severity.value,
        }
        audit_bundle_hash = sha256_json(bundle)
        report_digest = sha256_json({"purchaseId": purchase_id, "bundle": audit_bundle_hash}).split(
            ":", 1
        )[1]
        report_id = f"audit:{report_digest}"
        payload: JsonObject = {
            "auditBundleHash": audit_bundle_hash,
            "evidenceHeadEventHash": head.head_event_hash,
            "findings": finding_payloads,
            "reportId": report_id,
            "severity": severity.value,
        }
        event_types = {event.type for event in events}
        is_final = EventType.PAYMENT_FAILED in event_types or (
            EventType.PAYMENT_SETTLED in event_types and EventType.DELIVERED in event_types
        )
        if not is_final:
            return AuditReport(
                report_id=report_id,
                purchase_id=purchase_id,
                severity=severity,
                findings=tuple(findings),
                evidence_head_event_hash=head.head_event_hash,
                audit_bundle_hash=audit_bundle_hash,
            )
        try:
            event = await self._repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.AUDITED,
                occurred_at=self._clock.now(),
                actor={"id": "deterministic-audit-engine", "type": "service"},
                payload=payload,
                evidence_refs=(head.head_event_hash, audit_bundle_hash),
                expected_event_count=head.event_count,
                expected_head_event_hash=head.head_event_hash,
            )
        except EvidenceTransitionError:
            raced = await self._repository.list_events(purchase_id)
            reports = [item for item in raced if item.type == EventType.AUDITED]
            if len(reports) == 1:
                return _report_from_event(reports[0])
            raise
        return _report_from_event(event)

    @staticmethod
    def _deterministic_findings(
        events: list[EvidenceEvent],
    ) -> tuple[AuditFinding, ...]:
        by_type = {event.type: event for event in events}
        findings: list[AuditFinding] = []
        for event_type in (
            EventType.PAYMENT_INTENT_CLAIMED,
            EventType.PAYMENT_AUTHORIZED,
            EventType.PAYMENT_SETTLED,
            EventType.PAYMENT_FAILED,
        ):
            duplicates = [event for event in events if event.type == event_type]
            if len(duplicates) > 1:
                findings.append(
                    AuditFinding(
                        "AUD-DUPLICATE-PAYMENT-EVENT",
                        AuditSeverity.RISK,
                        "중복 결제 수명주기 이벤트가 발견됐습니다",
                        f"{event_type.value} 이벤트가 한 구매에 여러 번 기록됐습니다.",
                        tuple(event.event_hash for event in duplicates),
                    )
                )
        requested = by_type.get(EventType.REQUESTED)
        quoted = by_type.get(EventType.QUOTED)
        decided = by_type.get(EventType.DECIDED)
        claimed = by_type.get(EventType.PAYMENT_INTENT_CLAIMED)
        authorized = by_type.get(EventType.PAYMENT_AUTHORIZED)
        settled = by_type.get(EventType.PAYMENT_SETTLED)
        reconciliation = by_type.get(EventType.PAYMENT_RECONCILIATION_REQUIRED)
        failed = by_type.get(EventType.PAYMENT_FAILED)
        delivered = by_type.get(EventType.DELIVERED)

        if requested is None or quoted is None or decided is None:
            findings.append(
                AuditFinding(
                    "AUD-EVIDENCE-INCOMPLETE",
                    AuditSeverity.RISK,
                    "구매 판단 증거가 불완전합니다",
                    "요청·견적·결정 중 하나 이상이 없습니다.",
                    tuple(event.event_hash for event in events),
                )
            )
            return tuple(findings)

        winner = decided.payload.get("winner")
        winner_quote_id = winner.get("quote_id") if isinstance(winner, dict) else None
        quotes = quoted.payload.get("signedQuotes")
        selected_quotes = (
            [
                item
                for item in quotes
                if isinstance(item, dict) and item.get("quote_id") == winner_quote_id
            ]
            if isinstance(quotes, list)
            else []
        )
        if len(selected_quotes) != 1:
            findings.append(
                AuditFinding(
                    "AUD-SELECTION-EVIDENCE-MISSING",
                    AuditSeverity.RISK,
                    "선택된 견적 증거가 불완전합니다",
                    "결정의 winner quote ID와 정확히 일치하는 서명 견적이 없습니다.",
                    (quoted.event_hash, decided.event_hash),
                )
            )
        selected_quote = selected_quotes[0] if len(selected_quotes) == 1 else None
        if requested.payload.get("domain") == "ai_inference" and not isinstance(
            quoted.payload.get("benchmarkSnapshots"), list
        ):
            findings.append(
                AuditFinding(
                    "AUD-BENCHMARK-EVIDENCE-MISSING",
                    AuditSeverity.RISK,
                    "AI 선택 벤치마크 증거가 없습니다",
                    "AI 추론 후보를 비교한 benchmark snapshot 목록이 누락됐습니다.",
                    (quoted.event_hash,),
                )
            )
        identity = quoted.payload.get("quoteIdentityEvidence")
        selected_identities = (
            [
                item
                for item in identity
                if isinstance(item, dict)
                and item.get("quoteId") == winner_quote_id
                and item.get("identityVerified") is True
            ]
            if isinstance(identity, list)
            else []
        )
        if len(selected_identities) != 1:
            findings.append(
                AuditFinding(
                    "AUD-IDENTITY-UNVERIFIED",
                    AuditSeverity.RISK,
                    "판매 에이전트 신원이 검증되지 않았습니다",
                    "선택된 견적에 확인된 ERC-8004 신원 증거가 없습니다.",
                    (quoted.event_hash,),
                )
            )

        if claimed is not None:
            budget = requested.payload.get("budgetUnits")
            amount = claimed.payload.get("amountUnits")
            if isinstance(budget, int) and isinstance(amount, int) and amount > budget:
                findings.append(
                    AuditFinding(
                        "AUD-BUDGET-EXCEEDED",
                        AuditSeverity.RISK,
                        "결제 금액이 요청 예산을 초과했습니다",
                        "저장된 결제 의도의 금액이 사용자가 승인한 요청 예산보다 큽니다.",
                        (requested.event_hash, claimed.event_hash),
                    )
                )
            if selected_quote is not None:
                mismatches = [
                    field
                    for field, claimed_key, quote_key in (
                        ("quote", "quoteId", "quote_id"),
                        ("amount", "amountUnits", "amount_units"),
                        ("token", "token", "token"),
                        ("recipient", "payTo", "pay_to"),
                    )
                    if str(claimed.payload.get(claimed_key)).lower()
                    != str(selected_quote.get(quote_key)).lower()
                ]
                if mismatches:
                    findings.append(
                        AuditFinding(
                            "AUD-QUOTE-PAYMENT-MISMATCH",
                            AuditSeverity.RISK,
                            "선택 견적과 결제 의도가 일치하지 않습니다",
                            "불일치 필드: " + ", ".join(mismatches),
                            (quoted.event_hash, decided.event_hash, claimed.event_hash),
                        )
                    )
        if reconciliation is not None and settled is None and failed is None:
            findings.append(
                AuditFinding(
                    "AUD-PAYMENT-UNCONFIRMED",
                    AuditSeverity.CAUTION,
                    "결제 결과 확인이 필요합니다",
                    "같은 nonce로 온체인 영수증을 재조회해야 하며 새 결제를 만들면 안 됩니다.",
                    (reconciliation.event_hash,),
                )
            )
        elif failed is not None:
            findings.append(
                AuditFinding(
                    "AUD-PAYMENT-FAILED",
                    AuditSeverity.RISK,
                    "결제가 확정 실패했습니다",
                    "독립 영수증 또는 명시적 실패 증거가 기록되었습니다.",
                    (failed.event_hash,),
                )
            )
        elif settled is None:
            findings.append(
                AuditFinding(
                    "AUD-PAYMENT-PENDING",
                    AuditSeverity.CAUTION,
                    "결제가 아직 정산되지 않았습니다",
                    "결제 의도 또는 승인 이후 정산 증거가 없습니다.",
                    tuple(item.event_hash for item in (claimed, authorized) if item is not None),
                )
            )
        elif delivered is None:
            findings.append(
                AuditFinding(
                    "AUD-DELIVERY-MISSING",
                    AuditSeverity.RISK,
                    "결제 후 결과 전달 증거가 없습니다",
                    "정산은 확인됐지만 구매 결과 hash가 기록되지 않았습니다.",
                    (settled.event_hash,),
                )
            )
        elif not isinstance(delivered.payload.get("responseHash"), str):
            findings.append(
                AuditFinding(
                    "AUD-DELIVERY-HASH-MISSING",
                    AuditSeverity.RISK,
                    "전달 결과 hash가 없습니다",
                    "원문 대신 검증 가능한 응답 hash가 필요합니다.",
                    (delivered.event_hash,),
                )
            )
        elif selected_quote is not None:
            delivery_mismatches = [
                field
                for field, delivery_key, quote_key in (
                    ("seller", "sellerAgentId", "seller_agent_id"),
                    ("provider", "providerId", "provider_id"),
                    ("model", "modelId", "model_id"),
                    ("model version", "modelVersion", "model_version"),
                )
                if delivered.payload.get(delivery_key) != selected_quote.get(quote_key)
            ]
            if delivery_mismatches:
                findings.append(
                    AuditFinding(
                        "AUD-DELIVERY-SELECTION-MISMATCH",
                        AuditSeverity.RISK,
                        "전달 결과가 선택된 AI 모델과 일치하지 않습니다",
                        "불일치 필드: " + ", ".join(delivery_mismatches),
                        (quoted.event_hash, decided.event_hash, delivered.event_hash),
                    )
                )
        return tuple(findings)
