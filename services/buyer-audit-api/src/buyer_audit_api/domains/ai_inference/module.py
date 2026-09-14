from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from buyer_audit_api.adapters.local_priority import PriorityClassifier
from buyer_audit_api.core.models import JsonObject
from buyer_audit_api.domains.ai_inference.aa_request import (
    is_aegis_request,
    normalize_aegis_request,
)

#: Fixed server default for the prepayment ceiling when a request does not set one.
# `standard-answer-v1` reserves room for an 8,000-token answer.  It is a
# prepayment ceiling, not an observed-usage bill: the selected model's price is fixed
# before payment and recorded with the purchase evidence.
DEFAULT_MAX_OUTPUT_TOKENS = 8_000


class AiInferenceDomainModule:
    """One domain, one request schema.

    A raw request must declare `requestSchema: aegis-aa-v1`; it is normalized under the
    2026-09-09 policy, which accepts only default/price/speed/intelligence. Anything else
    is refused before any evidence is written.
    """

    def __init__(
        self,
        *,
        default_max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        system_prompt: str = "",
        priority_classifier: PriorityClassifier | None = None,
    ) -> None:
        if default_max_output_tokens <= 0:
            raise ValueError("default_max_output_tokens must be a positive integer")
        self._default_max_output_tokens = default_max_output_tokens
        self._system_prompt = system_prompt
        self._priority_classifier = priority_classifier

    @property
    def domain_id(self) -> str:
        return "ai_inference"

    def normalize_request(self, payload: Mapping[str, Any]) -> JsonObject:
        if not is_aegis_request(payload):
            raise ValueError("ai_inference request must declare requestSchema aegis-aa-v1")
        return normalize_aegis_request(
            payload,
            default_max_output_tokens=self._default_max_output_tokens,
            system_prompt=self._system_prompt,
            priority_classifier=self._priority_classifier,
        ).model_dump(mode="json")
