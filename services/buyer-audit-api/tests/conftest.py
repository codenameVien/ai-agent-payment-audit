from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from fakes.memory_repository import InMemoryEvidenceRepository

from buyer_audit_api.composition import AppContainer
from buyer_audit_api.core.auth_service import AuthService
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.models import JsonObject
from buyer_audit_api.core.purchase_service import PurchaseService

#: The single-user demo owner, identical to the server default in `Settings`.
LOCAL_OWNER = "0x00000000000000000000000000000000000a6e15"


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 2, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class FakeDomain:
    @property
    def domain_id(self) -> str:
        return "fake"

    def normalize_request(self, payload: Mapping[str, Any]) -> JsonObject:
        return {"normalized": str(payload["value"]).strip().lower()}


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def repository() -> InMemoryEvidenceRepository:
    return InMemoryEvidenceRepository()


@pytest.fixture
def container(
    repository: InMemoryEvidenceRepository,
    clock: MutableClock,
) -> AppContainer:
    auth_service = AuthService(repository=repository, clock=clock)
    purchase_service = PurchaseService(
        repository=repository,
        domains=DomainRegistry([FakeDomain()]),
        clock=clock,
    )
    return AppContainer(
        repository=repository,
        clock=clock,
        auth_service=auth_service,
        purchase_service=purchase_service,
        local_owner_address=LOCAL_OWNER,
        # Every app enforces the local Host boundary; TestClient sends Host: testserver.
        local_allowed_hosts=("localhost", "127.0.0.1", "testserver"),
    )
