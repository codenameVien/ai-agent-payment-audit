# WO-P6-01 Independent Review — Correction Round 2

## Reviewed identity and evidence

- Repository: `/Users/vien/MyProjects/PBL-coder`
- Branch: `wo/P6-01`
- Approved packet base: `685472c2178fac1ed1d16fedd4dde4dda7d3ed64`
- Requested short tip `50a34d4` resolved to: `50a34d4de646aa6f53b7abc516f6d7700ffc2779`
- Reviewed `HEAD`: `50a34d4de646aa6f53b7abc516f6d7700ffc2779`
- Correction implementation anchor: `16451a7817236e7b708835db7349a4557287de31`
- `git merge-base 685472c... HEAD`: `685472c2178fac1ed1d16fedd4dde4dda7d3ed64`
- `git merge-base HEAD main`: `888674533d7afbd27419b0037c03f6e4fdf892c6`
- Worktree before and after review: clean (`## wo/P6-01`)
- Work Order SHA-256: `a93814a1e60c5d6c1fcc839365c75acbda3eb753400c3341e13c48a26cc48a6e`
- Preserved prior rejection SHA-256: `939578fc09897113fc67cdc63777d448845e87a51b363a9d29f3082415773853`
- Coder evidence SHA-256: `13b8a1876c64f74430ff236ff64df14f6cc821e4b79b6d4bb9467c0bc9eb0ecc`

I independently read the Work Order, prior rejection, coder evidence, global/repository instructions, canonical Phase 6 requirements/design/tasks, and all source, test, and evidence paths changed in the approved-base-to-tip range. The coder report was treated as an untrusted claim.

Protected artifacts at the reviewed tip match the approved hashes:

| Artifact | SHA-256 |
|---|---|
| `aidlc-docs/inception/requirements.md` | `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f` |
| `aidlc-docs/inception/design.md` | `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768` |
| `aidlc-docs/inception/tasks.md` | `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe` |
| `docs/ERC3009_DEPLOYMENT_GATE.md` | `62fa20da465b450daf8e92cdbc084192470232fe36fec24b52cda028d5cd3208` |

## Exact commit and scope accounting

The approved-base-to-tip history is:

```text
449090805948f095f1d35373ffb7852f8f43a1c6 feat(phase6): implement canonical truth model
198ee43de2a142e70a0f50c73d3d755d47628383 docs(phase6): record WO-P6-01 coder evidence SHAs
c9e05e7c2b72378c5b706010cf6a70640891fbd5 docs(phase6): record WO-P6-01 coder evidence SHAs
0c2f5c959e4d5b09ffde45eb754a43f055b933e0 docs(phase6): record WO-P6-01 coder evidence SHAs
16451a7817236e7b708835db7349a4557287de31 fix(phase6): harden terminal truth model per review
50a34d4de646aa6f53b7abc516f6d7700ffc2779 docs(phase6): record WO-P6-01 correction evidence
```

`git diff --name-status 685472c...50a34d4...` returned exactly 21 allowed paths:

```text
M .agent/TURN_LOG.md
A .agent/outbox/WO-P6-01-coder.done.md
M services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/memory.py
M services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/mongo.py
M services/buyer-audit-api/src/buyer_audit_api/api/app.py
M services/buyer-audit-api/src/buyer_audit_api/api/schemas.py
M services/buyer-audit-api/src/buyer_audit_api/core/audit.py
M services/buyer-audit-api/src/buyer_audit_api/core/models.py
M services/buyer-audit-api/src/buyer_audit_api/core/payment.py
M services/buyer-audit-api/src/buyer_audit_api/core/ports.py
A services/buyer-audit-api/src/buyer_audit_api/core/projections.py
M services/buyer-audit-api/tests/test_api.py
M services/buyer-audit-api/tests/test_mongo_repository.py
M services/buyer-audit-api/tests/test_payment_service.py
A services/buyer-audit-api/tests/test_phase6_audit.py
A services/buyer-audit-api/tests/test_phase6_payment_terminal.py
A services/buyer-audit-api/tests/test_phase6_projections.py
M services/commerce-gateway/src/contracts.ts
M services/commerce-gateway/src/gateway.ts
M services/commerce-gateway/tests/gateway.test.ts
A services/commerce-gateway/tests/phase6-truth-model.test.ts
```

