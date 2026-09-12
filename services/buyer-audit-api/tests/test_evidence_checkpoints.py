from __future__ import annotations

import asyncio
from dataclasses import replace

from fastapi.testclient import TestClient

from buyer_audit_api.api.app import create_app
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.observer import ObserverFinding, validate_findings

HEADERS = {"Authorization": "Bearer test-internal-token"}


def test_observer_rejects_invented_event_ids(repository, clock) -> None:
    async def exercise() -> None:
        event = await repository.append_event(
            purchase_id="checkpoint-1",
            event_type=EventType.REQUESTED,
            occurred_at=clock.now(),
            actor={"id": "owner", "type": "user"},
            payload={},
        )
        assert validate_findings(
            (ObserverFinding("OBS-OK", "CAUTION", "ok", (event.event_id,)),), (event,)
        )
        try:
            validate_findings(
                (ObserverFinding("OBS-BAD", "RISK", "bad", ("invented-event",)),), (event,)
            )
        except ValueError as exc:
            assert "unknown event id" in str(exc)
        else:
            raise AssertionError("invented observer event ID was accepted")

    asyncio.run(exercise())


def test_checkpoint_phases_are_stable_idempotent_and_ordered(container, repository, clock) -> None:
    async def seed() -> None:
        requested = await repository.append_event(
            purchase_id="checkpoint-2",
            event_type=EventType.REQUESTED,
            occurred_at=clock.now(),
            actor={"id": "owner", "type": "user"},
            payload={},
        )
        decided = await repository.append_event(
            purchase_id="checkpoint-2",
            event_type=EventType.DECIDED,
            occurred_at=clock.now(),
            actor={"id": "rules", "type": "service"},
            payload={},
            expected_event_count=1,
            expected_head_event_hash=requested.event_hash,
        )
        observed = await repository.append_event(
            purchase_id="checkpoint-2",
            event_type=EventType.OBSERVER_COMPLETED,
            occurred_at=clock.now(),
            actor={"id": "observer", "type": "service"},
            payload={"phase": "decision", "mode": "mock", "findings": []},
            expected_event_count=2,
            expected_head_event_hash=decided.event_hash,
        )
        await repository.append_event(
            purchase_id="checkpoint-2",
            event_type=EventType.CHECKPOINT_TARGET_SELECTED,
            occurred_at=clock.now(),
            actor={"id": "coordinator", "type": "service"},
            payload={
                "phase": "decision",
                "eventCount": observed.sequence,
                "headEventHash": observed.event_hash,
            },
            expected_event_count=3,
            expected_head_event_hash=observed.event_hash,
        )

    asyncio.run(seed())
    configured = replace(container, observer_mode="mock", checkpoint_mode="mock")
    with TestClient(create_app(configured)) as client:
        decision_path = "/internal/evidence/purchases/checkpoint-2/checkpoints/decision"
        audit_path = "/internal/evidence/purchases/checkpoint-2/checkpoints/audit"
        decision = client.get(decision_path, headers=HEADERS)
        assert decision.status_code == 200
        target = decision.json()
        assert target["record"] is None
        assert client.get(decision_path, headers=HEADERS).json() == target
        # Audit phase cannot be selected before deterministic audit completion.
        assert client.get(audit_path, headers=HEADERS).status_code == 409

        recorded = client.post(
            decision_path,
            headers=HEADERS,
            json={
                "mode": "mock",
                "eventCount": target["eventCount"],
                "headEventHash": target["headEventHash"],
            },
        )
        assert recorded.status_code == 200
        assert recorded.json()["record"]["mode"] == "mock"
        assert (
            client.post(
                decision_path,
                headers=HEADERS,
                json={
                    "mode": "mock",
                    "eventCount": target["eventCount"],
                    "headEventHash": target["headEventHash"],
                },
            ).status_code
            == 200
        )
        # A retry cannot change a frozen target or its proof mode.
        assert (
            client.post(
                decision_path,
                headers=HEADERS,
                json={
                    "mode": "live",
                    "eventCount": target["eventCount"],
                    "headEventHash": target["headEventHash"],
                },
            ).status_code
            == 409
        )

        async def complete_audit_phase() -> None:
            events = await repository.list_events("checkpoint-2")
            audited = await repository.append_event(
                purchase_id="checkpoint-2",
                event_type=EventType.AUDITED,
                occurred_at=clock.now(),
                actor={"id": "rules", "type": "service"},
                payload={},
                expected_event_count=len(events),
                expected_head_event_hash=events[-1].event_hash,
            )
            observed = await repository.append_event(
                purchase_id="checkpoint-2",
                event_type=EventType.OBSERVER_COMPLETED,
                occurred_at=clock.now(),
                actor={"id": "observer", "type": "service"},
                payload={"phase": "audit", "mode": "mock", "findings": []},
                expected_event_count=audited.sequence,
                expected_head_event_hash=audited.event_hash,
            )
            await repository.append_event(
                purchase_id="checkpoint-2",
                event_type=EventType.CHECKPOINT_TARGET_SELECTED,
                occurred_at=clock.now(),
                actor={"id": "coordinator", "type": "service"},
                payload={
                    "phase": "audit",
                    "eventCount": observed.sequence,
                    "headEventHash": observed.event_hash,
                },
                expected_event_count=observed.sequence,
                expected_head_event_hash=observed.event_hash,
            )

        asyncio.run(complete_audit_phase())
        audit = client.get(audit_path, headers=HEADERS)
        assert audit.status_code == 200
        assert audit.json()["eventCount"] > target["eventCount"]
