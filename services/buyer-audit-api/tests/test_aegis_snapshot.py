from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from buyer_audit_api.adapters.artificial_analysis import (
    FixtureArtificialAnalysisSource,
    HttpArtificialAnalysisSource,
)
from buyer_audit_api.core.documents import verify_immutable_document
from buyer_audit_api.core.errors import ConfigurationError, ExternalEvidenceError
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_CATALOG_PATH,
    FIXTURE_PAGE_PATHS,
    AaFieldPaths,
    build_aa_snapshot,
    load_model_catalog,
    parse_aa_page,
    validate_capture,
)
from buyer_audit_api.domains.ai_inference.aa_models import (
    AA_SOURCE_URL,
    AaSnapshot,
    AaSourceMode,
    MappingProvenance,
    ModelCatalog,
)

FETCHED_AT = datetime(2026, 9, 9, 3, 0, tzinfo=UTC)


@pytest.fixture
def catalog() -> ModelCatalog:
    return load_model_catalog(FIXTURE_CATALOG_PATH)


@pytest.fixture
def pages() -> list[str]:
    return [path.read_text(encoding="utf-8") for path in FIXTURE_PAGE_PATHS]


def _rewrite(page: str, mutate: Any) -> str:
    """Re-serialize one page after a mutation; decimals stay decimal text."""
    document = parse_aa_page(page)
    mutate(document)
    return json.dumps(document, default=str)


def test_fixture_catalog_is_explicitly_marked_as_fixture_mapping(
    catalog: ModelCatalog,
) -> None:
    assert catalog.provenance == {MappingProvenance.FIXTURE}
    assert {entry.provider_id for entry in catalog.entries} == {
        "openai",
        "anthropic",
        "google",
    }
    # A fixture AA id must never be mistakable for a queried live Artificial Analysis id.
    assert all(entry.aa_model_id.startswith("fixture-aa-") for entry in catalog.entries)


def test_catalog_rejects_two_provider_models_sharing_one_aa_id(
    catalog: ModelCatalog,
) -> None:
    duplicated = catalog.model_dump(mode="python")
    duplicated["entries"][1]["aa_model_id"] = duplicated["entries"][0]["aa_model_id"]
    with pytest.raises(ValueError, match="one AA model id"):
        ModelCatalog.model_validate(duplicated)


def test_capture_collects_every_page_and_pins_one_index_version(
    pages: list[str],
) -> None:
    capture = validate_capture(pages, field_paths=AaFieldPaths())
    assert capture.page_count == 2
    assert capture.intelligence_index_version == Decimal("3")
    assert len(capture.models) == 4
    assert capture.raw_response_hash.startswith("sha256:")
    assert capture.raw_pages == tuple(pages)


def test_capture_hash_binds_the_captured_bytes(pages: list[str]) -> None:
    original = validate_capture(pages, field_paths=AaFieldPaths())
    mutated = [pages[0].replace("0.15", "9.99"), pages[1]]
    assert (
        validate_capture(mutated, field_paths=AaFieldPaths()).raw_response_hash
        != original.raw_response_hash
    )


def test_capture_rejects_a_repeated_page(pages: list[str]) -> None:
    with pytest.raises(ExternalEvidenceError, match="out of order"):
        validate_capture([pages[0], pages[0]], field_paths=AaFieldPaths())


def test_capture_rejects_a_truncated_collection(pages: list[str]) -> None:
    with pytest.raises(ExternalEvidenceError, match="has_more"):
        validate_capture([pages[0]], field_paths=AaFieldPaths())


def test_capture_rejects_a_total_pages_disagreement(pages: list[str]) -> None:
    def bump(document: Any) -> None:
        document["pagination"]["total_pages"] = 3

    mutated = [pages[0], _rewrite(pages[1], bump)]
    with pytest.raises(ExternalEvidenceError, match="total_pages"):
        validate_capture(mutated, field_paths=AaFieldPaths())


def test_capture_rejects_mixed_intelligence_index_versions(
    pages: list[str],
) -> None:
    def bump(document: Any) -> None:
        document["intelligence_index_version"] = 4

    mutated = [pages[0], _rewrite(pages[1], bump)]
    with pytest.raises(ExternalEvidenceError, match="intelligence_index_version"):
        validate_capture(mutated, field_paths=AaFieldPaths())


