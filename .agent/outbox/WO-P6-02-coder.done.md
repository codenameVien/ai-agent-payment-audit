# WO-P6-02 Coder evidence — terminal-audit reputation loop

## Identity and anchors

- Work Order: `work-orders/WO-P6-02-reputation-loop.md`
  (amended SHA-256 `b8385701c290d93d7e647c6c3364274ec5409e6dd81c9fa37678c179d3b4fc53`, read-only)
- Planner amendment receipt: `.agent/outbox/WO-P6-02-planner-amendment.done.md`, `READY_FOR_CODER`
- **Planner amendment commit:** `81204777c94e0c2ecad5c46de7c16f7f28e645cf`
  (`docs(phase6): amend WO-P6-02 event-type scope`)
- **Base / integration merge SHA:** `b8f3f3fd92a4f1d13ccad539899a5ff6fb3599ce`
  (`merge: apply WO-P6-02 planner scope amendment`) — this is the packet base
- Predecessor coder tip: `ca037c9c9e54a1701db995c90dd5d976df83dd4e` (ancestor of the base)
- Predecessor approval: `/Users/vien/MyProjects/PBL/.agent/outbox/WO-P6-01-review-r3.md`, `APPROVE`
- Preserved blocker commit: `0c10b91ce1067ffe3f245a3aef11b032cff9f249` and
  `.agent/outbox/WO-P6-02-blocked.md` are untouched and still in history
- Branch: `wo/P6-02` (no push, merge, rebase, reset, stash, amend, main commit)
- **Implementation commit:** `c201b391b77128086663e3c047c2fcf737b179cd`
  (`feat(phase6): automate reputation loop`) — contains every source, test and TURN_LOG change
- Branch tip: the last commit on `wo/P6-02`. A report cannot contain its own hash and the WO
  forbids `amend`, so if an evidence-only follow-up exists its sole diff is this file and the
  implementation SHA above stays the auditable anchor. Verify with `git rev-parse HEAD`.
- Delivery scope: `P6-DES-WO-02` only. No scenario controller/catalog, no dashboard, no
  Playwright, no AWS readiness.

## Start gate

| Check | Result |
|---|---|
| worktree / branch | `/Users/vien/MyProjects/PBL-coder`, `wo/P6-02`, clean at start |
| base contains amendment | `git merge-base --is-ancestor 8120477 HEAD` exit 0 |
| base contains prior integration | `git merge-base --is-ancestor feb13f9 HEAD` exit 0 |
| base contains blocker commit | `git merge-base --is-ancestor 0c10b91 HEAD` exit 0 |
| base contains predecessor tip | `git merge-base --is-ancestor ca037c9 HEAD` exit 0 |
| `aidlc-docs/inception/requirements.md` | `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f` — matches WO |
| `aidlc-docs/inception/design.md` | `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768` — matches WO |
| `aidlc-docs/inception/tasks.md` | `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe` — matches WO |
| amended WO hash | matches the Planner receipt exactly |
| TCP 27019 | free before and after (`lsof` exit 1); no foreign process signalled |
| Secret env | no `.env`, private key, RPC URL, provider or AWS credential read or printed |

## Amendment boundary — exactly what was permitted

`git diff <base>..HEAD -- core/models.py` is exactly two added lines:

```diff
     REPUTATION_RECORDED = "REPUTATION_RECORDED"
+    REPUTATION_DECIDED = "REPUTATION_DECIDED"
+    REPUTATION_PUBLICATION_CONFLICT = "REPUTATION_PUBLICATION_CONFLICT"
     EVIDENCE_ANCHORED = "EVIDENCE_ANCHORED"
```

No other member, value or order changed. No parallel event enum exists anywhere
(`grep -rn "StrEnum" core/reputation.py` declares only `DecisionKind`,
`ReputationReason`, `FreshnessStatus`, `OutboxStatus` — none of which is an event type and none of
which is ever passed to `append_event`). No existing event type was overloaded to stand in for the
two new ones.

## Changed files — 29 paths, all inside the amended allowed-write list

### Buyer/Audit source
- MODIFY `core/models.py` (the two enum members only)
- NEW `core/reputation.py`
- NEW `core/terminal.py`
- MODIFY `core/ports.py`
- MODIFY `adapters/repositories/memory.py`
- MODIFY `adapters/repositories/mongo.py`
- NEW `adapters/reputation_gateway.py`
- MODIFY `domains/ai_inference/{models,ports,workflow,selection}.py`
- MODIFY `api/schemas.py`, `api/app.py`
- MODIFY `composition.py`

