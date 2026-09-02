# Verification Boundary 1 — Second Fresh-Context Rereview Packet

Status: Ready for independent rereview
Prepared: 2026-09-02
Scope: Phase 1–2 and both remediation rounds; Phase 3 has not started
Prior result: `docs/reviews/verification-boundary-1-rereview-result.md`
Source/test manifest SHA-256: `a8274106b4bf1e9c8073a0bc2aa75053bccc7406c703bd207ab61629a7af0bb3`

## Reviewer contract

Perform a new read-only review without trusting prior summaries. Read the prior result, inspect source/tests directly, run all commands, reproduce the four prior findings concurrently where applicable, and search for regressions. Report P0–P3 findings with exact lines and decide `Approve Phase 3` or `Request changes`. Do not modify files, read `.env.local`, request secrets, call providers, write chain state, deploy, or publish.

## Four remediations to challenge

1. Concurrent quote lineage
   - SellerEngine registers a purchase-scoped in-flight quote promise before awaiting signing.
   - Same-fingerprint concurrent retries share the result; a different fingerprint/request ID is rejected while the first is in flight.
   - Rejected quote creation removes only its own cache entry.
2. Persisted REQUESTED trust root
   - Workflow verifies chain/head before quote discovery.
   - Exactly one leading `REQUESTED` is required; pre-existing `QUOTED`/`DECIDED` is rejected.
   - Stored normalized request, budget, and policy are parsed; caller request/budget must match.
   - Post-persistence reload requires one later `QUOTED`, no `DECIDED`, then verifies stored quotes and model versions before selection.
3. Mongo atomic event/head
   - Sequence/head read, event insert, and independent-head insert run in one PyMongo transaction.
   - Existing event/head divergence fails closed before append.
   - Injected head-write failure rolls back both collections, and a retry begins at sequence 1.
   - Native test runs a one-node replica set and retains 12-way concurrent append coverage.
4. Blank identifiers
   - `purchaseId`, `requestId`, and optional `preferredModelId` are trimmed and whitespace-only values are rejected.

## Required attacks

- Send two simultaneous quote requests for one purchase with different request IDs; exactly one may succeed.
- Send simultaneous identical requests; both may return only the same quote object/ID.
- Persist quality/250000 in `REQUESTED`, call decide with price/100000, and confirm no `QUOTED` is appended.
- Call decide with no `REQUESTED`, duplicate `REQUESTED`, existing `QUOTED`, or invalid lifecycle order.
- Inject failure between Mongo event/head writes and confirm both collections remain unchanged, then retry the same purchase.
- Pre-seed divergent event/head state and confirm append fails closed.
- Send blank and padded identifiers; blank fails and padded values normalize consistently.

Re-run the previous packet attacks too: evidence refs/tail/API integrity, prompt confidentiality, signed availability/reason, paid model binding, persisted QUOTED tamper, model-version match, strict numeric/unknown fields, and no false x402 version.

## Commands and expected baseline

```bash
npm run lint
npm test
npm run test:mongo:local
python3 -m py_compile scripts/setup_keys.py scripts/input_provider_keys.py
bash -n scripts/test_mongo_local.sh
```

Expected:

- Ruff and strict mypy pass for 36 Python source files; strict TypeScript passes.
- Python default: 46 passed, one native-Mongo test skipped.
- TypeScript: 18 passed.
- Schema boundary: pass.
- Native replica-set Mongo: one passed, including injected rollback, retry, concurrent append, and tail deletion.

Manifest reproduction uses the same command from `verification-boundary-1-rereview.md`, over source/tests/schemas/scripts only. Known upstream warnings and later external integration gates are unchanged.