The correction implementation range `0c2f5c9..16451a7` changes 15 allowed source/test/evidence paths. The post-anchor range `16451a7..50a34d4` changes only `.agent/outbox/WO-P6-01-coder.done.md`; excluding that file produces an empty diff.

There is no dashboard, scenario catalog/runner, reputation outbox/publisher, seller, provider, contract, infrastructure, package-manifest, or deployment change in the packet. Event/schema support needed by this truth-model packet is not counted as scenario or reputation implementation leakage.

## Commands and results

### Required fixed-order verification

| # | Exact command | Result |
|---|---|---|
| 1 | `uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests` | exit 0; `All checks passed!` |
| 2 | `uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src` | exit 0; no issues in 44 source files |
| 3 | `uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_projections.py services/buyer-audit-api/tests/test_phase6_audit.py services/buyer-audit-api/tests/test_phase6_payment_terminal.py services/buyer-audit-api/tests/test_payment_service.py services/buyer-audit-api/tests/test_api.py -q` | exit 0; `134 passed, 6 warnings in 1.47s` |
| 4 | `npm run build --workspace @pbl/commerce-gateway` | exit 0; TypeScript build clean |
| 5 | `node --test services/commerce-gateway/dist/tests/gateway.test.js services/commerce-gateway/dist/tests/phase6-truth-model.test.js` | exit 0; 41 tests, 41 pass, 0 fail |
| 6 | `npm run test:mongo:local` | exit 0; `4 passed, 1 warning in 6.41s` |
| 7 | `git diff --check` | exit 0 |

Before native Mongo, `lsof -nP -iTCP:27019 -sTCP:LISTEN` returned exit 1 with no listener. Afterward, the same command and `pgrep -af 'mongod.*pbl-mongo-test'` returned exit 1 with no process; `find /private/tmp -maxdepth 1 -name 'pbl-mongo-test.*' -print` returned no path. No foreign listener was stopped.

### Identity, packet, and protected-diff checks

| Exact command | Result |
|---|---|
| `git rev-parse HEAD` | `50a34d4de646aa6f53b7abc516f6d7700ffc2779` |
| `git rev-parse 50a34d4^{commit}` | same full SHA |
| `git merge-base 685472c2178fac1ed1d16fedd4dde4dda7d3ed64 HEAD` | exact approved base |
| `git diff --check 685472c2178fac1ed1d16fedd4dde4dda7d3ed64..50a34d4de646aa6f53b7abc516f6d7700ffc2779` | exit 0 |
| `git diff --exit-code 685472c...50a34d4... -- aidlc-docs/inception/requirements.md aidlc-docs/inception/design.md aidlc-docs/inception/tasks.md docs/ERC3009_DEPLOYMENT_GATE.md infra/contracts infra/aws` | exit 0 |
| `git diff --exit-code 685472c...50a34d4... -- work-orders package.json package-lock.json .gitignore .agent/CURRENT_STATE.md .agent/DECISIONS.md .agent/HANDOFF.md apps services/seller-service contracts infra docs` | exit 0 |
| `git diff --exit-code 16451a7...50a34d4... -- . ':(exclude).agent/outbox/WO-P6-01-coder.done.md'` | exit 0 |
| `git diff --check "$(git merge-base HEAD main)"..HEAD` | exit 0 |
| `git diff --name-only "$(git merge-base HEAD main)"..HEAD` | exit 0; includes the pre-packet Phase 6 planning relay |
| Work Order literal protected diff against `$(git merge-base HEAD main)` | exit 1; only because that merge-base is `8886745...`, before the approved planning packet |

The last exit 1 is not attributed to the coder. `8886745..685472c` contains the approved Phase 6 planning append, Work Orders, relay evidence, and worktree guard. The user-designated packet boundary is `685472c`; every protected diff from that base to the reviewed tip is zero.

### Independent negative probes

All probes were local and read-only except for in-memory repository mutations created inside the probe process:

