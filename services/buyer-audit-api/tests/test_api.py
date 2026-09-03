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
        assert first.json()["permit2_nonce"].isdecimal()
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
