# WO-P6-02 Coder evidence — review correction round 2

## Identity and anchors

- Rejected tip reviewed: `72e9611f70132bd63b268fd806ada327f4253981`
- Reviewer report (canonical, unmodified): `/Users/vien/MyProjects/PBL/.agent/outbox/WO-P6-02-review.md`
  SHA-256 `a9bb81fbe9252373f4c3c6e4a1150cce71e45da5ca191172ac6b45b18aeb3dae`
- Work Order (amended by the canonical planner commit, read-only):
  `work-orders/WO-P6-02-reputation-loop.md`
  SHA-256 `813b4577aab0d24673a808a781c1008713eb3e0dc71a6dde16dae3ef81397561`
- Planner amendment receipt: `.agent/outbox/WO-P6-02-planner-amendment-r2.done.md`
  SHA-256 `e91112e3eb4d5c91e3ed028811e7510c60e9b2b1243e972877bd4b5c938e200e`
- Canonical requirements/design/tasks unchanged:
  `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f`,
  `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768`,
  `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe`
- **Correction commit:** `2d5318548c4b937b65f80f2e48f685f63b98f11a`
  (`fix(phase6): bind reputation proofs and atomic publication evidence`)
- **Merge commit:** `b2597049d71032b7d2502fae6cf776fab8f4d4a0`
  (`merge: integrate canonical phase6 planner amendment into wo/P6-02`)
  parents `2d53185` (correction) and `411e475f728344bad728c81c72bb7437a30da15e` (canonical
  `feature/phase6-audit-e2e`, planner payment-ordering amendment)
- Branch: `wo/P6-02`. No push, no rebase, no reset, no stash, no amend, no main commit.
- Branch tip: the last commit on `wo/P6-02`. A report cannot contain its own hash, so if an
  evidence-only follow-up exists its entire diff is this file; the merge SHA above is the
  auditable anchor. Verify with `git rev-parse HEAD` and
  `git diff b2597049..HEAD -- . ':(exclude).agent/outbox/WO-P6-02-coder-r2.done.md'`.

### History preserved

| Artifact | State |
|---|---|
| `c201b391b77128086663e3c047c2fcf737b179cd` implementation commit | in history, unchanged |
| `72e9611f70132bd63b268fd806ada327f4253981` original evidence commit | in history, unchanged |
| `0c10b91ce1067ffe3f245a3aef11b032cff9f249` blocker commit | in history, unchanged |
| `.agent/outbox/WO-P6-02-coder.done.md` | byte-identical to `72e9611` (`git diff --exit-code` 0) |
| `.agent/outbox/WO-P6-02-blocked.md` | byte-identical to `0c10b91` (`git diff --exit-code` 0) |
| `.agent/outbox/WO-P6-02-review.md`, `...-planner-amendment-r2.done.md` | byte-identical to `411e475` |
| `aidlc-docs`, `docs`, `infra`, `apps`, `tools`, `work-orders`, manifests, `.gitignore` | byte-identical to `411e475` |

### TURN_LOG merge resolution

The only merge conflict was `.agent/TURN_LOG.md`. It was resolved append-only: the common
prefix and every `##` section from **both** parents were kept byte-for-byte, only the conflict
markers were removed, and the canonical planner amendment was ordered before the correction
handoff. Verified mechanically against both parents: 9 sections from `2d53185` and 7 from
`411e475`, `lines_missing=0` on each side, and every section string still present in the merged
file. Final order: `06:40 coder blocked` → `14:30 planner` → `09:30 coder implementation` →
`16:42 planner payment ordering` → `18:10 coder correction`.

## Finding-by-finding disposition

### C1 — `CONFIRMED` and append-only `REPUTATION_RECORDED` are now one atomic unit

- `ReputationOutboxPort.mark_confirmed` returns `tuple[EvidenceEvent, ReputationPublishJob]`.
  Memory (`memory.py`) does the append and the status change under one lock; Mongo
  (`mongo.py::_atomic_outbox_append`) does both inside one `session.with_transaction`, with the
  outbox CAS in the same transaction as the event insert and the immutable head insert.
