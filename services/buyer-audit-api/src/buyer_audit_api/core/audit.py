from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from buyer_audit_api.core.errors import EvidenceIntegrityError, EvidenceTransitionError
from buyer_audit_api.core.events import verify_event_chain
from buyer_audit_api.core.hashing import sha256_json
from buyer_audit_api.core.models import EventType, EvidenceEvent, EvidenceHead, JsonObject
from buyer_audit_api.core.ports import Clock

RULESET_VERSION = "phase6.rules.v1"
LEGACY_RULESET_VERSION = "legacy.pre-phase6"

_QUOTE_PAYMENT_FIELD_ORDER = ("quote", "amount", "token", "recipient")


class AuditSeverity(StrEnum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    RISK = "RISK"


class AuditAuthority(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    SEMANTIC_ADVISORY = "SEMANTIC_ADVISORY"


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
    deterministic = [
        item for item in findings if item.authority is AuditAuthority.DETERMINISTIC
    ]
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


class AuditReportReader:
    """Parses persisted audit evidence. It never evaluates rules and never writes."""

    def persisted(self, events: list[EvidenceEvent]) -> AuditReport | None:
        audited = [event for event in events if event.type == EventType.AUDITED]
        if not audited:
            return None
        if len(audited) != 1:
            raise EvidenceIntegrityError("multiple audit reports exist")
        return _report_from_event(audited[0])


class AuditEvaluator:
    """Pure deterministic rule evaluation over verified events; performs no I/O."""

    def evaluate(
        self,
        events: list[EvidenceEvent],
        ruleset_version: str = RULESET_VERSION,
    ) -> AuditDraft:
        if not events:
            raise EvidenceIntegrityError("purchase evidence is missing")
        purchase_id = events[0].purchase_id
        findings = self.deterministic_findings(events, ruleset_version=ruleset_version)
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
        report_digest = sha256_json(
            {"purchaseId": purchase_id, "bundle": audit_bundle_hash}
        ).split(":", 1)[1]
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
            EventType.PAYMENT_MISMATCH_CONFIRMED,
            EventType.PAYMENT_RECONCILED_NO_TRANSFER,
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

        rejected_attempts = [
            event for event in events if event.type == EventType.PAYMENT_ATTEMPT_REJECTED
        ]
        if rejected_attempts:
            reason_codes = sorted(
                {str(event.payload.get("reasonCode", "UNKNOWN")) for event in rejected_attempts}
            )
            add(
                "AUD-DUPLICATE-PAYMENT-ATTEMPT",
                AuditSeverity.RISK,
                "같은 구매에 추가 결제 시도가 있었습니다",
                "결제 guard가 거부한 추가 시도가 append-only 증거로 남아 있습니다.",
                tuple(event.event_hash for event in rejected_attempts),
                expected={"rejectedAttempts": 0},
                observed={
                    "rejectedAttempts": len(rejected_attempts),
                    "reasonCodes": reason_codes,
                },
            )
            if "ERC3009_NONCE_REUSE" in reason_codes:
                add(
                    "AUD-ERC3009-NONCE-REUSE",
                    AuditSeverity.RISK,
                    "ERC-3009 authorization nonce가 재사용됐습니다",
                    "같은 authorization nonce로 두 번째 제출이 시도되어 거부됐습니다.",
                    tuple(
                        event.event_hash
                        for event in rejected_attempts
                        if event.payload.get("reasonCode") == "ERC3009_NONCE_REUSE"
                    ),
                    expected={"nonceSubmissions": 1},
                    observed={"reasonCode": "ERC3009_NONCE_REUSE"},
                )

        requested = by_type.get(EventType.REQUESTED)
        quoted = by_type.get(EventType.QUOTED)
        decided = by_type.get(EventType.DECIDED)
        claimed = by_type.get(EventType.PAYMENT_INTENT_CLAIMED)
        authorized = by_type.get(EventType.PAYMENT_AUTHORIZED)
        settled = by_type.get(EventType.PAYMENT_SETTLED)
        reconciliation = by_type.get(EventType.PAYMENT_RECONCILIATION_REQUIRED)
        failed = by_type.get(EventType.PAYMENT_FAILED)
        mismatch = by_type.get(EventType.PAYMENT_MISMATCH_CONFIRMED)
        no_transfer = by_type.get(EventType.PAYMENT_RECONCILED_NO_TRANSFER)
        delivered = by_type.get(EventType.DELIVERED)
        checks = [
            event
            for event in events
            if event.type == EventType.PAYMENT_RECONCILIATION_CHECKED
        ]

        if requested is None or quoted is None or decided is None:
            add(
                "AUD-EVIDENCE-INCOMPLETE",
                AuditSeverity.RISK,
                "구매 판단 증거가 불완전합니다",
                "요청·견적·결정 중 하나 이상이 없습니다.",
                tuple(event.event_hash for event in events),
                expected={"events": ["REQUESTED", "QUOTED", "DECIDED"]},
                observed={"events": sorted({event.type.value for event in events})},
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
            add(
                "AUD-SELECTION-EVIDENCE-MISSING",
                AuditSeverity.RISK,
                "선택된 견적 증거가 불완전합니다",
                "결정의 winner quote ID와 정확히 일치하는 서명 견적이 없습니다.",
                (quoted.event_hash, decided.event_hash),
                expected={"matchingSignedQuotes": 1},
                observed={"matchingSignedQuotes": len(selected_quotes)},
            )
        selected_quote = selected_quotes[0] if len(selected_quotes) == 1 else None
        if requested.payload.get("domain") == "ai_inference" and not isinstance(
            quoted.payload.get("benchmarkSnapshots"), list
        ):
            add(
                "AUD-BENCHMARK-EVIDENCE-MISSING",
                AuditSeverity.RISK,
                "AI 선택 벤치마크 증거가 없습니다",
                "AI 추론 후보를 비교한 benchmark snapshot 목록이 누락됐습니다.",
                (quoted.event_hash,),
                expected={"benchmarkSnapshots": "list"},
                observed={"benchmarkSnapshots": None},
            )
        if requested.payload.get("domain") == "ai_inference":
            normalized = requested.payload.get("normalizedRequest")
            preset = decided.payload.get("preset")
            priority = normalized.get("priority") if isinstance(normalized, dict) else None
            if not isinstance(priority, str) or preset != priority:
                add(
                    "AUD-PRIORITY-PRESET-MISMATCH",
                    AuditSeverity.RISK,
                    "사용자 우선순위와 선택 점수표가 다릅니다",
                    "요청의 priority와 결정에 기록된 preset이 일치하지 않습니다.",
                    (requested.event_hash, decided.event_hash),
                    expected={"preset": priority},
                    observed={"preset": preset},
                    mismatched_fields=("preset",),
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
                add(
                    "AUD-SELECTION-WEIGHTS-MISMATCH",
                    AuditSeverity.RISK,
                    "후보 점수의 가중치가 우선순위 정책과 다릅니다",
                    "eligible 후보의 weights가 기록된 preset의 "
                    "고정 가중치와 일치하지 않습니다.",
                    (requested.event_hash, decided.event_hash),
                    expected={"weights": expected_weights},
                    observed={
                        "weights": [item.get("weights") for item in valid_eligible],
                    },
                    mismatched_fields=("weights",),
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
                add(
                    "AUD-INFERIOR-CANDIDATE-SELECTED",
                    AuditSeverity.RISK,
                    "최고 점수 후보가 선택되지 않았습니다",
                    "선택 결과가 저장된 eligible 후보의 최고 점수와 일치하지 않습니다.",
                    (quoted.event_hash, decided.event_hash),
                    expected={"maxEligibleScore": max(numeric_scores) if numeric_scores else None},
                    observed={"winnerScore": winner_score},
                    mismatched_fields=("total_score",),
                )
            explanation = decided.payload.get("generatedExplanation")
            winner_provider = winner.get("provider_id") if isinstance(winner, dict) else None
            winner_model = winner.get("model_id") if isinstance(winner, dict) else None
            explanation_tokens = (str(preset), str(winner_provider), str(winner_model))
            if not isinstance(explanation, str) or any(
                token not in explanation for token in explanation_tokens
            ):
                add(
                    "AUD-EXPLANATION-SELECTION-MISMATCH",
                    AuditSeverity.RISK,
                    "구매 에이전트 설명과 실제 선택이 다릅니다",
                    "설명에 실제 preset, 판매자, 모델 선택 근거가 모두 반영되지 않았습니다.",
                    (decided.event_hash,),
                    expected={"explanationMentions": list(explanation_tokens)},
                    observed={"explanationPresent": isinstance(explanation, str)},
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
                        observed_at = datetime.fromisoformat(
                            str(snapshot.get("observed_at")).replace("Z", "+00:00")
                        )
                        if (
                            decided.occurred_at - observed_at > timedelta(hours=24)
                            or observed_at > decided.occurred_at
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
                    add(
                        "AUD-HARD-FILTER-MISMATCH",
                        AuditSeverity.RISK,
                        "후보 필터 결과가 사용자 조건과 다릅니다",
                        "요청·예산·견적·신원·벤치마크로 재계산한 탈락 사유와 "
                        "저장된 eligible/rejected 결과가 일치하지 않습니다.",
                        (requested.event_hash, quoted.event_hash, decided.event_hash),
                        expected={
                            "eligible": sorted(expected_eligible),
                            "rejected": {
                                key: list(value) for key, value in sorted(expected_rejected.items())
                            },
                        },
                        observed={
                            "eligible": sorted(recorded_eligible),
                            "rejected": {
                                key: list(value) for key, value in sorted(recorded_rejected.items())
                            },
                        },
                    )
                wrongly_excluded = sorted(expected_eligible - recorded_eligible)
                if wrongly_excluded:
                    winner_total = winner_score if isinstance(winner_score, (int, float)) else None
                    add(
                        "AUD-ELIGIBLE-CANDIDATE-EXCLUDED",
                        AuditSeverity.RISK,
                        "적격 후보가 부당하게 제외됐습니다",
                        "hard filter를 통과하는 후보가 rejected로 기록되어 비교에서 빠졌습니다.",
                        (requested.event_hash, quoted.event_hash, decided.event_hash),
                        expected={"eligibleQuoteIds": sorted(expected_eligible)},
                        observed={
                            "excludedQuoteIds": wrongly_excluded,
                            "winnerTotalScore": winner_total,
                        },
                        mismatched_fields=("eligible",),
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
            add(
                "AUD-IDENTITY-UNVERIFIED",
                AuditSeverity.RISK,
                "판매 에이전트 신원이 검증되지 않았습니다",
                "선택된 견적에 확인된 ERC-8004 신원 증거가 없습니다.",
                (quoted.event_hash,),
                expected={"verifiedIdentities": 1},
                observed={"verifiedIdentities": len(selected_identities)},
            )

        if claimed is not None:
            budget = requested.payload.get("budgetUnits")
            amount = claimed.payload.get("amountUnits")
            if isinstance(budget, int) and isinstance(amount, int) and amount > budget:
                add(
                    "AUD-BUDGET-EXCEEDED",
                    AuditSeverity.RISK,
                    "결제 금액이 요청 예산을 초과했습니다",
                    "저장된 결제 의도의 금액이 사용자가 승인한 요청 예산보다 큽니다.",
                    (requested.event_hash, claimed.event_hash),
                    expected={"maxAmountUnits": budget},
                    observed={"amountUnits": amount},
                    mismatched_fields=("amount",),
                )
        quote_mismatch = _quote_payment_mismatch(
            selected_quote=selected_quote,
            claimed=claimed,
            mismatch=mismatch,
            settled=settled,
        )
        if quote_mismatch is not None:
            fields, expected_binding, observed_binding, refs = quote_mismatch
            add(
                "AUD-QUOTE-PAYMENT-MISMATCH",
                AuditSeverity.RISK,
                "선택 견적과 실제 결제가 일치하지 않습니다",
                "불일치 필드: " + ", ".join(fields),
                refs,
                expected=expected_binding,
                observed=observed_binding,
                mismatched_fields=fields,
            )

        facilitator_success_without_transfer = [
            event
            for event in checks
            if event.payload.get("verifierOutcome")
            == "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER"
        ]
        if facilitator_success_without_transfer:
            add(
                "AUD-FACILITATOR-SUCCESS-WITHOUT-TRANSFER",
                AuditSeverity.RISK,
                "성공 응답에 대응하는 실제 전송이 없습니다",
                "성공으로 보고된 제출에 matching Transfer/AuthorizationUsed 증거가 없습니다.",
                tuple(event.event_hash for event in facilitator_success_without_transfer),
                expected={"matchingTransfers": 1},
                observed={
                    "matchingTransfers": 0,
                    "checkedAttempts": len(checks),
                },
            )
        if no_transfer is not None:
            add(
                "AUD-PAYMENT-RECONCILED-NO-TRANSFER",
                AuditSeverity.RISK,
                "결제가 전송 없음으로 종결됐습니다",
                "bounded reconciliation이 끝난 뒤 전송이 없었음이 확정되어 예약이 해제됐습니다.",
                (no_transfer.event_hash,),
                expected={"transfers": 1},
                observed={
                    "reasonCode": no_transfer.payload.get("reasonCode"),
                    "attemptCount": no_transfer.payload.get("attemptCount"),
                    "transfers": 0,
                },
            )

        if settled is not None:
            if delivered is None:
                add(
                    "AUD-DELIVERY-MISSING",
                    AuditSeverity.RISK,
                    "결제 후 결과 전달 증거가 없습니다",
                    "정산은 확인됐지만 구매 결과 hash가 기록되지 않았습니다.",
                    (settled.event_hash,),
                    expected={"delivered": True},
                    observed={"delivered": False},
                )
            elif not isinstance(delivered.payload.get("responseHash"), str):
                add(
                    "AUD-DELIVERY-HASH-MISSING",
                    AuditSeverity.RISK,
                    "전달 결과 hash가 없습니다",
                    "원문 대신 검증 가능한 응답 hash가 필요합니다.",
                    (delivered.event_hash,),
                    expected={"responseHash": "sha256"},
                    observed={"responseHash": None},
                )
            elif selected_quote is not None:
                delivery_mismatches = tuple(
                    name
                    for name, delivery_key, quote_key in (
                        ("seller", "sellerAgentId", "seller_agent_id"),
                        ("provider", "providerId", "provider_id"),
                        ("model", "modelId", "model_id"),
                        ("model version", "modelVersion", "model_version"),
                    )
                    if delivered.payload.get(delivery_key) != selected_quote.get(quote_key)
                )
                if delivery_mismatches:
                    add(
                        "AUD-DELIVERY-SELECTION-MISMATCH",
                        AuditSeverity.RISK,
                        "전달 결과가 선택된 AI 모델과 일치하지 않습니다",
                        "불일치 필드: " + ", ".join(delivery_mismatches),
                        (quoted.event_hash, decided.event_hash, delivered.event_hash),
                        expected={
                            "sellerAgentId": selected_quote.get("seller_agent_id"),
                            "providerId": selected_quote.get("provider_id"),
                            "modelId": selected_quote.get("model_id"),
                            "modelVersion": selected_quote.get("model_version"),
                        },
                        observed={
                            "sellerAgentId": delivered.payload.get("sellerAgentId"),
                            "providerId": delivered.payload.get("providerId"),
                            "modelId": delivered.payload.get("modelId"),
                            "modelVersion": delivered.payload.get("modelVersion"),
                        },
                        mismatched_fields=delivery_mismatches,
                    )
        elif mismatch is not None or no_transfer is not None:
            pass
        elif failed is not None:
            add(
                "AUD-PAYMENT-FAILED",
                AuditSeverity.RISK,
                "결제가 확정 실패했습니다",
                "독립 영수증 또는 명시적 실패 증거가 기록되었습니다.",
                (failed.event_hash,),
                expected={"receiptStatus": 1},
                observed={"receiptStatus": failed.payload.get("receiptStatus")},
            )
        elif reconciliation is not None:
            add(
                "AUD-PAYMENT-UNCONFIRMED",
                AuditSeverity.CAUTION,
                "결제 결과 확인이 필요합니다",
                "같은 nonce로 온체인 영수증을 재조회해야 하며 새 결제를 만들면 안 됩니다.",
                (reconciliation.event_hash,),
                expected={"terminalPaymentEvidence": True},
                observed={
                    "terminalPaymentEvidence": False,
                    "checkedAttempts": len(checks),
                },
            )
        else:
            add(
                "AUD-PAYMENT-PENDING",
                AuditSeverity.CAUTION,
                "결제가 아직 정산되지 않았습니다",
                "결제 의도 또는 승인 이후 정산 증거가 없습니다.",
                tuple(item.event_hash for item in (claimed, authorized) if item is not None),
                expected={"settled": True},
                observed={"settled": False},
            )
        return tuple(findings)


def _quote_payment_mismatch(
    *,
    selected_quote: dict[str, object] | None,
    claimed: EvidenceEvent | None,
    mismatch: EvidenceEvent | None,
    settled: EvidenceEvent | None,
) -> tuple[tuple[str, ...], JsonObject, JsonObject, tuple[str, ...]] | None:
    """Compare the signed quote with the claimed intent and the verified actual proof."""
    if selected_quote is None:
        return None
    quote_binding: JsonObject = {
        "amountUnits": selected_quote.get("amount_units"),
        "quoteId": selected_quote.get("quote_id"),
        "recipient": str(selected_quote.get("pay_to", "")).lower(),
        "token": str(selected_quote.get("token", "")).lower(),
    }
    mismatched: set[str] = set()
    observed: JsonObject = {}
    refs: list[str] = []
    if claimed is not None:
        claimed_fields = {
            name
            for name, claimed_key, quote_key in (
                ("quote", "quoteId", "quote_id"),
                ("amount", "amountUnits", "amount_units"),
                ("token", "token", "token"),
                ("recipient", "payTo", "pay_to"),
            )
            if str(claimed.payload.get(claimed_key)).lower()
            != str(selected_quote.get(quote_key)).lower()
        }
        if claimed_fields:
            mismatched |= claimed_fields
            observed["claimedIntent"] = {
                "amountUnits": claimed.payload.get("amountUnits"),
                "quoteId": claimed.payload.get("quoteId"),
                "recipient": str(claimed.payload.get("payTo", "")).lower(),
                "token": str(claimed.payload.get("token", "")).lower(),
            }
            refs.append(claimed.event_hash)
    actual_source = mismatch if mismatch is not None else settled
    actual_transfer = _actual_transfer_payload(actual_source)
    if actual_source is not None and actual_transfer is not None:
        if actual_transfer.get("amountUnits") != selected_quote.get("amount_units"):
            mismatched.add("amount")
        if str(actual_transfer.get("token", "")).lower() != str(
            selected_quote.get("token", "")
        ).lower():
            mismatched.add("token")
        if str(actual_transfer.get("to", "")).lower() != str(
            selected_quote.get("pay_to", "")
        ).lower():
            mismatched.add("recipient")
        observed["actualTransfer"] = actual_transfer
        refs.append(actual_source.event_hash)
    if not mismatched:
        return None
    ordered = tuple(name for name in _QUOTE_PAYMENT_FIELD_ORDER if name in mismatched)
    return ordered, quote_binding, observed, tuple(refs)


def _actual_transfer_payload(event: EvidenceEvent | None) -> JsonObject | None:
    if event is None:
        return None
    if event.type == EventType.PAYMENT_MISMATCH_CONFIRMED:
        raw = event.payload.get("actualTransfer")
        return dict(raw) if isinstance(raw, dict) else None
    if event.type == EventType.PAYMENT_SETTLED:
        amount = event.payload.get("amountUnits")
        token = event.payload.get("token")
        recipient = event.payload.get("to")
        if not isinstance(amount, int) or not isinstance(token, str):
            return None
        return {
            "amountUnits": amount,
            "from": str(event.payload.get("from", "")),
            "to": str(recipient) if isinstance(recipient, str) else "",
            "token": token,
        }
    return None


def is_final_eligible(events: list[EvidenceEvent]) -> bool:
    """Terminal audit is only allowed after terminal payment/delivery evidence exists."""
    event_types = {event.type for event in events}
    if event_types & {
        EventType.PAYMENT_FAILED,
        EventType.PAYMENT_MISMATCH_CONFIRMED,
        EventType.PAYMENT_RECONCILED_NO_TRANSFER,
    }:
        return True
    return (
        EventType.PAYMENT_SETTLED in event_types and EventType.DELIVERED in event_types
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
        self._evaluator = AuditEvaluator()
        self._reader = AuditReportReader()

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
        persisted = self._reader.persisted(events)
        if persisted is not None:
            return persisted

        deterministic = self._evaluator.deterministic_findings(events)
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
            raced = await self._repository.list_events(purchase_id)
            report = self._reader.persisted(raced)
            if report is not None:
                return report
            raise
        return _report_from_event(event)