def test_capture_rejects_a_duplicate_model_id(pages: list[str]) -> None:
    def collide(document: Any) -> None:
        document["data"][0]["id"] = "fixture-aa-openai-alpha"

    mutated = [pages[0], _rewrite(pages[1], collide)]
    with pytest.raises(ExternalEvidenceError, match="repeats model id"):
        validate_capture(mutated, field_paths=AaFieldPaths())


def test_aa_numbers_are_parsed_as_decimals_not_binary_floats() -> None:
    payload = parse_aa_page('{"pricing": {"price_1m_input_tokens": 0.1}}')
    assert payload["pricing"]["price_1m_input_tokens"] == Decimal("0.1")
    assert not isinstance(
        payload["pricing"]["price_1m_input_tokens"], float
    )


def test_snapshot_maps_every_catalog_model_exactly_once(
    catalog: ModelCatalog, pages: list[str]
) -> None:
    capture = validate_capture(pages, field_paths=AaFieldPaths())
    snapshot, document = build_aa_snapshot(
        purchase_id="purchase-1",
        catalog=catalog,
        capture=capture,
        mode=AaSourceMode.FIXTURE,
        fetched_at=FETCHED_AT,
        field_paths=AaFieldPaths(),
    )
    assert [model.candidate_key for model in snapshot.models] == [
        entry.key for entry in catalog.entries
    ]
    alpha = snapshot.metrics("openai:fixture-openai-alpha:fixture-2026-09-09")
    assert alpha.input_price_per_million == Decimal("0.15")
    assert alpha.output_price_per_million == Decimal("0.6")
    assert alpha.median_end_to_end_seconds == Decimal("8.4")
    assert alpha.intelligence_index == Decimal("41.5")
    # The unmapped AA model is counted in the capture but never becomes a candidate.
    assert snapshot.total_model_count == 4
    assert snapshot.snapshot_id == document.document_id
    # The received bytes stay with the snapshot, so the hash can be rechecked later.
    assert snapshot.raw_pages == tuple(pages)
    verify_immutable_document(document)


def test_snapshot_id_is_the_content_hash_of_its_own_body(
    catalog: ModelCatalog, pages: list[str]
) -> None:
    capture = validate_capture(pages, field_paths=AaFieldPaths())
    first, first_document = build_aa_snapshot(
        purchase_id="purchase-1",
        catalog=catalog,
        capture=capture,
        mode=AaSourceMode.FIXTURE,
        fetched_at=FETCHED_AT,
        field_paths=AaFieldPaths(),
    )
    same, _ = build_aa_snapshot(
        purchase_id="purchase-1",
        catalog=catalog,
        capture=capture,
        mode=AaSourceMode.FIXTURE,
        fetched_at=FETCHED_AT,
        field_paths=AaFieldPaths(),
    )
    other, _ = build_aa_snapshot(
        purchase_id="purchase-2",
        catalog=catalog,
        capture=capture,
        mode=AaSourceMode.FIXTURE,
        fetched_at=FETCHED_AT,
        field_paths=AaFieldPaths(),
    )
    assert same.snapshot_id == first.snapshot_id
    assert other.snapshot_id != first.snapshot_id
    assert AaSnapshot.from_payload(first.to_payload()) == first
    assert first_document.payload == first.body_payload()


def test_missing_catalog_model_aborts_before_any_pricing(
    catalog: ModelCatalog, pages: list[str]
) -> None:
    def drop_gamma(document: Any) -> None:
        document["data"] = [document["data"][1]]

    mutated = [pages[0], _rewrite(pages[1], drop_gamma)]
    capture = validate_capture(mutated, field_paths=AaFieldPaths())
    with pytest.raises(ExternalEvidenceError, match="fixture-aa-google-gamma"):
        build_aa_snapshot(
            purchase_id="purchase-1",
            catalog=catalog,
            capture=capture,
            mode=AaSourceMode.FIXTURE,
            fetched_at=FETCHED_AT,
            field_paths=AaFieldPaths(),
        )


