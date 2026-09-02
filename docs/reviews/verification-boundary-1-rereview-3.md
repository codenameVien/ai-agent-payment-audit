# Verification Boundary 1 — Third Fresh-Context Rereview Packet

Status: Ready for independent rereview
Prepared: 2026-09-02
Scope: Phase 1–2 and three remediation rounds; Phase 3 has not started
Prior result: `docs/reviews/verification-boundary-1-rereview-2-result.md`
Source/test manifest SHA-256: `2d0fbd118cf4266367c0df4461a1357575b9049cf3de73347876fa58a2756d5c`

## Reviewer contract

Perform a new read-only review without trusting prior summaries. Inspect source and tests directly, reproduce the three latest findings, rerun all prior boundary attacks, and search for regressions. Report P0–P3 findings with exact lines and decide `Approve Phase 3` or `Request changes`. Do not modify files, read `.env.local`, request secrets, call providers, write chain state, deploy, or publish.

## Three remediations to challenge

1. Explicit business lifecycle projection
   - Only `SENSITIVE_PAYLOAD_ACCESSED` and `CORRECTION_RECORDED` are auxiliary.
   - Pre-quote business projection must be exactly `[REQUESTED]`.
   - Persisted candidate projection must be exactly `[REQUESTED, QUOTED]`.
   - Payment, delivery, audit, reputation, duplicate request, quote, or decision events therefore fail closed.
2. Atomic expected-head transition
   - `EvidenceRepository.append_event` accepts an expected event count and head hash.
   - Memory checks them under its append lock; Mongo checks them in the same transaction as event/head writes.
   - Workflow binds both `QUOTED` and `DECIDED` appends to the verified head it read.
   - `QUOTED` and `DECIDED` also have repository singleton defenses; Mongo enforces partial unique indexes.
3. Full Mongo anchor verification
   - Mongo reads all purchase events and all head records in sequence within the transaction.
   - It verifies the event hash chain, equal cardinality, every event-count/sequence pair, and every head/event hash pair before append.
   - Missing or altered interior anchors fail before any new write.

## Required attacks

- Persist `REQUESTED → PAYMENT_SETTLED`, invoke decide, and confirm no `QUOTED` is appended.
- Repeat with each non-auxiliary later event and malformed lifecycle ordering.
- Synchronize two decide calls so both pass the initial read; exactly one may commit and the final business projection must be `REQUESTED → QUOTED → DECIDED` with no trailing quote.
- Race the same expected count/head directly against both repository implementations; a stale writer must fail.
- Delete an interior Mongo head, then append; confirm append fails and both collection counts remain unchanged.
- Alter an interior head hash or remove an interior event/head pair; confirm full-chain verification rejects it.
- Rerun all earlier attacks: quote lineage, persisted REQUESTED trust root, transaction rollback/retry, blank identifiers, evidence refs/tail/API integrity, prompt confidentiality, signed availability/reason, paid-model binding, persisted QUOTED tamper, model-version match, strict wire input, and no false x402 version.

## Commands and expected baseline

```bash
npm run lint
npm test
npm run test:mongo:local
python3 -m py_compile scripts/setup_keys.py scripts/input_provider_keys.py
bash -n scripts/test_mongo_local.sh
git diff --check
find . \( -name '*.orig' -o -name '*.rej' \) -print
```

Expected:

- Ruff and strict mypy pass for 36 Python source files; strict TypeScript passes.
- Python default: 49 passed, one native-Mongo test skipped.
- TypeScript: 18 passed.
- Schema boundary: pass.
- Native replica-set Mongo: one passed, including rollback/retry, 12-way append, tail deletion, and interior-head deletion rejection.
- No `.orig` or `.rej` files.

## Manifest reproduction

```bash
export LC_ALL=C
find services/buyer-audit-api/src services/buyer-audit-api/tests services/seller-service/src services/seller-service/tests packages/schemas scripts -type f \( -name '*.py' -o -name '*.ts' -o -name '*.json' -o -name '*.mjs' -o -name '*.sh' \) -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256
```

Known upstream warnings and later external-integration gates are unchanged. A same-database event/head pair still does not replace the Phase 3 external/on-chain anchor.
