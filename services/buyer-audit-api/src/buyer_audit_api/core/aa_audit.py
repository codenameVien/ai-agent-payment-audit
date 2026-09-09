"""Independent recalculation of an `aa-three-factor-v1` decision.

Nothing the buyer wrote into the decision is taken on trust. The candidate set, the AA
numbers, the amounts, the converted completion times, the hard-filter outcome, the
normalization denominators, the three component scores and the final rank are all rebuilt
from the immutable snapshot document: its preserved raw pages, its captured catalog and
its per-model source values. The decision is then compared against that reconstruction,
so dropping a candidate or editing a price and its derived numbers together is caught.

This module is pure. It reads dictionaries that came out of the append-only chain and
returns structured mismatches; it performs no I/O and knows no provider names.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from buyer_audit_api.core.aa_policy import (
    AA_REQUEST_SCHEMA_VERSION,
    PRIORITY_KEYWORDS,
    PRIORITY_WEIGHTS,
    CandidateMetrics,
    PolicyNumberError,
    PolicyWeights,
    PriorityReason,
    RequestPriority,
    amount_units,
    completion_ms_from_seconds,
    decimal_text,
    fraction_text,
    rank_candidates,
    rejection_reasons,
)
from buyer_audit_api.core.documents import raw_parts_hash
from buyer_audit_api.core.models import JsonObject


@dataclass(frozen=True, slots=True)
class AaRecalculationIssue:
    """One recomputed disagreement, ready to become an audit finding."""

    rule_id: str
    detail: str
    expected: JsonObject
    observed: JsonObject
    mismatched_fields: tuple[str, ...] = ()
    #: `False` marks a disclosed limit of the recalculation rather than a contradiction.
    is_contradiction: bool = True


def is_aa_policy_request(requested_payload: Mapping[str, object]) -> bool:
    """Discriminate on the stored request schema, before any decision exists."""
    normalized = requested_payload.get("normalizedRequest")
    if not isinstance(normalized, Mapping):
        return False
    return normalized.get("request_schema_version") == AA_REQUEST_SCHEMA_VERSION


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Mapping[str, object]]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def recalculate_decision(
    *,
    requested_payload: Mapping[str, object],
    snapshot_payload: Mapping[str, object],
    decided_payload: Mapping[str, object],
) -> tuple[AaRecalculationIssue, ...]:
    """Rebuild the decision from the snapshot document and report every disagreement.

    The candidate set comes from the catalog captured inside the snapshot and the numbers
    come from the snapshot models, so a decision that quietly drops a candidate, or that
    edits a price together with everything derived from it, cannot look consistent.
    """
    issues: list[AaRecalculationIssue] = []
    normalized = _mapping(requested_payload.get("normalizedRequest"))
    budget_units = _int(requested_payload.get("budgetUnits"))
    entries = _sequence(snapshot_payload.get("catalogEntries"))
    snapshot_models = {
        str(model.get("candidateKey")): model
        for model in _sequence(snapshot_payload.get("models"))
    }
    if budget_units is None or not entries:
        return (
            AaRecalculationIssue(
                rule_id="AUD-AA-DECISION-EVIDENCE-MALFORMED",
                detail="예산 또는 snapshot의 고정 catalog가 없어 결정을 재계산할 수 없습니다.",
                expected={"budgetUnits": "int", "catalogEntries": "non-empty"},
                observed={
                    "budgetUnits": requested_payload.get("budgetUnits"),
                    "catalogEntries": len(entries),
                },
            ),
        )

    issues.extend(_snapshot_integrity_issues(snapshot_payload, decided_payload))
    tokens_in, tokens_out, token_issue = _token_estimate(normalized, decided_payload)
    if token_issue is not None:
        issues.append(token_issue)
    weights, priority_issues = _priority_issues(normalized, decided_payload)
    issues.extend(priority_issues)
    if tokens_in is None or tokens_out is None or weights is None:
        return tuple(issues)

    stored_candidates = {
        str(item.get("candidateKey")): item
        for item in _sequence(decided_payload.get("candidates"))
    }
    expected_keys = [str(entry.get("candidateKey")) for entry in entries]
    if sorted(stored_candidates) != sorted(expected_keys):
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-CANDIDATE-SET-INCOMPLETE",
                detail=(
                    "결정이 비교한 후보 집합이 snapshot에 고정된 catalog 전체와 다릅니다."
                ),
                expected={"candidateKeys": sorted(expected_keys)},
                observed={"candidateKeys": sorted(stored_candidates)},
                mismatched_fields=("candidates",),
            )
        )

    metrics: list[CandidateMetrics] = []
    expected_rejected: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        key = str(entry.get("candidateKey"))
        model = snapshot_models.get(key)
        if model is None:
            issues.append(
                AaRecalculationIssue(
                    rule_id="AUD-AA-SOURCE-VALUE-MISSING",
                    detail=f"{key} 후보의 AA 수치가 snapshot에 없습니다.",
                    expected={"candidateKey": key, "snapshotModel": "present"},
                    observed={"candidateKey": key, "snapshotModel": None},
                )
            )
            continue
        input_price = _decimal(model.get("inputPricePerMillion"))
        output_price = _decimal(model.get("outputPricePerMillion"))
        seconds = _decimal(model.get("medianEndToEndSeconds"))
        index = _decimal(model.get("intelligenceIndex"))
        if (
            input_price is None
            or output_price is None
            or seconds is None
            or index is None
        ):
            issues.append(
                AaRecalculationIssue(
                    rule_id="AUD-AA-SOURCE-VALUE-MISSING",
                    detail=f"{key} 후보의 snapshot 수치가 재계산 가능한 형태가 아닙니다.",
                    expected={"candidateKey": key, "source": "finite decimals"},
                    observed={"candidateKey": key, "source": dict(model)},
                )
            )
            continue
        try:
            expected_amount = amount_units(
                input_tokens=tokens_in,
                max_output_tokens=tokens_out,
                input_price_per_million=input_price,
                output_price_per_million=output_price,
            )
            expected_ms = completion_ms_from_seconds(seconds)
        except PolicyNumberError as exc:
            issues.append(
                AaRecalculationIssue(
                    rule_id="AUD-AA-SOURCE-VALUE-MISSING",
                    detail=f"{key} 후보의 AA 수치로는 금액을 계산할 수 없습니다: {exc}",
                    expected={"candidateKey": key, "source": "usable decimals"},
                    observed={"candidateKey": key, "error": str(exc)},
                )
            )
            continue
        capabilities = _strings(entry.get("capabilities"))
        provider_id = str(entry.get("providerId", ""))
        stored = stored_candidates.get(key)
        if stored is not None:
            issues.extend(
                _candidate_issues(
                    key=key,
                    entry=entry,
                    model=model,
                    stored=stored,
                    expected_amount=expected_amount,
                    expected_ms=expected_ms,
                )
            )
        reasons = rejection_reasons(
            provider_id=provider_id,
            amount_units=expected_amount,
            budget_units=budget_units,
            estimated_completion_ms=expected_ms,
            max_completion_ms=_int(normalized.get("max_completion_ms")),
            capabilities=capabilities,
            required_capabilities=_strings(normalized.get("required_capabilities")),
            allowed_providers=_strings(normalized.get("allowed_providers")),
        )
        if reasons:
            expected_rejected[key] = reasons
            continue
        if expected_amount <= 0:
            continue
        metrics.append(
            CandidateMetrics(
                key=key,
                provider_id=provider_id,
                model_id=str(entry.get("providerModelId", "")),
                amount_units=expected_amount,
                completion_ms=expected_ms,
                intelligence_index=index,
            )
        )

    issues.extend(_filter_issues(expected_rejected, metrics, decided_payload))
    if not metrics:
        return tuple(issues)
    issues.extend(_score_issues(metrics, weights=weights, decided_payload=decided_payload))
    return tuple(issues)


def _candidate_issues(
    *,
    key: str,
    entry: Mapping[str, object],
    model: Mapping[str, object],
    stored: Mapping[str, object],
    expected_amount: int,
    expected_ms: Decimal,
) -> list[AaRecalculationIssue]:
    """Compare one stored candidate with the snapshot it claims to have been priced from."""
    issues: list[AaRecalculationIssue] = []
    stored_source = _mapping(stored.get("source"))
    source_fields = (
        "aaModelId",
        "aaSlug",
        "inputPricePerMillion",
        "outputPricePerMillion",
        "medianEndToEndSeconds",
        "intelligenceIndex",
    )
    differing = tuple(
        field for field in source_fields if stored_source.get(field) != model.get(field)
    )
    if differing:
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-SOURCE-VALUE-MISMATCH",
                detail=f"{key} 후보에 기록된 AA 수치가 snapshot 원본과 다릅니다.",
                expected={field: model.get(field) for field in differing},
                observed={field: stored_source.get(field) for field in differing},
                mismatched_fields=differing,
            )
        )
    binding = tuple(
        field
        for field in ("providerId", "providerModelId", "modelVersion", "recipient")
        if stored.get(field) != entry.get(field)
    )
    if binding or _strings(stored.get("capabilities")) != _strings(
        entry.get("capabilities")
    ):
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-CATALOG-BINDING-MISMATCH",
                detail=f"{key} 후보의 모델 식별자·수신자·기능이 고정 catalog와 다릅니다.",
                expected={
                    **{field: entry.get(field) for field in binding},
                    "capabilities": list(_strings(entry.get("capabilities"))),
                },
                observed={
                    **{field: stored.get(field) for field in binding},
                    "capabilities": list(_strings(stored.get("capabilities"))),
                },
                mismatched_fields=binding or ("capabilities",),
            )
        )
    if _int(stored.get("amountUnits")) != expected_amount:
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-AMOUNT-MISMATCH",
                detail=f"{key} 후보의 선결제 금액이 AA 단가 산식과 다릅니다.",
                expected={"candidateKey": key, "amountUnits": expected_amount},
                observed={"candidateKey": key, "amountUnits": stored.get("amountUnits")},
                mismatched_fields=("amountUnits",),
            )
        )
    if _decimal(stored.get("estimatedCompletionMs")) != expected_ms:
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-COMPLETION-TIME-MISMATCH",
                detail=f"{key} 후보의 완료시간 환산값이 AA 초 단위와 다릅니다.",
                expected={
                    "candidateKey": key,
                    "estimatedCompletionMs": decimal_text(expected_ms),
                },
                observed={
                    "candidateKey": key,
                    "estimatedCompletionMs": stored.get("estimatedCompletionMs"),
                },
                mismatched_fields=("estimatedCompletionMs",),
            )
        )
    return issues


def _snapshot_integrity_issues(
    snapshot_payload: Mapping[str, object],
    decided_payload: Mapping[str, object],
) -> list[AaRecalculationIssue]:
    """The decision must cite this snapshot, and this snapshot must match its own bytes."""
    issues: list[AaRecalculationIssue] = []
    expected = {
        "snapshotId": snapshot_payload.get("snapshotId"),
        "snapshotHash": snapshot_payload.get("snapshotHash"),
        "catalogVersion": snapshot_payload.get("catalogVersion"),
    }
    observed = {
        "snapshotId": decided_payload.get("snapshotId"),
        "snapshotHash": decided_payload.get("snapshotHash"),
        "catalogVersion": decided_payload.get("catalogVersion"),
    }
    if expected != observed or not all(isinstance(item, str) for item in expected.values()):
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-SNAPSHOT-BINDING-MISMATCH",
                detail="결정이 기록된 AA snapshot과 다른 근거를 참조합니다.",
                expected=expected,
                observed=observed,
                mismatched_fields=("snapshotId", "snapshotHash", "catalogVersion"),
            )
        )
    raw_pages = snapshot_payload.get("rawPages")
    if isinstance(raw_pages, list) and all(isinstance(item, str) for item in raw_pages):
        recomputed = raw_parts_hash([str(item) for item in raw_pages])
        if recomputed != snapshot_payload.get("rawResponseHash"):
            issues.append(
                AaRecalculationIssue(
                    rule_id="AUD-AA-RAW-RESPONSE-HASH-MISMATCH",
                    detail="보존된 AA 원본 페이지가 기록된 응답 hash와 일치하지 않습니다.",
                    expected={"rawResponseHash": recomputed},
                    observed={"rawResponseHash": snapshot_payload.get("rawResponseHash")},
                    mismatched_fields=("rawResponseHash",),
                )
            )
    else:
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-RAW-RESPONSE-MISSING",
                detail="AA 원본 페이지가 보존되지 않아 응답 hash를 재확인할 수 없습니다.",
                expected={"rawPages": "array of page bodies"},
                observed={"rawPages": None},
            )
        )
    return issues


def _token_estimate(
    normalized: Mapping[str, object],
    decided_payload: Mapping[str, object],
) -> tuple[int | None, int | None, AaRecalculationIssue | None]:
    stored = _mapping(decided_payload.get("tokens"))
    request_in = _int(normalized.get("estimated_input_tokens"))
    request_out = _int(normalized.get("max_output_tokens"))
    request_bytes = _int(normalized.get("input_byte_length"))
    decision_in = _int(stored.get("estimatedInputTokens"))
    decision_out = _int(stored.get("maxOutputTokens"))
    derived = None if request_bytes is None else max(1, -(-request_bytes // 4))
    if (
        request_in is None
        or request_out is None
        or derived is None
        or request_in != derived
        or decision_in != request_in
        or decision_out != request_out
        or stored.get("estimationMethod") != normalized.get("estimation_method")
    ):
        return (
            None,
            None,
            AaRecalculationIssue(
                rule_id="AUD-AA-TOKEN-ESTIMATE-MISMATCH",
                detail="가격 산정에 쓰인 토큰 추정치가 요청 기록과 일치하지 않습니다.",
                expected={
                    "estimatedInputTokens": derived,
                    "maxOutputTokens": request_out,
                    "estimationMethod": normalized.get("estimation_method"),
                },
                observed={
                    "estimatedInputTokens": stored.get("estimatedInputTokens"),
                    "maxOutputTokens": stored.get("maxOutputTokens"),
                    "estimationMethod": stored.get("estimationMethod"),
                },
                mismatched_fields=("estimatedInputTokens", "maxOutputTokens"),
            ),
        )
    return request_in, request_out, None


def _priority_issues(
    normalized: Mapping[str, object],
    decided_payload: Mapping[str, object],
) -> tuple[PolicyWeights | None, list[AaRecalculationIssue]]:
    """Check the priority, its recorded justification and the fixed weight table.

    The prompt itself is never in the evidence chain, so a keyword classification cannot
    be re-derived here. Internal consistency and dictionary membership are checked, and
    the un-rederivable part is reported instead of being presented as verified.
    """
    issues: list[AaRecalculationIssue] = []
    stored = _mapping(decided_payload.get("priority"))
    raw_priority = normalized.get("effective_priority")
    try:
        priority = RequestPriority(str(raw_priority))
    except ValueError:
        priority = None
    expected_weights = None if priority is None else PRIORITY_WEIGHTS[priority]
    expected_priority = {
        "classificationMethod": "keyword-single-match-v1",
        "effectivePriority": raw_priority,
        "matchedKeywords": list(_strings(normalized.get("matched_keywords"))),
        "matchedPriorities": list(_strings(normalized.get("matched_priorities"))),
        "originalPriority": normalized.get("original_priority"),
        "reason": normalized.get("priority_reason"),
        "weights": None if expected_weights is None else expected_weights.to_payload(),
    }
    if expected_weights is None or dict(stored) != expected_priority:
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-PRIORITY-WEIGHTS-MISMATCH",
                detail="결정에 기록된 우선순위 분류 근거가 요청 기록과 다릅니다.",
                expected=expected_priority,
                observed=dict(stored),
                mismatched_fields=("priority", "weights"),
            )
        )
    if decided_payload.get("weights") != expected_priority["weights"]:
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-PRIORITY-WEIGHTS-MISMATCH",
                detail="적용된 고정 가중치가 우선순위 정책표와 다릅니다.",
                expected={"weights": expected_priority["weights"]},
                observed={"weights": decided_payload.get("weights")},
                mismatched_fields=("weights",),
            )
        )
    blocking = bool(issues)
    issues.extend(_classification_issues(normalized, priority))
    return (None if blocking else expected_weights), issues


def _classification_issues(
    normalized: Mapping[str, object],
    priority: RequestPriority | None,
) -> list[AaRecalculationIssue]:
    original = normalized.get("original_priority")
    raw_reason = normalized.get("priority_reason")
    matched_priorities = _strings(normalized.get("matched_priorities"))
    matched_keywords = _strings(normalized.get("matched_keywords"))
    try:
        reason = PriorityReason(str(raw_reason))
    except ValueError:
        return [
            AaRecalculationIssue(
                rule_id="AUD-AA-PRIORITY-CLASSIFICATION-INVALID",
                detail="우선순위 분류 사유가 정책이 정의한 값이 아닙니다.",
                expected={"reason": [item.value for item in PriorityReason]},
                observed={"reason": raw_reason},
                mismatched_fields=("priority_reason",),
            )
        ]
    if priority is None:
        return []
    expectations: dict[str, object] = {}
    if reason is PriorityReason.EXPLICIT:
        expectations = {
            "originalPriority": priority.value,
            "matchedPriorities": [],
            "matchedKeywords": [],
        }
        actual = {
            "originalPriority": original,
            "matchedPriorities": list(matched_priorities),
            "matchedKeywords": list(matched_keywords),
        }
    elif reason is PriorityReason.KEYWORD_MATCH:
        expectations = {
            "originalPriority": None,
            "matchedPriorities": [priority.value],
            "matchedKeywordsPresent": True,
        }
        actual = {
            "originalPriority": original,
            "matchedPriorities": list(matched_priorities),
            "matchedKeywordsPresent": bool(matched_keywords),
        }
    elif reason is PriorityReason.CONFLICTING_KEYWORD_MATCH:
        expectations = {
            "originalPriority": None,
            "effectivePriority": RequestPriority.DEFAULT.value,
            "matchedPriorityCountAtLeast": 2,
        }
        actual = {
            "originalPriority": original,
            "effectivePriority": priority.value,
            "matchedPriorityCountAtLeast": len(matched_priorities),
        }
    else:
        expectations = {
            "originalPriority": None,
            "effectivePriority": RequestPriority.DEFAULT.value,
            "matchedPriorities": [],
            "matchedKeywords": [],
        }
        actual = {
            "originalPriority": original,
            "effectivePriority": priority.value,
            "matchedPriorities": list(matched_priorities),
            "matchedKeywords": list(matched_keywords),
        }
    inconsistent = (
        reason is PriorityReason.CONFLICTING_KEYWORD_MATCH
        and (
            original is not None
            or priority is not RequestPriority.DEFAULT
            or len(matched_priorities) < 2
        )
    ) or (
        reason is PriorityReason.EXPLICIT
        and (original != priority.value or matched_priorities or matched_keywords)
    ) or (
        reason is PriorityReason.KEYWORD_MATCH
        and (
            original is not None
            or list(matched_priorities) != [priority.value]
            or not matched_keywords
        )
    ) or (
        reason is PriorityReason.NO_KEYWORD_MATCH
        and (
            original is not None
            or priority is not RequestPriority.DEFAULT
            or matched_priorities
            or matched_keywords
        )
    )
    issues: list[AaRecalculationIssue] = []
    if inconsistent:
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-PRIORITY-CLASSIFICATION-INCONSISTENT",
                detail="기록된 우선순위 분류 사유와 매치 근거가 서로 맞지 않습니다.",
                expected=expectations,
                observed=actual,
                mismatched_fields=("priority_reason", "original_priority"),
            )
        )
    unknown = sorted(
        keyword
        for keyword in matched_keywords
        if not any(keyword in group for group in PRIORITY_KEYWORDS.values())
    )
    if unknown:
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-PRIORITY-KEYWORD-UNKNOWN",
                detail="분류 근거에 정책 사전에 없는 키워드가 기록돼 있습니다.",
                expected={"keywordDictionary": "policy fixed terms"},
                observed={"unknownKeywords": unknown},
                mismatched_fields=("matched_keywords",),
            )
        )
    if reason in (PriorityReason.KEYWORD_MATCH, PriorityReason.CONFLICTING_KEYWORD_MATCH):
        # The prompt is never part of the evidence chain, so this audit can confirm the
        # recorded justification is internally consistent and uses only dictionary terms,
        # but it cannot re-derive the match from the original text.
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-PRIORITY-CLASSIFICATION-UNVERIFIABLE",
                detail=(
                    "키워드 분류는 원문이 증거에 없어 재도출할 수 없습니다. "
                    "기록된 근거의 내부 정합성만 확인했습니다."
                ),
                expected={"promptDerivedClassification": "not re-derivable"},
                observed={
                    "reason": reason.value,
                    "matchedKeywords": list(matched_keywords),
                },
                is_contradiction=False,
            )
        )
    return issues


def _filter_issues(
    expected_rejected: Mapping[str, tuple[str, ...]],
    metrics: Sequence[CandidateMetrics],
    decided_payload: Mapping[str, object],
) -> list[AaRecalculationIssue]:
    recorded_rejected = {
        str(item.get("candidateKey")): _strings(item.get("reasons"))
        for item in _sequence(decided_payload.get("rejected"))
    }
    recorded_eligible = [
        str(item.get("candidateKey")) for item in _sequence(decided_payload.get("eligible"))
    ]
    expected_eligible = [metric.key for metric in metrics]
    if dict(expected_rejected) == recorded_rejected and set(expected_eligible) == set(
        recorded_eligible
    ):
        return []
    return [
        AaRecalculationIssue(
            rule_id="AUD-AA-HARD-FILTER-MISMATCH",
            detail="예산·기능·허용시간·허용 제공자로 재계산한 필터 결과가 기록과 다릅니다.",
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
            mismatched_fields=("eligible", "rejected"),
        )
    ]


def _score_issues(
    metrics: Sequence[CandidateMetrics],
    *,
    weights: PolicyWeights,
    decided_payload: Mapping[str, object],
) -> list[AaRecalculationIssue]:
    issues: list[AaRecalculationIssue] = []
    references, ranked = rank_candidates(metrics, weights=weights)
    expected_references = references.to_payload()
    if decided_payload.get("scoreReferences") != expected_references:
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-SCORE-REFERENCE-MISMATCH",
                detail="정규화 분모가 필터를 통과한 후보 집합과 일치하지 않습니다.",
                expected=expected_references,
                observed=dict(_mapping(decided_payload.get("scoreReferences"))),
                mismatched_fields=("scoreReferences",),
            )
        )
    recorded = _sequence(decided_payload.get("eligible"))
    recorded_by_key = {str(item.get("candidateKey")): item for item in recorded}
    for position, (metric, score) in enumerate(ranked, start=1):
        stored = recorded_by_key.get(metric.key)
        expected_scores = score.to_payload()
        if stored is None or _mapping(stored.get("scores")) != expected_scores:
            issues.append(
                AaRecalculationIssue(
                    rule_id="AUD-AA-SCORE-MISMATCH",
                    detail=f"{metric.key} 후보의 세 요인 점수가 재계산 결과와 다릅니다.",
                    expected={"candidateKey": metric.key, "scores": expected_scores},
                    observed={
                        "candidateKey": metric.key,
                        "scores": None if stored is None else stored.get("scores"),
                    },
                    mismatched_fields=("scores",),
                )
            )
        elif _int(stored.get("rank")) != position:
            issues.append(
                AaRecalculationIssue(
                    rule_id="AUD-AA-RANK-MISMATCH",
                    detail=f"{metric.key} 후보의 순위가 결정적 정렬 결과와 다릅니다.",
                    expected={"candidateKey": metric.key, "rank": position},
                    observed={"candidateKey": metric.key, "rank": stored.get("rank")},
                    mismatched_fields=("rank",),
                )
            )
    expected_winner = ranked[0][0].key
    winner = _mapping(decided_payload.get("winner"))
    stored_winner = str(winner.get("candidateKey"))
    expected_amount = ranked[0][0].amount_units
    if stored_winner != expected_winner or _int(decided_payload.get("amountUnits")) != (
        expected_amount
    ):
        issues.append(
            AaRecalculationIssue(
                rule_id="AUD-AA-WINNER-MISMATCH",
                detail="선택된 모델 또는 결제 금액이 재계산된 1순위와 다릅니다.",
                expected={
                    "candidateKey": expected_winner,
                    "amountUnits": expected_amount,
                    "totalScore": fraction_text(ranked[0][1].total),
                },
                observed={
                    "candidateKey": stored_winner,
                    "amountUnits": decided_payload.get("amountUnits"),
                    "totalScore": _mapping(winner.get("scores")).get("total"),
                },
                mismatched_fields=("winner", "amountUnits"),
            )
        )
    return issues
