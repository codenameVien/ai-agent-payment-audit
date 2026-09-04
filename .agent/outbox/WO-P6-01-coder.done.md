# WO-P6-01 Coder evidence — canonical core truth model

## Identity

- Work Order: `work-orders/WO-P6-01-core-truth-model.md` (SHA-256 `a93814a1e60c5d6c1fcc839365c75acbda3eb753400c3341e13c48a26cc48a6e`, read-only)
- Branch: `wo/P6-01` (no push, no merge, no rebase, no amend, no main commit)
- Base SHA: `685472c2178fac1ed1d16fedd4dde4dda7d3ed64`
- Implementation commit SHA: `449090805948f095f1d35373ffb7852f8f43a1c6`
  (message `feat(phase6): implement canonical truth model`; contains every source, test and
  handoff change listed below)
- Branch tip: the last commit on `wo/P6-01`, an evidence-only follow-up whose entire diff is this
  report file. A report cannot contain its own commit hash and the Work Order forbids `amend` and
  `reset`, so the implementation SHA above is the auditable anchor. Verify the tip with
  `git rev-parse HEAD`; `git diff main..HEAD` is the complete packet
  (implementation `449090805948f095f1d35373ffb7852f8f43a1c6` plus this evidence file).
- Commit message: `feat(phase6): implement canonical truth model`
- Delivery scope: `P6-DES-WO-01` only. No reputation outbox/publisher, no scenario catalog or
  fakes, no dashboard, no Playwright, no AWS readiness.

## Start gate

| Check | Result |
|---|---|
| `pwd` | `/Users/vien/MyProjects/PBL-coder` |
| Branch | `wo/P6-01`, worktree clean at start |
| `aidlc-docs/inception/requirements.md` | `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f` — matches WO |
| `aidlc-docs/inception/design.md` | `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768` — matches WO |
| `aidlc-docs/inception/tasks.md` | `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe` — matches WO |
| Task marker | `- [ ] **P6-01 — canonical core truth model...**` present with the WO's requirement IDs |
| `docs/ERC3009_DEPLOYMENT_GATE.md` | `62fa20da465b450daf8e92cdbc084192470232fe36fec24b52cda028d5cd3208` (before) |
| TCP 27019 | free before and after the run; no foreign listener was signalled |
| Secret env | `MONGODB_URI`, `TEST_MONGODB_URI`, `RPC_URL`, `FACILITATOR_URL`, AWS/provider/wallet secrets never sourced or printed |

## Changed files (all inside the allowed-write list)

### Buyer/Audit source

- MODIFY `services/buyer-audit-api/src/buyer_audit_api/core/models.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/core/payment.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/core/audit.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/core/ports.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/core/projections.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/memory.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/mongo.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/api/schemas.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/api/app.py`

### Payment Executor source

- MODIFY `services/commerce-gateway/src/contracts.ts`
- MODIFY `services/commerce-gateway/src/gateway.ts`

### Tests

- NEW `services/buyer-audit-api/tests/test_phase6_projections.py`
- NEW `services/buyer-audit-api/tests/test_phase6_audit.py`
- NEW `services/buyer-audit-api/tests/test_phase6_payment_terminal.py`
- MODIFY `services/buyer-audit-api/tests/test_payment_service.py`
- MODIFY `services/buyer-audit-api/tests/test_api.py`
- MODIFY `services/buyer-audit-api/tests/test_mongo_repository.py`
- NEW `services/commerce-gateway/tests/phase6-truth-model.test.ts`
- MODIFY `services/commerce-gateway/tests/gateway.test.ts`

### Handoff evidence

- APPEND ONLY `.agent/TURN_LOG.md`
- NEW `.agent/outbox/WO-P6-01-coder.done.md`

`git status --porcelain` shows exactly these 20 paths (15 modified, 5 new) and nothing else.
`node_modules/`, `dist/` and `services/buyer-audit-api/.venv/` are gitignored build artifacts.

## Verification commands — fixed order

| # | Command | Exit | Key counts |
|---|---|---|---|
| 1 | `uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests` | 0 | `All checks passed!` |
| 2 | `uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src` | 0 | `no issues found in 44 source files` |
| 3 | `uv run --project services/buyer-audit-api pytest .../test_phase6_projections.py .../test_phase6_audit.py .../test_phase6_payment_terminal.py .../test_payment_service.py .../test_api.py -q` | 0 | `109 passed` (37 projection, 19 audit, 25 terminal, 19 payment-service, 9 API) |
| 4 | `npm run build --workspace @pbl/commerce-gateway` | 0 | `tsc` clean |
| 5 | `node --test services/commerce-gateway/dist/tests/gateway.test.js services/commerce-gateway/dist/tests/phase6-truth-model.test.js` | 0 | `# tests 37 / # pass 37 / # fail 0` (20 legacy + 17 new) |
| 6 | `npm run test:mongo:local` | 0 | `4 passed` |
| 7 | `git diff --check` | 0 | no whitespace errors |