- The event payload is the design 18.6.1 extended shape: `clientAddress`, `tag1`, `tag2`,
  `value`, `valueDecimals`, `feedbackUri`, `feedbackHash`, `transactionRef`, `blockNumber`,
  `logIndex`, `receiptProofRef`, `publishIdentityHash`, `evidenceSource`, plus the legacy keys
  (`chainId`, `erc8004AgentId`, `objectiveValue`, `registryAddress`, `transactionHash`) exactly
  where the existing parser reads them. `transactionHash` is present only for an EVM reference,
  so a synthetic run is never rendered as a chain hash.
- **The tx-only writer is removed, not disabled.**
  `POST /internal/evidence/purchases/{id}/reputation` and its `GET .../reputation-intent`
  companion are gone, together with `ReputationRecordRequest` and `ReputationIntentResponse`.
  The route now 404s; the regression that used to send an arbitrary hash and expect `200`
  asserts the `404` and then drives the real path.
- `/agents` was made tolerant of a synthetic publication (`transactionHash` absent → `None`)
  instead of raising `KeyError`.
- Evidence: `test_a_confirmed_job_is_terminal_and_idempotent` (event and job together, chain
  verified, idempotent retry, conflicting retry appends nothing),
  `test_confirmation_requires_a_matching_agent_value_and_transaction` (seven rejections leave
  zero `REPUTATION_RECORDED`), `test_reputation_outbox_transitions_are_leased_typed_and_idempotent`
  (the HTTP answer carries `{job, event}`), `test_internal_payment_api_is_credentialed_and_claims_from_evidence`
  (legacy route 404, publication only through the outbox), native
  `test_real_mongo_terminal_finalize_and_publication_are_atomic`.

### C2 — a restarted `PREPARED` job can no longer submit a second transaction

- `Erc8004ReputationPublisher.drive` captures the **starting** status. Only `PENDING` or
  `LEASED` may run `markPrepared` → `submitObjectiveFeedback`, once per call. A job that
  arrives already `PREPARED` or `SUBMITTED_UNKNOWN` with no confirmable reference is driven to
  `markSubmittedUnknown` **without** a transaction reference and never touches `giveFeedback`.
- `ReputationPublishJob` now allows `SUBMITTED_UNKNOWN` with `transaction_ref=None`: that is the
  fail-closed reconciliation state. It still requires the committed feedback hash, stays
  non-terminal and claimable, and read-only recovery keeps looking for the effect.
- Evidence: gateway `phase6-reputation-loop.test.ts` (restarted `PREPARED` → zero
  `giveFeedback`, zero second `markPrepared`, `markSubmittedUnknown` with no reference; same for
  `SUBMITTED_UNKNOWN`; a `LEASED` job still performs exactly one prepare and one submit),
  `test_an_unnameable_submission_is_recorded_without_a_reference`, and the independent Node probe
  below.

### H1 — the confirmation is bound to the whole commitment

- `PREPARED` now freezes the full pre-broadcast commitment: feedback hash **and** client address
  **and** feedback URI, and the URI must be the decided audit bundle
  (`assert_prepared_commitment`). A second prepare with any other value is a conflict.
- `ConfirmedFeedbackProof` gained `registry_address` and rejects a proof whose nested EVM
  `blockNumber`/`logIndex` contradict its own coordinates.
- `assert_proof_matches_job` now checks agent, tags, decided value, registry, chain, evidence
  source, the prepared feedback hash, the prepared client, the audit-bundle URI, the bound
  submission identity and any already-known block/log coordinates.
- `submission_identity` includes the evidence source and chain, so relabelling a Base submission
  as `HISTORICAL_ON_CHAIN` is a different submission, never the same one confirmed.
- The **full** proof is preserved on the job (`confirmed_proof`, persisted as `confirmedProof`)
  and a retry is idempotent only when `proof_hash` matches; anything else is a `409`.
- Evidence: `test_a_confirmed_proof_must_match_the_whole_prepared_commitment`,
  `test_a_confirmation_cannot_reclassify_a_base_submission_as_historical`,
  `test_a_confirmation_cannot_move_known_event_coordinates`,
  `test_a_proof_cannot_contradict_its_own_transaction_coordinates`,
  `test_a_second_prepare_can_never_change_the_commitment`, gateway `erc8004.test.ts` (four
  local rejections: registry, URI, client, prepared hash).

