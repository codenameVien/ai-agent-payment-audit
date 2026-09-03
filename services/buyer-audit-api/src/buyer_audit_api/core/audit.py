from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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
        if requested.payload.get("domain") == "ai_inference":
            normalized = requested.payload.get("normalizedRequest")
            preset = decided.payload.get("preset")
            priority = normalized.get("priority") if isinstance(normalized, dict) else None
            if not isinstance(priority, str) or preset != priority:
                findings.append(
                    AuditFinding(
                        "AUD-PRIORITY-PRESET-MISMATCH",
                        AuditSeverity.RISK,
                        "사용자 우선순위와 선택 점수표가 다릅니다",
                        "요청의 priority와 결정에 기록된 preset이 일치하지 않습니다.",
                        (requested.event_hash, decided.event_hash),
                    )
                )
            expected_weights = {
                "balanced": {
                    "quality": 40,
                    "price": 25,
                    "speed": 20,
                    "reputation": 10,
                    "freshness": 5,
                },
                "quality": {
                    "quality": 60,
                    "price": 10,
                    "speed": 15,
                    "reputation": 10,
                    "freshness": 5,
                },
                "price": {
                    "quality": 25,
                    "price": 50,
                    "speed": 10,
                    "reputation": 10,
                    "freshness": 5,
                },
                "speed": {
                    "quality": 25,
                    "price": 10,
                    "speed": 50,
                    "reputation": 10,
                    "freshness": 5,
                },
            }.get(str(preset))
            eligible = decided.payload.get("eligible")
            valid_eligible = (
                [item for item in eligible if isinstance(item, dict)]
                if isinstance(eligible, list)
                else []
            )
            weights_wrong = expected_weights is None or any(
                item.get("weights") != expected_weights for item in valid_eligible
            )
            if not valid_eligible or weights_wrong:
                findings.append(
                    AuditFinding(
                        "AUD-SELECTION-WEIGHTS-MISMATCH",
                        AuditSeverity.RISK,
                        "후보 점수의 가중치가 우선순위 정책과 다릅니다",
                        "eligible 후보의 weights가 기록된 preset의 "
                        "고정 가중치와 일치하지 않습니다.",
                        (requested.event_hash, decided.event_hash),
                    )
                )
            scores = [item.get("total_score") for item in valid_eligible]
            numeric_scores = [float(score) for score in scores if isinstance(score, (int, float))]
            winner_score = winner.get("total_score") if isinstance(winner, dict) else None
            if (
                not scores
                or len(numeric_scores) != len(scores)
                or not isinstance(winner_score, (int, float))
                or float(winner_score) != max(numeric_scores)
            ):
                findings.append(
                    AuditFinding(
                        "AUD-INFERIOR-CANDIDATE-SELECTED",
                        AuditSeverity.RISK,
                        "최고 점수 후보가 선택되지 않았습니다",
                        "선택 결과가 저장된 eligible 후보의 최고 점수와 일치하지 않습니다.",
                        (quoted.event_hash, decided.event_hash),
                    )
                )
            explanation = decided.payload.get("generatedExplanation")
            winner_provider = winner.get("provider_id") if isinstance(winner, dict) else None
            winner_model = winner.get("model_id") if isinstance(winner, dict) else None
            explanation_tokens = (str(preset), str(winner_provider), str(winner_model))
            if not isinstance(explanation, str) or any(
                token not in explanation for token in explanation_tokens
            ):
                findings.append(
                    AuditFinding(
                        "AUD-EXPLANATION-SELECTION-MISMATCH",
                        AuditSeverity.RISK,
                        "구매 에이전트 설명과 실제 선택이 다릅니다",
                        "설명에 실제 preset, 판매자, 모델 선택 근거가 모두 반영되지 않았습니다.",
                        (decided.event_hash,),
                    )
                )
            benchmarks = quoted.payload.get("benchmarkSnapshots")
            identities = quoted.payload.get("quoteIdentityEvidence")
            rejected = decided.payload.get("rejected")
            if (
                isinstance(normalized, dict)
                and isinstance(quotes, list)
                and isinstance(benchmarks, list)
                and isinstance(identities, list)
                and isinstance(rejected, list)
            ):
                identity_by_quote = {
                    item.get("quoteId"): item.get("identityVerified")
                    for item in identities
                    if isinstance(item, dict)
                }
                expected_rejected: dict[str, tuple[str, ...]] = {}
                expected_eligible: set[str] = set()
                for quote in (item for item in quotes if isinstance(item, dict)):
                    quote_id = quote.get("quote_id")
                    if not isinstance(quote_id, str):
                        continue
                    matches = [
                        item
                        for item in benchmarks
                        if isinstance(item, dict)
                        and item.get("provider_id") == quote.get("provider_id")
                        and item.get("model_id") == quote.get("model_id")
                        and item.get("model_version") == quote.get("model_version")
                    ]
                    reasons: list[str] = []
                    if len(matches) != 1:
                        reasons.append("quote_benchmark_mismatch")
                        snapshot: dict[str, object] = {}
                    else:
                        snapshot = matches[0]
                    if quote.get("available") is not True:
                        reasons.append("unavailable")
                    if identity_by_quote.get(quote_id) is not True:
                        reasons.append("identity_unverified")
                    budget = requested.payload.get("budgetUnits")
                    amount = quote.get("amount_units")
                    if isinstance(budget, int) and isinstance(amount, int) and amount > budget:
                        reasons.append("over_budget")
                    try:
                        expiry = datetime.fromisoformat(
                            str(quote.get("expires_at")).replace("Z", "+00:00")
                        )
                        if expiry <= decided.occurred_at:
                            reasons.append("quote_expired")
                    except ValueError:
                        reasons.append("quote_expired")
                    try:
                        observed = datetime.fromisoformat(
                            str(snapshot.get("observed_at")).replace("Z", "+00:00")
                        )
                        if (
                            decided.occurred_at - observed > timedelta(hours=24)
                            or observed > decided.occurred_at
                        ):
                            reasons.append("benchmark_stale")
                    except ValueError:
                        reasons.append("benchmark_stale")
                    allowed = normalized.get("allowed_sellers", [])
                    if (
                        isinstance(allowed, list)
                        and allowed
                        and str(quote.get("provider_id")).lower()
                        not in {str(value).lower() for value in allowed}
                    ):
                        reasons.append("seller_not_allowed")
                    for request_key, quote_key, reason in (
                        ("min_input_limit", "input_limit", "input_limit"),
                        ("min_output_limit", "output_limit", "output_limit"),
                    ):
                        required = normalized.get(request_key)
                        offered = quote.get(quote_key)
                        if isinstance(required, int) and (
                            not isinstance(offered, int) or offered < required
                        ):
                            reasons.append(reason)
                    max_latency = normalized.get("max_latency_ms")
                    latency = quote.get("expected_latency_ms")
                    if isinstance(max_latency, int) and (
                        not isinstance(latency, int) or latency > max_latency
                    ):
                        reasons.append("latency_limit")
                    required_capabilities = {
                        str(value).lower() for value in normalized.get("required_capabilities", [])
                    }
                    raw_capabilities = snapshot.get("capabilities", [])
                    available_capabilities = (
                        {str(value).lower() for value in raw_capabilities}
                        if isinstance(raw_capabilities, list)
                        else set()
                    )
                    if not required_capabilities.issubset(available_capabilities):
                        reasons.append("missing_capability")
                    if reasons:
                        expected_rejected[quote_id] = tuple(reasons)
                    else:
                        expected_eligible.add(quote_id)
                recorded_rejected = {
                    str(item.get("quote_id")): tuple(item.get("reasons", []))
                    for item in rejected
                    if isinstance(item, dict)
                }
                recorded_eligible = {str(item.get("quote_id")) for item in valid_eligible}
                if expected_rejected != recorded_rejected or expected_eligible != recorded_eligible:
                    findings.append(
                        AuditFinding(
                            "AUD-HARD-FILTER-MISMATCH",
                            AuditSeverity.RISK,
                            "후보 필터 결과가 사용자 조건과 다릅니다",
                            "요청·예산·견적·신원·벤치마크로 재계산한 탈락 사유와 "
                            "저장된 eligible/rejected 결과가 일치하지 않습니다.",
                            (requested.event_hash, quoted.event_hash, decided.event_hash),
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
