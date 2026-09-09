# Project Overview

## Current user override — 2026-09-10

The current goal is the presentation local demo defined in `.agent/DEMO_SCOPE.md`; it supersedes conflicting completion/assurance gates below. Coder is Terra (`gpt-5.6-terra`); Planner/Reviewer remain Astra Light. Finish only three Mock Provider request→AA selection→Mock x402 payment→result→audit E2Es, over-budget rejection, 402-condition mismatch rejection, duplicate-success prevention per purchaseId, dashboard build/basic lint, and minimal documentation. Preserve existing implementation, uncommitted changes and historical evidence.

Defer full Python outbound instrumentation, broad historical zero-write regression, Mongo failure-cleanup stress tests, broad-suite repeats and additional hardening. These are reported limitations, not demo completion blockers; do not claim they passed. Use owned disposable storage only. Do not invoke real AA/Provider APIs, deploy tokens, move assets, make real testnet payments or deploy AWS. Prior broader task checklists remain history, not mandatory work for this reduced goal.

This graduation PBL audits whether an AI buyer's model choice obeyed the request, budget, and deterministic Artificial Analysis policy. The 2026-09-09 confirmed scope supersedes earlier active seller/reputation/anchor/SIWE/RPC requirements. Preserve prior implementation and transaction history.

## Product Intent & Invariants

- New flow: user → /request → buyer agent → Provider Gateway → result. Gateways are ordinary code for OpenAI, Anthropic Claude, and Google Gemini; comparison units are exact model IDs/versions.
- Provider APIs and settlement use mocks for this delivery. Never describe mock evidence as live execution.
- AA free API snapshots and explicit mappings provide price, benchmark completion time, and intelligence. Fail before payment on missing/null/invalid evidence or mapping; no fuzzy matching or fabricated fallback scores.
- New scoringPolicyVersion is aa-three-factor-v1. Fixed price/time/intelligence ratios: default .4/.3/.3, price .6/.2/.2, speed .2/.6/.2, intelligence .2/.2/.6. Explicit priority wins. No reputation/freshness/manual scores in new selection.
- Apply hard filters before normalization; store all candidates/rejections, same snapshot, policy, exact amount, and priority reason.
- One purchaseId permits at most one successful payment. It is not the blockchain transaction hash.
- New execution uses the already deployed PBLC V2 ERC-3009 token (6 decimals) as payment terms. Existing PBLC contracts/history keep their original names and addresses. No reset/rebase/amend/squash or data rewrites.
- Use x402 v2 exact + ERC-3009 only. Payment execution module (user-facing Korean: 결제 실행 모듈) isolates keys and signs approval; Facilitator submits transfers. Gateway independently verifies price using the same trusted snapshot/policy and releases result only after settlement confirmation.
- New runtime excludes seller negotiation/counteroffer, SIWE, ERC-8004, Evidence Anchor, and application independent receipt/Transfer/AuthorizationUsed cross-checks. Preserve historical readers and evidence.
- Local single-user demo does not implement public multi-user authentication. Keep internal API protection, key isolation, and encrypted sensitive payload separation.
- Audit Evidence API alone owns MongoDB access and append-only event order/hash-chain checks. Gateway and signer use its API.
- Deterministic rules are authoritative; LLM explanations cannot override policy. Facilitator response is the new payment status basis, not independently verified chain truth.

## Delivery Profile

Product quality high; assurance baseline standard; security-sensitive yes; has-ui yes. Scoped high-assurance covers payment authorization/idempotency, internal access, evidence integrity, and encrypted sensitive payloads.

## Tech Stack and Structure

Next.js 16/TypeScript dashboard in apps; Python/FastAPI buyer/audit in services/buyer-audit-api; TypeScript Provider Gateway/payment execution code in existing seller-service/commerce-gateway packages; Solidity in infra/contracts; shared contracts in packages; MongoDB evidence via API. Keep reusable core separate from domains/ai_inference and preserve strict typed boundaries.

## Key Commands

- npm run setup:python
- npm run lint
- npm test
- npm run test:mongo:local
- npm run api
- npm run build --workspace @pbl/dashboard

Inspect workspace package scripts for exact typecheck/E2E commands. Full local completion requires unit/integration/contracts/schema/E2E/Mongo verification, lint/typecheck, and dashboard build.

## Security and Delivery Rules

Never commit keys, credentials, or plaintext sensitive payloads. Browser/LLM inputs must not receive signing keys or internal API secrets. Keep per-transaction/rolling/user budget checks, nonce binding, reservation atomicity, and ambiguous-settlement no-repurchase behavior. Historical GETs must not write evidence.

AA keys are server environment variables only; .env.example contains empty names. If absent, fixture tests continue and live AA verification stays an external gate. Use explicit fixture provenance and never relabel unqueried balances as on-chain balances.

Prepare but do not execute token deployment, asset transfer, or real testnet payment. Before those actions present wallets, deployment method/predicted address, gas estimate, and test amount for user approval. Stop before actual AWS deployment. Public access control and raw-payload retention remain pre-AWS decisions.

## Historical Caveats

PBLC V2 ERC-3009 proof remains in docs/ERC3009_DEPLOYMENT_GATE.md. Legacy SIWE readers/dependencies may remain for history: siwe 4.4.0 requires abnf 2.2.0; this is not a new login requirement. Docker Desktop's current VM kernel is unsupported by MongoDB; use npm run test:mongo:local on this Mac. Existing core/domain boundaries and historical Permit2/reputation/anchor evidence remain supported for reads, not new execution.