### H2 — snapshot provenance is bound to the question that was asked

- `GatewayReputationProvider` builds the requested scope first and accepts an answer only when
  `scope.query_fingerprint` equals it — one comparison that covers chain, registry, ERC-8004
  agent, the configured trusted-client allow-list and the tag pair — and when the window ends
  exactly at the head the gateway reports.
- `query_fingerprint` now excludes both `fromBlock` and `toBlock`, so it identifies the question
  rather than the window, which is what makes fallback reuse safe to compare.
- `ReputationSnapshot` rejects an event outside the queried range, an event whose evidence source
  is not the snapshot's, a non-EVM or wrong-chain reference, and nested coordinates that
  contradict the event.
- `latest_snapshot` is now keyed on `(seller_agent_id, query_fingerprint)` in both adapters, and
  `stale_or_neutral` reuses a stored answer only for exactly the same seller and fingerprint.
- Evidence: `test_an_answer_to_a_different_question_is_never_scored` (eight cases),
  `test_a_broken_event_in_a_valid_answer_fails_closed`,
  `test_a_snapshot_refuses_an_event_outside_the_queried_window`,
  `test_a_snapshot_refuses_an_event_that_contradicts_its_own_reference`,
  `test_a_snapshot_refuses_a_historical_or_synthetic_event`,
  `test_a_fallback_never_reuses_another_agents_snapshot`,
  `test_a_failed_query_reuses_only_the_same_questions_snapshot`,
  `test_the_query_fingerprint_identifies_the_question_not_the_window`, native
  `test_real_mongo_reputation_snapshots_are_immutable` (question-scoped lookup).

### H3 — production wiring and a real bounded window

- `build_container` always constructs `GatewayReputationProvider` with
  `HttpReputationQueryClient(base_url=settings.commerce_gateway_url,
  service_token=settings.gateway_service_token)` and passes it to
  `AiInferenceDecisionWorkflow(reputation=...)`. Building a container performs no external call.
- The trusted-client allow-list is **deployment configuration**: `PBL_AUDIT_FEEDBACK_CLIENTS` is
  read in the composition root and parsed strictly by `parse_feedback_clients` (comma-separated
  `0x`+40 hex, lowercased, de-duplicated, order preserved; a malformed entry raises rather than
  silently trusting nothing). **No wallet address is compiled in.** An absent or empty
  declaration is the default fail-closed state: `snapshot()` returns `NO_EVIDENCE/50` without
  any I/O and without storing anything.
- The genesis-only `from_block=0, to_block=0` query is gone. The buyer sends `lookbackBlocks`;
  the Payment Executor resolves `toBlock` from `latestBlock()` and
  `fromBlock = max(0, toBlock - lookbackBlocks + 1)`, echoes `latestBlock`, and the buyer
  asserts the window ends at that head and is no wider than requested
  (`ReputationQueryScope.resolved_for_lookback`).
- Evidence: `test_the_deployment_allow_list_is_parsed_strictly`,
  `test_an_undeclared_allow_list_is_neutral_without_any_call`,
  `test_a_declared_allow_list_asks_for_a_bounded_latest_window`,
  `test_a_provider_refuses_a_malformed_allow_list_or_window`,
  `test_a_resolved_window_must_end_at_a_real_head`, gateway `erc8004.test.ts` (range resolution
  and four rejected lookbacks), `runtime.test.ts` (`/reputation-query` rejects a missing or
  out-of-range `lookbackBlocks`, still 401s unauthenticated, returns `latestBlock`).

### H4 — conflicts are recorded and reachable

- `record_conflict` returns `tuple[EvidenceEvent, ReputationPublishJob]` and appends
  `REPUTATION_PUBLICATION_CONFLICT` with the design 18.6.1 payload
  (`publishIdentityHash`, `existingFingerprint`, `requestedFingerprint`, `reasonCode`)
  atomically with the `CONFLICT` status, in both adapters.
