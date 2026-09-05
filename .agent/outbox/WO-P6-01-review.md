# WO-P6-01 Independent Review

## Final verdict

**REJECT**

필수 focused suite와 native Mongo 검사는 모두 통과했지만, exact tip에는 결제 재실행, terminal proof 결합, transaction/provenance type safety, no-transfer finality, audit/read 무결성 계약을 위반하는 blocker가 남아 있다. 이 tip은 통합하거나 WO-P6-02의 base로 사용하면 안 된다.

## Reviewed identity and inputs

- Reviewer workspace: `/Users/vien/MyProjects/PBL-coder`
- Branch: `wo/P6-01`
- Exact requested base: `685472c2178fac1ed1d16fedd4dde4dda7d3ed64`
- Exact reviewed tip: `0c2f5c959e4d5b09ffde45eb754a43f055b933e0`
- Merge-base of requested base and tip: `685472c2178fac1ed1d16fedd4dde4dda7d3ed64`
- Implementation commit: `449090805948f095f1d35373ffb7852f8f43a1c6`
- Evidence-only commits: `198ee43de2a142e70a0f50c73d3d755d47628383`, `c9e05e7c2b72378c5b706010cf6a70640891fbd5`, `0c2f5c959e4d5b09ffde45eb754a43f055b933e0`
- `git merge-base HEAD main`: `888674533d7afbd27419b0037c03f6e4fdf892c6`
- Worktree before and after review commands: clean (`## wo/P6-01`)
- Work Order SHA-256: `a93814a1e60c5d6c1fcc839365c75acbda3eb753400c3341e13c48a26cc48a6e`

Independently read: `/Users/vien/AGENTS.md`, the AI-DLC workflow, repository instructions, the complete WO, canonical Phase 6 append blocks in requirements/design/tasks, `.agent/{CURRENT_STATE,DECISIONS,HANDOFF,TURN_LOG}.md`, coder evidence, and every changed source/test/evidence path in the exact requested range. Coder claims were treated as untrusted.

Protected inputs were byte-identical at base and tip:

| Artifact | SHA-256 at base and tip |
|---|---|
| `aidlc-docs/inception/requirements.md` | `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f` |
| `aidlc-docs/inception/design.md` | `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768` |
| `aidlc-docs/inception/tasks.md` | `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe` |
| `docs/ERC3009_DEPLOYMENT_GATE.md` | `62fa20da465b450daf8e92cdbc084192470232fe36fec24b52cda028d5cd3208` |

## Exact range and scope

`git diff --name-only 685472c2178fac1ed1d16fedd4dde4dda7d3ed64..0c2f5c959e4d5b09ffde45eb754a43f055b933e0` returned 21 paths, all in the WO allow-list:

```text
.agent/TURN_LOG.md
.agent/outbox/WO-P6-01-coder.done.md
services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/memory.py
services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/mongo.py
services/buyer-audit-api/src/buyer_audit_api/api/app.py
services/buyer-audit-api/src/buyer_audit_api/api/schemas.py
services/buyer-audit-api/src/buyer_audit_api/core/audit.py
services/buyer-audit-api/src/buyer_audit_api/core/models.py
services/buyer-audit-api/src/buyer_audit_api/core/payment.py
services/buyer-audit-api/src/buyer_audit_api/core/ports.py
services/buyer-audit-api/src/buyer_audit_api/core/projections.py
services/buyer-audit-api/tests/test_api.py
services/buyer-audit-api/tests/test_mongo_repository.py
services/buyer-audit-api/tests/test_payment_service.py
services/buyer-audit-api/tests/test_phase6_audit.py
services/buyer-audit-api/tests/test_phase6_payment_terminal.py
services/buyer-audit-api/tests/test_phase6_projections.py
services/commerce-gateway/src/contracts.ts
services/commerce-gateway/src/gateway.ts
services/commerce-gateway/tests/gateway.test.ts
services/commerce-gateway/tests/phase6-truth-model.test.ts
```

No dashboard, reputation outbox/publisher, scenario catalog/runner, provider, contract, infrastructure, package manifest, deployment, or public integration implementation was added.

## Independent command results

Required fixed-order commands were run at exact tip:

