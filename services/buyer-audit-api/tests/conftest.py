from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from buyer_audit_api.adapters.auth.siwe import PythonSiweVerifier
from buyer_audit_api.adapters.crypto.local_aes_gcm import LocalEnvelopeCipher
from buyer_audit_api.adapters.repositories.memory import InMemoryEvidenceRepository
from buyer_audit_api.composition import AppContainer
from buyer_audit_api.core.auth_service import AuthService
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.models import JsonObject
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.core.session import SessionCodec


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
    cipher = LocalEnvelopeCipher(master_key=b"p" * 32)
    auth_service = AuthService(
        repository=repository,
        verifier=PythonSiweVerifier(),
        session_codec=SessionCodec(b"s" * 32),
        clock=clock,
        domain="localhost",
        uri="http://localhost:3000",
        chain_id=84532,
    )
    purchase_service = PurchaseService(
        repository=repository,
        cipher=cipher,
        domains=DomainRegistry([FakeDomain()]),
        clock=clock,
    )
    return AppContainer(
        repository=repository,
        cipher=cipher,
        clock=clock,
        auth_service=auth_service,
        purchase_service=purchase_service,
        cookie_secure=False,
    )
