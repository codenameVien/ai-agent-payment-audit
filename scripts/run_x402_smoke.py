#!/usr/bin/env python3
"""Run one authenticated mock-provider purchase with a real Base Sepolia payment."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env.local"


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", maxsplit=1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[name] = value
    return values


def required(values: dict[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def checked(response: httpx.Response, label: str) -> httpx.Response:
    if response.is_success:
        return response
    try:
        detail: Any = response.json()
    except ValueError:
        detail = response.text[:500]
    raise RuntimeError(f"{label} failed ({response.status_code}): {detail}")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--purchase-id", help="resume an existing purchase")
    return parser.parse_args()


def main() -> None:
    args = arguments()
    env = parse_env(ENV_PATH.read_text(encoding="utf-8"))
    owner = Account.from_key(required(env, "DEPLOYER_PRIVATE_KEY"))
    buyer = Account.from_key(required(env, "BUYER_AGENT_PRIVATE_KEY"))
    token = required(env, "TOKEN_ADDRESS")
    internal_headers = {
        "Authorization": f"Bearer {required(env, 'INTERNAL_SERVICE_TOKEN')}"
    }
    admin_headers = {
        "Authorization": f"Bearer {required(env, 'ADMIN_SERVICE_TOKEN')}"
    }

    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180) as client:
        challenge = checked(
            client.post("/auth/siwe/challenge", json={"owner_address": owner.address}),
            "SIWE challenge",
        ).json()
        message = challenge["message"]
        signature = owner.sign_message(encode_defunct(text=message)).signature.hex()
        checked(
            client.post(
                "/auth/siwe/verify",
                json={"message": message, "signature": signature},
            ),
            "SIWE verification",
        )
        checked(
            client.put(
                "/internal/auth/buyer-wallet",
                headers=admin_headers,
                json={
                    "owner_address": owner.address,
                    "buyer_wallet_address": buyer.address,
                },
            ),
            "buyer wallet binding",
        )
        purchase_id = args.purchase_id
        if purchase_id is None:
            checked(
                client.put(
                    "/internal/evidence/wallet-policies",
                    headers=internal_headers,
                    json={
                        "buyer_wallet_address": buyer.address,
                        "policy_date": datetime.now(timezone.utc).date().isoformat(),
                        "token": token,
                        "per_transaction_limit_units": 250_000,
                        "daily_limit_units": 1_000_000,
                    },
                ),
                "wallet policy",
            )
            created = checked(
                client.post(
                    "/purchases",
                    json={
                        "domain": "ai_inference",
                        "request": {
                            "prompt": "Base Sepolia 결제 감사 스모크 테스트 응답을 한 문장으로 작성해줘.",
                            "priority": "balanced",
                        },
                        "budget_units": 250_000,
                        "policy": {"maxTransactionUnits": 250_000},
                    },
                ),
                "purchase creation",
            ).json()
            purchase_id = created["purchase_id"]
        run = checked(client.post(f"/purchases/{purchase_id}/run"), "purchase run").json()
        detail = checked(client.get(f"/purchases/{purchase_id}"), "purchase detail").json()
        wallet = checked(client.get("/wallet"), "wallet dashboard").json()

    payment = run["payment"]
    provider_result = payment.get("provider_result") or {}
    summary = detail["summary"]
    safe_result = {
        "purchaseId": purchase_id,
        "ownerAddress": owner.address,
        "buyerAddress": buyer.address,
        "status": summary.get("status"),
        "payment": {
            "state": payment.get("state"),
            "transactionHash": payment.get("transaction_hash"),
            "blockNumber": payment.get("block_number"),
            "transferLogIndex": payment.get("transfer_log_index"),
            "amountUnits": payment.get("amount_units"),
            "token": payment.get("token"),
            "payTo": payment.get("pay_to"),
        },
        "delivery": {
            "providerId": provider_result.get("providerId"),
            "responseId": provider_result.get("responseId"),
            "modelId": provider_result.get("modelId"),
        },
        "audit": {
            "severity": run["audit"].get("severity"),
            "findingCodes": [item["code"] for item in run["audit"].get("findings", [])],
            "bundleHash": run["audit"].get("audit_bundle_hash"),
        },
        "wallet": {
            "tokenBalanceUnits": wallet.get("token_balance_units"),
            "spentUnits": wallet.get("spent_units"),
            "reservedUnits": wallet.get("reserved_units"),
        },
    }
    print(json.dumps(safe_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