def test_slug_disagreement_is_not_resolved_by_similarity(
    catalog: ModelCatalog, pages: list[str]
) -> None:
    def rename(document: Any) -> None:
        document["data"][0]["slug"] = "fixture-aa-openai-alpha-v2"

    mutated = [_rewrite(pages[0], rename), pages[1]]
    capture = validate_capture(mutated, field_paths=AaFieldPaths())
    with pytest.raises(ExternalEvidenceError, match="does not match the configured"):
        build_aa_snapshot(
            purchase_id="purchase-1",
            catalog=catalog,
            capture=capture,
            mode=AaSourceMode.FIXTURE,
            fetched_at=FETCHED_AT,
            field_paths=AaFieldPaths(),
        )


@pytest.mark.parametrize(
    "path",
    [
        "pricing.price_1m_input_tokens",
        "performance.median_end_to_end_response_time_seconds",
        "evaluations.artificial_analysis_intelligence_index",
    ],
)
def test_a_null_measurement_aborts_and_is_never_read_as_zero(
    catalog: ModelCatalog, pages: list[str], path: str
) -> None:
    def blank(document: Any) -> None:
        target: Any = document["data"][0]
        segments = path.split(".")
        for segment in segments[:-1]:
            target = target[segment]
        target[segments[-1]] = None

    mutated = [_rewrite(pages[0], blank), pages[1]]
    capture = validate_capture(mutated, field_paths=AaFieldPaths())
    with pytest.raises(ExternalEvidenceError, match="null"):
        build_aa_snapshot(
            purchase_id="purchase-1",
            catalog=catalog,
            capture=capture,
            mode=AaSourceMode.FIXTURE,
            fetched_at=FETCHED_AT,
            field_paths=AaFieldPaths(),
        )


@pytest.mark.parametrize("value", ["0", "-3"])
def test_non_positive_time_or_intelligence_aborts(
    catalog: ModelCatalog, pages: list[str], value: str
) -> None:
    def zero(document: Any) -> None:
        document["data"][0]["performance"][
            "median_end_to_end_response_time_seconds"
        ] = value

    mutated = [_rewrite(pages[0], zero), pages[1]]
    capture = validate_capture(mutated, field_paths=AaFieldPaths())
    with pytest.raises(ExternalEvidenceError):
        build_aa_snapshot(
            purchase_id="purchase-1",
            catalog=catalog,
            capture=capture,
            mode=AaSourceMode.FIXTURE,
            fetched_at=FETCHED_AT,
            field_paths=AaFieldPaths(),
        )


def test_completion_time_is_read_from_the_documented_nested_path() -> None:
    assert (
        AaFieldPaths().median_end_to_end_seconds
        == "performance.median_end_to_end_response_time_seconds"
    )


def test_a_root_level_completion_time_is_not_silently_accepted(
    catalog: ModelCatalog, pages: list[str]
) -> None:
    def flatten(document: Any) -> None:
        entry = document["data"][0]
        entry["median_end_to_end_seconds"] = entry.pop("performance")[
            "median_end_to_end_response_time_seconds"
        ]

    mutated = [_rewrite(pages[0], flatten), pages[1]]
    capture = validate_capture(mutated, field_paths=AaFieldPaths())
    with pytest.raises(
        ExternalEvidenceError,
        match="performance.median_end_to_end_response_time_seconds is null",
    ):
        build_aa_snapshot(
            purchase_id="purchase-1",
            catalog=catalog,
            capture=capture,
            mode=AaSourceMode.FIXTURE,
            fetched_at=FETCHED_AT,
            field_paths=AaFieldPaths(),
        )


def test_fixture_numbers_can_never_be_stored_as_a_live_capture(
    catalog: ModelCatalog, pages: list[str]
) -> None:
    capture = validate_capture(pages, field_paths=AaFieldPaths())
    with pytest.raises(ExternalEvidenceError, match="explicitly configured catalog"):
        build_aa_snapshot(
            purchase_id="purchase-1",
            catalog=catalog,
            capture=capture,
            mode=AaSourceMode.LIVE,
            fetched_at=FETCHED_AT,
            field_paths=AaFieldPaths(),
        )


