"""Artificial Analysis response validation and immutable snapshot assembly.

Official contract: `GET https://artificialanalysis.ai/api/v2/language/models/free` with a
server-side `x-api-key`, a numeric root `intelligence_index_version`, a `data` array and
`page`/`page_size`/`total_pages`/`has_more` pagination
(https://artificialanalysis.ai/data-api/docs).

The per-model field paths below follow the documented v2 contract in
`docs/AA_API_CONTRACT_CHECK.md`; the completion time is
`performance.median_end_to_end_response_time_seconds`. They stay configurable for a
future contract revision, but there is no implicit fallback: a path that does not
resolve aborts the purchase with the exact path in the message instead of guessing a
value or substituting a default score.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from buyer_audit_api.core.aa_policy import PolicyNumberError, require_finite_decimal
from buyer_audit_api.core.documents import build_immutable_document, raw_parts_hash
from buyer_audit_api.core.errors import ConfigurationError, ExternalEvidenceError
from buyer_audit_api.core.models import ImmutableDocument, JsonObject
from buyer_audit_api.domains.ai_inference.aa_models import (
    AA_SNAPSHOT_DOCUMENT_KIND,
    AA_SOURCE_URL,
    AaModelMetrics,
    AaSnapshot,
    AaSourceMode,
    CatalogEntry,
    MappingProvenance,
    ModelCatalog,
)

DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 50

#: Shipped fixture data, shared with the cross-language schema checks.
REPO_FIXTURE_DIR = (
    Path(__file__).resolve().parents[6] / "packages" / "schemas" / "fixtures" / "aa"
)
FIXTURE_CATALOG_PATH = REPO_FIXTURE_DIR / "model-catalog.fixture.json"
FIXTURE_PAGE_PATHS = (
    REPO_FIXTURE_DIR / "free-models-page-1.json",
    REPO_FIXTURE_DIR / "free-models-page-2.json",
)


def parse_model_catalog(payload: Mapping[str, object]) -> ModelCatalog:
    """Validate an explicit catalog document; every mapping pair must be spelled out."""
    try:
        return ModelCatalog.model_validate(dict(payload))
    except ValueError as exc:
        raise ConfigurationError(f"model catalog is invalid: {exc}") from exc


def load_model_catalog(path: Path) -> ModelCatalog:
    if not path.is_file():
        raise ConfigurationError(f"model catalog file is missing: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"model catalog file is not valid JSON: {path}") from exc
    if not isinstance(document, dict):
        raise ConfigurationError(f"model catalog file must be an object: {path}")
    return parse_model_catalog(document)


@dataclasses.dataclass(frozen=True, slots=True)
class AaFieldPaths:
    """Dotted paths into one AA model object. Overridable configuration, not a guess."""

    model_id: str = "id"
    name: str = "name"
    slug: str = "slug"
    creator: str = "model_creator"
    input_price_per_million: str = "pricing.price_1m_input_tokens"
    output_price_per_million: str = "pricing.price_1m_output_tokens"
    median_end_to_end_seconds: str = (
        "performance.median_end_to_end_response_time_seconds"
    )
    intelligence_index: str = "evaluations.artificial_analysis_intelligence_index"

    @staticmethod
    def from_mapping(overrides: Mapping[str, object] | None) -> AaFieldPaths:
        if not overrides:
            return AaFieldPaths()
        known = {field.name for field in dataclasses.fields(AaFieldPaths)}
        unknown = sorted(set(overrides) - known)
        if unknown:
            raise ExternalEvidenceError(
                f"unknown AA field path override(s): {', '.join(unknown)}"
            )
        values: dict[str, str] = {}
        for key, value in overrides.items():
            if not isinstance(value, str) or not value.strip():
                raise ExternalEvidenceError(f"AA field path {key} must be a non-empty string")
            values[key] = value.strip()
        return dataclasses.replace(AaFieldPaths(), **values)

    def to_payload(self) -> JsonObject:
        return {
            field.name: getattr(self, field.name)
            for field in dataclasses.fields(AaFieldPaths)
        }


@dataclasses.dataclass(frozen=True, slots=True)
class AaRawCapture:
    """Every page exactly as received, in order, with the facts they agree on."""

    raw_pages: tuple[str, ...]
    pages: tuple[JsonObject, ...]
    intelligence_index_version: Decimal
    models: tuple[JsonObject, ...]
    raw_response_hash: str

    @property
    def page_count(self) -> int:
        return len(self.raw_pages)


#: The stored snapshot keeps the raw page bodies, so this hash stays checkable later.
raw_response_hash = raw_parts_hash


def parse_aa_page(body: str) -> JsonObject:
    """Parse one page without letting a binary float touch a published price."""
    try:
        payload = json.loads(body, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ExternalEvidenceError("AA response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ExternalEvidenceError("AA response is not a JSON object")
    return payload


def _resolve_path(model: Mapping[str, object], path: str) -> object:
    current: object = model
    for segment in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _numeric(value: object, *, name: str, allow_zero: bool) -> Decimal:
    """Accept only a finite JSON number; `null` means unmeasured, never zero."""
    if value is None:
        raise ExternalEvidenceError(f"AA field {name} is null (unmeasured), not zero")
    if isinstance(value, bool) or not isinstance(value, Decimal | int | str):
        raise ExternalEvidenceError(f"AA field {name} is not a number: {value!r}")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ExternalEvidenceError(f"AA field {name} is not a decimal: {value!r}") from exc
    try:
        return require_finite_decimal(number, name=f"AA field {name}", allow_zero=allow_zero)
    except PolicyNumberError as exc:
        # Every captured-evidence problem surfaces as one abort type for the caller.
        raise ExternalEvidenceError(str(exc)) from exc


def _creator_name(value: object, *, aa_model_id: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("name", "slug", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    raise ExternalEvidenceError(f"AA model {aa_model_id} has no usable model_creator")


def validate_capture(
    raw_pages: Sequence[str],
    *,
    field_paths: AaFieldPaths,
) -> AaRawCapture:
    """Check pagination and index-version consistency before any model is read.

    The input is the received page bodies, so the recorded hash covers the bytes rather
    than a re-serialization of them.
    """
    if not raw_pages:
        raise ExternalEvidenceError("AA response has no pages")
    if len(raw_pages) > MAX_PAGES:
        raise ExternalEvidenceError("AA pagination exceeded the bounded page limit")
    pages = [parse_aa_page(body) for body in raw_pages]
    ordered: list[JsonObject] = []
    models: list[JsonObject] = []
    seen_pages: set[int] = set()
    seen_model_ids: set[str] = set()
    index_version: Decimal | None = None
    declared_total_pages: int | None = None
    for position, page in enumerate(pages, start=1):
        if not isinstance(page, Mapping):
            raise ExternalEvidenceError("AA page is not an object")
        version = _numeric(
            page.get("intelligence_index_version"),
            name="intelligence_index_version",
            allow_zero=False,
        )
        if index_version is None:
            index_version = version
        elif version != index_version:
            raise ExternalEvidenceError(
                "AA pages mix intelligence_index_version values; "
                f"{index_version} then {version}"
            )
        pagination = page.get("pagination")
        if not isinstance(pagination, Mapping):
            raise ExternalEvidenceError("AA page has no pagination object")
        page_number = pagination.get("page")
        if isinstance(page_number, bool) or not isinstance(page_number, int):
            raise ExternalEvidenceError("AA pagination page must be an integer")
        if page_number != position:
            raise ExternalEvidenceError(
                f"AA pagination is out of order: expected page {position}, got {page_number}"
            )
        if page_number in seen_pages:
            raise ExternalEvidenceError(f"AA pagination repeated page {page_number}")
        seen_pages.add(page_number)
        page_size = pagination.get("page_size")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size <= 0:
            raise ExternalEvidenceError("AA pagination page_size must be a positive integer")
        total_pages = pagination.get("total_pages")
        if isinstance(total_pages, bool) or not isinstance(total_pages, int) or total_pages <= 0:
            raise ExternalEvidenceError("AA pagination total_pages must be a positive integer")
        if declared_total_pages is None:
            declared_total_pages = total_pages
        elif total_pages != declared_total_pages:
            raise ExternalEvidenceError("AA pages disagree about total_pages")
        has_more = pagination.get("has_more")
        if not isinstance(has_more, bool):
            raise ExternalEvidenceError("AA pagination has_more must be a boolean")
        is_last = position == len(pages)
        if has_more is is_last:
            raise ExternalEvidenceError(
                "AA pagination has_more contradicts the collected page order at page "
                f"{page_number}"
            )
        data = page.get("data")
        if not isinstance(data, list):
            raise ExternalEvidenceError("AA page data must be an array")
        for item in data:
            if not isinstance(item, Mapping):
                raise ExternalEvidenceError("AA model entry is not an object")
            aa_model_id = _resolve_path(item, field_paths.model_id)
            if not isinstance(aa_model_id, str) or not aa_model_id.strip():
                raise ExternalEvidenceError(
                    f"AA model entry has no {field_paths.model_id}"
                )
            if aa_model_id in seen_model_ids:
                raise ExternalEvidenceError(f"AA response repeats model id {aa_model_id}")
            seen_model_ids.add(aa_model_id)
            models.append(dict(item))
        ordered.append(dict(page))
    if declared_total_pages is not None and declared_total_pages != len(ordered):
        raise ExternalEvidenceError(
            f"AA declared {declared_total_pages} pages but {len(ordered)} were collected"
        )
    assert index_version is not None  # every page validated one
    return AaRawCapture(
        raw_pages=tuple(raw_pages),
        pages=tuple(ordered),
        intelligence_index_version=index_version,
        models=tuple(models),
        raw_response_hash=raw_response_hash(raw_pages),
    )


def _metrics_for(
    entry: CatalogEntry,
    model: Mapping[str, object],
    *,
    field_paths: AaFieldPaths,
) -> AaModelMetrics:
    slug = _resolve_path(model, field_paths.slug)
    if not isinstance(slug, str) or slug != entry.aa_slug:
        raise ExternalEvidenceError(
            f"AA model {entry.aa_model_id} slug {slug!r} does not match the configured "
            f"mapping {entry.aa_slug!r}"
        )
    name = _resolve_path(model, field_paths.name)
    if not isinstance(name, str) or not name.strip():
        raise ExternalEvidenceError(f"AA model {entry.aa_model_id} has no name")
    return AaModelMetrics(
        aa_model_id=entry.aa_model_id,
        aa_slug=entry.aa_slug,
        aa_name=name.strip(),
        model_creator=_creator_name(
            _resolve_path(model, field_paths.creator), aa_model_id=entry.aa_model_id
        ),
        candidate_key=entry.key,
        input_price_per_million=_numeric(
            _resolve_path(model, field_paths.input_price_per_million),
            name=f"{entry.aa_model_id}.{field_paths.input_price_per_million}",
            allow_zero=True,
        ),
        output_price_per_million=_numeric(
            _resolve_path(model, field_paths.output_price_per_million),
            name=f"{entry.aa_model_id}.{field_paths.output_price_per_million}",
            allow_zero=True,
        ),
        median_end_to_end_seconds=_numeric(
            _resolve_path(model, field_paths.median_end_to_end_seconds),
            name=f"{entry.aa_model_id}.{field_paths.median_end_to_end_seconds}",
            allow_zero=False,
        ),
        intelligence_index=_numeric(
            _resolve_path(model, field_paths.intelligence_index),
            name=f"{entry.aa_model_id}.{field_paths.intelligence_index}",
            allow_zero=False,
        ),
    )


def build_aa_snapshot(
    *,
    purchase_id: str,
    catalog: ModelCatalog,
    capture: AaRawCapture,
    mode: AaSourceMode,
    fetched_at: datetime,
    field_paths: AaFieldPaths,
    source_url: str = AA_SOURCE_URL,
) -> tuple[AaSnapshot, ImmutableDocument]:
    """Bind every catalog model to exactly one captured AA model, or abort.

    A live capture may only be read through a catalog whose mappings were explicitly
    configured; a fixture catalog can never be presented as verified live mapping.
    """
    provenance = catalog.provenance
    if mode is AaSourceMode.LIVE and provenance != {MappingProvenance.CONFIGURED}:
        raise ExternalEvidenceError(
            "a live AA capture requires an explicitly configured catalog mapping"
        )
    if mode is AaSourceMode.FIXTURE and MappingProvenance.CONFIGURED in provenance:
        raise ExternalEvidenceError(
            "a fixture AA capture must not be read through a live-mapped catalog"
        )
    by_id: dict[str, JsonObject] = {}
    for model in capture.models:
        aa_model_id = _resolve_path(model, field_paths.model_id)
        if isinstance(aa_model_id, str):
            by_id[aa_model_id] = model
    metrics: list[AaModelMetrics] = []
    for entry in catalog.entries:
        mapped = by_id.get(entry.aa_model_id)
        if mapped is None:
            raise ExternalEvidenceError(
                f"AA response does not contain configured model id {entry.aa_model_id}"
            )
        metrics.append(_metrics_for(entry, mapped, field_paths=field_paths))
    snapshot = AaSnapshot(
        snapshot_id="",
        purchase_id=purchase_id,
        source_url=source_url,
        mode=mode,
        fetched_at=fetched_at,
        intelligence_index_version=capture.intelligence_index_version,
        raw_response_hash=capture.raw_response_hash,
        raw_pages=capture.raw_pages,
        page_count=capture.page_count,
        total_model_count=len(capture.models),
        catalog_version=catalog.catalog_version,
        catalog_provenance=tuple(sorted(provenance, key=lambda item: item.value)),
        catalog_entries=catalog.entries,
        models=tuple(metrics),
    )
    document = build_immutable_document(
        purchase_id=purchase_id,
        kind=AA_SNAPSHOT_DOCUMENT_KIND,
        payload=snapshot.body_payload(),
        created_at=fetched_at,
    )
    return dataclasses.replace(snapshot, snapshot_id=document.document_id), document