- Reachable call sites: `POST /internal/evidence/reputation-outbox/identity/{identity_hash}/conflict`
  on the Evidence API, and `ReputationOutboxApi.recordConflict` invoked from the publisher's
  `#guard` whenever a durable-state conflict is observed (recording failure never masks the
  original error).
- The same conflict is idempotent; a different conflict appends its own finding and neither
  erases the other. A confirmed publication can never be turned into a conflict.
- Evidence: `test_a_conflict_stops_the_job_and_leaves_append_only_evidence`,
  `test_the_same_conflict_is_recorded_once_and_a_different_one_is_kept`,
  `test_a_publication_conflict_is_recorded_over_http` (401/404/200, idempotent, preserved),
  gateway `phase6-reputation-loop.test.ts` (exactly one `recordConflict`, original error still
  propagates).

### M1 — the collision preflight covers every new unique index

- `phase6_index_collision_report` gained `unique_reputation_job_id` and
  `unique_reputation_snapshot` specs, and `PHASE6_NEW_UNIQUE_INDEXES` in the drift test lists
  them, so the coverage assertion can no longer hide the gap.
- Evidence: `test_preflight_precedes_every_new_phase6_unique_index`,
  `test_collision_report_covers_every_new_phase6_unique_index`, and native index assertions in
  `test_real_mongo_reputation_outbox_is_exactly_once_under_races`.

### M2 — both atomic units run against a real replica set

- New `test_real_mongo_terminal_finalize_and_publication_are_atomic`:
  seeds a real settled+delivered chain in Mongo, runs `TerminalAuditCoordinator` against it and
  asserts `AUDITED + REPUTATION_DECIDED + job` with a verified hash chain; then claim → prepare →
  confirm and asserts `REPUTATION_RECORDED + CONFIRMED` with the head advanced by exactly one.
  **Rollback probe:** a proof bound to another registry commits nothing — event count unchanged,
  job still `PREPARED`. **Race probe:** four concurrent finalizes on a second purchase yield
  exactly one `AUDITED`, one `REPUTATION_DECIDED` and one outbox document. It also asserts the
  payment view still loads afterwards.
- The existing native race test now asserts that however many confirmations race, exactly one
  `REPUTATION_RECORDED` exists and every winner returns that same event hash.

### Evidence-report correction

The round-1 report said “29 paths”. The actual count was **30**. This report supersedes that
figure. Against the canonical merge parent this branch changes **33** paths (30 from round 1,
plus `core/payment.py`, `tests/test_payment_service.py` and this report).

## Scope

All changes are inside the Work Order allowed-write list as amended by
`411e475`, which added `core/payment.py` and `tests/test_payment_service.py`. Zero protected
paths are touched: `git diff --name-only 411e475..HEAD` contains no `aidlc-docs`, `docs`,
`infra`, `apps`, `tools`, `work-orders` or manifest entry, and the protected diff against
`411e475` for those paths exits 0.

`core/models.py` still differs from the packet base by exactly the two amended `EventType`
members and nothing else.

## Payment ordering amendment — implemented as specified

The amendment forbids an unconditional/global auxiliary filter, so
`_AUXILIARY_EVENT_TYPES` is unchanged and a new ordered validator
(`_post_payment_tail_is_valid`) governs the post-payment tail:

- members must be known post-payment events; the singletons appear at most once;
- `REPUTATION_DECIDED` requires a preceding `AUDITED`;
- `REPUTATION_PUBLICATION_CONFLICT` requires a preceding `REPUTATION_DECIDED` and may then
  repeat, because it is an append-only finding;
- when a chain carries both, `REPUTATION_RECORDED` must follow its decision — while a Phase 5
  chain that only ever had `REPUTATION_RECORDED` still loads, because history is read as written.

Payment state, accounting and authorization semantics are untouched.

