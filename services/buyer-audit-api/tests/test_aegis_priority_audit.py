"""The audit re-derives the priority classification from the stored original request.

`AEGIS-01` could only confirm that a decision's recorded justification was internally
consistent, so a normalization and a decision edited together looked fine. These cover the
closing of that gap: the encrypted original is decrypted server-side, re-classified, and
compared - while the prompt itself stays out of every finding, event and advisor input.
"""

from __future__ import annotations

import asyncio
from dataclasses import astuple, replace

import pytest
from fastapi.testclient import TestClient

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.api.app import create_app
from buyer_audit_api.composition import AppContainer
from buyer_audit_api.core.aa_audit import recalculate_decision
from buyer_audit_api.core.audit import AuditService
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.models import EventType, EvidenceEvent
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.core.request_classification import StoredRequestClassifier
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_PAGE_PATHS,
    RUNTIME_CATALOG_PATH,
    load_model_catalog,
)
from buyer_audit_api.domains.ai_inference.aa_models import TokenIdentity
from buyer_audit_api.domains.ai_inference.aa_workflow import (
    AegisDecisionWorkflow,
    AegisEvidenceReader,
)
from buyer_audit_api.domains.ai_inference.module import AiInferenceDomainModule

LOCAL_OWNER = "0x00000000000000000000000000000000000a6e15"
#: A prompt long enough to price, carrying one price keyword and nothing else.
CHEAP_PROMPT = "이 작업을 최대한 싸게 처리해 주세요. " + "세부 요구 사항입니다. " * 6
CONFLICT_PROMPT = "싸게 그리고 빨리 처리해 주세요. " + "세부 요구 사항입니다. " * 6
PLAIN_PROMPT = "이 문서를 요약해 주세요. " + "세부 요구 사항입니다. " * 6


@pytest.fixture
def runtime_container(container: AppContainer) -> AppContainer:
    return replace(
        container,
        local_owner_address=LOCAL_OWNER,
        local_allowed_hosts=("localhost", "127.0.0.1", "testserver"),
        purchase_service=PurchaseService(
            repository=container.repository,
            cipher=container.cipher,
            domains=DomainRegistry([AiInferenceDomainModule(default_max_output_tokens=1024)]),
            clock=container.clock,
        ),
        aegis_workflow=AegisDecisionWorkflow(
            repository=container.repository,
            clock=container.clock,
            catalog=load_model_catalog(RUNTIME_CATALOG_PATH),
            source=FixtureArtificialAnalysisSource(page_paths=FIXTURE_PAGE_PATHS),
            token=TokenIdentity(
                name="AEGIS",
                symbol="AEGIS",
                decimals=6,
                address="0x0000000000000000000000000000000000000000",
                chain_id=84532,
            ),
        ),
        aegis_evidence_reader=AegisEvidenceReader(repository=container.repository),
    )


def _decide(client: TestClient, prompt: str, **request: object) -> str:
    created = client.post(
        "/purchases",
        json={
            "domain": "ai_inference",
            "budget_units": 50_000,
            "policy": {"scoringPolicyVersion": "aa-three-factor-v1"},
            "request": {"requestSchema": "aegis-aa-v1", "prompt": prompt, **request},
        },
    )
    assert created.status_code == 201, created.text
    purchase_id = str(created.json()["purchase_id"])
    assert client.post(f"/purchases/{purchase_id}/decide").status_code == 200
    return purchase_id


def _classifier(container: AppContainer) -> StoredRequestClassifier:
    return StoredRequestClassifier(store=container.repository, cipher=container.cipher)


def _events(container: AppContainer, purchase_id: str) -> list[EvidenceEvent]:
    return asyncio.run(container.repository.list_events(purchase_id))


def _snapshot_body(container: AppContainer, events: list[EvidenceEvent]) -> dict[str, object]:
    snapshot = next(item for item in events if item.type == EventType.AA_SNAPSHOT_RECORDED)
    document = asyncio.run(
        container.repository.get_document(str(snapshot.payload["snapshotId"]))
    )
    assert document is not None
    return {
        **document.payload,
        "snapshotId": document.document_id,
        "snapshotHash": snapshot.payload["snapshotHash"],
    }


