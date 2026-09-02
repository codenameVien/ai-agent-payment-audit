from __future__ import annotations

from datetime import datetime, timedelta

from buyer_audit_api.domains.ai_inference.models import (
    BenchmarkSnapshot,
    Candidate,
    NormalizedAiRequest,
    PriorityPreset,
    RejectedCandidate,
    ScoredCandidate,
    SelectionDecision,
)

WEIGHT_PRESETS: dict[PriorityPreset, dict[str, int]] = {
    PriorityPreset.BALANCED: {
        "quality": 40,
        "price": 25,
        "speed": 20,
        "reputation": 10,
        "freshness": 5,
    },
    PriorityPreset.QUALITY: {
        "quality": 60,
        "price": 10,
        "speed": 15,
        "reputation": 10,
        "freshness": 5,
    },
    PriorityPreset.PRICE: {
        "quality": 25,
        "price": 50,
        "speed": 10,
        "reputation": 10,
        "freshness": 5,
    },
    PriorityPreset.SPEED: {
        "quality": 25,
        "price": 10,
        "speed": 50,
        "reputation": 10,
        "freshness": 5,
    },
}


def _freshness_score(snapshot: BenchmarkSnapshot, now: datetime) -> float:
    age = max(timedelta(), now - snapshot.observed_at)
    return max(0.0, 100.0 * (1.0 - age.total_seconds() / timedelta(hours=24).total_seconds()))


def _price_score(amount_units: int, budget_units: int) -> float:
    if budget_units == 0:
        return 100.0 if amount_units == 0 else 0.0
    return max(0.0, 100.0 * (budget_units - amount_units) / budget_units)


def _rejection_reasons(
    *,
    candidate: Candidate,
    request: NormalizedAiRequest,
    budget_units: int,
    now: datetime,
) -> tuple[str, ...]:
    quote = candidate.quote
    benchmark = candidate.benchmark
    reasons: list[str] = []
    if quote.purchase_id == "":
        reasons.append("missing_purchase_id")
    if (
        quote.provider_id != benchmark.provider_id
        or quote.model_id != benchmark.model_id
        or quote.model_version != benchmark.model_version
    ):
        reasons.append("quote_benchmark_mismatch")
    if not quote.available:
        reasons.append("unavailable")
    if not quote.identity_verified:
        reasons.append("identity_unverified")
    if quote.amount_units > budget_units:
        reasons.append("over_budget")
    if quote.expires_at <= now:
        reasons.append("quote_expired")
    if now - benchmark.observed_at > timedelta(hours=24) or benchmark.observed_at > now:
        reasons.append("benchmark_stale")
    if request.allowed_sellers and quote.provider_id.lower() not in request.allowed_sellers:
        reasons.append("seller_not_allowed")
    if quote.input_limit < request.min_input_limit:
        reasons.append("input_limit")
    if quote.output_limit < request.min_output_limit:
        reasons.append("output_limit")
    if request.max_latency_ms and quote.expected_latency_ms > request.max_latency_ms:
        reasons.append("latency_limit")
    available_capabilities = {value.lower() for value in benchmark.capabilities}
    if not set(request.required_capabilities).issubset(available_capabilities):
        reasons.append("missing_capability")
    return tuple(reasons)


class SelectionEngine:
    def decide(
        self,
        *,
        request: NormalizedAiRequest,
        budget_units: int,
        candidates: list[Candidate],
        now: datetime,
    ) -> SelectionDecision:
        if not candidates:
            raise ValueError("no candidates")
        weights = WEIGHT_PRESETS[request.priority]
        eligible: list[ScoredCandidate] = []
        rejected: list[RejectedCandidate] = []

        for candidate in candidates:
            reasons = _rejection_reasons(
                candidate=candidate,
                request=request,
                budget_units=budget_units,
                now=now,
            )
            quote = candidate.quote
            if reasons:
                rejected.append(
                    RejectedCandidate(
                        quote_id=quote.quote_id,
                        provider_id=quote.provider_id,
                        model_id=quote.model_id,
                        reasons=reasons,
                    )
                )
                continue
            components = {
                "freshness": _freshness_score(candidate.benchmark, now),
                "price": _price_score(quote.amount_units, budget_units),
                "quality": candidate.benchmark.quality_score,
                "reputation": candidate.benchmark.reputation_score,
                "speed": candidate.benchmark.speed_score,
            }
            total = sum(components[name] * weight for name, weight in weights.items()) / 100
            eligible.append(
                ScoredCandidate(
                    quote_id=quote.quote_id,
                    provider_id=quote.provider_id,
                    model_id=quote.model_id,
                    total_score=round(total, 6),
                    component_scores={name: round(value, 6) for name, value in components.items()},
                    weights=weights,
                )
            )

        if not eligible:
            raise ValueError("no eligible candidates")

        quote_amounts = {
            candidate.quote.quote_id: candidate.quote.amount_units for candidate in candidates
        }
        eligible.sort(
            key=lambda item: (
                -item.total_score,
                quote_amounts[item.quote_id],
                item.provider_id,
                item.model_id,
                item.quote_id,
            )
        )
        winner = eligible[0]
        explanation = (
            f"{request.priority.value} preset에서 {winner.provider_id}/{winner.model_id}가 "
            f"{winner.total_score:.2f}점으로 가장 높았습니다. "
            "필터와 점수는 결정적 코드가 계산했습니다."
        )
        return SelectionDecision(
            preset=request.priority,
            winner=winner,
            eligible=tuple(eligible),
            rejected=tuple(rejected),
            benchmark_snapshot_ids=tuple(
                sorted({candidate.benchmark.snapshot_id for candidate in candidates})
            ),
            explanation=explanation,
        )
