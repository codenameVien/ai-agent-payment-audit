# WO-P6-02 Coder evidence — review correction round 3

## Identity and anchors

- Rejected tip reviewed: `f7deffa582d5762ad19783b1872955082a578b7a`
- Reviewer report (canonical, unmodified): `.agent/outbox/WO-P6-02-review-r2.md`,
  brought in by canonical commit `66e72560a3ebbf06c18865d84a0712228d4ea1d7`
- **Merge commit:** `69d64b502221324e33f81f8ec8401022a84afe3a`
  (`merge: integrate canonical WO-P6-02 round-2 review into wo/P6-02`)
  parents `f7deffa` (previous coder tip) and `66e7256` (canonical `feature/phase6-audit-e2e`)
- **Correction commit:** `c8afbdcf5a9ff82caca40c3b10845c1a4f9557ec`
  (`fix(phase6): bind reputation ordering and classify outbox conflicts`)
- Branch: `wo/P6-02`. No push, no rebase, no reset, no stash, no amend, no main commit.
- Branch tip: the last commit on `wo/P6-02`. A report cannot contain its own hash, so this
  file is the entire diff of the evidence commit that follows the correction. Verify with
  `git rev-parse HEAD` and
  `git diff --exit-code c8afbdcf..HEAD -- . ':(exclude).agent/outbox/WO-P6-02-coder-r3.done.md'`.

### History and reports preserved

| Artifact | State |
|---|---|
| `c201b39` first implementation, `72e9611` r1 evidence, `0c10b91` blocker | in history, unchanged |
| `2d53185` r2 correction, `b259704` r2 merge, `7afe465` r2 evidence, `f7deffa` r2 acceptance | in history, unchanged |
| `.agent/outbox/WO-P6-02-coder.done.md`, `...-coder-r2.done.md`, `...-blocked.md` | byte-identical to `f7deffa` (`git diff --exit-code` 0) |
| `.agent/outbox/WO-P6-02-review.md`, `...-review-r2.md`, `...-planner-amendment-r2.done.md` | byte-identical to `66e7256` (`git diff --exit-code` 0) |
| `aidlc-docs`, `docs`, `infra`, `apps`, `tools`, `work-orders`, manifests, `.gitignore` | byte-identical to `66e7256` |

`git diff --name-only 411e475..HEAD | grep -E '^(aidlc-docs|docs|infra|apps|tools|work-orders|package)'`
returns **0** paths.

### TURN_LOG merge resolution

The single conflict was `.agent/TURN_LOG.md`, resolved append-only. Both parents were read
from the index (`:2:` and `:3:`), split on `##` headings, and reassembled: the common
236-line prefix plus every unique section, byte-for-byte, with only the conflict markers
removed. Verified mechanically against both parents — `lines_missing=0` on each side and
every section string still present in the result. The one section present on both sides
differed only by a trailing separator newline; its body is identical. Final order places
the canonical reviewer entry before this correction handoff:
`06:40 blocked` → `14:30 planner` → `09:30 implementation` → `16:42 planner ordering` →
`18:10 correction r2` → `17:24 reviewer r2` → `21:05 correction r3`.

## R2-H1 — payment ordering is bound to the decision's publish identity

`_post_payment_tail_is_valid` now takes `list[EvidenceEvent]` instead of
`list[EventType]`, and `load_payment_view` passes `business_events[3:]`. The rules:

- every member is a known post-payment event and the singletons appear at most once;
- `REPUTATION_DECIDED` requires a preceding `AUDITED` **and** must name a non-empty
  `publishIdentityHash` and `payloadFingerprint`;
- once a decision exists, every following `REPUTATION_PUBLICATION_CONFLICT` and
  `REPUTATION_RECORDED` must carry that decision's `publishIdentityHash`, and a conflict
  must also restate that decision's fingerprint as `existingFingerprint`. A missing,
  blank or mismatched value fails closed;
- a conflict still requires a preceding decision and may then repeat;
- **legal branching, both directions:** a chain that has recorded a conflict can never
  later record a publication, and a chain that has recorded a publication can never later
  record a conflict. A decision that arrives after its own publication or conflict is also
  rejected — that is an impossible ordering, not a legacy chain;
- **legacy isolation:** a tail with `REPUTATION_RECORDED` and **no** `REPUTATION_DECIDED`
  anywhere is the explicit Phase 5 branch. It loads exactly as written, including a payload
  with no publish identity at all, and is never backfilled.

Evidence (`tests/test_payment_service.py`, 8 new tests):

