# Verification Boundary 1 — Fresh-Context Rereview Packet

Status: Ready for independent rereview
Prepared: 2026-09-02
Scope: Phase 1–2 remediation only; Phase 3 has not started
Prior result: `docs/reviews/verification-boundary-1-result.md`
Source/test manifest SHA-256: `e0e49ffa62560bc02324942460d6421395af02a5a27949d08db5e7fb982b63ab`

The manifest covers Python and TypeScript source/tests, schema JSON, and project scripts while excluding dependencies and build artifacts. It is reproduced with:

```bash
export LC_ALL=C
find services/buyer-audit-api/src services/buyer-audit-api/tests services/seller-service/src services/seller-service/tests packages/schemas scripts -type f \( -name '*.py' -o -name '*.ts' -o -name '*.json' -o -name '*.mjs' -o -name '*.sh' \) -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256
```

## Reviewer contract

Perform a read-only review from a new context. Verify every prior P1/P2 remediation directly in source and tests; do not accept this packet, audit log, or historical test output as proof. Run the required commands and reproduce the threat cases. Report findings by severity with exact file and line references. Do not modify files, read `.env.local`, request secrets, call providers, write blockchain state, deploy AWS, or publish GitHub state.

## Remediation matrix to challenge

1. Evidence integrity
   - `evidenceRefs` is part of `eventHash`.
   - Memory and Mongo repositories append an independent head record for every event.
   - API event and sensitive-payload reads verify sequence, payload/hash linkage, expected count, and terminal hash before using evidence.
   - Tests mutate refs and delete the terminal event in memory/API/native Mongo paths.
2. Prompt confidentiality
   - AI `normalizedRequest` contains only `prompt_hash` and `prompt_length`; plaintext remains in encrypted sensitive storage.
   - Seller quote requests no longer contain the prompt.
3. Signed seller assertions
   - `available` and `counterofferReason` are in TypeScript/Python EIP-712 fields and wire models.
   - Buyer-derived `identity_verified` is excluded from `signedQuotes` and stored separately as `quoteIdentityEvidence`.
   - A TypeScript-produced fixture is parsed and cryptographically verified by Python.
4. Paid model binding
   - `/v1/inference` accepts no client model ID.
   - `PaymentGate.authorize` returns quote-bound purchase, quote, and model; the application rejects mismatched bindings and invokes only that model.
5. Persisted decision evidence
   - After `QUOTED`, Buyer reloads events and the independent head, verifies the chain, parses stored evidence, re-verifies seller signatures, and matches provider/model/version before scoring.
6. Quote lineage
   - A provider-specific SellerEngine caches by purchase ID, so another request ID cannot create a second quote/counteroffer lineage for the same purchase in that process.
7. Strict quote input
   - Unknown fields, invalid `maxLatencyMs`, and invalid `preferredModelId` are rejected.
8. x402 representation
   - The Phase 2 placeholder no longer advertises v1 or pretends that v2 is implemented. Actual v2 requirements remain a Phase 3 gate.

## Required attack reproductions

Confirm independently that each attempt fails:

- mutate only `EvidenceEvent.evidence_refs`;
- remove only the terminal event while preserving the independent head;
- retrieve an invalid chain through `/events` or sensitive-payload API;
- find plaintext AI prompt in `REQUESTED.normalizedRequest`;
- mutate `available` or `counterofferReason` without invalidating signature;
- send `modelId` to choose a different paid model;
- alter persisted `QUOTED` after append and still create `DECIDED`;
- pair a quote with the same provider/model but another model version;
- create two quote lineages using two request IDs for one purchase/provider;
- pass unknown or malformed optional quote fields;
- observe a false `x402Version` in the placeholder response.

Also inspect for regressions outside the prior findings, especially Mongo concurrency, signature/wire parity, owner authorization, sensitive access logging, deterministic selection, and core/domain separation.

## Required commands and expected baseline

```bash
npm run lint
npm test
npm run test:mongo:local
python3 -m py_compile scripts/setup_keys.py scripts/input_provider_keys.py
bash -n scripts/test_mongo_local.sh
```

Expected baseline:

- Ruff and strict mypy: pass for 36 Python source files.
- Strict TypeScript: pass.
- Python default suite: 44 passed, one native-Mongo test skipped.
- TypeScript: 17 passed.
- Schema boundary: pass.
- Isolated native Mongo integration: one passed, including tail-deletion detection.

The two known upstream deprecation warnings are not automatic findings. Real providers, ERC-20/Permit2/x402 v2, Base Sepolia, ERC-8004, dashboard, and AWS remain later-phase external gates.
