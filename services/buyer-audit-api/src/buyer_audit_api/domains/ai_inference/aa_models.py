"""Evidence value objects for the `aa-three-factor-v1` model purchase policy.

These are the exact facts a new purchase stores: the captured Artificial Analysis
snapshot, the explicit provider/AA model mapping, every priced candidate with its source
numbers, every rejection reason, the three component scores of every eligible candidate
and the winner binding a payment may later be checked against.

Prices, benchmark seconds and intelligence indices stay `Decimal` end to end and are
serialized as plain decimal text, so no binary float ever touches a price and the
canonical JSON hash of a snapshot is reproducible.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from buyer_audit_api.core.aa_policy import (
    AA_SCORING_POLICY_VERSION,
    CandidateScore,
    PolicyWeights,
    PriorityClassification,
    PriorityReason,
    RequestPriority,
    ScoreReferences,
    TokenEstimate,
    decimal_text,
    require_finite_decimal,
)
from buyer_audit_api.core.aa_policy import (
    terms_binding_hash as policy_terms_binding_hash,
)
from buyer_audit_api.core.documents import (
    DOCUMENT_HASH_ALGORITHM,
    DOCUMENT_HASH_ENCODING,
)
from buyer_audit_api.core.errors import ExternalEvidenceError
from buyer_audit_api.core.hashing import utc_iso
from buyer_audit_api.core.models import JsonObject

AA_SNAPSHOT_DOCUMENT_KIND = "aaSnapshot"
AA_SOURCE_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
AA_ATTRIBUTION_URL = "https://artificialanalysis.ai/"
#: The official end-to-end measurement assumes a 500 answer-token completion unless the
#: source states otherwise, so the derived milliseconds are a benchmark reference value.
AA_COMPLETION_BENCHMARK_NOTE = "aa-median-end-to-end-500-answer-tokens"


class AaSourceMode(StrEnum):
    """Where the captured numbers came from. Never relabel one as the other."""

    FIXTURE = "fixture"
    LIVE = "live"


class MappingProvenance(StrEnum):
    """How a provider model was bound to an AA model id."""

    FIXTURE = "fixture"
    CONFIGURED = "configured"


def parse_decimal_text(value: object, *, name: str) -> Decimal:
    """Read a stored decimal string back without going through a binary float."""
    if isinstance(value, Decimal):
        return require_finite_decimal(value, name=name)
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ExternalEvidenceError(f"{name} must be decimal text")
    try:
        return require_finite_decimal(Decimal(value), name=name)
    except (InvalidOperation, ValueError) as exc:
        raise ExternalEvidenceError(f"{name} is not a finite decimal: {value!r}") from exc


def _require_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExternalEvidenceError(f"stored evidence field {key} is malformed")
    return value


def _require_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExternalEvidenceError(f"stored evidence field {key} is malformed")
    return value


def _require_sequence(payload: Mapping[str, object], key: str) -> Sequence[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ExternalEvidenceError(f"stored evidence field {key} is malformed")
    return value


class CatalogEntry(BaseModel):
    """One explicitly configured provider model bound to exactly one AA model id.

    `provider_model_id` is the id the provider's own API needs; `aa_model_id` is the id
    Artificial Analysis publishes. They are unrelated strings, so only an explicit pair
    is accepted - no search, no similar-name guess, no model-family inference.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1)
    provider_model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    aa_model_id: str = Field(min_length=1)
    aa_slug: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    recipient: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    capabilities: tuple[str, ...] = ()
    mapping_provenance: MappingProvenance

    @field_validator("provider_id")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("capabilities")
    @classmethod
    def _normalize_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({item.strip().lower() for item in value if item.strip()}))

    @field_validator("recipient")
    @classmethod
    def _normalize_recipient(cls, value: str) -> str:
        return value.lower()

    @property
    def key(self) -> str:
        """The stable candidate identity used in evidence and tie-breaks."""
        return f"{self.provider_id}:{self.provider_model_id}:{self.model_version}"

    def to_payload(self) -> JsonObject:
        return {
            "aaModelId": self.aa_model_id,
            "aaSlug": self.aa_slug,
            "capabilities": list(self.capabilities),
            "capabilitiesSource": "catalog",
            "candidateKey": self.key,
            "displayName": self.display_name,
            "mappingProvenance": self.mapping_provenance.value,
            "modelVersion": self.model_version,
            "providerId": self.provider_id,
            "providerModelId": self.provider_model_id,
            "recipient": self.recipient,
        }