def test_a_configured_catalog_is_not_read_through_fixture_pages(
    catalog: ModelCatalog, pages: list[str]
) -> None:
    configured = ModelCatalog.model_validate(
        {
            "catalog_version": catalog.catalog_version,
            "entries": [
                {**entry.model_dump(mode="python"), "mapping_provenance": "configured"}
                for entry in catalog.entries
            ],
        }
    )
    capture = validate_capture(pages, field_paths=AaFieldPaths())
    with pytest.raises(ExternalEvidenceError, match="live-mapped catalog"):
        build_aa_snapshot(
            purchase_id="purchase-1",
            catalog=configured,
            capture=capture,
            mode=AaSourceMode.FIXTURE,
            fetched_at=FETCHED_AT,
            field_paths=AaFieldPaths(),
        )


def test_field_path_overrides_must_name_a_known_field() -> None:
    assert AaFieldPaths.from_mapping({"slug": "identifier"}).slug == "identifier"
    with pytest.raises(ExternalEvidenceError, match="unknown AA field path"):
        AaFieldPaths.from_mapping({"nope": "x"})


async def test_fixture_source_reports_fixture_mode_and_reads_every_page() -> None:
    source = FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS)
    assert source.mode is AaSourceMode.FIXTURE
    assert len(await source.fetch_pages()) == 2
    with pytest.raises(ConfigurationError):
        FixtureArtificialAnalysisSource(page_paths=())


def test_live_adapter_sends_only_the_documented_query_and_never_leaks_the_key() -> None:
    """The official Free endpoint documents `page`; `page_size` is a response field."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(
            200, text=FIXTURE_PAGE_PATHS[page - 1].read_text(encoding="utf-8")
        )

    async def run() -> tuple[str, ...]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            source = HttpArtificialAnalysisSource(api_key="server-only-key", client=client)
            assert source.mode is AaSourceMode.LIVE
            return await source.fetch_pages()

    pages = asyncio.run(run())

    assert len(pages) == 2
    assert [dict(request.url.params) for request in seen] == [
        {"page": "1"},
        {"page": "2"},
    ]
    for request in seen:
        assert "page_size" not in request.url.params
        assert request.headers["x-api-key"] == "server-only-key"
        assert str(request.url).startswith(AA_SOURCE_URL)
    # The captured bodies are the received bytes, so the recorded hash describes them.
    capture = validate_capture(pages, field_paths=AaFieldPaths())
    assert capture.raw_pages == pages


def test_live_adapter_stops_on_a_non_success_response_without_echoing_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            source = HttpArtificialAnalysisSource(api_key="server-only-key", client=client)
            await source.fetch_pages()

    with pytest.raises(ExternalEvidenceError) as error:
        asyncio.run(run())
    assert "429" in str(error.value)
    assert "server-only-key" not in str(error.value)


def test_a_live_source_requires_a_server_key() -> None:
    with pytest.raises(ConfigurationError):
        HttpArtificialAnalysisSource(api_key="   ")


def test_a_fractional_index_version_is_preserved_not_truncated(
    catalog: ModelCatalog, pages: list[str]
) -> None:
    """The official example carries values such as 4.1; storing 4 would misstate it."""

    def bump(document: Any) -> None:
        document["intelligence_index_version"] = "4.1"

    mutated = [_rewrite(page, bump) for page in pages]
    capture = validate_capture(mutated, field_paths=AaFieldPaths())
    assert capture.intelligence_index_version == Decimal("4.1")
    snapshot, _ = build_aa_snapshot(
        purchase_id="purchase-1",
        catalog=catalog,
        capture=capture,
        mode=AaSourceMode.FIXTURE,
        fetched_at=FETCHED_AT,
        field_paths=AaFieldPaths(),
    )
    assert snapshot.to_payload()["intelligenceIndexVersion"] == "4.1"


def test_a_creator_object_without_a_slug_is_accepted(
    catalog: ModelCatalog, pages: list[str]
) -> None:
    """The official creator example carries id and name; a creator slug is not required."""

    def trim(document: Any) -> None:
        for entry in document["data"]:
            entry["model_creator"] = {
                "id": entry["model_creator"]["id"],
                "name": entry["model_creator"]["name"],
            }

    mutated = [_rewrite(page, trim) for page in pages]
    snapshot, _ = build_aa_snapshot(
        purchase_id="purchase-1",
        catalog=catalog,
        capture=validate_capture(mutated, field_paths=AaFieldPaths()),
        mode=AaSourceMode.FIXTURE,
        fetched_at=FETCHED_AT,
        field_paths=AaFieldPaths(),
    )
    assert snapshot.models[0].model_creator == "Fixture OpenAI"