def _recalculate(
    container: AppContainer,
    purchase_id: str,
    *,
    requested_payload: dict[str, object] | None = None,
    decided_payload: dict[str, object] | None = None,
) -> tuple[str, ...]:
    events = _events(container, purchase_id)
    requested = events[0]
    decided = next(item for item in events if item.type == EventType.DECIDED)
    classification = asyncio.run(
        _classifier(container).classify(
            purchase_id=purchase_id,
            requested_payload=requested_payload or requested.payload,
        )
    )
    issues = recalculate_decision(
        requested_payload=requested_payload or requested.payload,
        snapshot_payload=_snapshot_body(container, events),
        decided_payload=decided_payload or decided.payload,
        original_classification=classification,
    )
    return tuple(issue.rule_id for issue in issues)


@pytest.mark.parametrize(
    ("prompt", "overrides"),
    [
        (CHEAP_PROMPT, {}),
        (CONFLICT_PROMPT, {}),
        (PLAIN_PROMPT, {}),
        (PLAIN_PROMPT, {"priority": "intelligence"}),
    ],
    ids=["keyword match", "conflicting keywords", "no keyword", "explicit priority"],
)
def test_a_faithful_classification_is_verified_against_the_original(
    runtime_container: AppContainer, prompt: str, overrides: dict[str, object]
) -> None:
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decide(client, prompt, **overrides)
    rules = _recalculate(runtime_container, purchase_id)
    # Re-derived from the original, so nothing is reported as un-verifiable any more.
    assert "AUD-AA-PRIORITY-CLASSIFICATION-UNVERIFIABLE" not in rules
    assert "AUD-AA-PRIORITY-ORIGINAL-UNAVAILABLE" not in rules
    assert "AUD-AA-PRIORITY-ORIGINAL-MISMATCH" not in rules


def test_a_normalization_and_decision_edited_together_are_still_detected(
    runtime_container: AppContainer,
) -> None:
    """The whole point: two consistent lies are caught by the third, independent source."""
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decide(client, CHEAP_PROMPT)
    events = _events(runtime_container, purchase_id)
    requested = dict(events[0].payload)
    normalized = dict(requested["normalizedRequest"])
    decided = next(item for item in events if item.type == EventType.DECIDED)
    decided_payload = dict(decided.payload)

    # Rewrite the request-time classification and the decision's copy of it in step, so
    # every evidence-local consistency check still passes.
    normalized["effective_priority"] = "speed"
    normalized["matched_priorities"] = ["speed"]
    normalized["matched_keywords"] = ["빨리"]
    requested["normalizedRequest"] = normalized
    decided_payload["priority"] = {
        **dict(decided_payload["priority"]),
        "effectivePriority": "speed",
        "matchedPriorities": ["speed"],
        "matchedKeywords": ["빨리"],
        "weights": {"completionTime": 60, "intelligence": 20, "price": 20},
    }
    decided_payload["weights"] = {"completionTime": 60, "intelligence": 20, "price": 20}

    rules = _recalculate(
        runtime_container,
        purchase_id,
        requested_payload=requested,
        decided_payload=decided_payload,
    )
    assert "AUD-AA-PRIORITY-ORIGINAL-MISMATCH" in rules


def test_an_original_from_another_purchase_is_an_integrity_problem(
    runtime_container: AppContainer,
) -> None:
    with TestClient(create_app(runtime_container)) as client:
        first = _decide(client, CHEAP_PROMPT)
        second = _decide(client, PLAIN_PROMPT)
    other = _events(runtime_container, second)[0].payload
    swapped = dict(_events(runtime_container, first)[0].payload)
    swapped["sensitivePayloadId"] = other["sensitivePayloadId"]
    swapped["rawRequestHash"] = other["rawRequestHash"]
    rules = _recalculate(runtime_container, first, requested_payload=swapped)
    assert "AUD-AA-PRIORITY-ORIGINAL-BINDING-INVALID" in rules