### Payment Executor source
- MODIFY `src/contracts.ts`, `src/erc8004.ts`, `src/adapters/http.ts`,
  `src/adapters/viem-erc8004.ts`, `src/main.ts`

### Tests
- NEW `tests/test_phase6_reputation.py`, `tests/test_phase6_terminal.py`
- MODIFY `tests/test_ai_inference_workflow.py`, `tests/test_ai_inference_domain.py`,
  `tests/test_api.py`, `tests/test_mongo_repository.py`
- NEW `services/commerce-gateway/tests/phase6-reputation-loop.test.ts`
- MODIFY `services/commerce-gateway/tests/erc8004.test.ts`, `tests/runtime.test.ts`

### Handoff evidence
- APPEND `.agent/TURN_LOG.md`
- NEW `.agent/outbox/WO-P6-02-coder.done.md`

`git diff --name-only <base>..HEAD | grep -E '^(aidlc-docs|docs|infra|apps|tools|work-orders|package)'`
returns **0** paths.

## Verification commands — fixed order

| # | Command | Exit | Key counts |
|---|---|---|---|
| 1 | `uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests` | 0 | `All checks passed!` |
| 2 | `uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src` | 0 | `no issues found in 47 source files` |
| 3 | `uv run --project services/buyer-audit-api pytest .../test_phase6_reputation.py .../test_phase6_terminal.py .../test_ai_inference_workflow.py .../test_ai_inference_domain.py .../test_api.py -q` | 0 | `95 passed` (35 reputation, 24 terminal, 19 workflow, 9 domain, 14 API — the pytest module split; 95 total) |
| 4 | `npm run build --workspace @pbl/commerce-gateway` | 0 | `tsc` clean |
| 5 | `node --test .../erc8004.test.js .../phase6-reputation-loop.test.js .../runtime.test.js` | 0 | `# tests 26 / # pass 26 / # fail 0` |
| 6 | `npm run test:mongo:local` | 0 | `8 passed` |
| 7 | `git diff --check` | 0 | no whitespace errors |

Additional preservation sweeps (no assertion weakened):

- `pytest services/buyer-audit-api/tests -q -m 'not mongo'` → exit 0, `265 passed, 8 deselected`.
- `npm test --workspace @pbl/commerce-gateway` (full gateway suite) → exit 0, `# pass 77`.
- `git diff --check "$(git merge-base HEAD main)"..HEAD` → exit 0.
- Protected diff against the **packet base** `b8f3f3f` for
  `aidlc-docs/inception/{requirements,design,tasks}.md docs/ERC3009_DEPLOYMENT_GATE.md
  infra/contracts infra/aws apps/dashboard tools work-orders package.json package-lock.json
  .gitignore` → exit 0.
- The WO's literal reviewer command uses `$(git merge-base HEAD main)` = `8886745`, which predates
  the approved Phase 6 planning packet, so it reports the planning appends as a diff. This is the
  same documented artifact the WO-P6-01 reviewer recorded and is not a coder change: I touched no
  protected path (count 0 above).

## Decision mapping table results (`P6-AC-05.2`)

| Evidence | Decision | Reason codes | Test |
|---|---|---|---|
| settled + delivered as quoted | `PUBLISH(100)` | `SELLER_DELIVERED_AS_QUOTED` | `test_seller_that_delivered_as_quoted_publishes_100` |
| buyer selection/explanation risk only | `PUBLISH(100)` | `+ BUYER_ATTRIBUTED_FINDINGS_ONLY` | `test_buyer_attributed_findings_still_publish_100` |
| semantic advisory only | `PUBLISH(100)` | unchanged | `test_semantic_only_advisory_never_lowers_the_seller_score` |
| confirmed mismatch | `PUBLISH(0)` | `PAYMENT_MISMATCH_CONFIRMED` | `test_confirmed_payment_failure_publishes_zero[MISMATCH_CONFIRMED]` |
| reconciled no-transfer | `PUBLISH(0)` | `PAYMENT_RECONCILED_NO_TRANSFER` | same, `[RECONCILED_NO_TRANSFER]` |
| seller-attributed confirmed failure | `PUBLISH(0)` | `SELLER_ATTRIBUTED_PAYMENT_FAILURE` | same, `[FAILED]` |
| delivery integrity failure | `PUBLISH(0)` | `DELIVERY_INTEGRITY_FAILURE` | `test_delivery_integrity_failure_publishes_zero` |
| `PAYMENT_CONFIRMATION_UNKNOWN` | `DEFER` | `PAYMENT_CONFIRMATION_UNKNOWN` | `test_payment_confirmation_unknown_defers_and_never_publishes` |
| identity unverified | `DEFER` | `SELLER_IDENTITY_UNKNOWN` | `test_unverified_seller_identity_defers` |
| conflicting proof | `DEFER` | `CONFLICTING_PROOF` | `test_conflicting_proof_defers` |
| attribution insufficient | `DEFER` | `ATTRIBUTION_INSUFFICIENT` | `test_insufficient_attribution_defers` |
| older ruleset / nonterminal | `DEFER` | `AUDIT_NOT_TERMINAL` | `test_an_older_ruleset_audit_cannot_drive_a_publication`, `test_a_nonterminal_payment_defers_instead_of_publishing` |

