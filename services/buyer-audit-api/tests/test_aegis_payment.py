"""The Evidence API side of the `aa-three-factor-v1` payment attempt.

These exercise the boundary the Provider Gateway and the payment execution module live
behind: one reservation per purchase, terms derived from evidence rather than from a
request body, a Facilitator answer that must belong to the decided chain and payer, and a
local composition that answers 404 for the superseded Phase 6 surfaces.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from buyer_audit_api.adapters.artificial_analysis import FixtureArtificialAnalysisSource
from buyer_audit_api.api.app import create_app, is_legacy_surface
from buyer_audit_api.composition import AppContainer
from buyer_audit_api.core.aegis_payment import AegisPaymentService, read_terms
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.errors import PaymentConflictError, PaymentEvidenceError
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.payment import PaymentService, WalletPolicy
from buyer_audit_api.core.purchase_service import PurchaseService
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
BUYER_WALLET = "0x00000000000000000000000000000000000b0001"
TOKEN_ADDRESS = "0x0000000000000000000000000000000000000000"
INTERNAL_HEADERS = {"Authorization": "Bearer test-internal-token"}
ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}
PROMPT = "x" * 100
#: The fixture OpenAI model at the shipped request size; see pricing-cases.json.
OPENAI_AMOUNT_UNITS = 619


@pytest.fixture
def runtime_container(container: AppContainer) -> AppContainer:
    """The runnable local composition: fixed owner, runtime catalog, no SIWE."""
    return replace(
        container,
        local_owner_address=LOCAL_OWNER,
        local_allowed_origins=("http://localhost:3000",),
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
                address=TOKEN_ADDRESS,
                chain_id=84532,
            ),
        ),
        aegis_evidence_reader=AegisEvidenceReader(repository=container.repository),
    )


async def _bind_wallet(container: AppContainer) -> None:
    await container.auth_service.bind_buyer_wallet(
        owner_address=LOCAL_OWNER, buyer_wallet_address=BUYER_WALLET
    )
    await PaymentService(
        repository=container.repository, clock=container.clock
    ).configure_wallet_policy(
        WalletPolicy(
            buyer_wallet_address=BUYER_WALLET,
            policy_date=container.clock.now().date().isoformat(),
            token=TOKEN_ADDRESS,
            per_transaction_limit_units=100_000,
            daily_limit_units=1_000_000,
        )
    )


def _decided(client: TestClient, *, budget_units: int = 50_000, **request: object) -> str:
    created = client.post(
        "/purchases",
        json={
            "domain": "ai_inference",
            "budget_units": budget_units,
            "policy": {"scoringPolicyVersion": "aa-three-factor-v1"},
            "request": {
                "requestSchema": "aegis-aa-v1",
                "prompt": PROMPT,
                "allowed_providers": ["openai"],
                **request,
            },
        },
    )
    assert created.status_code == 201, created.text
    purchase_id = str(created.json()["purchase_id"])
    decided = client.post(f"/purchases/{purchase_id}/decide")
    assert decided.status_code == 200, decided.text
    return purchase_id


def _settle_body(purchase_id: str, **overrides: object) -> dict[str, object]:
    return {
        "purchase_id": purchase_id,
        "facilitator_transaction": "x402mock:11112222333344445555666677778888",
        "facilitator_network": "eip155:84532",
        "facilitator_payer": BUYER_WALLET,
        **overrides,
    }


def test_a_reservation_is_derived_from_evidence_and_claimed_once(
    runtime_container: AppContainer,
) -> None:
    asyncio.run(_bind_wallet(runtime_container))
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decided(client)
        first = client.post(
            "/internal/evidence/aegis/payments/reserve",
            json={"purchase_id": purchase_id},
            headers=INTERNAL_HEADERS,
        )
        assert first.status_code == 200, first.text
        terms = first.json()["terms"]
        assert terms["amount_units"] == OPENAI_AMOUNT_UNITS
        assert terms["provider_model_id"] == "gpt-4.1-2025-04-14"
        assert terms["model_version"] == "2025-04-14"
        intent = first.json()["intent"]
        assert intent["state"] == "CLAIMED"
        assert intent["quote_id"] == terms["terms_binding_hash"]

        # A repeated reservation returns the same attempt instead of opening a second one.
        again = client.post(
            "/internal/evidence/aegis/payments/reserve",
            json={"purchase_id": purchase_id},
            headers=INTERNAL_HEADERS,
        )
        assert again.status_code == 200
        assert again.json()["intent"]["authorization_nonce"] == intent["authorization_nonce"]

        events = client.get(f"/purchases/{purchase_id}/events").json()
        claims = [item for item in events if item["type"] == EventType.PAYMENT_INTENT_CLAIMED]
        assert len(claims) == 1
        assert claims[0]["payload"]["termsBindingHash"] == terms["terms_binding_hash"]


def test_concurrent_reservations_of_one_purchase_produce_one_attempt(
    runtime_container: AppContainer,
) -> None:
    asyncio.run(_bind_wallet(runtime_container))
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decided(client)
        service = AegisPaymentService(
            repository=runtime_container.repository, clock=runtime_container.clock
        )

        async def race() -> list[object]:
            return list(
                await asyncio.gather(
                    *(service.reserve(purchase_id) for _ in range(6)),
                    return_exceptions=True,
                )
            )

        outcomes = asyncio.run(race())
        assert not [item for item in outcomes if isinstance(item, BaseException)], outcomes
        nonces = {
            intent.authorization_nonce for _, intent in outcomes  # type: ignore[misc]
        }
        assert len(nonces) == 1
        events = client.get(f"/purchases/{purchase_id}/events").json()
        assert (
            len([item for item in events if item["type"] == EventType.PAYMENT_INTENT_CLAIMED]) == 1
        )


def test_a_settlement_on_another_chain_is_refused(runtime_container: AppContainer) -> None:
    """A Facilitator success on a different network is not this purchase's settlement."""
    asyncio.run(_bind_wallet(runtime_container))
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decided(client)
        client.post(
            "/internal/evidence/aegis/payments/reserve",
            json={"purchase_id": purchase_id},
            headers=INTERNAL_HEADERS,
        )
        client.post(
            "/internal/evidence/payment-intents/authorize",
            json={
                "purchase_id": purchase_id,
                "authorization_hash": "0x" + "ab" * 32,
                "signature": "0x" + "cd" * 65,
            },
            headers=INTERNAL_HEADERS,
        )
        refused = client.post(
            "/internal/evidence/aegis/payments/settle",
            json=_settle_body(purchase_id, facilitator_network="eip155:1"),
            headers=INTERNAL_HEADERS,
        )
        assert refused.status_code == 409
        assert "eip155:84532" in refused.json()["detail"]
        events = client.get(f"/purchases/{purchase_id}/events").json()
        assert not [item for item in events if item["type"] == EventType.PAYMENT_SETTLED]


