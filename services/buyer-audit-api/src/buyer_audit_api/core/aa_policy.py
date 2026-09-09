"""Deterministic `aa-three-factor-v1` policy primitives.

This module is the single source of truth for the numbers the 2026-09-09 scope fixes:
priority classification, input-token estimation, exact decimal pricing, the three
normalized component scores, the fixed weight table and the tie-break order.

It lives in `core` because two independent callers need exactly the same arithmetic:
the buyer decision path in `domains/ai_inference` and the audit recalculation in
`core/audit`. The audit's independence comes from re-deriving its inputs from stored
evidence instead of trusting stored results, not from a second divergent copy of the
formulas - a duplicate implementation of decimal money math would be a correctness risk.

Everything here is pure: no I/O, no clock, no provider identities. Scores are compared
as exact `Fraction` values, never as rounded floats and never with an epsilon, so a tie
is a true tie. Decimal text is produced only for storage and display.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from fractions import Fraction
from typing import Literal

from buyer_audit_api.core.models import JsonObject

AaScoringPolicyVersion = Literal["aa-three-factor-v1"]
AaRequestSchemaVersion = Literal["aegis-aa-v1"]
TokenEstimationMethod = Literal["utf8-bytes-div4-v1"]

AA_SCORING_POLICY_VERSION: AaScoringPolicyVersion = "aa-three-factor-v1"
AA_REQUEST_SCHEMA_VERSION: AaRequestSchemaVersion = "aegis-aa-v1"
TOKEN_ESTIMATION_METHOD: TokenEstimationMethod = "utf8-bytes-div4-v1"
PRIORITY_CLASSIFICATION_METHOD = "keyword-single-match-v1"

# AEGIS is a 6-decimal token, so one unit is 1e-6 nominal USD.
AEGIS_DECIMALS = 6
AEGIS_UNITS_PER_TOKEN = 10**AEGIS_DECIMALS
# AA publishes prices per one million tokens.
AA_PRICE_TOKEN_BASE = 1_000_000
MAX_UINT256 = 2**256 - 1

# Stored score text is display-only; ranking always uses the exact `Fraction`.
SCORE_DECIMAL_PLACES = 18
_SCORE_QUANTUM = Decimal(1).scaleb(-SCORE_DECIMAL_PLACES)
#: Milliseconds are seconds scaled by 10**3, applied as an exact exponent shift.
_SECONDS_TO_MS_EXPONENT = 3


class PolicyNumberError(ValueError):
    """A required policy number is missing, non-finite, or out of its allowed range."""


class RequestPriority(StrEnum):
    """The only four priority values a new `aa-three-factor-v1` request may carry."""

    DEFAULT = "default"
    PRICE = "price"
    SPEED = "speed"
    INTELLIGENCE = "intelligence"


#: Names from the superseded weighted-preset policy. New requests reject them outright
#: instead of re-interpreting them, while history readers keep rendering stored values.
LEGACY_PRIORITY_NAMES: frozenset[str] = frozenset({"balanced", "quality", "performance"})


@dataclass(frozen=True, slots=True)
class PolicyWeights:
    """Fixed integer percentages; the three always sum to 100."""

    price: int
    completion_time: int
    intelligence: int

    def __post_init__(self) -> None:
        if self.price + self.completion_time + self.intelligence != 100:
            raise PolicyNumberError("policy weights must sum to 100")

    def to_payload(self) -> JsonObject:
        return {
            "completionTime": self.completion_time,
            "intelligence": self.intelligence,
            "price": self.price,
        }


PRIORITY_WEIGHTS: dict[RequestPriority, PolicyWeights] = {
    RequestPriority.DEFAULT: PolicyWeights(price=40, completion_time=30, intelligence=30),
    RequestPriority.PRICE: PolicyWeights(price=60, completion_time=20, intelligence=20),
    RequestPriority.SPEED: PolicyWeights(price=20, completion_time=60, intelligence=20),
    RequestPriority.INTELLIGENCE: PolicyWeights(price=20, completion_time=20, intelligence=60),
}

#: Classification dictionary. Only these fixed terms are ever stored as match evidence,
#: so recording the reason never leaks a fragment of the user's prompt.
PRIORITY_KEYWORDS: dict[RequestPriority, tuple[str, ...]] = {
    RequestPriority.PRICE: (
        "싸게",
        "저렴",
        "저비용",
        "값싼",
        "비용 최소화",
        "최소 비용",
        "cheap",
        "cheapest",
        "lowest cost",
        "low cost",
    ),
    RequestPriority.SPEED: (
        "빨리",
        "빠르게",
        "즉시",
        "실시간",
        "신속",
        "fast",
        "fastest",
        "immediately",
        "real-time",
        "realtime",
        "low latency",
    ),
    RequestPriority.INTELLIGENCE: (
        "정확하게",
        "정확도",
        "복잡한 추론",
        "추론 성능",
        "성능 중요",
        "고품질",
        "accurate",
        "accuracy",
        "complex reasoning",
        "high quality",
    ),
}


class PriorityReason(StrEnum):
    EXPLICIT = "explicit_priority"
    KEYWORD_MATCH = "keyword_match"
    NO_KEYWORD_MATCH = "no_keyword_match"
    CONFLICTING_KEYWORD_MATCH = "conflicting_keyword_match"


@dataclass(frozen=True, slots=True)
class PriorityClassification:
    """`AEGIS-US-01`: the effective priority plus the evidence that produced it."""

    effective: RequestPriority
    original: RequestPriority | None
    reason: PriorityReason
    matched_keywords: tuple[str, ...]
    matched_priorities: tuple[RequestPriority, ...]

    @property
    def weights(self) -> PolicyWeights:
        return PRIORITY_WEIGHTS[self.effective]

    def to_payload(self) -> JsonObject:
        return {
            "classificationMethod": PRIORITY_CLASSIFICATION_METHOD,
            "effectivePriority": self.effective.value,
            "matchedKeywords": list(self.matched_keywords),
            "matchedPriorities": [item.value for item in self.matched_priorities],
            "originalPriority": None if self.original is None else self.original.value,
            "reason": self.reason.value,
            "weights": self.weights.to_payload(),
        }


def parse_priority(value: object) -> RequestPriority | None:
    """Accept exactly the four new names; reject the superseded ones explicitly."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise PolicyNumberError("priority must be a string")
    candidate = value.strip().lower()
    if candidate == "":
        return None
    if candidate in LEGACY_PRIORITY_NAMES:
        raise PolicyNumberError(
            f"priority '{candidate}' belongs to the superseded weighted-preset policy; "
            "use one of default, price, speed, intelligence"
        )
    try:
        return RequestPriority(candidate)
    except ValueError as exc:
        raise PolicyNumberError(
            f"priority '{candidate}' is not supported; "
            "use one of default, price, speed, intelligence"
        ) from exc