| Case | Test |
|---|---|
| positive: view + claim after finalize (a) and after the confirmed publication (b), with no new claim, reservation, spend or event | `test_the_payment_view_survives_a_decision_and_a_publication` |
| positive: view + claim after one and after four conflicts | `test_the_payment_view_survives_one_and_many_conflicts` |
| negative: decision A then recorded identity B (reviewer probe) | `test_a_publication_of_another_identity_is_not_this_purchases_history` |
| negative: decision A then conflict identity B (reviewer probe) | `test_a_conflict_of_another_identity_is_not_this_purchases_history` |
| negative: conflict restates the wrong fingerprint | `test_a_conflict_must_restate_the_decisions_fingerprint` |
| negative: missing/blank identity or fingerprint on decision, recorded and conflict | `test_missing_identity_or_fingerprint_values_fail_closed` (5 cases) |
| negative: conflict→recorded and recorded→conflict | `test_the_conflict_and_publication_branches_are_exclusive` |
| legacy branch loads, and the same payload after a decision does not | `test_the_legacy_branch_is_isolated_from_identity_binding` |

The pre-existing conflict fixtures that used `{"reasonCode": ...}` only — which the
reviewer named as fixing the missing binding — now build identity-bound payloads through
`conflict_reputation_payload()`.

## R2-H2 — the same fingerprint is not a payload conflict

- `core/reputation.py` gains `OutboxConflictReason` (`PAYLOAD_FINGERPRINT_MISMATCH`,
  `LEASE_MOVED`, `STALE_STATUS`, `ALREADY_TERMINAL`, `TRANSACTION_ALREADY_BOUND`,
  `DIFFERENT_CONFIRMATION`, `PREPARED_COMMITMENT_CHANGED`,
  `SAME_FINGERPRINT_NOT_A_CONFLICT`) and `ReputationOutboxConflict`, a
  `PaymentConflictError` subclass carrying the reason. Every existing
  `except PaymentConflictError` keeps working; `core/errors.py` is untouched.
- `assert_is_payload_conflict` refuses `requested == existing`, and
  `publication_conflict_payload` calls it, so **no** caller can even construct the
  evidence. Memory and Mongo `record_conflict` therefore refuse before any state change:
  no event, no terminalization, and the job stays claimable by a recovery worker.
- All ten outbox refusal sites in both adapters now raise typed reasons.
- `api/app.py::_outbox_error` answers a typed conflict with
  `409 {"detail": {"reason", "message"}}`.
- `services/commerce-gateway`: `ReputationOutboxConflictError` carries a `reason`,
  `parseConflictReason` extracts it from the 409 body (unknown or absent →
  `UNSPECIFIED`), and `#guard` requests `recordConflict` **only** when the reason is
  `PAYLOAD_FINGERPRINT_MISMATCH`. The policy error message now names the reason.

Evidence:

| Case | Test |
|---|---|
| same fingerprint refused, typed, no event, job still `PENDING` and claimable; the payload builder refuses on its own | `test_the_same_fingerprint_is_never_a_publication_conflict` |
| `LEASE_MOVED`, `PAYLOAD_FINGERPRINT_MISMATCH`, `STALE_STATUS` are distinguishable and none appends evidence | `test_ordinary_lease_and_status_races_are_typed_but_leave_no_evidence` |
| HTTP: same-fingerprint conflict is `409 SAME_FINGERPRINT_NOT_A_CONFLICT` and leaves the conflict count unchanged | `test_a_publication_conflict_is_recorded_over_http` |
| HTTP: `LEASE_MOVED` vs `PAYLOAD_FINGERPRINT_MISMATCH` reasons, zero conflict evidence, job still `LEASED` | `test_ordinary_outbox_races_answer_with_a_distinguishable_reason` |
| gateway: seven non-payload reasons each record nothing (reviewer's CAS probe, promoted) | `ordinary lease and status races never request conflict evidence` |
| gateway: only a differing fingerprint records, exactly once | `only a differing immutable fingerprint requests conflict evidence` |
| gateway: reason parsing, including non-JSON and unknown reasons | `a typed 409 body is parsed into its reason, and anything else is unspecified` |

Three pre-existing gateway tests encoded the rejected behaviour and were retargeted, not
weakened: `a 409 on markPrepared is a policy failure with zero writes` now asserts the
journal is `claim, findFeedback, markPrepared` with **no** `recordConflict`, and the two
conflict-recording tests inject an explicit `PAYLOAD_FINGERPRINT_MISMATCH`.

## R2-M1 — concurrent identical conflicts converge on one event

- `conflict_key(identity_hash, existing_fingerprint, requested_fingerprint, reason_code)`
  is a canonical SHA-256 and is stored in the conflict payload as `conflictKey`.
  `conflict_is_identical` now compares that key.
- Mongo: `_atomic_outbox_append` accepts `dedupe`/`dedupe_index`. The dedupe lookup runs
  **inside** the transaction, and a `DuplicateKeyError` on
  `unique_reputation_conflict_key` resolves to the already-committed event, so a duplicate
  race returns the existing logical result rather than appending a second event.
- New unique partial index `unique_reputation_conflict_key` on `payload.conflictKey`,
  restricted to `REPUTATION_PUBLICATION_CONFLICT` with a string key, so absent Phase 5
  payloads are untouched. It is in the read-only collision preflight and in
  `PHASE6_NEW_UNIQUE_INDEXES`.
- Evidence: native `test_real_mongo_concurrent_identical_conflicts_are_one_event` — eight
  concurrent identical calls all succeed, return **one** distinct event hash, and the
  collection holds exactly **1** conflict document; a different conflict still appends its
  own finding (2 total); the chain verifies; the index is unique; the preflight is clean;
  and the same-fingerprint call is refused with no write. In-memory equivalent:
  `test_concurrent_identical_conflicts_converge_on_one_event`.

## Evidence path count correction

The r2 report said “33 paths”. The reviewer measured **34** against `411e475..f7deffa`,
and that is correct. This round changes **35** paths against the same base (34 plus this
report), and **34** against the canonical merge parent `66e7256`.

## Verification at the correction commit `c8afbdcf`

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `ruff check services/buyer-audit-api/src services/buyer-audit-api/tests` | 0 | `All checks passed!` |
| 2 | `mypy services/buyer-audit-api/src` | 0 | `no issues found in 47 source files` |
| 3 | focused pytest (6 files incl. `test_payment_service.py`) | 0 | `152 passed` |
| 4 | `npm run build --workspace @pbl/commerce-gateway` | 0 | `tsc` clean |
| 5 | `node --test .../erc8004.test.js .../phase6-reputation-loop.test.js .../runtime.test.js` | 0 | `# tests 37 / # pass 37 / # fail 0` |
| 6 | `npm run test:mongo:local` | 0 | `10 passed` |
| 7 | `git diff --check` | 0 | no whitespace errors |

Broad and boundary checks, same tip:

- `pytest services/buyer-audit-api/tests -q -m 'not mongo'` → exit 0,
  `303 passed, 10 deselected`.
- `npm test --workspace @pbl/commerce-gateway` → exit 0, `# tests 88 / # pass 88`.
- Protected diff against canonical `66e7256` → exit 0. Prior coder reports diff vs
  `f7deffa` → exit 0.
- Teardown: no listener on 27019, no `mongod` matching `pbl-mongo-test`, no
  `/private/tmp/pbl-mongo-test.*`.

## Independent probe reproduction

Written after the fixes and run at this tip; no product code changed afterwards to make a
probe pass.

| Probe | Finding | Result |
|---|---|---|
| same-fingerprint `record_conflict` (Python memory) | R2-H2 | refused `SAME_FINGERPRINT_NOT_A_CONFLICT`, status stays `PENDING`, 0 events |
| lease-moved 409 driven through the publisher (Node) | R2-H2 | journal `markPrepared` only, **no** `recordConflict` |
| real fingerprint mismatch (Node) | R2-H2 | exactly one `recordConflict` |
| 8 concurrent identical conflicts (Python memory) | R2-M1 | 8 successes, **1** event |
| decision A + recorded/conflict identity B (Python) | R2-H1 | both rejected; own identity accepted; legacy accepted |
| the seven round-1/round-2 probes (P1–P5, C1, H4, H3) | prior findings | still fail closed |

Scripts: `/tmp/r2_probe.py` (10 probes, exit 0), `/tmp/r2_probe_node.mjs` (7 probes,
exit 0). Native Mongo convergence is a permanent test rather than a scratch probe.

## Informational item from the review

The reviewer noted that the gateway supports an `ERC8004_REPUTATION_REGISTRY` override
while the buyer composition uses the canonical registry constant. That is unchanged in
this round: it is a deployment-configuration decision, not a defect, and the two agree on
the canonical value. It is recorded here so it is not lost.

## External boundaries

No public RPC, ERC-8004, provider, facilitator, Atlas, AWS or public-chain call or write.
Node chain interactions are fakes; Mongo is a loopback temporary replica set created and
dropped by `scripts/test_mongo_local.sh`. No canonical or historical database was
connected or mutated; the new index is a partial index over a field absent from Phase 5
rows. No secret, env file, private key or raw prompt/response was read or printed. No
prior review or evidence report was modified.

READY_FOR_REVIEW
