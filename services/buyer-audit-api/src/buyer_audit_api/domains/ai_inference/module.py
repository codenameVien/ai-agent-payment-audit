from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from buyer_audit_api.core.hashing import sha256_bytes
from buyer_audit_api.core.models import JsonObject
from buyer_audit_api.domains.ai_inference.models import NormalizedAiRequest


class AiInferenceDomainModule:
    @property
    def domain_id(self) -> str:
        return "ai_inference"

    def normalize_request(self, payload: Mapping[str, Any]) -> JsonObject:
        raw_prompt = payload.get("prompt", payload.get("query"))
        prompt = raw_prompt if isinstance(raw_prompt, str) else ""
        required = sorted(
            {
                str(value).strip().lower()
                for value in payload.get("required_capabilities", [])
                if str(value).strip()
            }
        )
        sellers = sorted(
            {
                str(value).strip().lower()
                for value in payload.get("allowed_sellers", [])
                if str(value).strip()
            }
        )
        try:
            normalized = NormalizedAiRequest.model_validate(
                {
                    "allowed_sellers": sellers,
                    "max_latency_ms": payload.get("max_latency_ms"),
                    "min_input_limit": payload.get("min_input_limit", 1),
                    "min_output_limit": payload.get("min_output_limit", 1),
                    "priority": payload.get("priority", "balanced"),
                    "prompt_hash": sha256_bytes(prompt.encode("utf-8")),
                    "prompt_length": len(prompt),
                    "required_capabilities": required,
                }
            )
        except ValidationError as exc:
            raise ValueError("invalid ai_inference request") from exc
        return normalized.model_dump(mode="json")

    def clarification(self, payload: Mapping[str, Any]):
        from buyer_audit_api.domains.ai_inference.models import Clarification

        prompt = str(payload.get("prompt", payload.get("query", ""))).strip()
        questions: list[str] = []
        if not prompt:
            questions.append("어떤 작업을 AI에게 맡기고 싶은지 입력해 주세요.")
        if payload.get("priority") not in (None, "balanced", "quality", "price", "speed"):
            questions.append("품질·가격·속도 중 무엇을 우선할지 선택해 주세요.")
        return Clarification(required=bool(questions), questions=tuple(questions))