def test_a_caller_cannot_label_its_own_execution_mode(
    runtime_container: AppContainer,
) -> None:
    """Mock execution is recorded from server configuration, never from the request."""
    asyncio.run(_bind_wallet(runtime_container))
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decided(client)
        client.post(
            "/internal/evidence/aegis/payments/reserve",
            json={"purchase_id": purchase_id},
            headers=INTERNAL_HEADERS,
        )
        client.post(
            "/internal/evidence/payment-intents/authorize",
            json={
                "purchase_id": purchase_id,
                "authorization_hash": "0x" + "ab" * 32,
                "signature": "0x" + "cd" * 65,
            },
            headers=INTERNAL_HEADERS,
        )
        rejected = client.post(
            "/internal/evidence/aegis/payments/settle",
            json=_settle_body(purchase_id, execution_mode="live"),
            headers=INTERNAL_HEADERS,
        )
        assert rejected.status_code == 422

        accepted = client.post(
            "/internal/evidence/aegis/payments/settle",
            json=_settle_body(purchase_id),
            headers=INTERNAL_HEADERS,
        )
        assert accepted.status_code == 200, accepted.text
        events = client.get(f"/purchases/{purchase_id}/events").json()
        settled = next(item for item in events if item["type"] == EventType.PAYMENT_SETTLED)
        assert settled["payload"]["executionMode"] == "mock"
        assert settled["payload"]["verificationBasis"] == "facilitator_response"
        assert settled["payload"]["evidenceSource"] == "SYNTHETIC_LOCAL"
        assert "transactionHash" not in settled["payload"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("facilitator_transaction", "0x" + "ef" * 32),
        ("facilitator_payer", "0x00000000000000000000000000000000000bad01"),
    ],
)
def test_a_settlement_answer_that_claims_the_wrong_thing_is_refused(
    runtime_container: AppContainer, field: str, value: str
) -> None:
    asyncio.run(_bind_wallet(runtime_container))
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decided(client)
        client.post(
            "/internal/evidence/aegis/payments/reserve",
            json={"purchase_id": purchase_id},
            headers=INTERNAL_HEADERS,
        )
        client.post(
            "/internal/evidence/payment-intents/authorize",
            json={
                "purchase_id": purchase_id,
                "authorization_hash": "0x" + "ab" * 32,
                "signature": "0x" + "cd" * 65,
            },
            headers=INTERNAL_HEADERS,
        )
        refused = client.post(
            "/internal/evidence/aegis/payments/settle",
            json=_settle_body(purchase_id, **{field: value}),
            headers=INTERNAL_HEADERS,
        )
        assert refused.status_code == 409, refused.text


