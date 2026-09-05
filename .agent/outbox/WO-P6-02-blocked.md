# WO-P6-02 Coder blocked — required event types live outside the allowed-write list

## Identity

- Work Order: `work-orders/WO-P6-02-reputation-loop.md`
  (SHA-256 `8ccb09351a28abd06d6337d259ab8651d30c728597505938cc0656e5648dd720`, read-only)
- Worktree: `/Users/vien/MyProjects/PBL-coder`
- Branch: `wo/P6-02` (no push, merge, reset, stash, rebase, amend, main commit)
- Base / integration SHA: `feb13f94d3a7934dec3a21a56c2cd3477afac179` (= `git rev-parse HEAD` at start and now)
- Predecessor coder tip: `ca037c9c9e54a1701db995c90dd5d976df83dd4e` — confirmed ancestor of base
  (`git merge-base --is-ancestor ca037c9 HEAD` exit 0)
- Predecessor approval: `/Users/vien/MyProjects/PBL/.agent/outbox/WO-P6-01-review-r3.md`
  (SHA-256 `910c7548472f469a323970ded83ed19bdcb4c64708e359b038b99a3a1b715766`), final line `APPROVE`,
  reviewed tip `ca037c9c9e54a1701db995c90dd5d976df83dd4e` — matches the tip named in this assignment
- Status: **BLOCKED before any implementation.** No product source or test file was modified.

## Start gate — all passed

| Check | Result |
|---|---|
| worktree / branch | `/Users/vien/MyProjects/PBL-coder`, `wo/P6-02`, clean (`git status --porcelain` empty) |
| base contains approved integration SHA | `git merge-base --is-ancestor feb13f94… HEAD` exit 0 |
| base contains predecessor tip | `git merge-base --is-ancestor ca037c9… HEAD` exit 0 |
| `aidlc-docs/inception/requirements.md` | `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f` — matches WO |
| `aidlc-docs/inception/design.md` | `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768` — matches WO |
| `aidlc-docs/inception/tasks.md` | `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe` — matches WO |
| Phase 6 task marker | `P6-02` present in `aidlc-docs/inception/tasks.md` (lines 174, 177, 186, 200, 216) |
| predecessor verdict/tip | read and matched, see Identity |
| TCP 27019 | free (`lsof -nP -iTCP:27019 -sTCP:LISTEN` exit 1, no listener); no process was signalled |
| secrets | no `.env`, private key, RPC URL, provider or AWS credential was read or printed |

## The blocker

The approved design requires two **new appendable purchase-event types**. The single declaration
site for appendable event types is `EventType` in
`services/buyer-audit-api/src/buyer_audit_api/core/models.py:19`, and that file is **not** in the
WO-P6-02 allowed-write list (`work-orders/WO-P6-02-reputation-loop.md:64-107`).

### Exactly what is required

`aidlc-docs/inception/design.md:944-953` (§18.6.1 "New event types and payloads"):

| Event type | Required payload | Cardinality |
|---|---|---|
| `REPUTATION_DECIDED` | `publishIdentity`, `publishIdentityHash`, `decision`, `value?`, `reasonCodes`, `sellerAgentId`, `erc8004AgentId`, `auditBundleHash`, `rulesetVersion`, `tag1`, `tag2`, `payloadFingerprint`, `evidenceSource` | singleton |
| `REPUTATION_PUBLICATION_CONFLICT` | `publishIdentityHash`, `existingFingerprint`, `requestedFingerprint`, `reasonCode`, `evidenceRefs` | append-only conflict |

`aidlc-docs/inception/design.md:1013` fixes an index **by event type name**:

> `unique_reputation_decision_per_purchase`: `purchaseId`, unique partial on `REPUTATION_DECIDED`.

`aidlc-docs/inception/design.md:1022`:

> `reputationOutbox.status`는 … lease 변경은 operational mutable state지만 `REPUTATION_DECIDED`,
> conflict, confirmed record는 purchase event chain에 **append-only**로 남는다.