| Amended acceptance | Evidence |
|---|---|
| view/claim after finalize (a), confirmed publication (b) | `test_the_payment_view_survives_a_decision_and_a_publication` — also asserts no new claim, reservation, spend or event |
| view/claim after one and after many conflicts (c) | `test_the_payment_view_survives_one_and_many_conflicts` |
| conflict before any decision fails closed | `test_a_conflict_before_any_decision_fails_closed` |
| decision before the audit fails closed | `test_a_decision_before_the_audit_fails_closed` |
| duplicate decision fails closed | `test_a_second_decision_fails_closed` |
| pre-terminal reputation events fail closed | `test_a_reputation_event_before_the_payment_terminal_fails_closed` (all three types) |
| recorded must follow its decision; legacy history still loads | `test_a_recorded_publication_must_follow_its_own_decision` |
| HTTP-level conflict then claim | `test_a_publication_conflict_is_recorded_over_http` |
| native Mongo view after decision + publication | `test_real_mongo_terminal_finalize_and_publication_are_atomic` |

## Two further defects found and fixed while wiring the real path

1. **`_winning_quote` read the ERC-8004 agent ID from the seller-supplied signed quote**
   (`match.get("erc8004_agent_id", "0")`). The agent ID is an identity fact, so it now comes
   from the verified `quoteIdentityEvidence` — the same source the read models report. Reading
   it from the quote made a purchase with verified identity defer as
   `ATTRIBUTION_INSUFFICIENT`.
2. **`_delivery_matches_quote` compared the delivery against the `DECIDED.winner` summary**,
   which only has to name the winning quote. It now compares against the winning **signed
   quote** (provider, model, version) — the contract the seller is judged against and the one
   the delivery endpoint already validates.

Both are in `core/terminal.py` (allowed-write) and both are exercised by the end-to-end
publication in `test_internal_payment_api_is_credentialed_and_claims_from_evidence`.

## Verification at the merged tip `b2597049`

Amended fixed order:

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `ruff check services/buyer-audit-api/src services/buyer-audit-api/tests` | 0 | `All checks passed!` |
| 2 | `mypy services/buyer-audit-api/src` | 0 | `no issues found in 47 source files` |
| 3 | focused pytest (`test_phase6_reputation`, `test_phase6_terminal`, `test_payment_service`, `test_ai_inference_workflow`, `test_ai_inference_domain`, `test_api`) | 0 | `142 passed` |
| 4 | `npm run build --workspace @pbl/commerce-gateway` | 0 | `tsc` clean |
| 5 | `node --test .../erc8004.test.js .../phase6-reputation-loop.test.js .../runtime.test.js` | 0 | `# tests 34 / # pass 34 / # fail 0` |
| 6 | `npm run test:mongo:local` | 0 | `9 passed` |
| 7 | `git diff --check` | 0 | no whitespace errors |

Broad preservation, same tip:

- `pytest services/buyer-audit-api/tests -q -m 'not mongo'` → exit 0, `293 passed, 9 deselected`.
- `npm test --workspace @pbl/commerce-gateway` → exit 0, `# tests 85 / # pass 85 / # fail 0`.
- Protected diff against `411e475` for canonical planning paths → exit 0.
- `git diff --exit-code 72e9611..HEAD -- .agent/outbox/WO-P6-02-coder.done.md` → 0.
- `git diff --exit-code 0c10b91..HEAD -- .agent/outbox/WO-P6-02-blocked.md` → 0.

The same suite was run and green at the pre-merge correction commit `2d53185`
(`116 passed` focused with the pre-amendment command list, `293 passed` non-mongo, `9 passed`
native, 85/85 gateway) before the merge was made.

## Independent probe reproduction

Written **after** the fixes and run at the merged tip; no product code was changed afterwards to
make a probe pass. Every probe asserts the safety condition the rejected implementation violated.

