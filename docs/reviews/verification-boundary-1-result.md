# Verification Boundary 1 — Independent Review Result

Reviewed: 2026-09-02
Reviewer: fresh-context `verification_boundary_1`
Reviewed worktree: `docs/inception-requirements`, no commits
Source-manifest SHA-256: `72f7c55708981b48d21a0b7d76079e12707ee9370b22aa7794d64fdb8eb55f55`
Decision: `Request changes`

## Findings

### P1

1. `evidenceRefs` is outside `eventHash`; deleting the terminal event leaves a valid prefix; API reads do not verify the chain. Hash references, verify on reads, and compare an independently stored expected count/head hash.
2. `normalizedRequest.prompt` copies plaintext prompt into the general event log. Keep prompt only in encrypted sensitive storage and expose a hash/reference in structured evidence.
3. `counterofferReason` and availability are unsigned seller assertions but are stored under `signedQuotes`; availability is fabricated as true by the Python wire adapter. Sign seller assertions used for selection or keep separately derived trust metadata.
4. Paid inference authorizes only purchase/quote IDs but accepts client-supplied model ID and prompt, allowing a cheap quote to invoke another enabled model. Resolve immutable quote terms after payment authorization.
5. `DECIDED` uses the pre-persistence candidate list rather than reloading and verifying `QUOTED`; quote/benchmark matching omits model version. Reload, chain-check, parse, signature-check, and require provider/model/version equality.

### P2

1. Counteroffer idempotency is keyed only by request ID, allowing two request IDs for one purchase/provider to create two counteroffers.
2. `/internal/quotes` does not strictly validate `maxLatencyMs`, `preferredModelId`, or unknown fields.
3. The payment placeholder returns `x402Version: 1` even though the approved protocol is x402 v2 and no v2 requirements contract exists yet.

No P0 or P3 findings were reported.

## Verification performed by reviewer

- `npm run lint`: passed.
- `npm test`: Python 38 passed/1 skipped; TypeScript 13 passed; schema boundary passed.
- `npm run test:mongo:local`: passed after localhost permission.
- Secret-handoff Python compilation, shell syntax, and `git diff --check`: passed.
- Diagnostic reproductions confirmed evidence-reference mutation, tail deletion, unsigned counteroffer-reason mutation, and multiple counteroffers for one purchase.

## Confirmed boundaries

Core/domain import direction, fake-domain composition, common/domain schema separation, SIWE validation and nonce consumption, wallet binding uniqueness, sensitive-payload owner checks and access events, Mongo concurrent sequence/hash linkage, invalid-signature rejection before `QUOTED`, and deterministic scoring weights/tie-breaks were confirmed.

## Remediation status

- [x] P1 findings fixed and focused tests passed
- [x] P2 findings fixed and focused tests passed
- [x] Broad suite 1 rerun — Python 44/1 skip, TypeScript 17, native Mongo 1, schema/lint pass
- [ ] Fresh-context independent rereview — `Request changes`; see `verification-boundary-1-rereview-result.md`
- [ ] Phase 3 approved