def test_a_settled_purchase_is_never_paid_or_failed_again(
    runtime_container: AppContainer,
) -> None:
    asyncio.run(_bind_wallet(runtime_container))
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decided(client)
        client.post(
            "/internal/evidence/aegis/payments/reserve",
            json={"purchase_id": purchase_id},
            headers=INTERNAL_HEADERS,
        )
        client.post(
            "/internal/evidence/payment-intents/authorize",
            json={
                "purchase_id": purchase_id,
                "authorization_hash": "0x" + "ab" * 32,
                "signature": "0x" + "cd" * 65,
            },
            headers=INTERNAL_HEADERS,
        )
        first = client.post(
            "/internal/evidence/aegis/payments/settle",
            json=_settle_body(purchase_id),
            headers=INTERNAL_HEADERS,
        )
        assert first.status_code == 200
        # A delivery failure retries the settlement record, which must be idempotent.
        replayed = client.post(
            "/internal/evidence/aegis/payments/settle",
            json=_settle_body(purchase_id),
            headers=INTERNAL_HEADERS,
        )
        assert replayed.status_code == 200
        conflicting = client.post(
            "/internal/evidence/aegis/payments/settle",
            json=_settle_body(purchase_id, facilitator_transaction="x402mock:ffff"),
            headers=INTERNAL_HEADERS,
        )
        assert conflicting.status_code == 409
        failed = client.post(
            "/internal/evidence/aegis/payments/fail",
            json={"purchase_id": purchase_id, "reason": "late refusal"},
            headers=INTERNAL_HEADERS,
        )
        assert failed.status_code == 409
        events = client.get(f"/purchases/{purchase_id}/events").json()
        assert len([item for item in events if item["type"] == EventType.PAYMENT_SETTLED]) == 1


def test_delivery_requires_a_settled_payment_and_the_decided_model(
    runtime_container: AppContainer,
) -> None:
    asyncio.run(_bind_wallet(runtime_container))
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decided(client)
        delivery = {
            "provider_id": "openai",
            "provider_model_id": "gpt-4.1-2025-04-14",
            "model_version": "2025-04-14",
            "response_id": "sha256:" + "1" * 64,
            "response_hash": "sha256:" + "2" * 64,
            "observed_execution_ms": 3,
        }
        unpaid = client.post(
            f"/internal/evidence/aegis/purchases/{purchase_id}/delivery",
            json=delivery,
            headers=INTERNAL_HEADERS,
        )
        assert unpaid.status_code == 409

        client.post(
            "/internal/evidence/aegis/payments/reserve",
            json={"purchase_id": purchase_id},
            headers=INTERNAL_HEADERS,
        )
        client.post(
            "/internal/evidence/payment-intents/authorize",
            json={
                "purchase_id": purchase_id,
                "authorization_hash": "0x" + "ab" * 32,
                "signature": "0x" + "cd" * 65,
            },
            headers=INTERNAL_HEADERS,
        )
        client.post(
            "/internal/evidence/aegis/payments/settle",
            json=_settle_body(purchase_id),
            headers=INTERNAL_HEADERS,
        )
        wrong_model = client.post(
            f"/internal/evidence/aegis/purchases/{purchase_id}/delivery",
            json={**delivery, "provider_model_id": "gpt-4.1"},
            headers=INTERNAL_HEADERS,
        )
        assert wrong_model.status_code == 409
        recorded = client.post(
            f"/internal/evidence/aegis/purchases/{purchase_id}/delivery",
            json=delivery,
            headers=INTERNAL_HEADERS,
        )
        assert recorded.status_code == 200, recorded.text
        assert recorded.json()["payload"]["executionMode"] == "mock"


def test_a_reservation_needs_a_budget_that_covers_the_decided_amount(
    runtime_container: AppContainer,
) -> None:
    asyncio.run(_bind_wallet(runtime_container))
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decided(client, budget_units=OPENAI_AMOUNT_UNITS)
        allowed = client.post(
            "/internal/evidence/aegis/payments/reserve",
            json={"purchase_id": purchase_id},
            headers=INTERNAL_HEADERS,
        )
        assert allowed.status_code == 200, allowed.text


