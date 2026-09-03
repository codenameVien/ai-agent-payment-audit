# 영향 범위 — `/request` 경계와 PBLC V2 ERC-3009 병렬 전환

Status: Approved by explicit user direction on 2026-09-04
Upstream: `requirements.md`, `design.md` revised 2026-09-04

## 1. 보존 기준

- Git baseline: `main`/`origin/main` commit `2e79ba0`; clean before this branch.
- Active rollback: current PBLC `0x9DFFfdDcF5d7E526Bda60728e4c8F79dBA50CeD9`, x402 v2 Permit2 + EIP-2612.
- Canonical settled record: purchase `378beb23-e352-49f0-b450-87da88791292`, tx `0x32562decbafa3c670280501bafbce01b72ce698d0391c63f4e3c5113f070a0a8`, 10 purchase events.
- Canonical incomplete record: purchase `14b7dd10-fba1-4ea0-afc9-fb44500d6b4b`, `RECONCILIATION_REQUIRED`, null tx hash, 6 purchase events.
- No migration deletes, rewrites, replays, or fabricates fields on existing `purchaseEvents`, `evidenceHeads`, `paymentIntents`, `sellerExecutions`, or `sensitivePayloads`.

## 2. Change map

| Area | Current | Target | Compatibility rule |
|---|---|---|---|
| Web route | `/experiments` normal runner | `/request` request/budget/priority; `/experiments` redirect | keep pending purchase localStorage key readable |
| Dashboard | `/` read-only | `/` and `/dashboard` same read-only audit UI | no purchase/scenario controls |
| Buyer role | orchestrator asks Commerce Gateway | Buyer Agent owns full purchase; Payment Executor is its tool | keep HTTP boundary/key isolation |
| Evidence | internal `EvidenceRepository` and `/internal/evidence` | user-facing Audit Evidence API / 감사 증거 기록 모듈 | internal class name and collections remain |
| SDK wrapper | adapters visible only in code | explicit Buyer/Seller wrapper grouping in docs and composition names | no speculative framework/refactor |
| Token | PBLC v1, ERC-20 + EIP-2612 | separate PBLC V2, ERC-20 + ERC-3009 | v1 address/history unchanged |
| x402 | exact + Permit2 payload | configurable exact + EIP-3009 payload | default remains Permit2 until approved real success |
| Receipt proof | exact ERC-20 Transfer | exact Transfer + EIP-3009 AuthorizationUsed | old Permit2 receipts remain valid |
| Audit | payment binding and evidence completeness | also recompute selection filters/weights/winner/explanation | historic sparse fixtures do not get rewritten |

## 3. Files and contracts affected

- `apps/dashboard`: new request page/component, legacy redirect, `/dashboard` alias, shell and route tests.
- `services/buyer-audit-api`: payment intent wire fields and deterministic selection-audit rules; Mongo serialization stays additive/backward-compatible.
- `services/commerce-gateway`: renamed user-facing runtime descriptions; transfer-method strategy, ERC-3009 signer/payload, AuthorizationUsed receipt proof.
- `services/seller-service`: configurable 402 challenge/requirement validator for `permit2 | eip3009`; provider adapters remain Seller SDK Wrapper implementations.
- `infra/contracts`: add `DemoTokenV2.sol` and negative/positive ERC-3009 tests; do not edit the deployed `DemoToken.sol` semantics.
- `scripts`: add PBLC V2 plan/deploy/status commands separated from existing deploy/recover commands. No deploy occurs in local implementation.
- `.env.example`/Compose: additive `PAYMENT_TRANSFER_METHOD`, `PBLC_V2_*` configuration; current `.env.local` remains Permit2 unless user later approves and smoke succeeds.
- README/HANDOFF/ROADMAP/AGENTS/AI-DLC/architecture artifact: terminology, proof status, and approval gate.

## 4. Data compatibility

- Add optional `transferMethod` and `authorizationNonce` to new payment intents. Missing values on old intents mean `permit2` and reuse existing `permit2Nonce`.
- Do not rename MongoDB collections or run a destructive migration.
- New `PAYMENT_SETTLED` evidence may include authorization event proof; existing settled events without it are recognized as historical Permit2.
- Duplicate protection remains `purchaseId`-scoped in MongoDB and nonce-scoped on PBLC V2.

## 5. Security and failure boundaries

- Buyer private key remains only in the Node Payment Executor process; no key is moved to FastAPI or dashboard.
- A 402 transfer-method downgrade/substitution is rejected.
- ERC-3009 signature must bind token domain, chain, buyer, seller, exact value, validity, and random nonce.
- Hashless/ambiguous settlement stays reconciliation-only; no new nonce/payment is generated on retry.
- Facilitator `success` is insufficient: exact Transfer plus AuthorizationUsed must be found independently.
- Failed PBLC V2 verify/settle leaves `PAYMENT_TRANSFER_METHOD=permit2` and preserves the returned failure evidence.

## 6. Verification matrix

| Boundary | Required evidence |
|---|---|
| Contract | success, wrong signer, expired, not-yet-valid, nonce replay, insufficient balance, zero address, low-s/v |
| x402 wire | exact/eip3009 402, accepted echo, EIP-712 recovery, random bytes32 nonce, downgrade/mismatch rejection |
| Payment Executor | one purchase/one intent, retry same nonce, independent Transfer + AuthorizationUsed, legacy Permit2 regression |
| Seller | eip3009 challenge and verify/settle body; Permit2 regression |
| Audit | priority/weights, hard filters, best eligible winner, explanation, quote/payment/chain evidence, duplicate settlement |
| UI | `/request` fields and safe retry; `/`, `/dashboard` read-only; `/experiments` redirect |
| Broad suite | Python, TypeScript, Solidity, Mongo integration, lint/typecheck, dashboard production build |

## 7. Release gates

1. Local code/tests/docs complete with Permit2 default unchanged.
2. Report deployer, buyer, Gemini/Nemotron sellers, intended initial supply, `0.1 PBLC` smoke amount, predicted PBLC V2 address when available, gas estimate and estimated ETH cost.
3. Wait for explicit user approval before any Base Sepolia deployment or mint.
4. After approval, deploy and run verify/settle/AuthorizationUsed/Transfer/replay proof.
5. Only after success, change the active environment/default from Permit2 to ERC-3009. Failure keeps Permit2 and produces a cause report.
