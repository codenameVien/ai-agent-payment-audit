"""Deterministic pricing, hard filtering and three-factor selection.

Order of operations is fixed by the 2026-09-09 scope: derive the exact prepayment amount
for every catalog model from the same AA snapshot, apply the hard filters, then normalize
and score only the candidates that survived. A missing or invalid required number aborts
the purchase before payment; it never becomes a default score or a silent drop.
"""

from __future__ import annotations

from buyer_audit_api.core.aa_policy import (
    CandidateMetrics,
    PolicyNumberError,
    amount_units,
    completion_ms_from_seconds,
    rank_candidates,
    rejection_reasons,
)
from buyer_audit_api.core.errors import SelectionAbortedError
from buyer_audit_api.domains.ai_inference.aa_models import (
    AaSelectionDecision,
    AaSnapshot,
    ModelCatalog,
    PricedCandidate,
    RejectedCandidate,
    ScoredCandidate,
    TokenIdentity,
    decision_explanation,
    terms_binding_hash,
)
from buyer_audit_api.domains.ai_inference.aa_request import AegisNormalizedRequest


def price_candidates(
    *,
    catalog: ModelCatalog,
    snapshot: AaSnapshot,
    request: AegisNormalizedRequest,
) -> tuple[PricedCandidate, ...]:
    """Fixed prepayment per model: `ceil(inTok*inPrice + maxOutTok*outPrice)`, markup 0."""
    if snapshot.catalog_version != catalog.catalog_version:
        raise SelectionAbortedError(
            "snapshot catalog version does not match the configured catalog"
        )
    priced: list[PricedCandidate] = []
    for entry in catalog.entries:
        metrics = snapshot.metrics(entry.key)
        try:
            units = amount_units(
                input_tokens=request.estimated_input_tokens,
                max_output_tokens=request.max_output_tokens,
                input_price_per_million=metrics.input_price_per_million,
                output_price_per_million=metrics.output_price_per_million,
            )
            completion_ms = completion_ms_from_seconds(metrics.median_end_to_end_seconds)
        except PolicyNumberError as exc:
            raise SelectionAbortedError(f"{entry.key}: {exc}") from exc
        if units == 0:
            # A zero unit price is allowed, but this policy cannot settle a zero payment.
            raise SelectionAbortedError(
                f"zero_payment_amount_unsupported: {entry.key} priced at 0 units"
            )
        priced.append(
            PricedCandidate(
                entry=entry,
                metrics=metrics,
                amount_units=units,
                estimated_completion_ms=completion_ms,
            )
        )
    return tuple(priced)


def apply_hard_filters(
    candidates: tuple[PricedCandidate, ...],
    *,
    request: AegisNormalizedRequest,
    budget_units: int,
) -> tuple[tuple[PricedCandidate, ...], tuple[RejectedCandidate, ...]]:
    """Split the priced candidates, preserving every rejection reason."""
    eligible: list[PricedCandidate] = []
    rejected: list[RejectedCandidate] = []
    for candidate in candidates:
        reasons = rejection_reasons(
            provider_id=candidate.entry.provider_id,
            amount_units=candidate.amount_units,
            budget_units=budget_units,
            estimated_completion_ms=candidate.estimated_completion_ms,
            max_completion_ms=request.max_completion_ms,
            capabilities=candidate.entry.capabilities,
            required_capabilities=request.required_capabilities,
            allowed_providers=request.allowed_providers,
        )
        if reasons:
            rejected.append(
                RejectedCandidate(
                    candidate_key=candidate.key,
                    provider_id=candidate.entry.provider_id,
                    provider_model_id=candidate.entry.provider_model_id,
                    model_version=candidate.entry.model_version,
                    reasons=reasons,
                )
            )
            continue
        eligible.append(candidate)
    return tuple(eligible), tuple(rejected)


def select(
    *,
    purchase_id: str,
    catalog: ModelCatalog,
    snapshot: AaSnapshot,
    snapshot_hash: str,
    request: AegisNormalizedRequest,
    budget_units: int,
    token: TokenIdentity,
) -> AaSelectionDecision:
    """Produce the complete fixed decision, or abort with an explicit reason."""
    if budget_units < 0:
        raise SelectionAbortedError("budget_units must be non-negative")
    candidates = price_candidates(catalog=catalog, snapshot=snapshot, request=request)
    eligible, rejected = apply_hard_filters(
        candidates, request=request, budget_units=budget_units
    )
    if not eligible:
        raise SelectionAbortedError(
            "no_eligible_candidate: every candidate was rejected by the request filters"
        )
    by_key = {candidate.key: candidate for candidate in eligible}
    try:
        metrics = [
            CandidateMetrics(
                key=candidate.key,
                provider_id=candidate.entry.provider_id,
                model_id=candidate.entry.provider_model_id,
                amount_units=candidate.amount_units,
                completion_ms=candidate.estimated_completion_ms,
                intelligence_index=candidate.metrics.intelligence_index,
            )
            for candidate in eligible
        ]
        references, ranked = rank_candidates(metrics, weights=request.weights)
    except PolicyNumberError as exc:
        raise SelectionAbortedError(str(exc)) from exc
    scored = tuple(
        ScoredCandidate(candidate=by_key[metric.key], score=score, rank=position)
        for position, (metric, score) in enumerate(ranked, start=1)
    )
    winner = scored[0]
    binding = terms_binding_hash(
        purchase_id=purchase_id,
        snapshot_hash=snapshot_hash,
        winner=winner.candidate,
        token=token,
    )
    return AaSelectionDecision(
        purchase_id=purchase_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot_hash,
        catalog_version=catalog.catalog_version,
        classification=request.classification,
        tokens=request.token_estimate,
        candidates=candidates,
        rejected=rejected,
        eligible=scored,
        references=references,
        winner=winner,
        token=token,
        explanation=decision_explanation(
            classification=request.classification, winner=winner
        ),
        terms_binding_hash=binding,
    )