The Work Order repeats this as non-negotiable:

- `work-orders/WO-P6-02-reputation-loop.md:32` (설계 결정 4) — `AUDITED + REPUTATION_DECIDED + outbox/deferred state`는 같은 Mongo transaction/CAS에서 만든다.
- `…:33` (설계 결정 5) — decision/conflict/confirmed evidence는 append-only **event**다.
- `…:127` — identity/fingerprint/proof 충돌은 `CONFLICT` + append-only evidence로 fail closed한다.
- `…:163` (완료 기준) — decision, outbox, lease, prepared, submitted-unknown, confirmed, deferred, conflict state가 typed/복구 가능하다.
- `…:165` (완료 기준) — different fingerprint는 new transaction 없이 conflict evidence를 만든다.

Requirement IDs that depend on these events: `P6-AC-05.1` (decision must be persisted with audit
bundle hash + ruleset version), `P6-AC-05.3` (unique publish identity + immutable fingerprint;
different fingerprint appends a conflict instead of re-submitting), `P6-NFR-AUD-01` (append-only
correction/reconciliation/audit/reputation records).

### Observed code path proving the constraint

```text
services/buyer-audit-api/src/buyer_audit_api/core/models.py:19    class EventType(StrEnum):
services/buyer-audit-api/src/buyer_audit_api/core/models.py:36        REPUTATION_RECORDED = "REPUTATION_RECORDED"
services/buyer-audit-api/src/buyer_audit_api/core/models.py:238       type: EventType          # EvidenceEvent.type
services/buyer-audit-api/src/buyer_audit_api/core/events.py:15        event_type: EventType,   # create_event(...) keyword
services/buyer-audit-api/src/buyer_audit_api/core/ports.py:111       event_type: EventType,   # EvidenceRepository.append_event(...)
```

- `EventType` is a closed `StrEnum`. Python enums with members cannot be subclassed, so no allowed
  file can extend it.
- `grep -rl "REPUTATION_RECORDED"` over `packages`, `apps`, `services` shows exactly one
  declaration site (`core/models.py`); the other five hits are consumers.
- `grep -rn "REPUTATION_DECIDED|REPUTATION_PUBLICATION_CONFLICT"` over `services`, `apps`,
  `packages` returns **no matches** — neither type exists yet.
- `services/buyer-audit-api/pyproject.toml:49` sets `strict = true`, so passing a foreign
  `StrEnum` member where `EventType` is declared is a type error, not a workaround.

### Rejected in-scope workarounds, and why

| Candidate | Why it was rejected |
|---|---|
| Declare a second `StrEnum` in the allowed `core/reputation.py` and pass its members to `append_event` | `EvidenceEvent.type` is `EventType` and mypy is strict; it would also create a second, parallel event-type convention beside the canonical one, which the repository rules forbid |
| Reuse `CORRECTION_RECORDED` with a payload discriminator | Silently overloads a different event's meaning, breaks the design's named payload contracts, and makes `unique_reputation_decision_per_purchase` (partial on `REPUTATION_DECIDED`) unimplementable as specified |
| Keep decision and conflict state only as `reputationOutbox` documents | Directly contradicts design §18.6.4 line 1022 and WO 설계 결정 5, and would leave the durable decision as mutable operational state instead of append-only evidence |
| Edit `core/models.py` anyway | `work-orders/WO-P6-02-reputation-loop.md:107` — "compatibility가 필요한 최소 수정도 이 allowed-write 안에서만 허용한다"; and `…:124` instructs stopping when a module outside the allowed-write list appears necessary |

## Minimum Planner decision needed

One line, nothing more:

