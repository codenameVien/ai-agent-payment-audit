# Verification Boundary 1 — Fresh-Context Rereview Result

Reviewed: 2026-09-02
Reviewer: fresh-context `verification_boundary_1_rereview`
Source/test manifest SHA-256: `e0e49ffa62560bc02324942460d6421395af02a5a27949d08db5e7fb982b63ab`
Decision: `Request changes`

## Findings

### P1

1. Concurrent quote lineage: `SellerEngine.quote()` checks the cache, awaits quote creation, then registers the result. Two concurrent requests for one purchase with different request IDs both observed a cache miss and produced valid quotes. Register an in-flight promise/lock atomically per purchase before awaiting.
2. Untrusted decision inputs: the workflow reloads `QUOTED` but selects with caller-supplied `normalized_request` and `budget_units`. A persisted quality/250000 request was decided as price/100000, and a repository with no `REQUESTED` could still create `QUOTED` and `DECIDED`. Parse and enforce the unique preceding `REQUESTED`, lifecycle order, and input equality from the verified chain.

### P2

1. Mongo event/head atomicity: event and independent head are separate writes. Injected head-write failure left one committed event and no head. Use a transaction with retry-safe semantics.
2. Blank identifiers: strict quote validation rejects empty strings but accepts whitespace-only `purchaseId`, `requestId`, and `preferredModelId`. Validate trimmed non-empty identifiers.

No P0/P3 findings.

## Confirmed remediations

The reviewer independently confirmed that reference mutation, tail deletion, API reads of damaged chains, plaintext prompt leakage, signed availability/reason tampering, paid-model substitution, persisted `QUOTED` tampering, model-version mismatch, sequential duplicate lineage, malformed numeric/unknown fields, and false x402 version representation are blocked.

## Command evidence

- `npm run lint`: passed; Ruff, strict mypy 36 files, strict TypeScript.
- `npm test`: Python 44 passed/1 skipped; TypeScript 17 passed; schema boundary passed.
- `npm run test:mongo:local`: one passed.
- Python secret-handoff compilation and shell syntax: passed.
- Focused independent attack suite: Python eight passed; TypeScript selected four passed.
- Manifest reproduced exactly.

## Residual risks

- Same-database event and head records do not detect a privileged attacker deleting both; an external/on-chain anchor is a later phase.
- Quote lineage is process-memory scoped until persistent seller storage is added.
- x402 v2, providers, ERC-20/Permit2, Base Sepolia, ERC-8004, and AWS remain external gates.
- The all-untracked worktree has no commit identity; the manifest identifies this snapshot.

## Remediation status

- [x] P1 concurrent quote lineage fixed
- [x] P1 persisted REQUESTED/lifecycle fixed
- [x] P2 Mongo transaction fixed
- [x] P2 blank identifiers fixed
- [x] Broad suite rerun — Python 46/1 skip, TypeScript 18, schema/lint pass; native Mongo 1
- [ ] Fresh-context rereview approved
