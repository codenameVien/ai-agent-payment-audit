# Verification Boundary 2 — Payment Trust Boundary

Date: 2026-09-02
Decision requested: `Approve Phase 4` or findings ranked P0–P3.

## Review contract

Review the implementation as hostile payment-boundary code. Do not trust this packet's claims without tracing code and tests. Focus on:

1. `purchaseId` must be the only caller-selected payment identifier; amount, token, recipient, quote, wallet, and nonce must come from verified evidence.
2. Atomic reservation and settlement must tolerate concurrent retries without double reserve/spend or duplicate lifecycle events.
3. DecisionAuthorization must bind purchase, decision event hash, quote, amount, token, recipient, and deterministic Permit2 nonce.
4. x402 v2 `PAYMENT-REQUIRED` must be matched exactly for scheme, CAIP-2 network, amount, asset, recipient, and `assetTransferMethod=permit2` before signing.
5. Permit2 signer must bind canonical proxy spender, fixed nonce, deadline, and witness recipient. Timeout/ambiguous results must not create another payment.
6. Facilitator results are not authoritative: independent RPC receipt and exact ERC-20 `Transfer` sender/recipient/amount must gate settlement.
7. MongoDB intent, lifecycle event, immutable head, and wallet budget changes must commit or roll back together.
8. ERC-8004 identity must bind quote signer to agent wallet; objective feedback is 100/0 and self-feedback is rejected.
9. Evidence anchor must preserve monotonic head continuity and record the exact transaction back into evidence.
10. Common payment code must not import the `ai_inference`, Gemini, or Nemotron domain.

## Primary files

- `services/buyer-audit-api/src/buyer_audit_api/core/payment.py`
- `services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/{memory,mongo}.py`
- `services/buyer-audit-api/src/buyer_audit_api/api/{app,schemas}.py`
- `services/commerce-gateway/src/{gateway,x402,eip712,erc8004,evidence-anchor}.ts`
- `services/commerce-gateway/src/adapters/*.ts`
- `services/seller-service/src/{http,x402}.ts`
- `infra/contracts/src/{DemoToken,EvidenceAnchor}.sol`

## Adversarial evidence

- `services/buyer-audit-api/tests/test_payment_service.py`
- `services/buyer-audit-api/tests/test_mongo_repository.py`
- `services/buyer-audit-api/tests/test_api.py`
- `services/commerce-gateway/tests/*.test.ts`
- `services/seller-service/tests/http.test.ts`
- `infra/contracts/test/*.t.sol`

## Commands

```bash
npm run lint
uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_payment_service.py services/buyer-audit-api/tests/test_api.py
npm run test:mongo:local
npm test --workspace @pbl/commerce-gateway
npm test --workspace @pbl/seller-service
npm run test:contracts
```

## Explicit residual gates

No real private key, paid API, token transfer, Base Sepolia deployment, CDP Facilitator call, or AWS resource was used. Contract addresses, RPC URL, agent IDs, and registry addresses remain environment-injected. The review may approve the local trust boundary while requiring those smoke artifacts before public deployment.