> Add `MODIFY services/buyer-audit-api/src/buyer_audit_api/core/models.py` to the WO-P6-02
> allowed-write list, scoped to **appending the two `EventType` members**
> `REPUTATION_DECIDED = "REPUTATION_DECIDED"` and
> `REPUTATION_PUBLICATION_CONFLICT = "REPUTATION_PUBLICATION_CONFLICT"`
> (plus, if the Planner wants it explicit, a `REPUTATION_EVENT_TYPES` tuple beside the existing
> `TERMINAL_PAYMENT_EVENT_TYPES` at `core/models.py:42`).

No existing member, value, ordering or payload contract needs to change, so this cannot weaken any
WO-P6-01 acceptance condition: the WO-P6-01 suites assert on `EventType` members they already use,
and appending members to a `StrEnum` does not alter existing values or the event hash of any
existing event.

Design §18.4.2 already lists `core/models.py` as MODIFY for the Phase 6 packet
(`aidlc-docs/inception/design.md:787` — "신규 event enum과 source/scenario metadata value objects");
WO-P6-01 consumed that row for the payment event types and the WO-P6-02 list simply omits the file.
This reads as an omission in the packet split rather than a deliberate restriction, but it is not
mine to decide, so I stopped instead of widening scope.

## Everything else in the packet is implementable inside the current allowed-write list

I mapped the full surface before stopping (read-only). No second blocker was found:

| WO step | Target files (all allowed) | Notes from the survey |
|---|---|---|
| 1 — pure core policy, identity, fingerprint, snapshot value objects | NEW `core/reputation.py` | no dependency outside allowed files |
| 2 — `TerminalAuditCoordinator.finalizeIfEligible` | NEW `core/terminal.py` | can import the already-pure `AuditEvaluator` (`core/audit.py:289`), `AuditReportReader` (`:252`), `require_current_audit` (`:230`), `is_final_eligible` (`:1005`) and `RULESET_VERSION` (`:14`) **read-only**, so `core/audit.py` needs no edit. `AuditService.audit()` keeps its WO-P6-01-approved behaviour and the recovery sweep covers "terminal evidence but no audit/decision" as the WO requires |
| 3 — outbox indexes, unique identity, lease CAS, conflict append, transaction-variant partial unique | MODIFY `adapters/repositories/{memory,mongo}.py`, `core/ports.py` | `mongo.py` already has the session/transaction pattern (`start_session` + `with_transaction` at `mongo.py:829/858`, `954/1075`, `1120/1252`) and the M2 read-only collision preflight from WO-P6-01, which the new unique indexes must extend |
| 4 — nine internal terminal/outbox endpoints | MODIFY `api/{schemas,app}.py` | WO-P6-01 already established the `extra="forbid"` + expected-state/head guard pattern and the 422/409/404 mapping to extend |
| 5 — publisher: find-before-submit, PREPARED, SUBMITTED_UNKNOWN, receipt+event confirmation | MODIFY `src/erc8004.ts`, `src/adapters/{http,viem-erc8004}.ts`, `src/contracts.ts`, `src/main.ts` | today `Erc8004ReputationPublisher` dedupes only in-process (`erc8004.ts:158,180`, successful keys never evicted), persists nothing between `findObjectiveFeedback` and `giveFeedback` (`erc8004.ts:204-215`), and `ReputationEvidenceApi` has exactly two members (`erc8004.ts:16,22`) over two endpoints (`http.ts:404,433`). All of that is inside the allowed list |
| 6 — `REPUTATION_RECORDED` payload additions | MODIFY `api/{schemas,app}.py`, `adapters/repositories/*` | the event type already exists (`core/models.py:36`), so this needs no enum change; existing parser sites are `app.py:1280,1324,1377,1574` |
| 7 — query provenance and aggregation | NEW `adapters/reputation_gateway.py`, MODIFY `src/adapters/viem-erc8004.ts` | `findFeedback` currently returns bare `Hex \| null` (`viem-erc8004.ts:123`) and `confirm` bare `boolean` (`:134`); block/log/range provenance has to be added there. Tags `"pbl-audit"`/`"payment-outcome"` are duplicated at `erc8004.ts:99,100,127,128,145,146` and `viem-erc8004.ts:161,162` |
| 8 — provider-level snapshot in selection | MODIFY `domains/ai_inference/{models,ports,workflow,selection}.py` | the reputation component is a single line, `selection.py:137` `"reputation": candidate.benchmark.reputation_score`, weight `10` in all four presets (`selection.py:20,27,34,41`). Hard filters already `continue` at `selection.py:131` **before** the `components` dict is built at `:133`, so `P6-AC-06.4` precedence is structural. The identity-verification seam for a snapshot lookup is `workflow.py:231-232`, before the single QUOTED append at `:234-263`; a new QUOTED payload key must also be read back in `_load_persisted_candidates` (`workflow.py:108-171`, which today reads only `benchmarkSnapshots`/`signedQuotes`/`quoteIdentityEvidence` at `:115-117`) or the resume path would score from a different source |
| 9 — two-worker, restart, conflict, shared snapshot, sequential influence, hard-ineligible rejection | all listed test files | `tests/conftest.py` `MutableClock`/`clock`/`repository`/`container` fixtures and the existing fakes are reusable; `test_ai_inference_domain.py:156` is the existing precedent for a hard-filter test |