| Probe | Reviewer finding | Result |
|---|---|---|
| P1 confirmation with another feedback hash | Critical/H1 | fails closed: `confirmed feedback is not the payload this job prepared` |
| P2 restarted `PREPARED`, no reference, no on-chain match, live mode | C2 | `giveFeedbackCalls=0`, `markPrepared` calls `0`, `markSubmittedUnknown` with no reference, status `SUBMITTED_UNKNOWN` |
| P2b same from `SUBMITTED_UNKNOWN` | C2 | `giveFeedbackCalls=0` |
| P3 Base-bound reference confirmed with a `HISTORICAL_ON_CHAIN` proof | H1 | fails closed: `not the submitted transaction of this job` |
| P4 answer for agent `999`, client B, block `999`, nested `3/4`, historical source | H2 | `derivedScore=50.0`, nothing stored |
| P5 agent `1` fresh snapshot reused for an agent `2` query | H2 | `50.0 / NO_EVIDENCE` |
| C1 confirmed implies recorded | C1 | `CONFIRMED` with exactly one `REPUTATION_RECORDED`, same event hash |
| H4 conflict evidence | H4 | `CONFLICT` with two findings, same conflict idempotent |
| C1 tx-only writer removed | C1 | no legacy route or schema reference in `app.py` |
| H3 bounded latest window | H3 | `from=9001 to=10000 latest=10000` |

Scripts: `/tmp/r2_probe.py` (7 probes, exit 0), `/tmp/r2_probe_node.mjs` (5 probes, exit 0).

## Pre-existing expectations that changed, and why

| Test | Change | Reason |
|---|---|---|
| `test_api.py::test_internal_payment_api_is_credentialed_and_claims_from_evidence` | the legacy `POST .../reputation` round-trip that expected `200` now expects `404`, and the publication is driven through finalize → claim → prepared → confirmed | that round-trip *was* the proof-free success path; the assertion encoded the C1 defect |
| `test_api.py::test_reputation_outbox_transitions_are_leased_typed_and_idempotent` | prepared body carries the client and URI; a reference-free `submitted-unknown` is accepted; the confirmed response is `{job, event}`; six binding rejections and event-count checks added | the old body could prepare without a commitment and the old response could not observe the atomic unit |
| `test_phase6_reputation.py::test_a_submitted_job_must_carry_its_transaction_reference` | renamed to `test_a_prepared_job_must_carry_its_whole_commitment`; `SUBMITTED_UNKNOWN` no longer requires a reference | requiring one left the restarted-`PREPARED` case with nowhere fail-closed to go, which is C2 |
| `test_phase6_reputation.py::test_the_query_fingerprint_ignores_to_block_...` | renamed to `..._identifies_the_question_not_the_window`; also asserts five different questions change the hash | the fingerprint is now the fallback comparison key, so its contract is stronger |
| `test_phase6_terminal.py::test_a_conflict_stops_the_job_without_a_new_transaction` | renamed to `..._and_leaves_append_only_evidence`; asserts the appended finding and the verified chain | the old test accepted a status-only conflict, which is H4 |
| `erc8004.test.ts` publisher tests | rewritten against the durable outbox contract | the in-process publication map they described no longer exists |
| `phase6-reputation-loop.test.ts` prepare ordering test | asserts the prepare carries no reference and the reference is bound at confirmation | matches the corrected prepare contract |

No assertion was weakened and no test was deleted to make the suite pass.

## Preservation and external boundaries

- Legacy `REPUTATION_RECORDED` history reads unchanged: the extended payload keeps every legacy
  key, and a Phase 5 chain without a decision still passes the payment lifecycle validator.
- Permit2/historical read-only behaviour, the hard-filter-before-reputation ordering, the `10`
  reputation weight in all four presets, and the unused legacy `BenchmarkSnapshot.reputation_score`
  are all still covered by the full non-mongo suite (`293 passed`).
- `ERC8004_WRITE_MODE` unset or empty is `disabled`, `fake` is rejected at startup, the publisher
  defaults to `disabled` in its own constructor, and `/reputation-query` is read-only behind the
  existing bearer guard.
- Zero real ERC-8004, RPC, facilitator, provider, AWS or Atlas calls. No canonical or historical
  MongoDB database was connected, mutated, backfilled or cleaned; native tests use a loopback
  replica set on `127.0.0.1:27019` created and dropped by `scripts/test_mongo_local.sh`.
- No secret, env file, private key or raw prompt/response was read or printed.

## Process and port teardown

No long-running process was started. After the final run: no listener on 27019, no `mongod`
matching `pbl-mongo-test`, no `/private/tmp/pbl-mongo-test.*` directory.

READY_FOR_REVIEW