def classify_priority(*, prompt: str, explicit: object = None) -> PriorityClassification:
    """An explicit priority always wins, including an explicit `default`."""
    declared = parse_priority(explicit)
    if declared is not None:
        return PriorityClassification(
            effective=declared,
            original=declared,
            reason=PriorityReason.EXPLICIT,
            matched_keywords=(),
            matched_priorities=(),
        )
    haystack = unicodedata.normalize("NFKC", prompt).casefold()
    matched: dict[RequestPriority, list[str]] = {}
    for priority, keywords in PRIORITY_KEYWORDS.items():
        hits = [
            keyword
            for keyword in keywords
            if unicodedata.normalize("NFKC", keyword).casefold() in haystack
        ]
        if hits:
            matched[priority] = hits
    matched_priorities = tuple(
        priority for priority in RequestPriority if priority in matched
    )
    matched_keywords = tuple(
        keyword for priority in matched_priorities for keyword in matched[priority]
    )
    if len(matched_priorities) == 1:
        return PriorityClassification(
            effective=matched_priorities[0],
            original=None,
            reason=PriorityReason.KEYWORD_MATCH,
            matched_keywords=matched_keywords,
            matched_priorities=matched_priorities,
        )
    return PriorityClassification(
        effective=RequestPriority.DEFAULT,
        original=None,
        reason=(
            PriorityReason.CONFLICTING_KEYWORD_MATCH
            if matched_priorities
            else PriorityReason.NO_KEYWORD_MATCH
        ),
        matched_keywords=matched_keywords,
        matched_priorities=matched_priorities,
    )


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    """`utf8-bytes-div4-v1`: an estimate, never a measured tokenizer count."""

    input_bytes: int
    input_tokens: int
    max_output_tokens: int

    def to_payload(self) -> JsonObject:
        return {
            "estimatedInputTokens": self.input_tokens,
            "estimationMethod": TOKEN_ESTIMATION_METHOD,
            "inputByteLength": self.input_bytes,
            "isEstimate": True,
            "maxOutputTokens": self.max_output_tokens,
        }


def require_positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyNumberError(f"{name} must be an integer")
    if value <= 0:
        raise PolicyNumberError(f"{name} must be a positive integer")
    if value > MAX_UINT256:
        raise PolicyNumberError(f"{name} exceeds the uint256 range")
    return value