Additional regression sweeps (not required by the WO, run for safety, no assertions weakened):

- `pytest services/buyer-audit-api/tests -q -m 'not mongo'` → exit 0, `159 passed, 4 deselected`.
- `npm test --workspace @pbl/commerce-gateway` (full gateway suite) → exit 0, `# pass 52`.

Reviewer re-verification commands were left to the Reviewer at the same SHA; the protected-path
check was run here and is reported below.

## Terminal race, idempotency, and read-side evidence

| Behaviour | Evidence |
|---|---|
| Terminal exclusivity under race (memory) | `test_only_one_terminal_outcome_survives_a_race`: concurrent `confirm_mismatch` + `reconcile_no_transfer` + `fail_confirmed` → exactly 1 succeeded, exactly 1 terminal event, exactly 1 `terminalOutcomeKey` (`terminal:<purchaseId>`), `reserved_units == 0` |
| Terminal exclusivity under race (native Mongo) | `test_real_mongo_terminal_outcome_is_exclusive_under_concurrency`: 1 of 3 terminal calls succeeded, `purchaseEvents` terminal count `== 1`, `payload.terminalOutcomeKey` count `== 1`, verified hash chain, `reserved_units == 0`, ≤1 `confirmedOutflows` row |
| Same proof retry | `test_same_mismatch_proof_returns_the_existing_terminal_result` (8 concurrent identical proofs → identical result, 1 event, 1 outflow, `spent == 200000`); `test_no_transfer_releases_the_reservation_exactly_once` (8 concurrent identical proofs → 1 event, single release); native Mongo `test_real_mongo_reconciliation_attempts_and_actual_spend_are_exact` (6 concurrent identical proofs all return `MISMATCH_CONFIRMED`) |
| Conflicting proof | `test_different_mismatch_proof_on_a_terminal_payment_is_a_conflict` (no second event, no budget delta); API `409` for a conflicting reconciliation attempt, stale head, stale `expected_state`, and post-mismatch `reconcile-no-transfer` |
| Attempt uniqueness | `test_same_attempt_evidence_is_idempotent_and_different_evidence_conflicts`, `test_reconciliation_attempts_must_be_consecutive`, `test_concurrent_checks_keep_one_event_per_attempt_number`; native Mongo 8-way concurrent attempt 1 → exactly 1 `PAYMENT_RECONCILIATION_CHECKED` document |
| Reservation / spend exactness | same-token mismatch replaces `Q` reservation with actual `A` in `spent` (200000); wrong-token releases `Q` and records the alternate-token outflow only in `confirmedOutflows` with no policy row created; wrong-recipient spends `Q`; no-transfer and confirmed failure release exactly once with `spent == 0` |
| GET zero-write | `test_read_paths_never_change_the_evidence_head`: `/purchases`, `/purchases/{id}`, `/purchases/{id}/events`, `/audit-alerts`, `/agents`, `/wallet` leave `event_count` and `head_event_hash` byte-identical; detail returns `audit: null` and alerts stay `[]` until an explicit audit POST |
| A09 intermediate state | `test_bounded_reconciliation_reaches_a_terminal_no_transfer` asserts `PAYMENT_CONFIRMATION_UNKNOWN + PENDING_AUDIT` with 0 terminal audit/alerts, then `RECONCILED_NO_TRANSFER + AUDITED_RISK` with the earlier events preserved |
| Native Mongo old-document compatibility | `test_real_mongo_phase6_indexes_are_partial_and_old_documents_stay_readable`: pre-existing `PAYMENT_SETTLED` rows without `terminalOutcomeKey` do not block index creation (`ensure_indexes` run twice), and a legacy Permit2 `paymentIntents` document without `reconciliation`/`actualTransfer` parses with safe defaults |
| Index contract | asserted names and keys: `unique_phase6_terminal_outcome`, `unique_phase6_reconciliation_attempt`, `unique_phase6_rejected_attempt`, `unique_payment_mismatch_per_purchase`, `unique_payment_no_transfer_per_purchase`, `unique_confirmed_outflow_terminal`, `unique_confirmed_outflow_transaction`, `unique_confirmed_outflow_transaction_local` |
| Type confusion | Python: `localtx:` rejected as `transactionHash`, `0x` rejected as `localTransactionId`, both-fields payload rejected, uppercase/short hash rejected, synthetic EVM ref rejected, foreign-run local ref rejected. API: 4 confused `transaction_ref` bodies → `422`. Node: `evmTransactionRef`/`localTransactionRef`/`assertTransactionRef` reject the same cases |
| Synthetic never becomes a chain hash | projection exposes `transaction_hash = null` for synthetic terminals; gateway mismatch proof for a local receipt contains no EVM hash (`JSON.stringify(proof)` asserted free of the hash) |