def test_a_rewritten_raw_request_hash_is_an_integrity_problem(
    runtime_container: AppContainer,
) -> None:
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decide(client, CHEAP_PROMPT)
    tampered = dict(_events(runtime_container, purchase_id)[0].payload)
    tampered["rawRequestHash"] = "sha256:" + "0" * 64
    rules = _recalculate(runtime_container, purchase_id, requested_payload=tampered)
    assert "AUD-AA-PRIORITY-ORIGINAL-BINDING-INVALID" in rules


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda payload: payload.pop("sensitivePayloadId"), id="no reference"),
        pytest.param(
            lambda payload: payload.__setitem__("sensitivePayloadId", "missing-payload"),
            id="reference to nothing",
        ),
    ],
)
def test_a_missing_original_is_reported_as_unverified_not_as_agreement(
    runtime_container: AppContainer, mutate: object
) -> None:
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decide(client, CHEAP_PROMPT)
    payload = dict(_events(runtime_container, purchase_id)[0].payload)
    mutate(payload)  # type: ignore[operator]
    rules = _recalculate(runtime_container, purchase_id, requested_payload=payload)
    assert "AUD-AA-PRIORITY-ORIGINAL-UNAVAILABLE" in rules
    assert "AUD-AA-PRIORITY-ORIGINAL-MISMATCH" not in rules


def test_an_undecryptable_original_is_reported_without_any_cipher_detail(
    runtime_container: AppContainer,
) -> None:
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decide(client, CHEAP_PROMPT)

    class BrokenCipher:
        def decrypt_json(self, payload: object) -> dict[str, object]:
            raise ValueError("nonce mismatch for key material aabbccdd")

    classifier = StoredRequestClassifier(
        store=runtime_container.repository,
        cipher=BrokenCipher(),  # type: ignore[arg-type]
    )
    requested = _events(runtime_container, purchase_id)[0]
    result = asyncio.run(
        classifier.classify(purchase_id=purchase_id, requested_payload=requested.payload)
    )
    assert result.status == "unavailable"
    assert result.effective_priority is None
    assert "aabbccdd" not in str(result.detail)


def test_the_audit_report_and_the_advisor_never_receive_the_prompt(
    runtime_container: AppContainer,
) -> None:
    seen: list[str] = []

    class RecordingAdvisor:
        async def advise(
            self, *, purchase_id: str, public_events: tuple[EvidenceEvent, ...]
        ) -> tuple[()]:
            seen.append(repr([event.payload for event in public_events]))
            return ()

    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decide(client, CHEAP_PROMPT)

    async def run() -> object:
        service = AuditService(
            repository=runtime_container.repository,
            clock=runtime_container.clock,
            semantic_advisor=RecordingAdvisor(),  # type: ignore[arg-type]
            original_requests=_classifier(runtime_container),
        )
        return await service.audit(purchase_id)

    report = asyncio.run(run())
    fragment = "최대한 싸게"
    rendered = repr([astuple(finding) for finding in report.findings]) + repr(report)
    assert fragment not in rendered
    assert seen and all(fragment not in item for item in seen)
    # The whole stored chain, including the audit event, is free of the prompt while the
    # fixed dictionary term that matched is recorded as classification evidence.
    stored = repr([event.payload for event in _events(runtime_container, purchase_id)])
    assert fragment not in stored
    assert "싸게" in stored


def test_without_a_reader_the_historical_unverified_report_is_unchanged(
    runtime_container: AppContainer,
) -> None:
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decide(client, CHEAP_PROMPT)
    events = _events(runtime_container, purchase_id)
    decided = next(item for item in events if item.type == EventType.DECIDED)
    issues = recalculate_decision(
        requested_payload=events[0].payload,
        snapshot_payload=_snapshot_body(runtime_container, events),
        decided_payload=decided.payload,
    )
    assert "AUD-AA-PRIORITY-CLASSIFICATION-UNVERIFIABLE" in {
        issue.rule_id for issue in issues
    }