A published value is structurally only `100` or `0`, and a deferred decision carries no value
(`test_a_published_value_is_only_ever_100_or_0`, `test_a_deferred_decision_carries_no_value`).

## Two-worker / restart / PREPARED / SUBMITTED_UNKNOWN / conflict / confirmed-only

| Behaviour | Evidence |
|---|---|
| audit + decision + job appear together or not at all | `test_settled_delivery_finalizes_audit_decision_and_job_together` asserts the last two events are `AUDITED, REPUTATION_DECIDED`, the chain verifies, and the decision payload carries `publishIdentityHash`, `payloadFingerprint`, the audit bundle hash and the current ruleset |
| unknown/preview creates zero jobs | `test_a_nonterminal_purchase_creates_no_decision_and_no_job` (no `AUDITED`, no `REPUTATION_DECIDED`, no job by identity) |
| two concurrent finalizes → one decision, one job | `test_two_workers_finalizing_concurrently_create_one_decision_and_one_job` |
| ten concurrent native-Mongo claims → one lease | `test_real_mongo_reputation_outbox_is_exactly_once_under_races` (1 winner of 10, `attempt_count == 1`) |
| lease CAS | `test_only_one_worker_holds_the_lease_at_a_time`, `test_a_worker_without_the_lease_cannot_move_a_job`, native `lease is not held` probe |
| expired lease is reclaimable and counted | `test_an_expired_lease_is_reclaimable_and_counts_the_attempt` |
| PREPARED records the commitment, binds the reference once | `test_a_prepared_job_is_never_prepared_twice_with_another_transaction`, `phase6-reputation-loop.test.ts` "the intent to submit is persisted before the irreversible write" (journal order `claim, findFeedback, markPrepared, giveFeedback, confirm, markConfirmed`; the prepare call carries **no** transaction reference) |
| restart recovery finds the recorded job | `test_a_recovered_job_is_still_claimable_after_a_restart` |
| PREPARED/SUBMITTED_UNKNOWN never build a second transaction | `phase6-reputation-loop.test.ts` PREPARED-crash and SUBMITTED_UNKNOWN cases assert zero further `giveFeedback` |
| find-before-submit | gateway test: existing on-chain feedback confirms with zero writes |
| different fingerprint can never move a job | `test_a_different_fingerprint_can_never_move_a_job` |
| conflict stops the job without a new transaction | `test_a_conflict_stops_the_job_without_a_new_transaction` (terminal, no transaction reference, nothing claimable) |
| a confirmed publication cannot become a conflict | `test_a_confirmed_publication_cannot_be_turned_into_a_conflict` |
| one feedback transaction cannot back two jobs | `test_one_feedback_transaction_cannot_back_two_jobs` and the native cross-job probe |
| confirmed-only recording | `test_confirmation_requires_a_matching_agent_value_and_transaction` (agent/value/transaction rejections leave the job `PREPARED`); gateway `confirm()` returns a proof built from receipt status + registry + decoded `NewFeedback`, and `null` never yields `CONFIRMED` |
| confirmed is terminal and idempotent | `test_a_confirmed_job_is_terminal_and_idempotent` (same proof 200, different receipt conflicts, nothing claimable after) |
| recovery sweep only finalizes what lacks a decision | `test_the_recovery_sweep_only_finalizes_purchases_without_a_decision`, `test_rerunning_the_sweep_is_free_of_new_effects` |
| a `localtx:` feedback submission works for synthetic runs | `test_a_local_feedback_submission_is_accepted_for_a_synthetic_run` |
| HTTP surface semantics | `test_reputation_outbox_transitions_are_leased_typed_and_idempotent`: 401 unauthenticated, 404 missing job / nothing claimable, 422 malformed feedback hash and wrong-agent proof, 409 wrong worker and different confirmation, 200 same-proof retry; 503 when the store is unconfigured (`test_reputation_outbox_is_disabled_without_a_configured_store`) |
| finalize head guard | `test_terminal_finalize_creates_one_decision_and_one_claimable_job` (stale head 409, idempotent second call, 503 without a coordinator) |

