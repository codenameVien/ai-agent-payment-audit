from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from buyer_audit_api.api.app import create_app
from buyer_audit_api.core.domain_registry import DomainRegistry
from buyer_audit_api.core.models import EventType
from buyer_audit_api.core.payment import WalletPolicy
from buyer_audit_api.core.purchase_service import PurchaseService
from buyer_audit_api.domains.ai_inference.module import AiInferenceDomainModule

TOKEN = "0x0000000000000000000000000000000000000003"
SELLER = "0x0000000000000000000000000000000000000004"
CONTRACT = "0x0000000000000000000000000000000000000005"
INTERNAL_HEADERS = {"Authorization": "Bearer test-internal-token"}
ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def sign(account, message: str) -> str:
    return account.sign_message(encode_defunct(text=message)).signature.hex()


def authenticate(client: TestClient, account) -> None:
    challenge = client.post(
        "/auth/siwe/challenge",
        json={"owner_address": account.address},
    )
    assert challenge.status_code == 200
    message = challenge.json()["message"]
    verified = client.post(
        "/auth/siwe/verify",
        json={"message": message, "signature": sign(account, message)},
    )
    assert verified.status_code == 204
    assert "HttpOnly" in verified.headers["set-cookie"]
    assert "SameSite=lax" in verified.headers["set-cookie"]


def test_authenticated_fake_domain_purchase_round_trip(container) -> None:
    app = create_app(container)
    account = Account.create()

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        authenticate(client, account)
        assert client.get("/auth/me").json() == {
            "owner_address": account.address.lower(),
            "buyer_wallet_address": None,
        }
        buyer = Account.create()
        assert (
            client.put(
                "/auth/buyer-wallet",
                json={"buyer_wallet_address": buyer.address},
            ).status_code
            == 404
        )
        assert (
            client.put(
                "/internal/auth/buyer-wallet",
                json={"owner_address": account.address, "buyer_wallet_address": buyer.address},
            ).status_code
            == 401
        )
        assert (
            client.put(
                "/internal/auth/buyer-wallet",
                headers=INTERNAL_HEADERS,
                json={"owner_address": account.address, "buyer_wallet_address": buyer.address},
            ).status_code
            == 401
        )
        binding = client.put(
            "/internal/auth/buyer-wallet",
            headers=ADMIN_HEADERS,
            json={"owner_address": account.address, "buyer_wallet_address": buyer.address},
        )
        assert binding.status_code == 200
        assert client.get("/auth/me").json()["buyer_wallet_address"] == buyer.address.lower()

        created = client.post(
            "/purchases",
            json={
                "domain": "fake",
                "request": {"value": "  EXAMPLE ", "prompt": "private prompt"},
                "budget_units": 7,
                "policy": {"priority": "quality"},
            },
        )
        assert created.status_code == 201
        body = created.json()

        events = client.get(f"/purchases/{body['purchase_id']}/events")
        assert events.status_code == 200
        assert events.json()[0]["type"] == "REQUESTED"
        assert "private prompt" not in str(events.json())

        sensitive = client.get(
            f"/purchases/{body['purchase_id']}/sensitive/{body['sensitive_payload_id']}"
        )
        assert sensitive.status_code == 200
        assert sensitive.json()["value"]["prompt"] == "private prompt"
        events_after_access = client.get(f"/purchases/{body['purchase_id']}/events").json()
        assert events_after_access[-1]["type"] == "SENSITIVE_PAYLOAD_ACCESSED"

        client.cookies.clear()
        authenticate(client, Account.create())
        assert client.get(f"/purchases/{body['purchase_id']}/events").status_code == 404
        assert (
            client.get(
                f"/purchases/{body['purchase_id']}/sensitive/{body['sensitive_payload_id']}"
            ).status_code
            == 404
        )


