"""The `aegis-aa-v1` normalized request.

A new request carries only the four supported priority values, the deterministic priority
classification evidence and the token estimate the fixed prepayment is computed from. The
superseded weighted-preset names are rejected with an explicit message rather than being
re-interpreted, while `NormalizedAiRequest` keeps serving stored history unchanged.

The prompt itself never enters this model: only its hash, its byte length and dictionary
keywords that matched, so recording the classification reason leaks no prompt content.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from buyer_audit_api.adapters.local_priority import PriorityClassifier
from buyer_audit_api.core.aa_policy import (
    AA_REQUEST_SCHEMA_VERSION,
    PRIORITY_CLASSIFICATION_METHOD,
    TOKEN_ESTIMATION_METHOD,
    AaRequestSchemaVersion,
    PolicyNumberError,
    PolicyWeights,
    PriorityClassification,
    PriorityReason,
    RequestPriority,
    TokenEstimate,
    TokenEstimationMethod,
    classification_inputs,
    classify_priority,
    estimate_tokens,
)
from buyer_audit_api.core.hashing import sha256_bytes
from buyer_audit_api.core.models import JsonObject

#: Raw-request discriminator that selects this schema instead of the legacy one.
AA_REQUEST_SCHEMA_KEY = "requestSchema"


class AegisNormalizedRequest(BaseModel):
    """The protocol-neutral, prompt-free normalization stored in `REQUESTED`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_schema_version: AaRequestSchemaVersion = AA_REQUEST_SCHEMA_VERSION
    prompt_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prompt_length: int = Field(ge=1, le=20_000)
    prompt_byte_length: int = Field(ge=1)
    system_prompt_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    system_prompt_byte_length: int = Field(ge=0)
    input_byte_length: int = Field(ge=1)
    estimated_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    estimation_method: TokenEstimationMethod = TOKEN_ESTIMATION_METHOD
    original_priority: RequestPriority | None = None
    effective_priority: RequestPriority
    priority_reason: PriorityReason
    matched_keywords: tuple[str, ...] = ()
    matched_priorities: tuple[RequestPriority, ...] = ()
    classification_method: str = PRIORITY_CLASSIFICATION_METHOD
    classification_model: str | None = None
    classification_evidence: str | None = None
    required_capabilities: tuple[str, ...] = ()
    allowed_providers: tuple[str, ...] = ()
    max_completion_ms: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _consistent_estimate(self) -> AegisNormalizedRequest:
        if self.prompt_byte_length + self.system_prompt_byte_length != self.input_byte_length:
            raise ValueError("input byte length must be the prompt plus system prompt bytes")
        expected = max(1, -(-self.input_byte_length // 4))
        if self.estimated_input_tokens != expected:
            raise ValueError("estimated input tokens do not match utf8-bytes-div4-v1")
        return self

    @property
    def classification(self) -> PriorityClassification:
        return PriorityClassification(
            effective=self.effective_priority,
            original=self.original_priority,
            reason=self.priority_reason,
            matched_keywords=self.matched_keywords,
            matched_priorities=self.matched_priorities,
            classification_method=self.classification_method,
            classification_model=self.classification_model,
            classification_evidence=self.classification_evidence,
        )

    @property
    def weights(self) -> PolicyWeights:
        return self.classification.weights

    @property
    def token_estimate(self) -> TokenEstimate:
        return TokenEstimate(
            input_bytes=self.input_byte_length,
            input_tokens=self.estimated_input_tokens,
            max_output_tokens=self.max_output_tokens,
        )


def is_aegis_request(payload: Mapping[str, Any]) -> bool:
    """True only for an explicit `requestSchema: aegis-aa-v1` declaration."""
    return payload.get(AA_REQUEST_SCHEMA_KEY) == AA_REQUEST_SCHEMA_VERSION


def normalize_aegis_request(
    payload: Mapping[str, Any],
    *,
    default_max_output_tokens: int,
    system_prompt: str = "",
    priority_classifier: PriorityClassifier | None = None,
) -> AegisNormalizedRequest:
    """Build the stored normalization, including the whole input the model will receive."""
    prompt, explicit_priority = classification_inputs(payload)
    raw_max_output = payload.get("max_output_tokens", payload.get("maxOutputTokens"))
    max_output_tokens = (
        default_max_output_tokens if raw_max_output is None else raw_max_output
    )
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int):
        raise ValueError("max_output_tokens must be a positive integer")
    try:
        classification = classify_priority(prompt=prompt, explicit=explicit_priority)
        if classification.original is None and priority_classifier is not None:
            classification = priority_classifier.classify(prompt=prompt)
        estimate = estimate_tokens(
            model_input_parts=(system_prompt, prompt),
            max_output_tokens=max_output_tokens,
        )
    except PolicyNumberError as exc:
        raise ValueError(str(exc)) from exc
    required = tuple(
        sorted(
            {
                str(value).strip().lower()
                for value in payload.get("required_capabilities", ())
                if str(value).strip()
            }
        )
    )
    providers = tuple(
        sorted(
            {
                str(value).strip().lower()
                for value in payload.get("allowed_providers", ())
                if str(value).strip()
            }
        )
    )
    try:
        return AegisNormalizedRequest(
            prompt_hash=sha256_bytes(prompt.encode("utf-8")),
            prompt_length=len(prompt),
            prompt_byte_length=len(prompt.encode("utf-8")),
            system_prompt_hash=sha256_bytes(system_prompt.encode("utf-8")),
            system_prompt_byte_length=len(system_prompt.encode("utf-8")),
            input_byte_length=estimate.input_bytes,
            estimated_input_tokens=estimate.input_tokens,
            max_output_tokens=estimate.max_output_tokens,
            original_priority=classification.original,
            effective_priority=classification.effective,
            priority_reason=classification.reason,
            matched_keywords=classification.matched_keywords,
            matched_priorities=classification.matched_priorities,
            classification_method=classification.classification_method,
            classification_model=classification.classification_model,
            classification_evidence=classification.classification_evidence,
            required_capabilities=required,
            allowed_providers=providers,
            max_completion_ms=payload.get("max_completion_ms"),
        )
    except ValidationError as exc:
        raise ValueError("invalid aegis-aa-v1 request") from exc


def normalized_request_payload(request: AegisNormalizedRequest) -> JsonObject:
    return request.model_dump(mode="json")
