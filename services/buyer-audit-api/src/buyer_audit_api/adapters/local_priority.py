"""Bounded loopback-only Qwen priority classification for the local demo."""

from __future__ import annotations

import json
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from buyer_audit_api.core.aa_policy import (
    LOCAL_QWEN_PRIORITY_CLASSIFICATION_METHOD,
    PriorityClassification,
    PriorityReason,
    RequestPriority,
)


class PriorityClassificationError(ValueError):
    """A safe error exposed before a purchase or payment is created."""


class PriorityClassifier(Protocol):
    def classify(self, *, prompt: str) -> PriorityClassification: ...


class LocalQwenPriorityClassifier:
    """Calls only an explicitly configured loopback Ollama `/api/chat` endpoint."""

    _EVIDENCE = frozenset({"price", "speed", "intelligence", "default"})

    def __init__(self, *, base_url: str, model: str, timeout_seconds: float) -> None:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname.lower() if parsed.hostname else ""
        if (
            parsed.scheme != "http"
            or hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("AEGIS_OLLAMA_URL must use a loopback HTTP address")
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("AEGIS_OLLAMA_TIMEOUT_SECONDS must be between 0 and 30")
        self._url = f"{urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))}/api/chat"
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds
        if not self._model:
            raise ValueError("AEGIS_OLLAMA_MODEL must not be empty")

    def classify(self, *, prompt: str) -> PriorityClassification:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["priority"],
            "properties": {
                "priority": {"type": "string", "enum": sorted(self._EVIDENCE)},
            },
        }
        try:
            response = httpx.post(
                self._url,
                json={
                    "model": self._model,
                    "stream": False,
                    # Qwen 3.5 otherwise spends the bounded output budget on hidden
                    # reasoning and can return no structured content at all.
                    "think": False,
                    "format": schema,
                    "options": {"temperature": 0, "num_predict": 48},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Classify the user's model-selection priority. Return JSON only. "
                                "price means cost, speed means latency, intelligence means quality "
                                "or reasoning, default means no clear preference. "
                                "Conflicting priorities must be default. "
                                "Interpret negated priorities as absent."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            content = body.get("message", {}).get("content") if isinstance(body, dict) else None
            if not isinstance(content, str):
                raise ValueError("missing structured content")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("structured content is not an object")
            raw_priority = result.get("priority")
            if not isinstance(raw_priority, str):
                raise ValueError("priority is missing")
            priority = RequestPriority(raw_priority)
            # A controlled prompt-free reason avoids model text echoing the request.
            evidence = priority.value
        except (httpx.HTTPError, ValueError, TypeError):
            # Never propagate server bodies, model output or a URL: they can contain prompt data.
            raise PriorityClassificationError(
                "로컬 Qwen 우선순위 분류에 실패했습니다. "
                "Ollama(qwen3.5:4b) 실행 상태를 확인한 뒤 다시 시도해 주세요."
            ) from None
        return PriorityClassification(
            effective=priority,
            original=None,
            reason=PriorityReason.LOCAL_MODEL,
            matched_keywords=(),
            matched_priorities=(),
            classification_method=LOCAL_QWEN_PRIORITY_CLASSIFICATION_METHOD,
            classification_model=self._model,
            classification_evidence=evidence,
        )
