from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.models import EventType, EvidenceEvent
from buyer_audit_api.core.observer import (
    LocalQwenEvidenceObserver,
    ObserverFailureCode,
    ObserverPhase,
    ObserverUnavailable,
)


class _Response:
    def __init__(self, value: Any | Exception) -> None:
        self._value = value

    def raise_for_status(self) -> None:
        if isinstance(self._value, Exception):
            raise self._value

    def json(self) -> Any:
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


class _Client:
    def __init__(
        self, *, response: Any | Exception, captured: list[dict[str, Any]], **_kwargs: Any
    ) -> None:
        self._response = response
        self._captured = captured

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, _url: str, *, json: dict[str, Any]) -> _Response:
        self._captured.append(json)
        return _Response(self._response)


async def _event() -> EvidenceEvent:
    repository = InMemoryEvidenceRepository()
    from datetime import UTC, datetime

    return await repository.append_event(
        purchase_id="observer-unit",
        event_type=EventType.DECIDED,
        occurred_at=datetime(2026, 9, 12, tzinfo=UTC),
        actor={"id": "rules", "type": "service"},
        payload={
            "candidates": [{"modelId": "candidate-a", "scores": {"price": 1}}],
            "eligible": [{"modelId": "candidate-a", "score": 0.9}],
            "explanation": "deterministic winner explanation",
            "priority": {"effective": "price"},
            "rejected": [{"modelId": "candidate-b", "reason": "budget"}],
            "weights": {"price": 0.6, "speed": 0.2, "intelligence": 0.2},
            "winner": {"modelId": "candidate-a", "score": 0.9},
            "authorizationSignature": "must-not-send",
            "authorizationNonce": "must-not-send",
        },
    )


@pytest.mark.asyncio
async def test_local_observer_sends_allowlisted_decision_facts_and_actual_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = await _event()
    captured: list[dict[str, Any]] = []
    response = {
        "message": {
            "content": json.dumps(
                {
                    "findings": [
                        {
                            "code": "OBS-01",
                            "severity": "CAUTION",
                            "detail": "check score",
                            "eventIds": [event.event_id],
                        }
                    ]
                }
            )
        }
    }
    monkeypatch.setattr(
        "buyer_audit_api.core.observer.httpx.AsyncClient",
        lambda **kwargs: _Client(response=response, captured=captured, **kwargs),
    )
    observer = LocalQwenEvidenceObserver(
        base_url="http://127.0.0.1:11434", model="qwen3.5:4b", timeout_seconds=1
    )
    findings = await observer.observe(
        phase=ObserverPhase.DECISION, prompt="비공개 요청", events=(event,)
    )
    assert observer.model == "qwen3.5:4b"
    assert findings[0].event_ids == (event.event_id,)
    request = json.loads(captured[0]["messages"][1]["content"])
    wire_content = captured[0]["messages"][1]["content"]
    facts = request["events"][0]["facts"]
    assert facts["explanation"] == "deterministic winner explanation"
    assert facts["winner"]["modelId"] == "candidate-a"
    assert "authorizationSignature" not in facts
    assert "authorizationNonce" not in facts
    assert "비공개 요청" in wire_content
    assert "\\u" not in wire_content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            {
                "message": {
                    "content": (
                        '{"findings":[{"code":"X","severity":"RISK","detail":"x",'
                        '"eventIds":["invented"]}]}'
                    )
                }
            },
            ObserverFailureCode.UNKNOWN_EVENT_ID,
        ),
        (
            {
                "message": {
                    "content": (
                        '{"findings":[{"code":1,"severity":"RISK","detail":"x","eventIds":[]}]}'
                    )
                }
            },
            ObserverFailureCode.RESPONSE_SCHEMA_INVALID,
        ),
        (httpx.ConnectError("request secret must not surface"), ObserverFailureCode.HTTP),
    ],
)
async def test_local_observer_rejects_bad_or_unavailable_results_without_echoing_prompt(
    monkeypatch: pytest.MonkeyPatch, response: Any | Exception, expected: ObserverFailureCode
) -> None:
    event = await _event()
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "buyer_audit_api.core.observer.httpx.AsyncClient",
        lambda **kwargs: _Client(response=response, captured=captured, **kwargs),
    )
    observer = LocalQwenEvidenceObserver(
        base_url="http://localhost:11434", model="qwen3.5:4b", timeout_seconds=1
    )
    with pytest.raises(ObserverUnavailable) as error:
        await observer.observe(
            phase=ObserverPhase.DECISION,
            prompt="do not echo this private request",
            events=(event,),
        )
    assert error.value.code is expected
    assert "private request" not in str(error.value)
