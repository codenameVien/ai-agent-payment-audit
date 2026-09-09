from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from buyer_audit_api.core.aa_policy import (
    MAX_UINT256,
    PRIORITY_WEIGHTS,
    CandidateMetrics,
    PolicyNumberError,
    PriorityReason,
    RequestPriority,
    amount_units,
    classify_priority,
    completion_ms_from_seconds,
    decimal_text,
    estimate_tokens,
    fraction_text,
    parse_priority,
    rank_candidates,
    rejection_reasons,
    score_references,
)


def test_weight_table_matches_the_confirmed_ratios() -> None:
    assert {
        priority.value: (
            weights.price,
            weights.completion_time,
            weights.intelligence,
        )
        for priority, weights in PRIORITY_WEIGHTS.items()
    } == {
        "default": (40, 30, 30),
        "price": (60, 20, 20),
        "speed": (20, 60, 20),
        "intelligence": (20, 20, 60),
    }


@pytest.mark.parametrize("legacy", ["balanced", "quality", "performance"])
def test_superseded_priority_names_are_rejected_with_the_new_names(legacy: str) -> None:
    with pytest.raises(PolicyNumberError) as error:
        parse_priority(legacy)
    message = str(error.value)
    assert legacy in message
    for allowed in ("default", "price", "speed", "intelligence"):
        assert allowed in message


def test_explicit_default_skips_keyword_classification() -> None:
    classification = classify_priority(prompt="최대한 싸게 해줘", explicit="default")
    assert classification.effective is RequestPriority.DEFAULT
    assert classification.original is RequestPriority.DEFAULT
    assert classification.reason is PriorityReason.EXPLICIT
    assert classification.matched_keywords == ()


def test_single_keyword_group_classifies_and_records_its_evidence() -> None:
    classification = classify_priority(prompt="이거 최대한 싸게 처리해줘")
    assert classification.effective is RequestPriority.PRICE
    assert classification.original is None
    assert classification.reason is PriorityReason.KEYWORD_MATCH
    assert classification.matched_keywords == ("싸게",)


def test_conflicting_keyword_groups_fall_back_to_default() -> None:
    classification = classify_priority(prompt="아주 빨리 그리고 정확하게 해줘")
    assert classification.effective is RequestPriority.DEFAULT
    assert classification.reason is PriorityReason.CONFLICTING_KEYWORD_MATCH
    assert classification.matched_priorities == (
        RequestPriority.SPEED,
        RequestPriority.INTELLIGENCE,
    )


def test_no_keyword_match_falls_back_to_default() -> None:
    classification = classify_priority(prompt="이 문서를 요약해줘")
    assert classification.effective is RequestPriority.DEFAULT
    assert classification.reason is PriorityReason.NO_KEYWORD_MATCH
    assert classification.matched_keywords == ()


def test_token_estimate_counts_every_byte_handed_to_the_model() -> None:
    estimate = estimate_tokens(
        model_input_parts=("system", "가"),
        max_output_tokens=512,
    )
    # 6 ASCII bytes + 3 UTF-8 bytes = 9 bytes, ceil(9/4) = 3.
    assert (estimate.input_bytes, estimate.input_tokens) == (9, 3)
    assert estimate.to_payload()["estimationMethod"] == "utf8-bytes-div4-v1"


def test_token_estimate_never_drops_below_one_token() -> None:
    assert estimate_tokens(model_input_parts=("",), max_output_tokens=1).input_tokens == 1


def test_amount_units_rounds_up_to_the_next_whole_unit() -> None:
    # 1 * 0.000001 + 1 * 0.000001 = 0.000002 USD -> exactly 2 units, no rounding needed.
    assert (
        amount_units(
            input_tokens=1,
            max_output_tokens=1,
            input_price_per_million=Decimal("1"),
            output_price_per_million=Decimal("1"),
        )
        == 2
    )
    # A fractional unit always rounds up, never down or to nearest.
    assert (
        amount_units(
            input_tokens=1,
            max_output_tokens=1,
            input_price_per_million=Decimal("0.4"),
            output_price_per_million=Decimal("0.4"),
        )
        == 1
    )