## Two real defects found by native Mongo and fixed

1. A same-proof terminal retry could lose the unique-index race and surface `409` instead of the
   committed result. `transition_payment_intent` now retries same-purchase Phase 6 duplicate-key
   losses so the CAS state comparison decides: identical proof returns the committed intent, a
   different proof still conflicts.
2. Cross-purchase reuse of one transaction reference had to stay a hard conflict while
   same-purchase retries did not. `_transaction_ref_belongs_elsewhere` distinguishes the two, and
   the in-memory reference repository now classifies both cases with the same messages.

## Preservation checks

- [x] Existing successful/failed/incomplete fixtures parse without byte mutation or backfill —
      legacy event and payment-intent documents were inserted verbatim and read back; the whole
      pre-existing suite (159 non-mongo tests) passes unchanged apart from the two intentional
      test updates listed above.
- [x] Historical Permit2 execute/reconcile rejection preserved — gateway still rejects with
      `read-only` (`gateway.test.ts` and `phase6-truth-model.test.ts`), and the three new terminal
      operations fail closed for `transfer_method != "eip3009"` in memory and native Mongo.
- [x] Canonical history DB was never connected, cloned, seeded, migrated or cleaned. Only
      ephemeral `pbl_phase1_test_*` / `pbl_phase6_*_test_*` databases on the script's loopback
      replica set were used, each dropped in `finally`.
- [x] Protected diff is zero against the base commit:
      `git diff --exit-code 685472c..HEAD -- aidlc-docs/inception/{requirements,design,tasks}.md docs/ERC3009_DEPLOYMENT_GATE.md infra/contracts infra/aws work-orders package.json package-lock.json .gitignore .agent/{CURRENT_STATE,DECISIONS,HANDOFF}.md`
      → exit 0, and `git diff --name-only 685472c..HEAD` lists only the 21 allowed-write paths.
      After-hashes are identical to the start gate: requirements `65824ffa…`, design `e89ab056…`,
      tasks `97eedc61…`, ERC-3009 gate `62fa20da…`.

  **Reviewer note on `merge-base`.** The Work Order's reviewer command uses
  `$(git merge-base HEAD main)`, which resolves to `8886745` here because `main` does not yet
  contain the planning baseline commit `685472c` (`git merge-base --is-ancestor 685472c main`
  exits 1; the Phase 6 planning append lives on `feature/phase6-audit-e2e`). Run against that
  merge-base the protected-path diff is non-zero, but every byte of it is the Planner's already
  approved Phase 6 append block plus the `.gitignore`/`.agent` planning commits — none of it comes
  from this packet. Use `685472c..HEAD` (or integrate the planning baseline into `main` first) to
  see this packet's true diff.
- [x] Zero public RPC / facilitator / ERC-8004 / provider / AWS commands or writes. No network
      calls beyond `npm ci --ignore-scripts` (registry, lockfile unchanged) and the loopback Mongo.
- [x] Native Mongo teardown: after the run `lsof -iTCP:27019 -sTCP:LISTEN` finds nothing,
      `pgrep -f "mongod --dbpath /private/tmp/pbl-mongo-test"` finds nothing, and
      `/private/tmp/pbl-mongo-test.*` no longer exists. No foreign listener was signalled.
- [x] No raw prompt/response, signature, full authorization nonce or secret in source, tests or
      output. Authorization nonces are only ever recorded as `sha256:` digests; a secret-pattern
      scan over the new files returns no matches; live/historical transaction hashes and purchase
      IDs are not reused anywhere in the diff.

## Completion criteria

- [x] Seven non-conflated payment statuses plus the four audit statuses and the three evidence
      sources (and the honest `null` absence) are fixed by table-driven unit and API tests.
- [x] The local/EVM union rejects type confusion in Python, in the FastAPI request schemas and in
      the Payment Executor; synthetic proofs never produce a legacy transaction hash.
- [x] Mismatch / no-transfer / failed / settled terminals are mutually exclusive under race in
      both repositories, with correct same-proof and conflicting-proof semantics.
- [x] `reserved`, `spent` and `confirmedOutflows` move exactly once for amount, token and
      recipient mismatches and for no-transfer.
