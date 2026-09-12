from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.local_priority import (
    LocalQwenPriorityClassifier,
    PriorityClassificationError,
)
from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.aa_audit import _priority_issues
from buyer_audit_api.core.aa_policy import (
    LOCAL_QWEN_PRIORITY_CLASSIFICATION_METHOD,
    PriorityClassification,
    PriorityReason,
)
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.core.request_classification import OriginalClassification
from buyer_audit_api.core.time import SystemClock
from buyer_audit_api.domains.ai_inference.aa_request import normalize_aegis_request
from buyer_audit_api.domains.ai_inference.module import AiInferenceDomainModule


def test_local_qwen_classification_is_recorded_without_keyword_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
        return httpx.Response(
            200,
            request=request,
            json={"message": {"content": json.dumps({"priority": "price"})}},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    normalized = normalize_aegis_request(
        {"prompt": "예산을 아끼면서 비교해 주세요."},
        default_max_output_tokens=128,
        priority_classifier=LocalQwenPriorityClassifier(
            base_url="http://127.0.0.1:11434", model="qwen3.5:4b", timeout_seconds=2
        ),
    )
    assert normalized.effective_priority == "price"
    assert normalized.priority_reason == "local_model_classification"
    assert normalized.classification_method == LOCAL_QWEN_PRIORITY_CLASSIFICATION_METHOD
    assert normalized.classification_model == "qwen3.5:4b"
    assert normalized.classification_evidence == "price"
    assert normalized.matched_keywords == ()


def test_explicit_priority_bypasses_local_qwen(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_post(*args: object, **kwargs: object) -> httpx.Response:
        raise AssertionError("explicit priority must not call Ollama")

    monkeypatch.setattr(httpx, "post", fail_post)
    normalized = normalize_aegis_request(
        {"prompt": "가장 싼 것을 골라 주세요.", "priority": "speed"},
        default_max_output_tokens=128,
        priority_classifier=LocalQwenPriorityClassifier(
            base_url="http://localhost:11434", model="qwen3.5:4b", timeout_seconds=2
        ),
    )
    assert normalized.effective_priority == "speed"
    assert normalized.priority_reason == "explicit_priority"


def test_invalid_local_model_response_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
        return httpx.Response(200, request=request, json={"message": {"content": "not json"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    classifier = LocalQwenPriorityClassifier(
        base_url="http://127.0.0.1:11434", model="qwen3.5:4b", timeout_seconds=2
    )
    with pytest.raises(PriorityClassificationError, match="로컬 Qwen"):
        classifier.classify(prompt="test")


@pytest.mark.parametrize(
    "url",
    ["http://localhost.evil:11434", "https://localhost:11434", "http://10.0.0.2:11434"],
)
def test_local_qwen_rejects_non_loopback_urls(url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        LocalQwenPriorityClassifier(base_url=url, model="qwen3.5:4b", timeout_seconds=2)


def test_audit_does_not_treat_keyword_reclassification_as_local_qwen_corruption() -> None:
    normalized = {
        "effective_priority": "intelligence",
        "original_priority": None,
        "priority_reason": "local_model_classification",
        "matched_keywords": [],
        "matched_priorities": [],
        "classification_method": LOCAL_QWEN_PRIORITY_CLASSIFICATION_METHOD,
        "classification_model": "qwen3.5:4b",
        "classification_evidence": "intelligence",
    }
    decision = {
        "priority": {
            "classificationMethod": LOCAL_QWEN_PRIORITY_CLASSIFICATION_METHOD,
            "classificationModel": "qwen3.5:4b",
            "classificationEvidence": "intelligence",
            "effectivePriority": "intelligence",
            "originalPriority": None,
            "reason": "local_model_classification",
            "matchedKeywords": [],
            "matchedPriorities": [],
            "weights": {"price": 20, "completionTime": 20, "intelligence": 60},
        },
        "weights": {"price": 20, "completionTime": 20, "intelligence": 60},
    }
    keyword_reclassification = OriginalClassification(
        status="classified",
        effective_priority="price",
        original_priority=None,
        reason=PriorityReason.KEYWORD_MATCH.value,
        matched_keywords=("싸게",),
        matched_priorities=("price",),
    )
    _, issues = _priority_issues(normalized, decision, keyword_reclassification)
    assert not issues


def test_purchase_creation_bypasses_explicit_and_fails_before_any_write() -> None:
    class FailingClassifier:
        calls = 0

        def classify(self, *, prompt: str) -> PriorityClassification:
            self.calls += 1
            raise PriorityClassificationError("safe local model failure")

    async def exercise() -> None:
        repository = InMemoryEvidenceRepository()
        classifier = FailingClassifier()
        service = PurchaseService(
            repository=repository,
            cipher=LocalEnvelopeCipher(master_key=b"p" * 32),
            domains=DomainRegistry(
                [
                    AiInferenceDomainModule(
                        default_max_output_tokens=128,
                        priority_classifier=classifier,
                    )
                ]
            ),
            clock=SystemClock(),
        )
        created = await service.create(
            owner_address="0x00000000000000000000000000000000000a6e15",
            domain_id="ai_inference",
            raw_request={
                "requestSchema": "aegis-aa-v1",
                "prompt": "빠르게 처리해 주세요.",
                "priority": "price",
            },
            budget_units=1,
            policy={},
        )
        assert classifier.calls == 0
        assert len(await repository.list_events(created.purchase_id)) == 1
        with pytest.raises(PriorityClassificationError, match="safe local model failure"):
            await service.create(
                owner_address="0x00000000000000000000000000000000000a6e15",
                domain_id="ai_inference",
                raw_request={"requestSchema": "aegis-aa-v1", "prompt": "아무거나"},
                budget_units=1,
                policy={},
            )
        assert classifier.calls == 1
        assert len(repository._events) == 1  # noqa: SLF001 - proves no purchase event
        assert len(repository._sensitive) == 1  # noqa: SLF001 - proves no encrypted request write

    asyncio.run(exercise())