Two coupling facts the Planner should know, because they constrain step 8 regardless of the
unblock decision:

1. `core/audit.py:510-544` re-declares the four weight presets verbatim and
   `AUD-SELECTION-WEIGHTS-MISMATCH` (`core/audit.py:551`) fires unless every
   `DECIDED.payload.eligible[].weights` equals them. Changing the reputation component's **source**
   does not touch this; changing weight keys or values would, and `core/audit.py` is also outside
   this WO's allowed-write list.
2. `BenchmarkSnapshot.reputation_score` (`domains/ai_inference/models.py:41`) is required with no
   default and is supplied at 12 sites including `composition.py:112,123`. Design §18.5.4 keeps it
   as a historical-read field, so it stays declared; only `selection.py:137` stops reading it for
   new decisions.

## Verification commands

The WO's fixed verification sequence was **not** run: there is no implementation to verify, and
running the focused suite would only re-prove the WO-P6-01 baseline. Read-only commands actually
executed were the start-gate checks in the table above plus `git`, `grep`, `sha256sum` and `wc`
inspection. `git status --porcelain` is empty apart from this report and the TURN_LOG append.

## Forbidden operations — none performed

- No live ERC-8004, RPC, facilitator, provider, AWS, Atlas or public-chain call or write.
- No `mongod` start, no MongoDB connection, no canonical or historical data read, mutation,
  backfill, delete or cleanup. TCP 27019 was free before and after; no process was signalled.
- No production fake/live write mode enabled anywhere.
- No file outside the WO-P6-02 allowed-write list was modified. Only
  `.agent/outbox/WO-P6-02-blocked.md` (new) and `.agent/TURN_LOG.md` (append-only) were written.
- No scenario, dashboard, Playwright or AWS readiness implementation.
- No secret, env value, private key or raw prompt/response was read, printed or committed.
- No `main` commit, push, merge, reset, stash, rebase or amend. Branch `wo/P6-02` remains
  unintegrated, which is the prescribed rollback state.

## Process and port teardown

No long-running process was started. No child process remains. No listener on 27019, no
`mongod` process, no `/private/tmp/pbl-mongo-test.*` path.

## Exact next step

Planner decides the one-line allowed-write amendment above (or names a different in-scope
mechanism for persisting `REPUTATION_DECIDED` / `REPUTATION_PUBLICATION_CONFLICT` as append-only
purchase events). On amendment, WO-P6-02 is implementable end to end from this same base with no
further scope question; the survey above is the implementation map.

WO-P6-03 must not start. WO-P6-02 remains unstarted.

BLOCKED_AWAITING_PLANNER_DECISION