def test_unauthenticated_and_unknown_domain_are_rejected(container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        assert (
            client.post(
                "/purchases",
                json={"domain": "fake", "request": {}, "budget_units": 0},
            ).status_code
            == 401
        )
        authenticate(client, Account.create())
        response = client.post(
            "/purchases",
            json={"domain": "missing", "request": {}, "budget_units": 0},
        )
        assert response.status_code == 422


def test_purchase_without_budget_uses_bound_wallet_transaction_limit(container) -> None:
    app = create_app(container)
    owner = Account.create()
    buyer = Account.create()
    with TestClient(app) as client:
        authenticate(client, owner)
        assert (
            client.put(
                "/internal/auth/buyer-wallet",
                headers=ADMIN_HEADERS,
                json={"owner_address": owner.address, "buyer_wallet_address": buyer.address},
            ).status_code
            == 200
        )
        asyncio.run(
            container.repository.put_wallet_policy(
                WalletPolicy(
                    buyer_wallet_address=buyer.address.lower(),
                    policy_date=container.clock.now().date().isoformat(),
                    token=TOKEN,
                    per_transaction_limit_units=321_000,
                    daily_limit_units=1_000_000,
                )
            )
        )
        created = client.post(
            "/purchases",
            json={"domain": "fake", "request": {"value": "optional budget"}},
        )
        assert created.status_code == 201
        events = client.get(f"/purchases/{created.json()['purchase_id']}/events").json()
        assert events[0]["payload"]["budgetUnits"] == 321_000


def test_run_purchase_keeps_gateway_credential_server_side_and_forwards_private_prompt(
    container,
) -> None:
    class FakeDecisionWorkflow:
        async def decide(self, *, purchase_id, normalized_request, budget_units):
            del normalized_request, budget_units
            quoted = await container.repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.QUOTED,
                occurred_at=container.clock.now(),
                actor={"id": "buyer-orchestrator", "type": "service"},
                payload={
                    "benchmarkSnapshots": [],
                    "signedQuotes": [
                        {
                            "quote_id": "quote-run",
                            "purchase_id": purchase_id,
                            "seller_agent_id": "seller-gemini",
                            "provider_id": "gemini",
                            "model_id": "gemini-test",
                            "model_version": "v1",
                            "amount_units": 100_000,
                            "token": TOKEN,
                            "pay_to": SELLER,
                            "available": True,
                            "expires_at": (container.clock.now() + timedelta(hours=1)).isoformat(),
                            "signer_address": SELLER,
                            "chain_id": 84532,
                            "verifying_contract": CONTRACT,
                        }
                    ],
                    "quoteIdentityEvidence": [
                        {
                            "quoteId": "quote-run",
                            "erc8004AgentId": "1",
                            "identityVerified": True,
                        }
                    ],
                },
            )
            await container.repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.DECIDED,
                occurred_at=container.clock.now(),
                actor={"id": "buyer-agent", "type": "agent"},
                payload={"winner": {"quote_id": "quote-run"}},
                expected_event_count=quoted.sequence,
                expected_head_event_hash=quoted.event_hash,
            )

    class FakeGateway:
        calls = []

        async def execute(self, *, purchase_id, resource_body):
            self.calls.append((purchase_id, resource_body))
            return {"purchase_id": purchase_id, "state": "CLAIMED"}

    container.purchase_service = PurchaseService(
        repository=container.repository,
        cipher=container.cipher,
        domains=DomainRegistry([AiInferenceDomainModule()]),
        clock=container.clock,
    )
    container.ai_inference_workflow = FakeDecisionWorkflow()
    gateway = FakeGateway()
    container.commerce_gateway = gateway
    app = create_app(container)
    owner = Account.create()
    with TestClient(app) as client:
        authenticate(client, owner)
        created = client.post(
            "/purchases",
            json={
                "domain": "ai_inference",
                "request": {"prompt": "private run prompt", "priority": "balanced"},
                "budget_units": 250_000,
                "policy": {},
            },
        )
        assert created.status_code == 201
        purchase_id = created.json()["purchase_id"]
        response = client.post(f"/purchases/{purchase_id}/run")
        assert response.status_code == 200
        assert response.json()["purchase_id"] == purchase_id
        assert gateway.calls == [(purchase_id, {"prompt": "private run prompt"})]
        public_events = client.get(f"/purchases/{purchase_id}/events").json()
        assert "private run prompt" not in str(public_events)


def test_event_api_rejects_a_deleted_terminal_event(container) -> None:
    app = create_app(container)
    account = Account.create()
    with TestClient(app) as client:
        authenticate(client, account)
        created = client.post(
            "/purchases",
            json={
                "domain": "fake",
                "request": {"value": "integrity"},
                "budget_units": 1,
            },
        )
        assert created.status_code == 201
        purchase_id = created.json()["purchase_id"]

        stored_events = container.repository._events[purchase_id]  # type: ignore[attr-defined]
        stored_events.pop()

        response = client.get(f"/purchases/{purchase_id}/events")
        assert response.status_code == 409
        assert response.json()["detail"] == "evidence integrity check failed"