| # | Command | Exit/result |
|---|---|---|
| 1 | `uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests` | `0`; `All checks passed!` |
| 2 | `uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src` | `0`; no issues in 44 source files |
| 3 | `uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_projections.py services/buyer-audit-api/tests/test_phase6_audit.py services/buyer-audit-api/tests/test_phase6_payment_terminal.py services/buyer-audit-api/tests/test_payment_service.py services/buyer-audit-api/tests/test_api.py -q` | `0`; `109 passed, 2 warnings in 1.28s` |
| 4 | `npm run build --workspace @pbl/commerce-gateway` | `0`; TypeScript build clean |
| 5 | `node --test services/commerce-gateway/dist/tests/gateway.test.js services/commerce-gateway/dist/tests/phase6-truth-model.test.js` | `0`; 37 tests, 37 pass, 0 fail |
| 6 | `npm run test:mongo:local` | `0`; `4 passed, 1 warning in 6.53s` |
| 7 | `git diff --check` | `0` |

Native Mongo preservation checks:

- Before run, `lsof -nP -iTCP:27019 -sTCP:LISTEN` returned no listener.
- After run, the same `lsof` returned no listener; `pgrep -f "mongod --dbpath /private/tmp/pbl-mongo-test"` returned no process.
- `find /private/tmp -maxdepth 1 -name "pbl-mongo-test.*" -print` returned no path.
- No foreign listener was stopped and the coder worktree remained clean.

Reviewer/protected-diff commands:

| Command | Exit/result |
|---|---|
| `git diff --check "$(git merge-base HEAD main)"..HEAD` | `0` |
| `git diff --name-only "$(git merge-base HEAD main)"..HEAD` | `0`; includes the pre-packet Phase 6 planning baseline because `main` has not incorporated it |
| WO literal protected diff against `$(git merge-base HEAD main)` | `1`; diff is the approved Phase 6 append because main merge-base is `8886745...`, not packet base `685472c...` |
| Same protected diff against exact requested base `685472c...` | `0` |
| Exact-base protected packet paths (`work-orders`, manifests, `.gitignore`, `.agent/{CURRENT_STATE,DECISIONS,HANDOFF}.md`) | `0` |
| `git diff --check 685472c...0c2f5c...` | `0` |

The literal main-relative failure is a branch-topology artifact, not a coder mutation in the requested range. It is disclosed rather than relabeled as a pass.

Additional independent negative probes, all read-only/local:

| Probe | Observed result |
|---|---|
| Execute a mocked gateway intent already in `MISMATCH_CONFIRMED` | `{"outcome":"SELLER_CALLED","sellerCalls":1}` |
| Parse EVM ref with unknown source and negative block/log | Accepted; source silently became `BASE_SEPOLIA_VERIFIED`, block `-1`, log `-7` |
| Project one BASE event plus one synthetic event | Accepted as `SYNTHETIC_LOCAL` instead of integrity failure |
| Project synthetic terminal payload carrying a BASE EVM transaction ref | Returned projection source `SYNTHETIC_LOCAL` and transaction source `BASE_SEPOLIA_VERIFIED` together |
| Construct Python mismatch proof with BASE proof source and HISTORICAL transaction source | Accepted |
| Validate settlement request containing `0x` plus 64 non-hex `z` characters | Accepted by `InternalPaymentSettlementRequest` |

No public chain, facilitator, ERC-8004, provider, Atlas, or AWS command/call was made.

## Findings

### Critical

#### C1 — New terminal intents are executable again and can reach a second seller/payment attempt

`services/commerce-gateway/src/gateway.ts:225-231` returns early only for `SETTLED` and `FAILED`. `MISMATCH_CONFIRMED` and `RECONCILED_NO_TRANSFER` fall through to the new-payment path at `gateway.ts:256`. The independent probe proved that an already `MISMATCH_CONFIRMED` intent calls the seller once.

This violates terminal exclusivity, P6-A07 replay behavior, and the project invariant that one request produces at most one successful payment. A mismatch already represents a confirmed buyer outflow; executing it again can authorize/submit another payment. Both new terminal states must be terminal at the gateway boundary, with zero seller/signing/submission calls on retry, and must have focused regression tests.

### High

#### H1 — Transaction-reference and evidence-source unions accept contradictory or fabricated provenance

The cross-field invariant is missing in several layers:

- `services/commerce-gateway/src/contracts.ts:333-354` requires `ReceiptProof.transactionHash` even when optional `transactionRef` is `LOCAL`. The synthetic test at `tests/phase6-truth-model.test.ts:445-465` starts with a fabricated EVM hash and adds a local ref. This is not the required discriminated receipt/proof union and conflicts with the WO prohibition on fabricated receipt/hash evidence.
- `gateway.ts:119-131` treats every unknown EVM `evidenceSource` as `BASE_SEPOLIA_VERIFIED` and accepts negative block/log coordinates. The probe demonstrated both.
- `gateway.ts:171-179` accepts an optional receipt transaction ref without checking it against the receipt hash/block/log/source.
- `core/projections.py:195-231` only collects nonterminal `SYNTHETIC_LOCAL` payload sources; it ignores nonterminal BASE/HISTORICAL sources and nested transaction-ref sources. `projections.py:299-306` never checks resolved source against an EVM ref's source. Both contradictory projections were reproduced.
- `core/payment.py:242-248` checks only LOCAL-versus-EVM shape, not equality of proof and transaction-ref evidence source. A BASE/HISTORICAL mismatch was accepted.

These paths can relabel untrusted/malformed evidence as verified or return an internally impossible read model. Reject unknown sources and invalid coordinates; use a true receipt/proof discriminated union; require exact source/ref/receipt/scenario agreement; scan all relevant event and nested-ref sources; remove the fabricated EVM hash from local evidence tests.

#### H2 — Terminal proof binding and same/conflicting-proof semantics are incomplete

`core/payment.py:854-934` does not require an EVM mismatch ref to equal the intent's submitted transaction hash. An unrelated buyer outflow can therefore terminalize the purchase and alter spend. Its retry comparison at `payment.py:865-870` compares only `proofRef` and actual transfer, ignoring transaction ref, evidence source, and scenario.

`payment.py:936-990` trusts caller-provided no-transfer metadata instead of tying it to the append-only reconciliation checks. It compares attempt count and last outcome but not checked chain, submission ref, first/last times, authorization nonce hash, evidence source, or recorded finality. The caller can report zero finality in the check and then submit a proof claiming sufficient confirmations. Once terminal, `payment.py:945-948` treats matching `proofRef` alone as the same proof even if every other field changes.

The internal Evidence API is a high-assurance bearer boundary, so a label such as `proofRef` cannot substitute for structural proof binding. Same-proof idempotency must compare the complete canonical immutable proof; aliases must conflict. Mismatch/no-transfer evidence must be bound to the purchase, signed quote, submitted transaction/authorization, recorded reconciliation series, and source/scenario.

#### H3 — Gateway closes no-transfer without configured finality and disagrees with the Evidence API

`services/commerce-gateway/src/gateway.ts:713-742` gates terminal no-transfer only on attempt count; it calls `reconcileNoTransfer` even when confirmations are zero/unknown. The new test at `tests/phase6-truth-model.test.ts:495-504` explicitly expects `RECONCILED_NO_TRANSFER` with zero confirmations, contrary to the `ReceiptProof.confirmations` comment, P6-AC-04.2, and the Python service's minimum-finality check at `core/payment.py:966-972`.

With the future real HTTP adapter, this path records the check and then receives a 422 instead of staying `PAYMENT_CONFIRMATION_UNKNOWN`; the permissive fake currently hides the integration failure. The gateway must keep on-chain evidence nonterminal until both bounded attempts and configured finality are satisfied. A separately attested synthetic oracle may use its explicit local rule.

#### H4 — Audit/read integrity silently becomes stale, and one GET still mutates the evidence head

`api/app.py:1798-1821` keeps `GET /purchases/{purchaseId}/sensitive/{payloadId}` as a write that appends `SENSITIVE_PAYLOAD_ACCESSED`. The zero-write test at `tests/test_api.py:1064-1097` deliberately omits this GET, although WO completion requires every GET/read path to leave event count/head unchanged.

Separately, `core/audit.py:976-988` returns any persisted audit without checking whether later evidence exists or whether the stored `evidenceHeadEventHash`/ruleset still describes the auditable head. The sensitive GET can itself append after an audit, after which re-audit silently returns the stale report. This violates P6-AC-03.5's explicit no-silent-overwrite/re-audit-policy rule and weakens audit integrity.

