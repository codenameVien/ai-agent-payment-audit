from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.core.errors import EvidenceIntegrityError, EvidenceTransitionError
from buyer_audit_api.core.events import create_event, verify_event_chain
from buyer_audit_api.core.hashing import build_event_hash, sha256_json
from buyer_audit_api.core.models import EventType


def test_canonical_hash_ignores_object_key_order() -> None:
    assert sha256_json({"a": 1, "b": 2}) == sha256_json({"b": 2, "a": 1})


def test_event_hash_uses_bson_millisecond_timestamp() -> None:
    event = create_event(
        purchase_id="purchase-bson-time",
        sequence=1,
        event_type=EventType.REQUESTED,
        occurred_at=datetime(2026, 9, 2, 12, 40, 21, 123456, tzinfo=UTC),
        actor={"id": "tester", "type": "test"},
        payload={"value": "round-trip"},
        previous_event_hash=None,
        evidence_refs=(),
    )

    assert event.occurred_at.microsecond == 123000
    verify_event_chain([event])


@pytest.mark.asyncio
async def test_append_is_serialized_and_hash_chained(clock) -> None:
    repository = InMemoryEvidenceRepository()

    async def append(index: int):
        return await repository.append_event(
            purchase_id="purchase-1",
            event_type=EventType.CORRECTION_RECORDED,
            occurred_at=clock.now(),
            actor={"id": "tester", "type": "test"},
            payload={"index": index},
        )

    await asyncio.gather(*(append(index) for index in range(20)))
    events = await repository.list_events("purchase-1")

    assert [event.sequence for event in events] == list(range(1, 21))
    assert events[0].previous_event_hash is None
    for previous, current in zip(events, events[1:], strict=False):
        assert current.previous_event_hash == previous.event_hash
        assert current.event_hash == build_event_hash(
            purchase_id=current.purchase_id,
            sequence=current.sequence,
            event_type=current.type,
            occurred_at=current.occurred_at,
            actor=current.actor,
            previous_event_hash=current.previous_event_hash,
            payload_hash=current.payload_hash,
            evidence_refs=current.evidence_refs,
        )
    head = await repository.get_event_head("purchase-1")
    assert head is not None
    verify_event_chain(
        events,
        expected_event_count=head.event_count,
        expected_head_event_hash=head.head_event_hash,
    )


@pytest.mark.asyncio
async def test_payload_mutation_changes_hash(clock) -> None:
    repository = InMemoryEvidenceRepository()
    event = await repository.append_event(
        purchase_id="purchase-1",
        event_type=EventType.REQUESTED,
        occurred_at=clock.now(),
        actor={"id": "tester", "type": "test"},
        payload={"budgetUnits": 10},
    )

    assert sha256_json({"budgetUnits": 11}) != event.payload_hash
    with pytest.raises(EvidenceIntegrityError, match="payload hash"):
        verify_event_chain([replace(event, payload={"budgetUnits": 11})])


@pytest.mark.asyncio
async def test_evidence_reference_mutation_breaks_event_hash(clock) -> None:
    repository = InMemoryEvidenceRepository()
    event = await repository.append_event(
        purchase_id="purchase-1",
        event_type=EventType.DECIDED,
        occurred_at=clock.now(),
        actor={"id": "buyer", "type": "agent"},
        payload={"winner": "quote-1"},
        evidence_refs=("quote-1", "snapshot-1"),
    )

    with pytest.raises(EvidenceIntegrityError, match="event hash"):
        verify_event_chain([replace(event, evidence_refs=("quote-2", "snapshot-1"))])


@pytest.mark.asyncio
async def test_terminal_event_deletion_is_detected_by_independent_head(clock) -> None:
    repository = InMemoryEvidenceRepository()
    for index in range(2):
        await repository.append_event(
            purchase_id="purchase-1",
            event_type=EventType.CORRECTION_RECORDED,
            occurred_at=clock.now(),
            actor={"id": "tester", "type": "test"},
            payload={"index": index},
        )
    events = await repository.list_events("purchase-1")
    head = await repository.get_event_head("purchase-1")
    assert head is not None

    with pytest.raises(EvidenceIntegrityError, match="event count"):
        verify_event_chain(
            events[:-1],
            expected_event_count=head.event_count,
            expected_head_event_hash=head.head_event_hash,
        )


@pytest.mark.asyncio
async def test_singleton_payment_events_reject_duplicates(clock) -> None:
    repository = InMemoryEvidenceRepository()
    for event_type in (EventType.PAYMENT_INTENT_CLAIMED, EventType.PAYMENT_SETTLED):
        await repository.append_event(
            purchase_id="purchase-1",
            event_type=event_type,
            occurred_at=clock.now(),
            actor={"id": "gateway", "type": "service"},
            payload={},
        )
        with pytest.raises(EvidenceTransitionError, match="duplicate singleton"):
            await repository.append_event(
                purchase_id="purchase-1",
                event_type=event_type,
                occurred_at=clock.now(),
                actor={"id": "gateway", "type": "service"},
                payload={},
            )


@pytest.mark.asyncio
async def test_expected_head_compare_and_swap_rejects_stale_append(clock) -> None:
    repository = InMemoryEvidenceRepository()
    requested = await repository.append_event(
        purchase_id="purchase-cas",
        event_type=EventType.REQUESTED,
        occurred_at=clock.now(),
        actor={"id": "user", "type": "user"},
        payload={},
    )
    await repository.append_event(
        purchase_id="purchase-cas",
        event_type=EventType.QUOTED,
        occurred_at=clock.now(),
        actor={"id": "buyer", "type": "agent"},
        payload={},
        expected_event_count=1,
        expected_head_event_hash=requested.event_hash,
    )

    with pytest.raises(EvidenceTransitionError, match="head changed"):
        await repository.append_event(
            purchase_id="purchase-cas",
            event_type=EventType.QUOTED,
            occurred_at=clock.now(),
            actor={"id": "buyer", "type": "agent"},
            payload={},
            expected_event_count=1,
            expected_head_event_hash=requested.event_hash,
        )

    assert [event.type for event in await repository.list_events("purchase-cas")] == [
        EventType.REQUESTED,
        EventType.QUOTED,
    ]