def test_internal_payment_api_is_credentialed_and_claims_from_evidence(container) -> None:
    app = create_app(container)
    owner = Account.create()
    buyer = Account.create()

    with TestClient(app) as client:
        assert (
            client.post(
                "/internal/evidence/payment-intents/claim",
                json={"purchase_id": "missing"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/internal/evidence/payment-intents/claim",
                headers={"Authorization": "Bearer wrong"},
                json={"purchase_id": "missing"},
            ).status_code
            == 401
        )

        authenticate(client, owner)
        binding = client.put(
            "/internal/auth/buyer-wallet",
            headers=ADMIN_HEADERS,
            json={"owner_address": owner.address, "buyer_wallet_address": buyer.address},
        )
        assert binding.status_code == 200
        created = client.post(
            "/purchases",
            json={
                "domain": "fake",
                "request": {"value": "portable payment rail"},
                "budget_units": 250_000,
                "policy": {"maxTransactionUnits": 200_000},
            },
        )
        assert created.status_code == 201
        purchase_id = created.json()["purchase_id"]

        asyncio.run(
            container.repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.QUOTED,
                occurred_at=container.clock.now(),
                actor={"id": "buyer-orchestrator", "type": "service"},
                payload={
                    "signedQuotes": [
                        {
                            "quote_id": "quote-api",
                            "purchase_id": purchase_id,
                            "seller_agent_id": "seller-agent-api",
                            "provider_id": "gemini",
                            "model_id": "domain-neutral-model",
                            "model_version": "v1",
                            "amount_units": 100_000,
                            "token": TOKEN,
                            "pay_to": SELLER,
                            "available": True,
                            "expires_at": (container.clock.now() + timedelta(hours=1)).isoformat(),
                            "signer_address": SELLER,
                            "chain_id": 84532,
                            "verifying_contract": CONTRACT,
                            "signature": "0xprivate-quote-signature",
                        }
                    ],
                    "quoteIdentityEvidence": [
                        {
                            "quoteId": "quote-api",
                            "erc8004AgentId": "1",
                            "identityVerified": True,
                        }
                    ],
                },
                evidence_refs=("quote-api",),
            )
        )
        asyncio.run(
            container.repository.append_event(
                purchase_id=purchase_id,
                event_type=EventType.DECIDED,
                occurred_at=container.clock.now(),
                actor={"id": "buyer-agent", "type": "agent"},
                payload={"winner": {"quote_id": "quote-api"}},
                evidence_refs=("quote-api",),
            )
        )

        configured = client.put(
            "/internal/evidence/wallet-policies",
            headers=INTERNAL_HEADERS,
            json={
                "buyer_wallet_address": buyer.address,
                "policy_date": container.clock.now().date().isoformat(),
                "token": TOKEN,
                "per_transaction_limit_units": 200_000,
                "daily_limit_units": 1_000_000,
            },
        )
        assert configured.status_code == 200
        assert configured.json()["reserved_units"] == 0

        view = client.get(
            f"/internal/evidence/purchases/{purchase_id}/payment-view",
            headers=INTERNAL_HEADERS,
        )
        assert view.status_code == 200
        assert view.json()["quote"]["amount_units"] == 100_000
        assert view.json()["quote"]["pay_to"] == SELLER

        first = client.post(
            "/internal/evidence/payment-intents/claim",
            headers=INTERNAL_HEADERS,
            json={"purchase_id": purchase_id},
        )
        second = client.post(
            "/internal/evidence/payment-intents/claim",
            headers=INTERNAL_HEADERS,
            json={"purchase_id": purchase_id},
        )
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["state"] == "CLAIMED"
        assert first.json()["amount_units"] == 100_000
        assert first.json()["pay_to"] == SELLER
        assert first.json()["permit2_nonce"] is None
        assert first.json()["transfer_method"] == "eip3009"
        assert len(first.json()["authorization_nonce"]) == 66
        authorized = client.post(
            "/internal/evidence/payment-intents/authorize",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "authorization_hash": "0xauthorization",
                "signature": "0xsignature",
            },
        )
        assert authorized.status_code == 200
        assert authorized.json()["state"] == "AUTHORIZED"
        prompt = "durable inference prompt"
        execution = client.post(
            "/internal/evidence/seller-executions/claim",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "quote_id": "quote-api",
                "seller_agent_id": "seller-agent-api",
                "prompt": prompt,
                "prompt_hash": "sha256:" + hashlib.sha256(prompt.encode()).hexdigest(),
                "payment_proof_hash": "sha256:" + "11" * 32,
            },
        )
        assert execution.status_code == 200
        assert execution.json()["state"] == "CLAIMED"
        submitted = client.post(
            "/internal/evidence/seller-executions/authorization",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "value": {
                    "purchaseId": purchase_id,
                    "quoteId": "quote-api",
                    "modelId": "domain-neutral-model",
                    "settlementContext": {"opaque": "encrypted-at-rest"},
                },
            },
        )
        assert submitted.status_code == 200
        assert submitted.json()["state"] == "SUBMITTED"
        durable_settlement = client.post(
            "/internal/evidence/seller-executions/settlement",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "value": {
                    "success": True,
                    "transaction": "0x" + "ab" * 32,
                    "network": "eip155:84532",
                },
            },
        )
        assert durable_settlement.status_code == 200
        assert durable_settlement.json()["state"] == "SETTLED"
        provider_submitted = client.post(
            "/internal/evidence/seller-executions/provider-submission",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "provider_attempt_id": "sha256:" + "33" * 32,
                "provider_attempt_token": "provider-attempt-token-000000000001",
            },
        )
        assert provider_submitted.status_code == 200
        assert provider_submitted.json()["state"] == "PROVIDER_SUBMITTED"
        durable_result = client.post(
            "/internal/evidence/seller-executions/result",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "value": {
                    "providerId": "gemini",
                    "modelId": "domain-neutral-model",
                    "modelVersion": "v1",
                    "responseId": "response-api",
                    "text": "encrypted result",
                },
            },
        )
        assert durable_result.status_code == 200
        assert durable_result.json()["state"] == "DELIVERED"
        assert durable_result.json()["prompt"] == prompt
        changed_proof = client.post(
            "/internal/evidence/seller-executions/claim",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "quote_id": "quote-api",
                "seller_agent_id": "seller-agent-api",
                "prompt": prompt,
                "prompt_hash": "sha256:" + hashlib.sha256(prompt.encode()).hexdigest(),
                "payment_proof_hash": "sha256:" + "22" * 32,
            },
        )
        assert changed_proof.status_code == 409
        reconciled = client.post(
            "/internal/evidence/payment-intents/reconciliation",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "reason": "facilitator response timed out",
            },
        )
        assert reconciled.status_code == 200
        assert reconciled.json()["state"] == "RECONCILIATION_REQUIRED"
        assert reconciled.json()["transaction_hash"] is None
        bound_submission = client.post(
            "/internal/evidence/payment-intents/reconciliation-transaction",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "transaction_hash": "0x" + "ab" * 32,
            },
        )
        assert bound_submission.status_code == 200
        assert bound_submission.json()["transaction_hash"] == "0x" + "ab" * 32
        conflicting_submission = client.post(
            "/internal/evidence/payment-intents/reconciliation-transaction",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "transaction_hash": "0x" + "ef" * 32,
            },
        )
        assert conflicting_submission.status_code == 409
        settled = client.post(
            "/internal/evidence/payment-intents/settle",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "transaction_hash": "0x" + "ab" * 32,
                "block_number": 42,
                "transfer_log_index": 3,
                "receipt_status": 1,
                "token": TOKEN,
                "from_address": buyer.address,
                "to_address": SELLER,
                "amount_units": 100_000,
            },
        )
        assert settled.status_code == 200
        assert settled.json()["state"] == "SETTLED"
        fetched = client.get(
            f"/internal/evidence/payment-intents/{purchase_id}",
            headers=INTERNAL_HEADERS,
        )
        assert fetched.status_code == 200
        assert fetched.json()["transaction_hash"] == "0x" + "ab" * 32
        delivery = client.post(
            f"/internal/evidence/purchases/{purchase_id}/delivery",
            headers=INTERNAL_HEADERS,
            json={
                "seller_agent_id": "seller-agent-api",
                "provider_id": "gemini",
                "response_id": "response-api",
                "response_hash": "sha256:response-api",
                "model_id": "domain-neutral-model",
                "model_version": "v1",
            },
        )
        assert delivery.status_code == 200
        report = client.post(
            f"/internal/evidence/purchases/{purchase_id}/audit",
            headers=INTERNAL_HEADERS,
        )
        assert report.status_code == 200
        assert report.json()["severity"] == "NORMAL"
        assert report.json()["findings"] == []
        reputation_tx = "0x" + "ef" * 32
        reputation = client.post(
            f"/internal/evidence/purchases/{purchase_id}/reputation",
            headers=INTERNAL_HEADERS,
            json={
                "erc8004_agent_id": "1",
                "objective_value": 100,
                "feedback_hash": "0x" + report.json()["audit_bundle_hash"].split(":")[-1],
                "transaction_hash": reputation_tx,
                "chain_id": 84532,
                "registry_address": CONTRACT,
            },
        )
        assert reputation.status_code == 200
        terminal_retry = client.post(
            "/internal/evidence/payment-intents/claim",
            headers=INTERNAL_HEADERS,
            json={"purchase_id": purchase_id},
        )
        assert terminal_retry.status_code == 200
        assert terminal_retry.json()["state"] == "SETTLED"
        assert terminal_retry.json()["transaction_hash"] == "0x" + "ab" * 32

        wallet = client.get("/wallet")
        assert wallet.status_code == 200
        assert wallet.json()["buyer_wallet_address"] == buyer.address.lower()
        assert wallet.json()["spent_units"] == 100_000
        assert wallet.json()["reserved_units"] == 0
        assert wallet.json()["balance_status"] == "rpc_not_configured"

        class FixedBalanceReader:
            async def balance_of(self, *, token: str, wallet: str) -> int:
                assert token == TOKEN.lower()
                assert wallet == buyer.address.lower()
                return 900_000

        container.token_balance_reader = FixedBalanceReader()
        chain_wallet = client.get("/wallet")
        assert chain_wallet.json()["token_balance_units"] == 900_000
        assert chain_wallet.json()["balance_status"] == "base_sepolia_verified"
        purchases = client.get("/purchases")
        assert purchases.status_code == 200
        assert len(purchases.json()) == 1
        assert purchases.json()[0]["status"] == "REPUTATION_RECORDED"
        assert purchases.json()[0]["audit_severity"] == "NORMAL"
        detail = client.get(f"/purchases/{purchase_id}")
        assert detail.status_code == 200
        assert detail.json()["audit"]["severity"] == "NORMAL"
        assert "0xprivate-quote-signature" not in str(detail.json())
        quoted_event = next(item for item in detail.json()["events"] if item["type"] == "QUOTED")
        assert quoted_event["redacted"] is True
        assert quoted_event["payload"]["signedQuotes"][0]["signature"] == "<redacted>"
        assert client.get("/audit-alerts").json() == []
        agents = client.get("/agents")
        assert agents.status_code == 200
        assert agents.json()[0]["seller_agent_id"] == "seller-agent-api"
        assert agents.json()[0]["identity_verified"] is True
        assert agents.json()[0]["objective_feedback_value"] == 100
        assert agents.json()[0]["reputation_transaction_hash"] == reputation_tx
        head = client.get(
            f"/internal/evidence/purchases/{purchase_id}/head",
            headers=INTERNAL_HEADERS,
        )
        assert head.status_code == 200
        anchor_body = {
            "anchored_event_count": head.json()["event_count"],
            "anchored_head_event_hash": head.json()["head_event_hash"],
            "transaction_hash": "0x" + "cd" * 32,
            "chain_id": 84532,
            "contract_address": CONTRACT,
        }
        anchor = client.post(
            f"/internal/evidence/purchases/{purchase_id}/external-anchors",
            headers=INTERNAL_HEADERS,
            json=anchor_body,
        )
        repeated_anchor = client.post(
            f"/internal/evidence/purchases/{purchase_id}/external-anchors",
            headers=INTERNAL_HEADERS,
            json=anchor_body,
        )
        assert anchor.status_code == repeated_anchor.status_code == 200
        assert anchor.json() == repeated_anchor.json()
        assert anchor.json()["type"] == "EVIDENCE_ANCHORED"
        changed_anchor = client.post(
            f"/internal/evidence/purchases/{purchase_id}/external-anchors",
            headers=INTERNAL_HEADERS,
            json={**anchor_body, "transaction_hash": "0x" + "ef" * 32},
        )
        assert changed_anchor.status_code == 409

        events = asyncio.run(container.repository.list_events(purchase_id))
        assert [event.type for event in events].count(EventType.PAYMENT_INTENT_CLAIMED) == 1
        assert [event.type for event in events].count(EventType.PAYMENT_SETTLED) == 1
        policy = asyncio.run(
            container.repository.get_wallet_policy(
                buyer_wallet_address=buyer.address,
                policy_date=container.clock.now().date().isoformat(),
            )
        )
        assert policy is not None
        assert policy.reserved_units == 0
        assert policy.spent_units == 100_000