## Snapshot provenance / mean-neutral / hard filter / sequential selection

| Behaviour | Evidence |
|---|---|
| full query provenance stored and returned | `test_a_snapshot_records_its_full_query_provenance` (chain, registry, agent, trusted clients, tags, from/to block, queriedAt, event count, raw values with decimals/block/logIndex, aggregation method, evidence source, snapshot hash) |
| decimals-normalized arithmetic mean | `test_the_derived_score_is_the_decimals_normalized_arithmetic_mean` (`100`, `0`, `10000@2dp` → 66.67) |
| untrusted client / wrong tag excluded | `test_untrusted_clients_and_wrong_tags_are_excluded_from_the_mean`, `test_a_snapshot_cannot_hold_an_untrusted_or_mismatched_event` |
| no evidence → neutral 50 + `NO_EVIDENCE` | `test_no_matching_evidence_is_neutral_fifty_and_labelled` |
| stale labelling and query-failure fallback | `test_a_snapshot_older_than_the_window_is_stale_not_fresh`, `test_a_query_failure_reuses_a_fresh_snapshot_as_stale`, `test_a_query_failure_without_a_fresh_snapshot_is_no_evidence_fifty` |
| snapshots are immutable | `test_snapshots_are_immutable_once_stored`, native `test_real_mongo_reputation_snapshots_are_immutable` (same answer is a no-op, a different answer for the same query+block conflicts) |
| provider-level sharing across models | `test_one_seller_agent_yields_one_snapshot_for_all_its_models` (one provider call, one `snapshot_id`) |
| model benchmark vs agent reputation kept separate | `ReputationScoreEvidence` is a distinct field; no scoring path reads `benchmark.reputation_score` (`grep` returns nothing in `domains/ai_inference/*.py`) |
| sequential score influence | `test_provider_reputation_is_persisted_and_changes_the_winner`, `test_provider_reputation_decides_between_otherwise_equal_candidates` |
| resume scores from the persisted snapshot | `test_resuming_after_quoted_scores_from_the_persisted_snapshot` (zero further provider calls) |
| provider failure is neutral, never favourable | `test_a_failing_reputation_provider_falls_back_to_neutral_fifty`, `test_a_candidate_without_reputation_evidence_scores_neutral_fifty` |
| hard-filter precedence | `test_reputation_never_rescues_a_hard_filtered_candidate` (reputation 100 + `available=False` stays rejected while an eligible reputation-0 candidate wins). Structural too: `selection.py` `continue`s on any rejection reason **before** the `components` dict exists |
| weights unchanged | `reputation: 10` in all four presets; `test_weight_presets_match_approved_requirements` still passes, so `AUD-SELECTION-WEIGHTS-MISMATCH` cannot fire |

## Live / outbound / write evidence

- `ERC8004_WRITE_MODE` unset or empty resolves to `"disabled"`; `"fake"` throws
  `ERC8004_WRITE_MODE=fake is not allowed: production roots never fake a chain`; anything other
  than `disabled`/`live` throws. `Erc8004ReputationPublisher` also defaults `writeMode` to
  `"disabled"` in its own constructor, so an unwired caller cannot submit.
- The publisher throws `reputation writer is disabled` **after** the find-existing step, so a
  recovery of an already-published effect still completes without a write.
- No `giveFeedback` is reachable without a prior persisted `markPrepared`; the gateway journal test
  asserts the order.
- No code path treats a transaction-shaped string as success: `ReputationReceiptConfirmer.confirm`
  returns `ConfirmedFeedbackProof | null` built from the receipt plus a decoded `NewFeedback`
  event, and `ConfirmedFeedbackProof` requires a receipt proof reference, exact `100`/`0`,
  `valueDecimals == 0`, non-negative coordinates and a lowercase client address.
- Zero real ERC-8004, RPC, facilitator, provider, AWS or Atlas calls. Every Python test uses the
  in-memory repository or the native loopback replica set; every Node test uses in-process fakes.
  `npm run test:mongo:local` starts and stops its own `mongod` on 27019 via
  `scripts/test_mongo_local.sh`.
- No canonical or historical MongoDB database was connected, mutated, backfilled or cleaned. The
  new indexes are created only after the read-only M2 collision preflight, which now also covers
  `unique_reputation_decision_per_purchase`, `unique_reputation_publish_identity`,
  `unique_reputation_evm_transaction`, `unique_reputation_local_transaction` and
  `unique_reputation_snapshot_query`; the existing call-order and coverage-drift tests were
  extended to include them.