def test_terms_reject_a_decision_whose_amount_was_edited_after_capture(
    runtime_container: AppContainer,
) -> None:
    """The terms binding is recomputed, so an edited winner cannot authorize a payment."""
    asyncio.run(_bind_wallet(runtime_container))
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decided(client)
    events = asyncio.run(runtime_container.repository.list_events(purchase_id))
    tampered = []
    for event in events:
        if event.type != EventType.DECIDED:
            tampered.append(event)
            continue
        payload = dict(event.payload)
        payload["amountUnits"] = 1
        payload["winner"] = {**payload["winner"], "amountUnits": 1}
        payload["candidates"] = [
            {**item, "amountUnits": 1}
            if item["candidateKey"] == payload["winner"]["candidateKey"]
            else item
            for item in payload["candidates"]
        ]
        tampered.append(replace(event, payload=payload))
    with pytest.raises(PaymentEvidenceError, match="terms binding"):
        read_terms(tampered)


def test_terms_reject_a_purchase_decided_under_the_superseded_policy() -> None:
    with pytest.raises(PaymentEvidenceError):
        read_terms([])


def test_the_local_composition_has_no_siwe_and_no_phase6_surface(
    runtime_container: AppContainer,
) -> None:
    with TestClient(create_app(runtime_container)) as client:
        siwe = client.post("/auth/siwe/challenge", json={"owner_address": BUYER_WALLET})
        assert siwe.status_code == 404
        assert client.get("/auth/me").json()["owner_address"] == LOCAL_OWNER
        for path in (
            "/internal/evidence/seller-executions/claim",
            "/internal/evidence/payment-intents/settle",
            "/internal/evidence/payment-intents/reconcile-no-transfer",
            "/internal/evidence/reputation-outbox/claim",
        ):
            assert client.post(path, json={}, headers=INTERNAL_HEADERS).status_code == 404


def test_the_legacy_composition_still_serves_its_historical_surfaces(
    container: AppContainer,
) -> None:
    """With no local owner configured, the PBLC history composition is untouched."""
    with TestClient(create_app(container)) as client:
        challenge = client.post(
            "/auth/siwe/challenge",
            json={"owner_address": "0x0000000000000000000000000000000000000001"},
        )
        assert challenge.status_code == 200
        # Present, and protected, rather than removed.
        assert client.get("/internal/evidence/seller-executions/anything").status_code == 401
        assert client.get("/internal/evidence/purchases/anything/payment-view").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "http://evil.example"},
        {"sec-fetch-site": "cross-site"},
        {"host": "attacker.example"},
    ],
)
def test_a_cross_origin_browser_request_cannot_reach_the_local_owner(
    runtime_container: AppContainer, headers: dict[str, str]
) -> None:
    with TestClient(create_app(runtime_container)) as client:
        response = client.post(
            "/purchases",
            json={"domain": "ai_inference", "request": {}, "budget_units": 1},
            headers=headers,
        )
        assert response.status_code == 403


def test_the_allowed_local_origin_is_still_accepted(runtime_container: AppContainer) -> None:
    with TestClient(create_app(runtime_container)) as client:
        response = client.get(
            "/purchases",
            headers={"origin": "http://localhost:3000", "sec-fetch-site": "same-origin"},
        )
        assert response.status_code == 200


def test_the_legacy_surface_predicate_keeps_the_new_runtime_routes() -> None:
    assert is_legacy_surface("/internal/evidence/purchases/abc/payment-view")
    assert is_legacy_surface("/internal/evidence/payment-intents/claim")
    assert not is_legacy_surface("/internal/evidence/payment-intents/authorize")
    assert not is_legacy_surface("/internal/evidence/payment-intents/reconciliation")
    assert not is_legacy_surface("/internal/evidence/aegis/payments/reserve")
    assert not is_legacy_surface("/internal/evidence/aegis/purchases/abc/delivery")


def test_the_service_refuses_an_unknown_execution_mode() -> None:
    with pytest.raises(ValueError, match="execution mode"):
        AegisPaymentService(repository=None, clock=None, execution_mode="pretend")  # type: ignore[arg-type]


def test_a_conflicting_reservation_binding_is_refused(
    runtime_container: AppContainer,
) -> None:
    asyncio.run(_bind_wallet(runtime_container))
    with TestClient(create_app(runtime_container)) as client:
        purchase_id = _decided(client)
        client.post(
            "/internal/evidence/aegis/payments/reserve",
            json={"purchase_id": purchase_id},
            headers=INTERNAL_HEADERS,
        )

    async def tamper_and_reserve() -> None:
        service = AegisPaymentService(
            repository=runtime_container.repository, clock=runtime_container.clock
        )
        intent = await runtime_container.repository.get_payment_intent(purchase_id)
        assert intent is not None
        runtime_container.repository._payment_intents[purchase_id] = replace(  # noqa: SLF001
            intent, amount_units=intent.amount_units + 1
        )
        with pytest.raises(PaymentConflictError):
            await service.reserve(purchase_id)

    asyncio.run(tamper_and_reserve())