| Probe command | Observed result |
|---|---|
| `node --input-type=module - <<'NODE'` calling `classifyReceipt()` with a BASE submission and same-hash `HISTORICAL_ON_CHAIN` receipt | Accepted: `{"accepted":true,"submissionSource":"BASE_SEPOLIA_VERIFIED","receiptSource":"HISTORICAL_ON_CHAIN"}` |
| `uv run --project services/buyer-audit-api python - <<'PY'` calling `confirm_mismatch()` with the bound hash but a HISTORICAL EVM proof | Accepted and charged: `MISMATCH_CONFIRMED`, terminal source `HISTORICAL_ON_CHAIN`, `spentUnits=200000` |
| Same Python form, recording three checks and a no-transfer proof whose scenario is run B against the run-A `localtx:` submission | Accepted: `RECONCILED_NO_TRANSFER`; submission run `aaaa...`, proof run `bbbb...` |
| Same Python form, placing a same-head persisted audit with `phase6.rules.old` before calling `AuditService.audit()` | Returned the old report although current ruleset is `phase6.rules.v1` |
| Same Python form, simulating an audit append race followed by a later access event before loser re-read | Returned stale audit; returned evidence head differed from current head and last event was not `AUDITED` |
| Same Python form, projecting an `AUDITED` event whose `evidenceHeadEventHash` is deliberately unrelated to its predecessor | Accepted as `AUDITED_NORMAL`, `audit_covers_head=True` |
| Same Python form, validating missing terminal guards and a non-hex settlement hash | Both rejected by Pydantic |
| Same Python form with fake Mongo collections recording `ensure_indexes()` call order | `unique_payment_mismatch_per_purchase` and `unique_payment_no_transfer_per_purchase` were created before `phase6_index_collision_report()` |

No public RPC, facilitator, ERC-8004, provider, Atlas, or AWS call/write was made. No live credentials, secret environment values, private keys, or canonical Mongo data were read.

## Findings

### High — H1 remains open: submitted evidence source is not bound to receipt/terminal proof source

`services/commerce-gateway/src/gateway.ts:208` constructs every hash-backed active intent as `BASE_SEPOLIA_VERIFIED`. `classifyReceipt()` at `gateway.ts:246-254` reconstructs the receipt reference using the receipt's declared source but compares only the hash to the submission. It accepts the same hash with `HISTORICAL_ON_CHAIN` and returns contradictory provenance.

Python repeats the gap. `core/payment.py:464-475` derives an EVM submission as BASE, while `confirm_mismatch()` at `payment.py:976-986` compares only transaction kind and hash. It never compares `proof.evidence_source` or `proof.transaction_ref.evidence_source` with the submission source. The probe terminalized the active purchase as historical and applied 200,000 units of actual spend.

This violates the discriminated truth model, the historical-read-only invariant, and the high-assurance evidence/payment boundary. Require exact source equality against the submitted reference in both gateway and core, and add same-hash/wrong-source negative tests. New EIP-3009 execution must not consume `HISTORICAL_ON_CHAIN` proof.

### High — H2 remains open: synthetic reconciliation can switch scenario runs after submission

`record_reconciliation_check()` validates the submission string, source, and presence of a scenario at `core/payment.py:879-897`, but never checks that the scenario equals the metadata on the original `PAYMENT_RECONCILIATION_REQUIRED` event or that its `runId` matches the bound local submission. `_assert_no_transfer_series()` at `payment.py:1193-1207` only proves that later checks and the final proof repeat each other's scenario.

The probe bound the intent to `localtx:aaaa...`, recorded all checks as scenario run `bbbb...`, and reached `RECONCILED_NO_TRANSFER`. Thus a consistent forged series from a different run can release the reservation and produce terminal evidence. Bind every synthetic check and terminal proof to the original reconciliation event's complete scenario metadata and to the local reference runId, then cover foreign-run checks and no-transfer proofs.

### High — H4 remains open: stale or wrong-ruleset audits can still be returned as current

The normal persisted path checks only `covers_head` at `core/audit.py:1013-1021`; it does not require `persisted.report.ruleset_version == RULESET_VERSION`. A same-head `phase6.rules.old` audit was returned under the current service.

The append-race recovery at `audit.py:1060-1064` calls `AuditReportReader.persisted()` and returns immediately, bypassing even the new head-coverage check. A realistic winner-audit/later-event/loser-read sequence returned a stale report.

The list/read projection has another inconsistent path: `_audit_projection()` at `core/projections.py:273-295` checks only that `AUDITED` is the last sequence. It does not validate that `evidenceHeadEventHash` equals the preceding event, so a malformed audit was presented as `AUDITED_NORMAL` with `audit_covers_head=True` even though `AuditReportReader` correctly rejects the same event.

P6-AC-03.5 requires both the same evidence head and ruleset. Centralize persisted-audit validation, require current ruleset and head coverage in normal and race paths, and make summary projection fail closed on the same malformed audit that detail/audit parsing rejects.