- Existing `REPUTATION_RECORDED` history and the legacy on-chain feedback parser still read without
  backfill: the enum member and every existing reader in `api/app.py` are unchanged.

## Design decisions the Reviewer should confirm

1. **`PREPARED` records the payload commitment, not a fabricated transaction reference.** An EVM
   hash does not exist before broadcast. The first implementation of this slice derived a
   `SYNTHETIC_LOCAL` `localtx:` placeholder for a live submission; I removed it, because inventing
   synthetic provenance for a real write is exactly the source confusion WO-P6-01 hardened against
   (H1). `PREPARED` therefore freezes the immutable feedback hash, and the transaction reference is
   bound **exactly once** by whichever step first knows it, then frozen. A different reference for
   a bound job is a conflict, never a second submission. `ReputationPublishJob` enforces this:
   `PREPARED` requires the feedback hash, `SUBMITTED_UNKNOWN` requires the reference, `CONFIRMED`
   requires both plus the receipt proof.
2. **Failure reason codes are derived from the event-backed projection**, not the mutable
   `paymentIntents` document, so a lagging or absent intent cannot change an objective reason. A
   terminal intent state that contradicts the projection is treated as conflicting proof and
   defers (`_STATUS_FOR_STATE` cross-check).
3. **`AuditService.audit()` was not taken over.** Design 18.7 says only the coordinator appends
   `AUDITED`, but `core/audit.py` is not in this WO's allowed-write list and its behaviour is
   WO-P6-01-approved. The coordinator therefore appends the audit when none exists and otherwise
   **reuses the persisted current audit**, appending only the decision and the job — still
   atomically, still under the caller's head CAS. `P6-AC-03.5` still governs: a stale or
   wrong-ruleset persisted audit blocks the decision
   (`test_a_stale_persisted_audit_blocks_the_decision`).
4. **The Base Sepolia reputation registry is a protocol constant.** `settings.py` is not in the
   allowed-write list and has no `erc8004_reputation_registry`, so
   `BASE_SEPOLIA_REPUTATION_REGISTRY` lives in `core/reputation.py` and matches the Payment
   Executor's compiled-in default.
5. **The gateway query direction.** `POST /reputation-query` on the Payment Executor is read-only
   (log query only) and bearer-protected by the existing `GATEWAY_SERVICE_TOKEN` guard; the buyer
   calls it through `adapters/reputation_gateway.py`. No new write surface was added.
6. **`conftest.py` is not writable**, so the API tests wire the coordinator and outbox with
   `dataclasses.replace(container, ...)` inside `test_api.py`.

## Pre-existing expectations that changed, and why

- `erc8004.test.ts`: the two publisher tests were rewritten against the durable outbox. The old
  `ReputationEvidenceApi` (`prepare`/`record`) and the in-process `#publishing` Map no longer
  exist, so "concurrent publication coalesces" became a durable-claim assertion and the
  "already recorded" case became an already-`CONFIRMED` job returned untouched. The three policy
  tests (identity binding, exactly 100/0, self-feedback and empty trusted-client) pass unchanged.
- `phase6-reputation-loop.test.ts` "the intent to submit is persisted before the irreversible
  write": now asserts the prepare call carries **no** transaction reference and that the reference
  is bound by the confirmation, per decision 1 above.
- `runtime.test.ts`: extended with the unset-write-mode build, the bearer guard on
  `/reputation-query`, and `ERC8004_WRITE_MODE: "fake"` throwing.
- `test_mongo_repository.py`: `PHASE6_NEW_UNIQUE_INDEXES` and the call-order recorder gained the
  five new reputation indexes and the two new collections, so the M2 preflight guarantees still
  cover every newly introduced unique index.

## Commands not run, and why

- `npm run lint`, `npm test`, the dashboard/seller builds, `docker compose ... config`,
  `npm audit --omit=dev`: not part of this WO's fixed block; the broad matrix belongs to WO-P6-04.
  The full Python suite and the full gateway suite were still run to prove no regression.
- `npm run test:e2e:local`: the harness is a WO-P6-03/WO-P6-04 deliverable and does not exist.
- `terraform`, AWS CLI/SDK, Atlas, ECR, real RPC/facilitator/ERC-8004/provider calls: forbidden by
  this WO and never attempted.

## Process and port teardown

No long-running process was started. The only child processes were the verification commands and
the `mongod` started and stopped by `scripts/test_mongo_local.sh`. Afterwards: no listener on
27019, no `mongod` process matching `pbl-mongo-test`, no `/private/tmp/pbl-mongo-test.*` path.

READY_FOR_REVIEW