RUN_ID = "a" * 32
LOCAL_TX_ID = f"localtx:{RUN_ID}:payment:000001"
EVM_TX = "0x" + "ab" * 32
SCENARIO_BODY = {
    "run_id": RUN_ID,
    "scenario_id": "P6-A04-WRONG-AMOUNT",
    "catalog_version": "phase6.v1",
    "catalog_hash": "sha256:" + "cd" * 32,
}


def reconciling_purchase(container, client: TestClient) -> tuple[str, str]:
    """Drives a purchase to RECONCILIATION_REQUIRED through the real HTTP boundary."""
    owner = Account.create()
    buyer = Account.create()
    authenticate(client, owner)
    assert (
        client.put(
            "/internal/auth/buyer-wallet",
            headers=ADMIN_HEADERS,
            json={"owner_address": owner.address, "buyer_wallet_address": buyer.address},
        ).status_code
        == 200
    )
    created = client.post(
        "/purchases",
        json={
            "domain": "fake",
            "request": {"value": "terminal truth"},
            "budget_units": 250_000,
            "policy": {},
        },
    )
    assert created.status_code == 201
    purchase_id = created.json()["purchase_id"]
    asyncio.run(
        container.repository.append_event(
            purchase_id=purchase_id,
            event_type=EventType.QUOTED,
            occurred_at=container.clock.now(),
            actor={"id": "buyer-orchestrator", "type": "service"},
            payload={
                "signedQuotes": [
                    {
                        "quote_id": "quote-terminal",
                        "purchase_id": purchase_id,
                        "seller_agent_id": "seller-terminal",
                        "provider_id": "gemini",
                        "model_id": "model-a",
                        "model_version": "v1",
                        "amount_units": 100_000,
                        "token": TOKEN,
                        "pay_to": SELLER,
                        "available": True,
                        "expires_at": (container.clock.now() + timedelta(hours=1)).isoformat(),
                        "signer_address": SELLER,
                        "chain_id": 84532,
                        "verifying_contract": CONTRACT,
                    }
                ],
                "quoteIdentityEvidence": [
                    {
                        "quoteId": "quote-terminal",
                        "erc8004AgentId": "1",
                        "identityVerified": True,
                    }
                ],
            },
        )
    )
    asyncio.run(
        container.repository.append_event(
            purchase_id=purchase_id,
            event_type=EventType.DECIDED,
            occurred_at=container.clock.now(),
            actor={"id": "buyer-agent", "type": "agent"},
            payload={"winner": {"quote_id": "quote-terminal"}},
        )
    )
    assert (
        client.put(
            "/internal/evidence/wallet-policies",
            headers=INTERNAL_HEADERS,
            json={
                "buyer_wallet_address": buyer.address,
                "policy_date": container.clock.now().date().isoformat(),
                "token": TOKEN,
                "per_transaction_limit_units": 250_000,
                "daily_limit_units": 1_000_000,
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/internal/evidence/payment-intents/claim",
            headers=INTERNAL_HEADERS,
            json={"purchase_id": purchase_id},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/internal/evidence/payment-intents/authorize",
            headers=INTERNAL_HEADERS,
            json={
                "purchase_id": purchase_id,
                "authorization_hash": "0xauthorization",
                "signature": "0xsignature",
            },
        ).status_code
        == 200
    )
    reconciled = client.post(
        "/internal/evidence/payment-intents/reconciliation",
        headers=INTERNAL_HEADERS,
        json={
            "purchase_id": purchase_id,
            "reason": "facilitator returned submitted transaction",
            "transaction_hash": EVM_TX,
        },
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["state"] == "RECONCILIATION_REQUIRED"
    return purchase_id, buyer.address.lower()


def check_body(purchase_id: str, attempt: int, outcome: str) -> dict[str, object]:
    return {
        "purchase_id": purchase_id,
        "attempt_number": attempt,
        "checked_at": f"2026-09-04T12:0{attempt}:00Z",
        "checked_chain_id": 84532,
        "submission_ref": EVM_TX,
        "verifier_outcome": outcome,
        "finality_confirmations": 3,
        "proof_ref": "sha256:" + str(attempt) * 64,
        "evidence_source": "SYNTHETIC_LOCAL",
        "receipt_status": 1,
        "block_number": 46_349_821,
        "scenario": SCENARIO_BODY,
    }


def mismatch_body(
    purchase_id: str, buyer_address: str, *, amount_units: int = 200_000
) -> dict[str, object]:
    return {
        "purchase_id": purchase_id,
        "actual_amount_units": amount_units,
        "actual_token": TOKEN,
        "actual_from": buyer_address,
        "actual_to": SELLER,
        "transaction_ref": {"kind": "LOCAL", "id": LOCAL_TX_ID, "run_id": RUN_ID},
        "proof_ref": "sha256:" + "22" * 32,
        "evidence_source": "SYNTHETIC_LOCAL",
        "scenario": SCENARIO_BODY,
    }


def no_transfer_body(purchase_id: str, *, attempts: int = 3) -> dict[str, object]:
    return {
        "purchase_id": purchase_id,
        "reason_code": "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER",
        "checked_chain_id": 84532,
        "attempt_count": attempts,
        "first_checked_at": "2026-09-04T12:01:00Z",
        "last_checked_at": "2026-09-04T12:09:00Z",
        "authorization_nonce_hash": "sha256:" + "44" * 32,
        "finality_evidence": {"confirmations": 3},
        "proof_ref": "sha256:" + "33" * 32,
        "evidence_source": "SYNTHETIC_LOCAL",
        "submission_ref": LOCAL_TX_ID,
        "scenario": SCENARIO_BODY,
    }


def test_terminal_endpoints_are_credentialed_typed_and_idempotent(container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        assert (
            client.post(
                "/internal/evidence/payment-intents/reconciliation-checks",
                json=check_body("missing", 1, "RECEIPT_NOT_FOUND"),
            ).status_code
            == 401
        )
        purchase_id, buyer_address = reconciling_purchase(container, client)

        assert (
            client.post(
                "/internal/evidence/payment-intents/confirm-mismatch",
                headers=INTERNAL_HEADERS,
                json=mismatch_body("purchase-missing", buyer_address),
            ).status_code
            == 404
        )

        first = client.post(
            "/internal/evidence/payment-intents/reconciliation-checks",
            headers=INTERNAL_HEADERS,
            json=check_body(purchase_id, 1, "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER"),
        )
        assert first.status_code == 200
        assert first.json()["state"] == "RECONCILIATION_REQUIRED"
        assert first.json()["reconciliation_attempt_count"] == 1
        repeated = client.post(
            "/internal/evidence/payment-intents/reconciliation-checks",
            headers=INTERNAL_HEADERS,
            json=check_body(purchase_id, 1, "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER"),
        )
        assert repeated.status_code == 200
        assert repeated.json() == first.json()
        conflicting = client.post(
            "/internal/evidence/payment-intents/reconciliation-checks",
            headers=INTERNAL_HEADERS,
            json=check_body(purchase_id, 1, "RECEIPT_REVERTED"),
        )
        assert conflicting.status_code == 409

        stale = client.post(
            "/internal/evidence/payment-intents/confirm-mismatch",
            headers=INTERNAL_HEADERS,
            json={
                **mismatch_body(purchase_id, buyer_address),
                "expected_head_event_hash": "sha256:" + "ee" * 32,
            },
        )
        assert stale.status_code == 409
        wrong_state = client.post(
            "/internal/evidence/payment-intents/confirm-mismatch",
            headers=INTERNAL_HEADERS,
            json={**mismatch_body(purchase_id, buyer_address), "expected_state": "SETTLED"},
        )
        assert wrong_state.status_code == 409

        for confused in (
            {"kind": "EVM", "hash": LOCAL_TX_ID},
            {"kind": "LOCAL", "id": EVM_TX, "run_id": RUN_ID},
            {"kind": "EVM", "hash": EVM_TX, "id": LOCAL_TX_ID},
            {"kind": "EVM", "hash": "0x" + "AB" * 32},
        ):
            response = client.post(
                "/internal/evidence/payment-intents/confirm-mismatch",
                headers=INTERNAL_HEADERS,
                json={
                    **mismatch_body(purchase_id, buyer_address),
                    "transaction_ref": confused,
                },
            )
            assert response.status_code == 422, confused

        exact = client.post(
            "/internal/evidence/payment-intents/confirm-mismatch",
            headers=INTERNAL_HEADERS,
            json=mismatch_body(purchase_id, buyer_address, amount_units=100_000),
        )
        assert exact.status_code == 422

        confirmed = client.post(
            "/internal/evidence/payment-intents/confirm-mismatch",
            headers=INTERNAL_HEADERS,
            json=mismatch_body(purchase_id, buyer_address),
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["state"] == "MISMATCH_CONFIRMED"
        assert confirmed.json()["mismatched_fields"] == ["amount"]
        assert confirmed.json()["local_transaction_id"] == LOCAL_TX_ID
        assert confirmed.json()["terminal_evidence_source"] == "SYNTHETIC_LOCAL"
        again = client.post(
            "/internal/evidence/payment-intents/confirm-mismatch",
            headers=INTERNAL_HEADERS,
            json=mismatch_body(purchase_id, buyer_address),
        )
        assert again.status_code == 200
        assert again.json() == confirmed.json()
        assert (
            client.post(
                "/internal/evidence/payment-intents/reconcile-no-transfer",
                headers=INTERNAL_HEADERS,
                json=no_transfer_body(purchase_id, attempts=1),
            ).status_code
            == 409
        )

        events = asyncio.run(container.repository.list_events(purchase_id))
        assert [event.type for event in events].count(
            EventType.PAYMENT_MISMATCH_CONFIRMED
        ) == 1
        assert [event.type for event in events].count(
            EventType.PAYMENT_RECONCILIATION_CHECKED
        ) == 1
        policy = asyncio.run(
            container.repository.get_wallet_policy(
                buyer_wallet_address=events[3].payload["buyerWalletAddress"],
                policy_date=container.clock.now().date().isoformat(),
                token=TOKEN,
            )
        )
        assert policy is not None
        assert policy.reserved_units == 0
        assert policy.spent_units == 200_000


def test_bounded_reconciliation_reaches_a_terminal_no_transfer(container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        purchase_id, _buyer_address = reconciling_purchase(container, client)
        for attempt in (1, 2, 3):
            assert (
                client.post(
                    "/internal/evidence/payment-intents/reconciliation-checks",
                    headers=INTERNAL_HEADERS,
                    json=check_body(
                        purchase_id, attempt, "SUCCESS_RECEIPT_WITHOUT_MATCHING_TRANSFER"
                    ),
                ).status_code
                == 200
            )

        intermediate = client.get(f"/purchases/{purchase_id}")
        assert intermediate.json()["summary"]["payment_status"] == (
            "PAYMENT_CONFIRMATION_UNKNOWN"
        )
        assert intermediate.json()["summary"]["audit_status"] == "PENDING_AUDIT"
        assert intermediate.json()["audit"] is None
        assert client.get("/audit-alerts").json() == []

        closed = client.post(
            "/internal/evidence/payment-intents/reconcile-no-transfer",
            headers=INTERNAL_HEADERS,
            json=no_transfer_body(purchase_id),
        )
        assert closed.status_code == 200
        assert closed.json()["state"] == "RECONCILED_NO_TRANSFER"

        audited = client.post(
            f"/internal/evidence/purchases/{purchase_id}/audit",
            headers=INTERNAL_HEADERS,
        )
        assert audited.status_code == 200
        assert audited.json()["severity"] == "RISK"
        assert audited.json()["ruleset_version"] == "phase6.rules.v1"
        rule_ids = {item["rule_id"] for item in audited.json()["findings"]}
        assert {
            "AUD-PAYMENT-RECONCILED-NO-TRANSFER",
            "AUD-FACILITATOR-SUCCESS-WITHOUT-TRANSFER",
        }.issubset(rule_ids)

        detail = client.get(f"/purchases/{purchase_id}")
        summary = detail.json()["summary"]
        assert summary["payment_status"] == "RECONCILED_NO_TRANSFER"
        assert summary["audit_status"] == "AUDITED_RISK"
        assert summary["evidence_source"] == "SYNTHETIC_LOCAL"
        assert summary["transaction_hash"] is None
        assert summary["scenario"] == {
            "run_id": RUN_ID,
            "scenario_id": "P6-A04-WRONG-AMOUNT",
            "catalog_version": "phase6.v1",
        }
        assert summary["status"] == "AUDITED"
        alerts = client.get("/audit-alerts").json()
        assert {item["rule_id"] for item in alerts} == rule_ids
        assert all(item["evidence_source"] == "SYNTHETIC_LOCAL" for item in alerts)
        checked = next(
            item
            for item in detail.json()["events"]
            if item["type"] == "PAYMENT_RECONCILIATION_CHECKED"
        )
        assert checked["evidence_source"] == "SYNTHETIC_LOCAL"
        assert checked["transaction_ref"] is None


def test_read_paths_never_change_the_evidence_head(container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        purchase_id, buyer_address = reconciling_purchase(container, client)
        client.post(
            "/internal/evidence/payment-intents/confirm-mismatch",
            headers=INTERNAL_HEADERS,
            json=mismatch_body(purchase_id, buyer_address),
        )

        def head() -> dict[str, object]:
            response = client.get(
                f"/internal/evidence/purchases/{purchase_id}/head",
                headers=INTERNAL_HEADERS,
            )
            assert response.status_code == 200
            return response.json()

        before = head()
        for path in (
            "/purchases",
            f"/purchases/{purchase_id}",
            f"/purchases/{purchase_id}/events",
            "/audit-alerts",
            "/agents",
            "/wallet",
        ):
            assert client.get(path).status_code == 200, path
        after = head()

        assert before == after
        assert client.get(f"/purchases/{purchase_id}").json()["audit"] is None
        assert client.get("/audit-alerts").json() == []
        assert head() == before