def test_amount_units_uses_exact_decimal_math_not_binary_floats() -> None:
    # 3 * 0.1 is 0.30000000000000004 in binary floating point, which would round up to 1.
    assert (
        amount_units(
            input_tokens=3,
            max_output_tokens=1,
            input_price_per_million=Decimal("0.1"),
            output_price_per_million=Decimal("0"),
        )
        == 1
    )
    assert (
        amount_units(
            input_tokens=1000,
            max_output_tokens=500,
            input_price_per_million=Decimal("0.15"),
            output_price_per_million=Decimal("0.60"),
        )
        == 450
    )


def test_zero_unit_prices_produce_a_zero_amount_for_the_caller_to_refuse() -> None:
    assert (
        amount_units(
            input_tokens=10,
            max_output_tokens=10,
            input_price_per_million=Decimal("0"),
            output_price_per_million=Decimal("0"),
        )
        == 0
    )


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), None])
def test_non_finite_or_negative_prices_are_refused(bad: object) -> None:
    with pytest.raises(PolicyNumberError):
        amount_units(
            input_tokens=1,
            max_output_tokens=1,
            input_price_per_million=bad,  # type: ignore[arg-type]
            output_price_per_million=Decimal("1"),
        )


def test_amount_units_refuses_to_leave_the_uint256_range() -> None:
    with pytest.raises(PolicyNumberError):
        amount_units(
            input_tokens=1,
            max_output_tokens=1,
            input_price_per_million=Decimal(MAX_UINT256),
            output_price_per_million=Decimal(MAX_UINT256),
        )


def test_completion_ms_conversion_keeps_sub_second_precision() -> None:
    assert completion_ms_from_seconds(Decimal("4.75")) == Decimal("4750.00")
    with pytest.raises(PolicyNumberError):
        completion_ms_from_seconds(Decimal("0"))


def test_decimal_text_preserves_every_published_digit() -> None:
    """A price longer than the default 28-digit context must not be shortened."""
    value = Decimal("1.0000000000000000000000000001")
    assert decimal_text(value) == "1.0000000000000000000000000001"
    assert Decimal(decimal_text(value)) == value
    # The same value must still price to 2 units, not to 1.
    assert (
        amount_units(
            input_tokens=1,
            max_output_tokens=1,
            input_price_per_million=value,
            output_price_per_million=Decimal("1"),
        )
        == 3
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.000000", "0"),
        ("-0", "0"),
        ("1E+3", "1000"),
        ("4.750", "4.75"),
        ("0.1", "0.1"),
    ],
)
def test_decimal_text_is_plain_and_stable(value: str, expected: str) -> None:
    assert decimal_text(Decimal(value)) == expected


def test_completion_ms_conversion_is_an_exact_exponent_shift() -> None:
    seconds = Decimal("1.0000000000000000000000000001")
    assert decimal_text(completion_ms_from_seconds(seconds)) == (
        "1000.0000000000000000000000001"
    )


def test_hard_filters_report_every_reason_in_a_fixed_order() -> None:
    assert rejection_reasons(
        provider_id="openai",
        amount_units=1_000,
        budget_units=500,
        estimated_completion_ms=Decimal("9000"),
        max_completion_ms=5_000,
        capabilities=("korean",),
        required_capabilities=("korean", "vision"),
        allowed_providers=("google",),
    ) == (
        "over_budget",
        "missing_capability",
        "completion_time_limit",
        "provider_not_allowed",
    )


def test_budget_is_compared_against_the_rounded_up_amount() -> None:
    assert rejection_reasons(
        provider_id="openai",
        amount_units=501,
        budget_units=500,
        estimated_completion_ms=Decimal("10"),
        max_completion_ms=None,
        capabilities=(),
        required_capabilities=(),
        allowed_providers=(),
    ) == ("over_budget",)
    assert (
        rejection_reasons(
            provider_id="openai",
            amount_units=500,
            budget_units=500,
            estimated_completion_ms=Decimal("10"),
            max_completion_ms=None,
            capabilities=(),
            required_capabilities=(),
            allowed_providers=(),
        )
        == ()
    )


def _metric(
    key: str,
    *,
    provider: str,
    model: str,
    units: int,
    ms: str,
    index: str,
) -> CandidateMetrics:
    return CandidateMetrics(
        key=key,
        provider_id=provider,
        model_id=model,
        amount_units=units,
        completion_ms=Decimal(ms),
        intelligence_index=Decimal(index),
    )


