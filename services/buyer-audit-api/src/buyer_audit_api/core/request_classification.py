"""Re-classify the stored original request, in memory, for the audit only.

`AEGIS-01` could check that a decision's recorded priority justification was internally
consistent, but not that it described the request the user actually sent: the prompt is
deliberately absent from the evidence chain. The original request is nevertheless stored,
encrypted, by the purchase service, so the audit server can decrypt it under its own
authority and re-run the deterministic classifier over it.

What leaves this module is only the classification result - policy names, the reason and
the fixed dictionary terms that matched. The prompt itself never reaches an audit finding,
the semantic advisor, a public event or a log line, and no error message here carries
plaintext.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from buyer_audit_api.core.aa_policy import (
    PRIORITY_CLASSIFICATION_METHOD,
    PolicyNumberError,
    classification_inputs,
    classify_priority,
)
from buyer_audit_api.core.models import JsonObject, SensitivePayload
from buyer_audit_api.core.ports import PayloadCipher

#: The kind the purchase service stores the original request under.
REQUEST_PAYLOAD_KIND = "request"

OriginalClassificationStatus = Literal["classified", "unavailable", "binding_invalid"]


@dataclass(frozen=True, slots=True)
class OriginalClassification:
    """A prompt-free classification result, or an explicit reason there is none."""

    status: OriginalClassificationStatus
    effective_priority: str | None = None
    original_priority: str | None = None
    reason: str | None = None
    matched_keywords: tuple[str, ...] = ()
    matched_priorities: tuple[str, ...] = ()
    classification_method: str = PRIORITY_CLASSIFICATION_METHOD
    classification_model: str | None = None
    classification_evidence: str | None = None
    #: A fixed, plaintext-free explanation of why no classification was produced.
    detail: str | None = None

    def to_payload(self) -> JsonObject:
        return {
            "classificationMethod": self.classification_method,
            "classificationModel": self.classification_model,
            "classificationEvidence": self.classification_evidence,
            "effectivePriority": self.effective_priority,
            "matchedKeywords": list(self.matched_keywords),
            "matchedPriorities": list(self.matched_priorities),
            "originalPriority": self.original_priority,
            "reason": self.reason,
        }


class OriginalRequestStore(Protocol):
    """The narrow read the audit needs; it can reach nothing else."""

    async def get_sensitive_payload(self, payload_id: str) -> SensitivePayload | None: ...


class StoredRequestClassifier:
    """Decrypts one stored request and returns only its deterministic classification."""

    def __init__(self, *, store: OriginalRequestStore, cipher: PayloadCipher) -> None:
        self._store = store
        self._cipher = cipher

    async def classify(
        self, *, purchase_id: str, requested_payload: JsonObject
    ) -> OriginalClassification:
        payload_id = requested_payload.get("sensitivePayloadId")
        raw_request_hash = requested_payload.get("rawRequestHash")
        if not isinstance(payload_id, str) or not isinstance(raw_request_hash, str):
            return OriginalClassification(
                status="unavailable",
                detail="저장된 요청 원문 참조가 없습니다.",
            )
        stored = await self._store.get_sensitive_payload(payload_id)
        if stored is None:
            return OriginalClassification(
                status="unavailable",
                detail="참조된 요청 원문을 찾지 못했습니다.",
            )
        # Binding first: a payload from another purchase, of another kind, or one whose
        # content hash is not the hash the request recorded, is an integrity problem and
        # must never be classified as if it were this request.
        if stored.purchase_id != purchase_id:
            return OriginalClassification(
                status="binding_invalid",
                detail="요청 원문이 다른 구매에 속합니다.",
            )
        if stored.kind != REQUEST_PAYLOAD_KIND:
            return OriginalClassification(
                status="binding_invalid",
                detail="참조된 저장 payload가 요청 원문이 아닙니다.",
            )
        if stored.content_hash != raw_request_hash:
            return OriginalClassification(
                status="binding_invalid",
                detail="요청 원문 해시가 REQUESTED에 기록된 값과 다릅니다.",
            )
        try:
            raw_request = self._cipher.decrypt_json(stored)
        except Exception:  # noqa: BLE001 - any cipher failure is reported without detail
            # The exception text could echo ciphertext or key material, so it is dropped.
            return OriginalClassification(
                status="unavailable",
                detail="요청 원문을 복호화하지 못했습니다.",
            )
        prompt, explicit = classification_inputs(raw_request)
        try:
            classification = classify_priority(prompt=prompt, explicit=explicit)
        except (PolicyNumberError, ValueError):
            return OriginalClassification(
                status="unavailable",
                detail="요청 원문의 우선순위 값이 현재 정책의 값이 아닙니다.",
            )
        return OriginalClassification(
            status="classified",
            effective_priority=classification.effective.value,
            original_priority=(
                None if classification.original is None else classification.original.value
            ),
            reason=classification.reason.value,
            matched_keywords=classification.matched_keywords,
            matched_priorities=tuple(
                item.value for item in classification.matched_priorities
            ),
        )
