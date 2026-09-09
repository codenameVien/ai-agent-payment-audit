from __future__ import annotations

from dataclasses import replace

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.api.app import create_app
from buyer_audit_api.composition import AppContainer
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.domains.ai_inference.aa_catalog import (
    FIXTURE_CATALOG_PATH,
    FIXTURE_PAGE_PATHS,
    load_model_catalog,
)
from buyer_audit_api.domains.ai_inference.aa_models import TokenIdentity
from buyer_audit_api.domains.ai_inference.aa_workflow import (
    AegisDecisionWorkflow,
    AegisEvidenceReader,
)
from buyer_audit_api.domains.ai_inference.module import AiInferenceDomainModule

INTERNAL_HEADERS = {"Authorization": "Bearer test-internal-token"}
PROMPT = "x" * 100


def authenticate(client: TestClient, account: Account) -> None:
    challenge = client.post(
        "/auth/siwe/challenge", json={"owner_address": account.address}
    )
    message = challenge.json()["message"]
    signature = account.sign_message(encode_defunct(text=message)).signature.hex()
    assert (
        client.post(
            "/auth/siwe/verify", json={"message": message, "signature": signature}
        ).status_code
        == 204
    )


@pytest.fixture
def aegis_container(container: AppContainer) -> AppContainer:
    """The shared container plus the ai_inference domain and the new decision workflow."""
    return replace(
        container,
        purchase_service=PurchaseService(
            repository=container.repository,
            cipher=container.cipher,
            domains=DomainRegistry([AiInferenceDomainModule(default_max_output_tokens=1024)]),
            clock=container.clock,
        ),
        aegis_workflow=AegisDecisionWorkflow(
            repository=container.repository,
            clock=container.clock,
            catalog=load_model_catalog(FIXTURE_CATALOG_PATH),
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


def _create(client: TestClient, *, budget_units: int = 5_000, **request: object) -> str:
    response = client.post(
        "/purchases",
        json={
            "domain": "ai_inference",
            "budget_units": budget_units,
            "policy": {"scoringPolicyVersion": "aa-three-factor-v1"},
            "request": {
                "requestSchema": "aegis-aa-v1",
                "prompt": PROMPT,
                **request,
            },
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["purchase_id"])


def test_request_to_decision_round_trip_over_http(aegis_container: AppContainer) -> None:
    with TestClient(create_app(aegis_container)) as client:
        authenticate(client, Account.create())
        purchase_id = _create(client)

        decided = client.post(f"/purchases/{purchase_id}/decide")
        assert decided.status_code == 200, decided.text
        assert decided.json()["type"] == EventType.DECIDED.value

        events = client.get(f"/purchases/{purchase_id}/events").json()
        assert [item["type"] for item in events] == [
            EventType.REQUESTED.value,
            EventType.AA_SNAPSHOT_RECORDED.value,
            EventType.DECIDED.value,
        ]


def test_repeating_the_decision_returns_the_same_recorded_event(
    aegis_container: AppContainer,
) -> None:
    with TestClient(create_app(aegis_container)) as client:
        authenticate(client, Account.create())
        purchase_id = _create(client)
        first = client.post(f"/purchases/{purchase_id}/decide").json()
        second = client.post(f"/purchases/{purchase_id}/decide").json()
        assert first["event_hash"] == second["event_hash"]


def test_a_request_with_no_affordable_model_is_refused_without_a_decision(
    aegis_container: AppContainer,
) -> None:
    with TestClient(create_app(aegis_container)) as client:
        authenticate(client, Account.create())
        purchase_id = _create(client, budget_units=10)
        refused = client.post(f"/purchases/{purchase_id}/decide")
        assert refused.status_code == 409
        assert "no_eligible_candidate" in refused.json()["detail"]
        events = client.get(f"/purchases/{purchase_id}/events").json()
        assert [item["type"] for item in events] == [
            EventType.REQUESTED.value,
            EventType.AA_SNAPSHOT_RECORDED.value,
        ]


def test_a_superseded_priority_name_is_refused_by_the_new_request_api(
    aegis_container: AppContainer,
) -> None:
    with TestClient(create_app(aegis_container)) as client:
        authenticate(client, Account.create())
        response = client.post(
            "/purchases",
            json={
                "domain": "ai_inference",
                "budget_units": 5_000,
                "policy": {},
                "request": {
                    "requestSchema": "aegis-aa-v1",
                    "prompt": PROMPT,
                    "priority": "balanced",
                },
            },
        )
        assert response.status_code == 422


def test_internal_decision_evidence_is_protected_and_complete(
    aegis_container: AppContainer,
) -> None:
    with TestClient(create_app(aegis_container)) as client:
        authenticate(client, Account.create())
        purchase_id = _create(client)
        client.post(f"/purchases/{purchase_id}/decide")

        assert (
            client.get(f"/internal/purchases/{purchase_id}/aegis-decision").status_code
            == 401
        )
        evidence = client.get(
            f"/internal/purchases/{purchase_id}/aegis-decision", headers=INTERNAL_HEADERS
        )
        assert evidence.status_code == 200, evidence.text
        body = evidence.json()
        assert body["decision"]["scoringPolicyVersion"] == "aa-three-factor-v1"
        # Default weights select the faster/high-intelligence Google fixture.
        assert body["decision"]["amountUnits"] == 2_568
        assert body["decision"]["snapshotHash"] == body["snapshot_hash"]
        assert body["snapshot"]["mode"] == "fixture"
        # The gateway can recompute the amount from the preserved AA source values.
        assert body["snapshot"]["models"][0]["inputPricePerMillion"] == "0.4"


def test_internal_decision_evidence_is_absent_before_a_decision(
    aegis_container: AppContainer,
) -> None:
    with TestClient(create_app(aegis_container)) as client:
        authenticate(client, Account.create())
        purchase_id = _create(client)
        missing = client.get(
            f"/internal/purchases/{purchase_id}/aegis-decision", headers=INTERNAL_HEADERS
        )
        assert missing.status_code == 404
