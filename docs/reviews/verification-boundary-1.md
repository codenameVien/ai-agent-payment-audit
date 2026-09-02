# Verification Boundary 1 — Independent Review Packet

Status: Awaiting independent new-context review
Prepared: 2026-09-02
Scope: Phase 1–2 only; Phase 3 has not started

## Reviewer contract

Perform a read-only review from a fresh context. Do not rely on `aidlc-docs/audit.md`, README claims, or prior conversation as proof. Inspect implementation and tests directly, run the commands below, and report findings ordered by severity (`P0`–`P3`) with exact file and line references. If there are no findings, state that explicitly and list residual risks or unverified assumptions.

Do not implement fixes in the review pass. Do not access `.env.local`, ask for secrets, call real providers, create blockchain transactions, deploy AWS resources, or publish to GitHub.

## Required review questions

### 1. Core/domain import direction

- Does anything under `services/buyer-audit-api/src/buyer_audit_api/core/` import `domains.ai_inference`, Gemini, or Nemotron?
- Are protocol-neutral authentication, evidence, encryption, and purchase lifecycle components usable with the fake domain?
- Are AI-specific benchmark, selection, quote extension, wire conversion, and explanation concerns confined to `domains/ai_inference` or `packages/schemas/domains/ai_inference`?
- Does `packages/schemas/common/seller-quote.schema.json` remain free of AI-only provider/model fields?

### 2. API and cross-service schema contracts

- Do TypeScript `SellerQuote`, EIP-712 types, JSON wire serialization, Python `SellerQuoteWire`, and Python EIP-712 typed data use exactly the same names, integer representation, optional counteroffer representation, and signing order?
- Can JavaScript integer precision loss, extra unsigned fields, address substitution, or snake/camel-case drift enter evidence?
- Does `/internal/quotes` validate all constraints that affect a quote, and does `/v1/inference` stay behind the injected payment gate?
- Does provider-level counteroffer idempotency reject reuse of one `requestId` with changed constraints?

### 3. Authentication and sensitive-data boundary

- Is SIWE nonce consumption atomic and one-time, with domain, URI, chain, expiry, and replay checks enforced server-side?
- Is the owner-to-buyer-wallet binding unique in memory and MongoDB implementations?
- Can an unauthenticated or wrong owner read a sensitive payload?
- Does every successful sensitive-payload access append an audit event without exposing plaintext or keys in ordinary responses/logs?

### 4. Evidence integrity and decision boundary

- Are event sequence allocation and previous-hash linkage atomic under concurrent MongoDB writes?
- Can duplicate singleton payment event types or a modified/deleted/reordered event evade verification?
- Is an invalid EIP-712 signature rejected before `QUOTED` is stored?
- Are only stored signed quotes and benchmark snapshots used to create `DECIDED`?
- Are budget, availability, seller identity, expiry, benchmark freshness, capabilities, latency, input/output limits, and allowed-seller checks applied before scoring?
- Are scoring weights exactly those approved in requirements and are tie-breaks deterministic?

## Files that require direct inspection

- `services/buyer-audit-api/src/buyer_audit_api/core/`
- `services/buyer-audit-api/src/buyer_audit_api/adapters/auth/siwe.py`
- `services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/`
- `services/buyer-audit-api/src/buyer_audit_api/adapters/crypto/`
- `services/buyer-audit-api/src/buyer_audit_api/domains/ai_inference/`
- `services/seller-service/src/contracts.ts`
- `services/seller-service/src/eip712.ts`
- `services/seller-service/src/seller-engine.ts`
- `services/seller-service/src/http.ts`
- `services/seller-service/src/adapters/`
- `packages/schemas/common/`
- `packages/schemas/domains/ai_inference/`
- Related tests under both services

## Reproduction commands

Run from `/Users/vien/MyProjects/PBL`:

```bash
npm run lint
npm test
npm run test:mongo:local
python3 -m py_compile scripts/setup_keys.py scripts/input_provider_keys.py
bash -n scripts/test_mongo_local.sh
git diff --check
```

Expected pre-review baseline:

- Ruff and strict mypy pass for 36 Python source files.
- Strict TypeScript passes.
- Python: 38 passed, one real-Mongo test skipped in the default suite.
- TypeScript: 13 passed.
- Schema boundary: pass.
- Isolated native Mongo integration: one passed.

Any mismatch from this baseline is a finding, not a reason to reuse the historical result.

## Known constraints, not automatic findings

- No real Gemini/NVIDIA request has been made; final requirements still demand actual provider evidence.
- No ERC-20, Permit2, x402 Facilitator, Base Sepolia, or ERC-8004 path exists yet; those belong to Phase 3.
- No dashboard or AWS deployment exists yet; those belong to Phase 4.
- Docker Mongo cannot run on the current Docker Desktop kernel, so the isolated native Mongo test is the authoritative local integration path.
- The default Python suite emits upstream `websockets.legacy` and FastAPI TestClient/httpx2 deprecation warnings.
- GitHub remote and team repository are not configured, and no commit exists yet.

## Review result

To be completed only by the independent reviewer:

- Reviewer/task: fresh-context `verification_boundary_1`
- Reviewed revision or worktree state: uncommitted manifest `72f7c55708981b48d21a0b7d76079e12707ee9370b22aa7794d64fdb8eb55f55`
- Commands executed: required suite plus read-only diagnostic reproductions
- Findings: [verification-boundary-1-result.md](verification-boundary-1-result.md)
- Residual risks: listed in the result and unchanged external-integration gates
- Decision: `Request changes`