Preserving access logging is reasonable, but it cannot silently override the approved read contract. The operation must become an explicit mutation or receive a Planner-approved contract change. Audit retry must distinguish the AUDITED event itself from later evidence and fail closed or invoke an approved correction policy.

### Medium

#### M1 — Required mutation guards and transaction validation are optional/incomplete

`api/schemas.py:313-318` makes `expected_state`, `expected_event_count`, and `expected_head_event_hash` optional, while Design §18.12.2 says every internal mutation body validates expected state/head. Omitting them lets callers bypass their observation/CAS precondition even though the repository still performs an internal snapshot CAS.

`api/schemas.py:295-303` also leaves settlement `transaction_hash` unpatterned, and `core/payment.py:683-684` checks only prefix/length rather than hex. The malformed hash probe passed schema validation. The binding endpoint usually prevents this in the ordinary flow, but the type boundary itself violates Design §18.5.2 and must reject malformed persisted/API transaction hashes.

#### M2 — Required pre-index collision report is absent

Design §18.19 item 7 requires a read-only collision report before creating new indexes. `adapters/repositories/mongo.py:430-493` creates the Phase 6 terminal/attempt/outflow indexes directly, and neither source nor native-Mongo tests contain a collision-report path. Partial filters protect old rows that lack new fields, but do not report collisions among rows that already carry those fields. Add the required preflight without mutating/backfilling old rows.

### Low / process

#### L1 — Three iterative evidence commits do not match the claimed commit cadence

The implementation is one coherent commit, but it is followed by three commits with the identical subject `docs(phase6): record WO-P6-01 coder evidence SHAs`. Independent diff verification showed `4490908..0c2f5c9` changes only `.agent/outbox/WO-P6-01-coder.done.md`; executable code is unchanged.

The files are in the allowed evidence path, so they are not an executable scope violation. However, three iterative follow-ups do not satisfy the WO's “coherent commit 하나” cadence, and the coder report describes only one follow-up. This is a process deviation, not an independent technical reject reason. Do not rewrite/reset/rebase/amend the branch; preserve history and use one accurate final evidence update after authorized corrections.

## Rulings on the four flagged scope decisions

1. **Index subset — ACCEPTED as packet scope.** Truth-model indexes belong here; reputation/outbox/scenario indexes belong to WO-P6-02/03 with their writers. Keeping `unique_confirmed_outflow_transaction` for EVM and adding `_local` for the mutually exclusive LOCAL variant is reasonable. This does not waive finding M2.
2. **Gateway terminal capability — ACCEPTED only as staged packet wiring.** `src/adapters/http.ts` is owned by WO-P6-02, so runtime fail-closed behavior when the optional capability is absent is compatible with this packet boundary. WO-P6-02 must make the production adapter capability complete. This ruling does not waive C1 or H3.
3. **`PAYMENT_ATTEMPT_REJECTED` — ACCEPTED.** The event type, unique partial index, and deterministic audit readers belong to this truth-model packet; the scenario-only writer correctly remains deferred to WO-P6-03.
4. **`SENSITIVE_PAYLOAD_ACCESSED` GET carve-out — REJECTED.** The Phase 6 gap statement and WO completion criterion say GET/read must not change the event head. Security access logging does not authorize an undocumented exception. Convert it to an explicit mutation or return to Planner for an approved contract change.

## What passed but does not clear the blockers

- Memory and native-Mongo terminal CAS/index races admitted one terminal event in the tested calls.
- Tested identical proofs returned one result and tested conflicting samples returned conflicts.
- Reservation/spent/outflow accounting passed the supplied amount/token/recipient cases.
- Old Permit2 and incomplete Mongo documents remained readable in supplied tests, and legacy execution remained fail-closed.
- Focused Python/Node suites, native Mongo cleanup, exact-base allow-list, and protected hashes passed.

Those tests do not cover terminal gateway re-execution, canonical full-proof aliasing, recorded-finality binding, complete provenance cross-validation, post-audit head change, or the sensitive GET.

## Required disposition

- Preserve this branch/worktree/history; do not integrate this tip and do not start WO-P6-02.
- Correct only within approved WO-P6-01 acceptance. Any intended exception to the all-GET zero-write contract requires Planner resolution.
- Re-run the entire fixed focused sequence, native Mongo, exact-base and literal reviewer protected diffs, plus regression tests for every finding, at the new exact tip.

**REJECT**