def test_normalization_uses_only_the_candidates_that_survived_the_filters() -> None:
    eligible = [
        _metric("a", provider="openai", model="a", units=200, ms="2000", index="40"),
        _metric("b", provider="google", model="b", units=400, ms="4000", index="80"),
    ]
    references = score_references(eligible)
    assert references.min_amount_units == 200
    assert references.min_completion_ms == Decimal("2000")
    assert references.max_intelligence_index == Decimal("80")


def test_three_factor_scores_and_weighted_total_are_exact() -> None:
    metrics = [
        _metric("a", provider="openai", model="a", units=200, ms="2000", index="40"),
        _metric("b", provider="google", model="b", units=400, ms="4000", index="80"),
    ]
    _, ranked = rank_candidates(metrics, weights=PRIORITY_WEIGHTS[RequestPriority.DEFAULT])
    scores = {metric.key: score for metric, score in ranked}
    assert scores["a"].price == Fraction(100)
    assert scores["a"].completion_time == Fraction(100)
    assert scores["a"].intelligence == Fraction(50)
    # 0.4*100 + 0.3*100 + 0.3*50 = 85
    assert scores["a"].total == Fraction(85)
    # 0.4*50 + 0.3*50 + 0.3*100 = 65
    assert scores["b"].total == Fraction(65)
    assert [metric.key for metric, _ in ranked] == ["a", "b"]


def test_priority_changes_the_winner_without_changing_the_inputs() -> None:
    metrics = [
        _metric("cheap", provider="openai", model="cheap", units=100, ms="9000", index="40"),
        _metric("fast", provider="google", model="fast", units=900, ms="1000", index="41"),
    ]
    price_first = rank_candidates(metrics, weights=PRIORITY_WEIGHTS[RequestPriority.PRICE])[1]
    speed_first = rank_candidates(metrics, weights=PRIORITY_WEIGHTS[RequestPriority.SPEED])[1]
    assert price_first[0][0].key == "cheap"
    assert speed_first[0][0].key == "fast"


def test_ties_break_on_amount_then_time_then_provider_then_model() -> None:
    # Identical price, time and index give three exactly equal totals.
    metrics = [
        _metric("z", provider="zeta", model="m", units=100, ms="1000", index="50"),
        _metric("a2", provider="alpha", model="m2", units=100, ms="1000", index="50"),
        _metric("a1", provider="alpha", model="m1", units=100, ms="1000", index="50"),
    ]
    _, ranked = rank_candidates(metrics, weights=PRIORITY_WEIGHTS[RequestPriority.DEFAULT])
    totals = {score.total for _, score in ranked}
    assert len(totals) == 1
    assert [metric.key for metric, _ in ranked] == ["a1", "a2", "z"]


def test_cheaper_candidate_wins_an_equal_total_before_provider_name() -> None:
    metrics = [
        _metric("dear", provider="aaa", model="m", units=200, ms="1000", index="100"),
        _metric("cheap", provider="zzz", model="m", units=100, ms="4000", index="25"),
    ]
    _, ranked = rank_candidates(metrics, weights=PRIORITY_WEIGHTS[RequestPriority.PRICE])
    # 0.6*100 + 0.2*25 + 0.2*25 == 0.6*50 + 0.2*100 + 0.2*100 == 70.
    assert {score.total for _, score in ranked} == {Fraction(70)}
    # The lower amount wins the tie even though its provider sorts last.
    assert [metric.key for metric, _ in ranked] == ["cheap", "dear"]


def test_stored_score_text_is_plain_decimal_with_documented_precision() -> None:
    assert fraction_text(Fraction(100)) == "100"
    assert fraction_text(Fraction(1, 3)) == "0.333333333333333333"
    assert fraction_text(Fraction(-1, 8)) == "-0.125"


@pytest.mark.parametrize("units", [0, -1])
def test_scored_candidates_must_carry_a_positive_amount(units: int) -> None:
    with pytest.raises(PolicyNumberError):
        _metric("a", provider="openai", model="a", units=units, ms="1000", index="10")
