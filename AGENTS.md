# Project Overview

This graduation PBL audits whether an AI buyer agent's model choice obeyed the user's request, budget, and policy, then cross-checks the actual payment on Base Sepolia.

## Product Intent & Invariants

- The demo domain is autonomous AI-model purchasing, not a generic marketplace.
- One provider-level seller agent may offer multiple models. MVP sellers are Gemini and Nemotron.
- The buyer must use objective benchmark evidence and live seller quotes before choosing.
- One user request produces at most one successful payment.
- Payments use the project-issued 6-decimal ERC-20 demo credit through x402 Permit2 on Base Sepolia.
- The demo credit implements EIP-2612; x402 payment approval uses an exact-amount gas-sponsored permit, not buyer-funded manual allowance.
- The Commerce Gateway is the only component allowed to sign or submit blockchain writes.
- MongoDB stores an auditable evidence chain that connects request, candidates, quotes, decision, payment, delivery, and audit.
- ERC-8004 provides agent identity and objective reputation evidence; semantic concerns stay off-chain.
- Rules are authoritative. LLM audit output is advisory and cannot override deterministic findings.
- Raw prompts and responses are encrypted in a separate sensitive store. MVP retention is indefinite, but this must be reconsidered before public AWS deployment.

## Delivery Profile

- Product quality: high
- Engineering assurance baseline: standard
- Scoped high-assurance boundary: authentication, wallet/payment authorization, audit integrity, and encrypted sensitive payloads
- Security-sensitive: yes
- Human-facing UI: yes

## Tech Stack

- Web dashboard: Next.js 16 and TypeScript
- Buyer and audit API: Python with FastAPI
- Seller agents and Commerce Gateway: Node.js with TypeScript
- Smart contracts: Solidity on Base Sepolia
- Payment: x402 v2 with Permit2 and Coinbase CDP Facilitator
- Data: MongoDB Atlas
- Identity and reputation: ERC-8004
- Local model strategy: swappable model adapters; Nemotron and Gemini integrations first
- Target deployment: AWS Amplify, ECS Fargate, Secrets Manager, and CloudWatch in ap-northeast-2

## Key Commands

- `npm run setup:python`: install the locked Python 3.12 environment
- `npm run lint`: Ruff plus strict mypy
- `npm test`: focused tests; real Mongo tests skip unless configured
- `npm run test:mongo:local`: isolated native MongoDB integration test with automatic cleanup
- `npm run api`: run FastAPI after the user creates `.env.local` with `python scripts/setup_keys.py`

## Planned Top-Level Structure

- `apps/`: user-facing dashboard
- `services/`: buyer API, seller agents, Commerce Gateway, and audit service; each service keeps reusable `core/` separate from `domains/ai_inference/`
- `contracts/`: ERC-20 and blockchain integration code
- `packages/`: protocol-neutral schemas, domain schemas, and generated client libraries
- `aidlc-docs/`: approved requirements, design, tasks, audit, and state
- `docs/`: operator handoff and implementation roadmap

## Coding Rules

- Keep provider/model logic behind adapters; do not hard-code one LLM into orchestration.
- Keep authentication, evidence, payment, and reputation cores independent of `domains/ai_inference`; core modules must not import domain implementations.
- Add a future purchase domain through domain ports and `packages/schemas/domains/<domain>`, never through scattered `if domain == ...` branches.
- Use strict TypeScript and typed Python boundaries.
- Use one canonical `purchaseId` across every event and external reference.
- Make payment submission idempotent and reject duplicate settlement.
- Keep deterministic policy checks separate from LLM explanations.
- Do not let agents access MongoDB or blockchain signing directly; route through owned APIs.
- Never commit secrets, private keys, real credentials, or unredacted sensitive payloads.
- Do not report external integration success without a real response, stored record, or transaction hash.

## Security Rules

- Store production/deployed secrets in AWS Secrets Manager; use local environment files only through the approved secret-input flow.
- Encrypt raw prompts and responses separately from structured audit events.
- Verify quote amount, token, recipient, expiry, agent identity, and purchase ID before signing.
- Enforce per-transaction, rolling daily, and user-specified budget limits before payment.
- Preserve append-only audit events and verify their hash chain.
- Public AWS deployment is blocked until the sensitive-payload retention policy is reviewed.

## Caveats

- Coinbase Facilitator support for the custom Permit2 token must be proven with a real Base Sepolia transaction; documentation compatibility alone is insufficient.
- MetaMask is used for SIWE and user/admin actions. The autonomous buyer uses a separate programmatic wallet because the MetaMask x402 helper does not cover this Permit2 path.
- Provider prices, limits, and model availability change; live quotes and benchmark freshness must be visible in evidence.
- `siwe==4.4.0` must keep `abnf==2.2.0`, matching the upstream v4.4.0 lockfile. Newer `abnf 2.9` rejects SIWE's bundled RFC 5234 grammar during import.
- The Python `siwe 4.4.0` package states that it has not had a formal security audit. Keep strict local domain/URI/chain checks and atomic server-side nonce consumption; do not treat library verification alone as the auth boundary.
- The current Docker Desktop VM kernel is in MongoDB's unsupported Linux 6.19–7.0.13 range, so Mongo containers stop intentionally. Use `npm run test:mongo:local` on this Mac until Docker's kernel is updated; Compose remains for compatible hosts.