- [x] Structured Phase 6 rules and the legacy finding parser both pass, including the
      `AUD-QUOTE-PAYMENT-MISMATCH` exact `mismatchedFields` for A04/A05/A06, A08's
      `AUD-FACILITATOR-SUCCESS-WITHOUT-TRANSFER`, A09's `AUD-PAYMENT-RECONCILED-NO-TRANSFER`,
      A11's `AUD-PAYMENT-FAILED`, A07's duplicate/nonce-reuse rules, A02's
      `AUD-ELIGIBLE-CANDIDATE-EXCLUDED` and A10's semantic warning ceiling.
- [x] Every GET/list/detail/alerts/agents path returns persisted evidence only and leaves the
      event count and head hash unchanged.
- [x] Native Mongo indexes, CAS, concurrency and old-document compatibility pass.
- [x] No tracked or untracked change outside the allowed-write list.
- [x] All focused commands exit 0 with one coherent commit and this handoff evidence.

## Commands not run, and why

- `npm run lint`, `npm test`, `npm run test --workspace @pbl/dashboard`, the seller/dashboard
  builds, `docker compose ... config`, `npm audit --omit=dev`: not part of this WO's fixed command
  block; the broad matrix belongs to WO-P6-04. The gateway build/test and the full Python suite
  were still run to prove no regression in the surfaces this WO touches.
- `npm run test:e2e:local`: the script and harness are WO-P6-03/WO-P6-04 deliverables and do not
  exist yet.
- `terraform`, AWS CLI/SDK, Atlas, ECR, real RPC/facilitator/ERC-8004/provider calls: forbidden by
  this WO and never attempted.

## Scope decisions the Reviewer should confirm

1. **Index subset.** Only truth-model indexes were created (terminal outcome, reconciliation
   attempt, rejected attempt, the two new terminal singletons, and the `confirmedOutflows` set).
   `reputationSnapshots`, `reputationOutbox`, `scenarioRunMetadata` and
   `unique_reputation_decision_per_purchase` are owned by WO-P6-02/WO-P6-03 together with the code
   that writes them; creating them here would be dead schema. Design §18.6.4 names
   `unique_confirmed_outflow_transaction` for "EVM hash or `(runId, localTransactionId)`, mutually
   exclusive partial unique indexes"; the EVM variant keeps that exact name and the LOCAL variant
   is `unique_confirmed_outflow_transaction_local`.
2. **Gateway terminal capability.** Design §18.5.3 adds the terminal operations to the gateway
   `EvidenceApi`, but `src/adapters/http.ts` (the class that implements it) is in WO-P6-02's
   allowed writes, not this WO's. `EvidenceApi` therefore extends
   `Partial<TerminalPaymentEvidenceApi>` and `CommerceGateway` requires an explicitly injected,
   runtime-checked `TerminalPaymentEvidenceApi` before it emits any terminal proof. Without that
   capability the gateway keeps today's fail-closed unknown behaviour and invents nothing — locked
   by `gateway.test.ts`'s "without the terminal Evidence API no terminal outcome is ever invented".
   WO-P6-02 completes the HTTP wiring.
3. **`PAYMENT_ATTEMPT_REJECTED`.** The event type, its unique partial index and the
   `AUD-DUPLICATE-PAYMENT-ATTEMPT` / `AUD-ERC3009-NONCE-REUSE` rules that read it are here because
   the deterministic ruleset is this WO's; the scenario-only writer
   (`recordRejectedPaymentAttempt`) remains WO-P6-03's.
4. **`SENSITIVE_PAYLOAD_ACCESSED`.** `GET /purchases/{id}/sensitive/{payloadId}` still appends its
   access record. `P6-AC-03.3` forbids read handlers from appending audit, reconciliation and
   feedback events; this is a pre-existing security access log on the encrypted-payload boundary,
   and removing it would weaken an AGENTS.md high-assurance control. The zero-write test therefore
   covers list/detail/events/alerts/agents/wallet and excludes that one deliberate write. Flagging
   it for an explicit Planner ruling rather than changing it silently.
5. **`WalletPolicy` invariant.** Per design §18.6.4 the constructor no longer rejects
   `spent + reserved > dailyLimit`, so a proof-confirmed mismatch stays readable; the claim guard
   still rejects every further payment in that state
   (`test_confirmed_mismatch_over_the_daily_limit_stays_readable_and_blocks_new_claims`).
6. **Report filename.** The Work Order's allowed-write list names
   `.agent/outbox/WO-P6-01-coder.done.md`, so this report uses that exact path.

## Background processes and ports

No long-running process was started. The only child processes were the verification commands and
the `mongod` instance started and stopped by `scripts/test_mongo_local.sh`, which shut down
cleanly and removed its own `/private/tmp/pbl-mongo-test.*` directory. Nothing is listening on
27019 and no foreign process was signalled.

READY_FOR_REVIEW