class ModelCatalog(BaseModel):
    """The full explicit catalog. Duplicate identities are a configuration error."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_version: str = Field(min_length=1)
    entries: tuple[CatalogEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_identities(self) -> ModelCatalog:
        keys = [entry.key for entry in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("catalog contains duplicate provider model identities")
        aa_ids = [entry.aa_model_id for entry in self.entries]
        if len(set(aa_ids)) != len(aa_ids):
            raise ValueError("catalog maps one AA model id to more than one provider model")
        slugs = [entry.aa_slug for entry in self.entries]
        if len(set(slugs)) != len(slugs):
            raise ValueError("catalog maps one AA slug to more than one provider model")
        return self

    @property
    def provenance(self) -> frozenset[MappingProvenance]:
        return frozenset(entry.mapping_provenance for entry in self.entries)

    def entry(self, key: str) -> CatalogEntry:
        for candidate in self.entries:
            if candidate.key == key:
                return candidate
        raise ExternalEvidenceError(f"catalog entry {key} is not configured")

    def to_payload(self) -> JsonObject:
        return {
            "catalogVersion": self.catalog_version,
            "entries": [entry.to_payload() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class AaModelMetrics:
    """The AA facts of one catalog-mapped model, preserved as exact decimals."""

    aa_model_id: str
    aa_slug: str
    aa_name: str
    model_creator: str
    candidate_key: str
    input_price_per_million: Decimal
    output_price_per_million: Decimal
    median_end_to_end_seconds: Decimal
    intelligence_index: Decimal

    def to_payload(self) -> JsonObject:
        return {
            "aaModelId": self.aa_model_id,
            "aaName": self.aa_name,
            "aaSlug": self.aa_slug,
            "candidateKey": self.candidate_key,
            "inputPricePerMillion": decimal_text(self.input_price_per_million),
            "intelligenceIndex": decimal_text(self.intelligence_index),
            "medianEndToEndSeconds": decimal_text(self.median_end_to_end_seconds),
            "modelCreator": self.model_creator,
            "outputPricePerMillion": decimal_text(self.output_price_per_million),
        }

    @staticmethod
    def from_payload(payload: Mapping[str, object]) -> AaModelMetrics:
        return AaModelMetrics(
            aa_model_id=_require_str(payload, "aaModelId"),
            aa_slug=_require_str(payload, "aaSlug"),
            aa_name=_require_str(payload, "aaName"),
            model_creator=_require_str(payload, "modelCreator"),
            candidate_key=_require_str(payload, "candidateKey"),
            input_price_per_million=parse_decimal_text(
                payload.get("inputPricePerMillion"), name="inputPricePerMillion"
            ),
            output_price_per_million=parse_decimal_text(
                payload.get("outputPricePerMillion"), name="outputPricePerMillion"
            ),
            median_end_to_end_seconds=parse_decimal_text(
                payload.get("medianEndToEndSeconds"), name="medianEndToEndSeconds"
            ),
            intelligence_index=parse_decimal_text(
                payload.get("intelligenceIndex"), name="intelligenceIndex"
            ),
        )


@dataclass(frozen=True, slots=True)
class AaSnapshot:
    """An immutable capture of the AA free language-model list for one purchase.

    `raw_response_hash` binds the ordered raw pages exactly as received, so the derived
    per-model numbers can always be traced back to the bytes they came from.
    """

    snapshot_id: str
    purchase_id: str
    source_url: str
    mode: AaSourceMode
    fetched_at: datetime
    intelligence_index_version: Decimal
    raw_response_hash: str
    raw_pages: tuple[str, ...]
    page_count: int
    total_model_count: int
    catalog_version: str
    catalog_provenance: tuple[MappingProvenance, ...]
    catalog_entries: tuple[CatalogEntry, ...]
    models: tuple[AaModelMetrics, ...]

    def body_payload(self) -> JsonObject:
        """Everything except the id, because the id is the hash of exactly this body."""
        return {
            "attributionUrl": AA_ATTRIBUTION_URL,
            "catalogEntries": [entry.to_payload() for entry in self.catalog_entries],
            "catalogProvenance": [item.value for item in self.catalog_provenance],
            "catalogVersion": self.catalog_version,
            "completionBenchmark": AA_COMPLETION_BENCHMARK_NOTE,
            "fetchedAt": utc_iso(self.fetched_at),
            "hashAlgorithm": DOCUMENT_HASH_ALGORITHM,
            "hashEncoding": DOCUMENT_HASH_ENCODING,
            "intelligenceIndexVersion": decimal_text(self.intelligence_index_version),
            "mode": self.mode.value,
            "models": [model.to_payload() for model in self.models],
            "pageCount": self.page_count,
            "purchaseId": self.purchase_id,
            "rawPages": list(self.raw_pages),
            "rawResponseHash": self.raw_response_hash,
            "sourceUrl": self.source_url,
            "totalModelCount": self.total_model_count,
        }

    def to_payload(self) -> JsonObject:
        return {**self.body_payload(), "snapshotId": self.snapshot_id}

    @staticmethod
    def from_payload(payload: Mapping[str, object]) -> AaSnapshot:
        raw_models = _require_sequence(payload, "models")
        provenance = _require_sequence(payload, "catalogProvenance")
        raw_pages = _require_sequence(payload, "rawPages")
        raw_entries = _require_sequence(payload, "catalogEntries")
        return AaSnapshot(
            snapshot_id=_require_str(payload, "snapshotId"),
            purchase_id=_require_str(payload, "purchaseId"),
            source_url=_require_str(payload, "sourceUrl"),
            mode=AaSourceMode(_require_str(payload, "mode")),
            fetched_at=datetime.fromisoformat(
                _require_str(payload, "fetchedAt").replace("Z", "+00:00")
            ),
            intelligence_index_version=parse_decimal_text(
                payload.get("intelligenceIndexVersion"), name="intelligenceIndexVersion"
            ),
            raw_response_hash=_require_str(payload, "rawResponseHash"),
            raw_pages=tuple(str(item) for item in raw_pages),
            page_count=_require_int(payload, "pageCount"),
            total_model_count=_require_int(payload, "totalModelCount"),
            catalog_version=_require_str(payload, "catalogVersion"),
            catalog_provenance=tuple(
                MappingProvenance(str(item)) for item in provenance
            ),
            catalog_entries=tuple(
                CatalogEntry(
                    provider_id=_require_str(item, "providerId"),
                    provider_model_id=_require_str(item, "providerModelId"),
                    model_version=_require_str(item, "modelVersion"),
                    aa_model_id=_require_str(item, "aaModelId"),
                    aa_slug=_require_str(item, "aaSlug"),
                    display_name=_require_str(item, "displayName"),
                    recipient=_require_str(item, "recipient"),
                    capabilities=tuple(
                        str(value) for value in _require_sequence(item, "capabilities")
                    ),
                    mapping_provenance=MappingProvenance(
                        _require_str(item, "mappingProvenance")
                    ),
                )
                for item in raw_entries
                if isinstance(item, Mapping)
            ),
            models=tuple(
                AaModelMetrics.from_payload(item)
                for item in raw_models
                if isinstance(item, Mapping)
            ),
        )

    def metrics(self, candidate_key: str) -> AaModelMetrics:
        for model in self.models:
            if model.candidate_key == candidate_key:
                return model
        raise ExternalEvidenceError(f"snapshot has no AA metrics for {candidate_key}")


@dataclass(frozen=True, slots=True)
class TokenIdentity:
    """The settlement token this purchase is priced in. Server configuration is truth.

    `status` is part of the evidence and part of the terms binding: a purchase priced
    against a token that is only prepared can never be presented as one priced against a
    deployed token.
    """

    name: str
    symbol: str
    decimals: int
    address: str
    chain_id: int
    status: str = "prepared"

    def to_payload(self) -> JsonObject:
        return {
            "address": self.address,
            "chainId": self.chain_id,
            "decimals": self.decimals,
            "name": self.name,
            "status": self.status,
            "symbol": self.symbol,
        }

    @staticmethod
    def from_payload(payload: Mapping[str, object]) -> TokenIdentity:
        return TokenIdentity(
            name=_require_str(payload, "name"),
            symbol=_require_str(payload, "symbol"),
            decimals=_require_int(payload, "decimals"),
            address=_require_str(payload, "address"),
            chain_id=_require_int(payload, "chainId"),
            status=_require_str(payload, "status"),
        )


@dataclass(frozen=True, slots=True)
class PricedCandidate:
    """A catalog candidate with its AA source values and derived fixed price."""

    entry: CatalogEntry
    metrics: AaModelMetrics
    amount_units: int
    estimated_completion_ms: Decimal

    @property
    def key(self) -> str:
        return self.entry.key

    def to_payload(self) -> JsonObject:
        return {
            "amountUnits": self.amount_units,
            "candidateKey": self.key,
            "capabilities": list(self.entry.capabilities),
            "capabilitiesSource": "catalog",
            "estimatedCompletionMs": decimal_text(self.estimated_completion_ms),
            "estimatedCompletionMsBasis": AA_COMPLETION_BENCHMARK_NOTE,
            "mappingProvenance": self.entry.mapping_provenance.value,
            "modelVersion": self.entry.model_version,
            "providerId": self.entry.provider_id,
            "providerModelId": self.entry.provider_model_id,
            "recipient": self.entry.recipient,
            "source": self.metrics.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    """A numerically sound candidate that does not fit this request."""

    candidate_key: str
    provider_id: str
    provider_model_id: str
    model_version: str
    reasons: tuple[str, ...]

    def to_payload(self) -> JsonObject:
        return {
            "candidateKey": self.candidate_key,
            "modelVersion": self.model_version,
            "providerId": self.provider_id,
            "providerModelId": self.provider_model_id,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """One eligible candidate with its exact three component scores and rank."""

    candidate: PricedCandidate
    score: CandidateScore
    rank: int

    @property
    def key(self) -> str:
        return self.candidate.key

    def to_payload(self) -> JsonObject:
        return {
            "amountUnits": self.candidate.amount_units,
            "candidateKey": self.key,
            "estimatedCompletionMs": decimal_text(self.candidate.estimated_completion_ms),
            "intelligenceIndex": decimal_text(self.candidate.metrics.intelligence_index),
            "modelVersion": self.candidate.entry.model_version,
            "providerId": self.candidate.entry.provider_id,
            "providerModelId": self.candidate.entry.provider_model_id,
            "rank": self.rank,
            "scores": self.score.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class AaSelectionDecision:
    """The complete fixed decision a payment and an audit are both checked against."""

    purchase_id: str
    snapshot_id: str
    snapshot_hash: str
    catalog_version: str
    classification: PriorityClassification
    tokens: TokenEstimate
    candidates: tuple[PricedCandidate, ...]
    rejected: tuple[RejectedCandidate, ...]
    eligible: tuple[ScoredCandidate, ...]
    references: ScoreReferences
    winner: ScoredCandidate
    token: TokenIdentity
    explanation: str
    terms_binding_hash: str

    @property
    def amount_units(self) -> int:
        return self.winner.candidate.amount_units

    @property
    def weights(self) -> PolicyWeights:
        return self.classification.weights

    def to_payload(self) -> JsonObject:
        return {
            "amountUnits": self.amount_units,
            "candidates": [item.to_payload() for item in self.candidates],
            "catalogVersion": self.catalog_version,
            "eligible": [item.to_payload() for item in self.eligible],
            "explanation": self.explanation,
            "priority": self.classification.to_payload(),
            "purchaseId": self.purchase_id,
            "rejected": [item.to_payload() for item in self.rejected],
            "scoreReferences": self.references.to_payload(),
            "scoringPolicyVersion": AA_SCORING_POLICY_VERSION,
            "snapshotHash": self.snapshot_hash,
            "snapshotId": self.snapshot_id,
            "termsBindingHash": self.terms_binding_hash,
            "token": self.token.to_payload(),
            "tokens": self.tokens.to_payload(),
            "weights": self.weights.to_payload(),
            "winner": self.winner.to_payload(),
        }


def terms_binding_hash(
    *,
    purchase_id: str,
    snapshot_hash: str,
    winner: PricedCandidate,
    token: TokenIdentity,
) -> str:
    """The single hash a 402 challenge and the signing module both bind themselves to."""
    return policy_terms_binding_hash(
        purchase_id=purchase_id,
        snapshot_hash=snapshot_hash,
        provider_id=winner.entry.provider_id,
        provider_model_id=winner.entry.provider_model_id,
        model_version=winner.entry.model_version,
        amount_units=winner.amount_units,
        recipient=winner.entry.recipient,
        token=token.to_payload(),
    )


def decision_explanation(
    *,
    classification: PriorityClassification,
    winner: ScoredCandidate,
) -> str:
    """Deterministic Korean explanation. An LLM may restate it but never change it."""
    reason = {
        PriorityReason.EXPLICIT: "명시적 우선순위",
        PriorityReason.KEYWORD_MATCH: "요청 문구 분류",
        PriorityReason.NO_KEYWORD_MATCH: "분류 키워드 없음",
        PriorityReason.CONFLICTING_KEYWORD_MATCH: "상충 키워드",
        PriorityReason.LOCAL_MODEL: "로컬 Qwen 요청 의미 분류",
    }[classification.reason]
    priority: RequestPriority = classification.effective
    weights = classification.weights
    return (
        f"{priority.value} 우선순위({reason})의 고정 가중치 "
        f"가격 {weights.price}/완료시간 {weights.completion_time}/"
        f"성능 {weights.intelligence}로 "
        f"{winner.candidate.entry.provider_id}/"
        f"{winner.candidate.entry.provider_model_id}를 선택했습니다. "
        f"선결제 금액은 {winner.candidate.amount_units} 단위입니다. "
        "필터와 점수는 결정적 코드가 같은 AA snapshot으로 계산했습니다."
    )