### Medium — M2 remains partially open: the collision report is not actually before every new Phase 6 unique index

`MongoEvidenceRepository.ensure_indexes()` creates `unique_payment_mismatch_per_purchase` and `unique_payment_no_transfer_per_purchase` at `adapters/repositories/mongo.py:497-516`; only afterward does it call `phase6_index_collision_report()` at lines 517-526. The report also has no duplicate grouping for those two per-purchase singleton keys.

The native test proves the report can find a terminal-outcome-key collision when invoked manually, then deletes that fixture before `ensure_indexes()`. It does not prove that `ensure_indexes()` reports every new-index collision before creation. The call-order probe confirmed both new singleton indexes precede preflight. Move a comprehensive read-only preflight ahead of every newly introduced Phase 6 unique index.

## Disposition of every prior finding

| Prior finding | Round-2 disposition | Independent basis |
|---|---|---|
| C1 | **RESOLVED** | All four terminal states return before seller/signing/submission; four focused gateway regressions pass. `SETTLED` alone may finish already-staged delivery. |
| H1 | **OPEN / PARTIAL** | True receipt union, unknown-source rejection, coordinate checks, and projection contradiction checks were added; same-hash wrong-source receipt/proof remains accepted as described above. |
| H2 | **OPEN / PARTIAL** | Full proof fingerprints, transaction-value binding, alias conflicts, and tested cross-purchase uniqueness were added; source binding and original synthetic-run binding remain incomplete. |
| H3 | **RESOLVED as originally reported** | Gateway requires both bounded attempts and configured finality (`gateway.ts:857-864`); zero/unknown confirmations stay nonterminal, and Python binds count/window/chain/submission/nonce/finality to recorded checks. The foreign-run issue is recorded under H2. |
| H4 | **OPEN / PARTIAL** | Sensitive access is now an explicit POST and ordinary sequential post-audit head advance fails closed. Current-ruleset reuse, race recovery, and summary projection still violate audit integrity. |
| M1 | **RESOLVED** | New terminal mutation schemas require expected state/count/head; stale guards conflict; schema and core reject malformed transaction hashes. |
| M2 | **OPEN / PARTIAL** | Collision aggregation and native evidence exist, but two new singleton indexes are created before the preflight and are absent from its coverage. |

## Scope rulings and preservation

1. **Index subset — accepted as packet scope.** Truth-model indexes belong here; reputation outbox/snapshot and scenario-run-store indexes stay with WO-P6-02/03. The separate local confirmed-outflow index is a reasonable realization of the discriminated union. This does not waive the M2 ordering/coverage defect.
2. **Optional gateway terminal capability — accepted as staged wiring.** `EvidenceHttpClient` terminal wiring is owned by the next packet; fail-closed behavior without an injected complete capability is appropriate here. This does not waive malformed proof acceptance in the gateway/core types.
3. **`PAYMENT_ATTEMPT_REJECTED` support — accepted.** The enum, unique key, and deterministic readers belong to the truth model; no scenario writer was added.
4. **Sensitive access decision — resolved and accepted.** The prior GET carve-out is gone. `POST /purchases/{id}/sensitive/{payloadId}/access` returns 201 and appends the access event; GET returns 405. User GET/list/detail/alerts/agents/wallet paths are zero-write in the supplied tests and inspected code.
5. **Evidence-only commits — allowed-path, no executable scope leak.** Commits `198ee43...`, `c9e05e7...`, `0c2f5c9...`, and `50a34d4...` each modify only `.agent/outbox/WO-P6-01-coder.done.md`. The first three remain a documented cadence deviation from the original one-follow-up expectation; the final post-correction evidence commit is justified by the no-amend/self-reference constraint. No history rewrite is requested.

Native Mongo tests verified tested terminal exclusivity, same-proof concurrency, attempt uniqueness, budget/outflow accounting, partial indexes, and legacy parsing. Source inspection and exact-base diff show no update/delete/backfill path for historical records. Legacy Permit2 remains readable and execution/reconciliation fail closed. No canonical history store was connected or changed.

## Required disposition

Do not integrate `50a34d4de646aa6f53b7abc516f6d7700ffc2779` or use it as the WO-P6-02 base. Preserve the branch and reports. Correct H1, H2, H4, and M2 within the approved WO-P6-01 boundaries, add regressions matching the reproduced probes, and rerun the full fixed-order suite plus native Mongo and both exact-base/main-relative protected checks.

REJECT