def estimate_tokens(
    *,
    model_input_parts: Iterable[str],
    max_output_tokens: int,
) -> TokenEstimate:
    """Every byte handed to the model counts, not just the user's own prompt."""
    input_bytes = sum(len(part.encode("utf-8")) for part in model_input_parts)
    input_tokens = max(1, -(-input_bytes // 4))
    return TokenEstimate(
        input_bytes=input_bytes,
        input_tokens=input_tokens,
        max_output_tokens=require_positive_int(max_output_tokens, name="maxOutputTokens"),
    )


def require_finite_decimal(
    value: object,
    *,
    name: str,
    allow_zero: bool = True,
) -> Decimal:
    """Reject `None`, NaN, Infinity and negatives before any money math happens."""
    if value is None:
        raise PolicyNumberError(f"{name} is missing")
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise PolicyNumberError(f"{name} must be a decimal number")
    number = Decimal(value)
    if not number.is_finite():
        raise PolicyNumberError(f"{name} must be finite")
    if number < 0:
        raise PolicyNumberError(f"{name} must not be negative")
    if not allow_zero and number == 0:
        raise PolicyNumberError(f"{name} must be greater than zero")
    return number


def amount_units(
    *,
    input_tokens: int,
    max_output_tokens: int,
    input_price_per_million: Decimal,
    output_price_per_million: Decimal,
) -> int:
    """`ceil(inputTokens*inputPrice + maxOutputTokens*outputPrice)` in AEGIS units.

    `quoteAEGIS = (inputTokens*inputPrice + maxOutputTokens*outputPrice)/1e6` and
    `amountUnits = ceil(quoteAEGIS*1e6)`, so the 1e6 price base and the 1e6 token unit
    base cancel. Markup is 0. The arithmetic is exact rational math on the decimal
    literals AA published, so no binary float ever rounds a price.
    """
    tokens_in = require_positive_int(input_tokens, name="estimatedInputTokens")
    tokens_out = require_positive_int(max_output_tokens, name="maxOutputTokens")
    input_price = require_finite_decimal(input_price_per_million, name="inputPricePerMillion")
    output_price = require_finite_decimal(output_price_per_million, name="outputPricePerMillion")
    total = (
        Fraction(tokens_in) * Fraction(input_price)
        + Fraction(tokens_out) * Fraction(output_price)
    ) * Fraction(AEGIS_UNITS_PER_TOKEN, AA_PRICE_TOKEN_BASE)
    units = -(-total.numerator // total.denominator)
    if units > MAX_UINT256:
        raise PolicyNumberError("amountUnits exceeds the uint256 range")
    return units


def completion_ms_from_seconds(seconds: Decimal) -> Decimal:
    """`estimatedCompletionMs = seconds*1000`, a benchmark reference, not an SLA."""
    value = require_finite_decimal(seconds, name="medianEndToEndSeconds", allow_zero=False)
    # Multiplying would round at the active decimal context precision; shifting the
    # exponent keeps every published digit.
    sign, digits, exponent = value.as_tuple()
    return Decimal((sign, digits, int(exponent) + _SECONDS_TO_MS_EXPONENT))


@dataclass(frozen=True, slots=True)
class CandidateMetrics:
    """The three comparable facts of one eligible candidate, plus its tie-break identity."""

    key: str
    provider_id: str
    model_id: str
    amount_units: int
    completion_ms: Decimal
    intelligence_index: Decimal

    def __post_init__(self) -> None:
        require_positive_int(self.amount_units, name="amountUnits")
        require_finite_decimal(self.completion_ms, name="estimatedCompletionMs", allow_zero=False)
        require_finite_decimal(
            self.intelligence_index, name="intelligenceIndex", allow_zero=False
        )


@dataclass(frozen=True, slots=True)
class ScoreReferences:
    """Normalization denominators taken from the eligible set only."""

    min_amount_units: int
    min_completion_ms: Decimal
    max_intelligence_index: Decimal

    def to_payload(self) -> JsonObject:
        return {
            "maxIntelligenceIndex": decimal_text(self.max_intelligence_index),
            "minAmountUnits": self.min_amount_units,
            "minEstimatedCompletionMs": decimal_text(self.min_completion_ms),
        }


@dataclass(frozen=True, slots=True)
class CandidateScore:
    """Exact scores. `total` decides rank; the decimal text is for storage and UI."""

    key: str
    price: Fraction
    completion_time: Fraction
    intelligence: Fraction
    total: Fraction

    def to_payload(self) -> JsonObject:
        return {
            "completionTime": fraction_text(self.completion_time),
            "intelligence": fraction_text(self.intelligence),
            "price": fraction_text(self.price),
            "total": fraction_text(self.total),
        }


def decimal_text(value: Decimal) -> str:
    """Lossless plain-decimal text; never scientific notation, never a float repr.

    `Decimal.normalize()` is deliberately not used: it rounds to the active context
    precision, which would silently shorten a published price before it is stored.
    Formatting and trailing-zero stripping are pure string operations, so every digit
    survives a store and a reload.
    """
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-", "-0"):
        return "0"
    return text


def fraction_text(value: Fraction, *, places: int = SCORE_DECIMAL_PLACES) -> str:
    """Quantize an exact score to a documented fixed precision for storage/display."""
    with localcontext() as context:
        context.prec = places + 40
        quantized = (
            Decimal(value.numerator) / Decimal(value.denominator)
        ).quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_EVEN)
    return decimal_text(quantized)


def score_references(metrics: Sequence[CandidateMetrics]) -> ScoreReferences:
    if not metrics:
        raise PolicyNumberError("score references require at least one eligible candidate")
    return ScoreReferences(
        min_amount_units=min(item.amount_units for item in metrics),
        min_completion_ms=min(item.completion_ms for item in metrics),
        max_intelligence_index=max(item.intelligence_index for item in metrics),
    )


def score_candidate(
    metric: CandidateMetrics,
    *,
    references: ScoreReferences,
    weights: PolicyWeights,
) -> CandidateScore:
    hundred = Fraction(100)
    price = Fraction(references.min_amount_units, metric.amount_units) * hundred
    completion = (
        Fraction(references.min_completion_ms) / Fraction(metric.completion_ms)
    ) * hundred
    intelligence = (
        Fraction(metric.intelligence_index) / Fraction(references.max_intelligence_index)
    ) * hundred
    total = (
        price * weights.price
        + completion * weights.completion_time
        + intelligence * weights.intelligence
    ) / hundred
    return CandidateScore(
        key=metric.key,
        price=price,
        completion_time=completion,
        intelligence=intelligence,
        total=total,
    )


def rank_key(
    metric: CandidateMetrics, score: CandidateScore
) -> tuple[Fraction, int, Fraction, str, str]:
    """Deterministic order: score desc, then amount, completion time, provider, model."""
    return (
        -score.total,
        metric.amount_units,
        Fraction(metric.completion_ms),
        metric.provider_id,
        metric.model_id,
    )


def rank_candidates(
    metrics: Sequence[CandidateMetrics],
    *,
    weights: PolicyWeights,
) -> tuple[ScoreReferences, tuple[tuple[CandidateMetrics, CandidateScore], ...]]:
    """Score every eligible candidate and order it by the fixed tie-break rule."""
    references = score_references(metrics)
    scored = [
        (metric, score_candidate(metric, references=references, weights=weights))
        for metric in metrics
    ]
    scored.sort(key=lambda pair: rank_key(pair[0], pair[1]))
    return references, tuple(scored)


class FilterReason(StrEnum):
    """Hard-filter rejection codes.

    A rejection means a numerically sound candidate does not fit this request. It is not
    the same thing as an abort: a missing or invalid AA number stops the whole purchase
    instead of quietly removing one candidate.
    """

    OVER_BUDGET = "over_budget"
    MISSING_CAPABILITY = "missing_capability"
    COMPLETION_TIME_LIMIT = "completion_time_limit"
    PROVIDER_NOT_ALLOWED = "provider_not_allowed"


def rejection_reasons(
    *,
    provider_id: str,
    amount_units: int,
    budget_units: int,
    estimated_completion_ms: Decimal,
    max_completion_ms: int | None,
    capabilities: Iterable[str],
    required_capabilities: Iterable[str],
    allowed_providers: Iterable[str],
) -> tuple[str, ...]:
    """Every reason this candidate fails, in a fixed order, evaluated after ceiling.

    Budget is compared against the rounded-up `amountUnits`, the time limit against the
    converted milliseconds, and capabilities against the explicit catalog configuration -
    never against an AA measurement.
    """
    reasons: list[str] = []
    if amount_units > budget_units:
        reasons.append(FilterReason.OVER_BUDGET.value)
    available = {str(item).strip().lower() for item in capabilities}
    required = {str(item).strip().lower() for item in required_capabilities}
    if not required.issubset(available):
        reasons.append(FilterReason.MISSING_CAPABILITY.value)
    if max_completion_ms is not None and estimated_completion_ms > max_completion_ms:
        reasons.append(FilterReason.COMPLETION_TIME_LIMIT.value)
    allowed = {str(item).strip().lower() for item in allowed_providers}
    if allowed and provider_id.strip().lower() not in allowed:
        reasons.append(FilterReason.PROVIDER_NOT_ALLOWED.value)
    return tuple(reasons)
