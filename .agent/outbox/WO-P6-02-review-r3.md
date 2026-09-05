# WO-P6-02 독립 리뷰 — correction round 3

## 최종 판정 요약

`APPROVE`다. exact tip에서 이전 r2 반려의 세 결함을 독립적으로 다시 공격했고 모두 닫힌 것을 확인했다. Payment tail은 decision의 `publishIdentityHash`와 `payloadFingerprint`에 결속되고, ordinary lease/status 409는 conflict evidence를 만들지 않으며, native Mongo의 동일 logical conflict 8-way race는 event 한 건으로 수렴한다. 이전 C1/C2/H1-H4/M1/M2, payment-ordering amendment, history/Permit2/read-side 경계도 회귀하지 않았다.

## 검토 식별자와 입력

- Reviewer 역할: 독립 검토 전용
- Coder worktree: `/Users/vien/MyProjects/PBL-coder`
- branch: `wo/P6-02`
- 요청 및 실제 검토 tip: `ce6b403833c1e58240dfd02f9f286ac32ae97c6a`
- canonical packet base: `66e72560a3ebbf06c18865d84a0712228d4ea1d7`
- round-3 correction implementation: `c8afbdcf5a9ff82caca40c3b10845c1a4f9557ec`
- correction merge parent: `69d64b502221324e33f81f8ec8401022a84afe3a`
- evidence-only tip parent: `c8afbdcf5a9ff82caca40c3b10845c1a4f9557ec`
- `git merge-base 66e72560... ce6b4038...`: `66e72560a3ebbf06c18865d84a0712228d4ea1d7`
- Coder 검토 전·후: exact tip 유지, clean
- canonical 검토 시작점: `feature/phase6-audit-e2e` at `66e72560a3ebbf06c18865d84a0712228d4ea1d7`; 기존 untracked `.githooks/` 보존

입력 SHA-256:

| 파일 | SHA-256 |
|---|---|
| `work-orders/WO-P6-02-reputation-loop.md` | `813b4577aab0d24673a808a781c1008713eb3e0dc71a6dde16dae3ef81397561` |
| 이전 `.agent/outbox/WO-P6-02-review-r2.md` | `3c8577f939599ee87420997af9d289da4ccf86ceaaa29eb055fbed4ef7bc7b56` |
| Coder `.agent/outbox/WO-P6-02-coder-r3.done.md` | `943a9ea12883263925fe447f879d752d79a13938ffca75061ef739288947e8a9` |
| canonical requirements | `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f` |
| canonical design | `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768` |
| canonical tasks | `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe` |

Work Order, 원 rejection, r2 rejection, Coder r3 evidence, canonical Phase 6 requirements/design/tasks 및 exact base-to-tip source/tests를 직접 읽었다. Coder self-report는 주장으로만 취급했다.

## commit 및 scope 회계

`git log --reverse 66e72560...ce6b4038...`에는 merge ancestry를 보존한 11개 commit이 있다. 이번 correction 고유 흐름은 다음과 같다.

```text
69d64b502221324e33f81f8ec8401022a84afe3a merge: integrate canonical WO-P6-02 round-2 review into wo/P6-02
c8afbdcf5a9ff82caca40c3b10845c1a4f9557ec fix(phase6): bind reputation ordering and classify outbox conflicts
ce6b403833c1e58240dfd02f9f286ac32ae97c6a docs(phase6): record WO-P6-02 round-3 correction evidence
```

- Exact base-to-tip diff: **35 paths**, 13,203 insertions / 437 deletions.
- 경로 회계: source/test 30개 + `.agent/TURN_LOG.md` 1개 + blocker/세대별 Coder evidence 4개 = 35개다.
- `69d64b5...c8afbdc` correction은 13 paths, 1,169 insertions / 110 deletions이다. 구성은 source/test 12개와 append-only TURN_LOG 1개다.
- `c8afbdc...ce6b403`은 `.agent/outbox/WO-P6-02-coder-r3.done.md` 한 파일만 추가한 evidence-only commit이다. 구현 뒤 제품 변화가 없다.
- 30개 제품/테스트 경로는 amended WO의 allowed-write 안에 있다. Dashboard, scenario controller/catalog, reputation UI, AWS, contracts, deployment gate, tools 범위 누출은 없다.
- Exact packet base 기준 requirements/design/tasks, Work Orders, `docs/ERC3009_DEPLOYMENT_GATE.md`, `infra/contracts`, `infra/aws`, `apps/dashboard`, `tools` diff는 0이다.
- `main` merge-base `888674533d7afbd27419b0037c03f6e4fdf892c6` 기준 보이는 requirements/design/tasks 및 WO-P6-01/02/03/04는 `66e72560` 이전 승인 planning/packet 역사다. Coder 변경으로 귀속하지 않았다.

## 독립 실행 명령과 결과

### Work Order 고정 순서

| # | 정확한 명령 | 결과 |
|---|---|---|
| 1 | `uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests` | exit 0, `All checks passed!` |
| 2 | `uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src` | exit 0, 47 source files 오류 없음 |
| 3 | `uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_reputation.py services/buyer-audit-api/tests/test_phase6_terminal.py services/buyer-audit-api/tests/test_payment_service.py services/buyer-audit-api/tests/test_ai_inference_workflow.py services/buyer-audit-api/tests/test_ai_inference_domain.py services/buyer-audit-api/tests/test_api.py -q` | exit 0, `152 passed, 2 warnings in 2.02s` |
| 4 | `npm run build --workspace @pbl/commerce-gateway` | exit 0, TypeScript build clean |
| 5 | `node --test services/commerce-gateway/dist/tests/erc8004.test.js services/commerce-gateway/dist/tests/phase6-reputation-loop.test.js services/commerce-gateway/dist/tests/runtime.test.js` | exit 0, 37/37 pass |
| 6 | `npm run test:mongo:local` | exit 0, isolated native `127.0.0.1:27019` replica set, `10 passed, 1 warning in 15.49s` |
| 7 | `git diff --check 66e72560a3ebbf06c18865d84a0712228d4ea1d7..ce6b403833c1e58240dfd02f9f286ac32ae97c6a && git diff --check && git status --short` | exit 0, whitespace 오류 없음, Coder clean |

### Broad 및 집중 회귀

| 정확한 명령 | 결과 |
|---|---|
| `npm run test:unit` | exit 0, full non-Mongo `303 passed, 10 deselected, 6 warnings in 2.87s` |
| `npm test --workspace @pbl/commerce-gateway` | exit 0, build 후 88/88 pass |
| 아래 legacy/Permit2/read-side 7-node pytest 명령 | exit 0, 7/7 pass |
| 아래 기존 finding 집중 19-node pytest 명령 | exit 0, 19/19 pass |
| 아래 추가 legacy/read/no-evidence/hard-filter 6-node pytest 교차검사 | exit 0, 6/6 pass |

정확한 7-node 명령:

```text
uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_projections.py::test_legacy_permit2_record_is_historical_and_not_base_verified services/buyer-audit-api/tests/test_phase6_payment_terminal.py::test_historical_permit2_intent_cannot_reconcile_or_close services/buyer-audit-api/tests/test_phase6_payment_terminal.py::test_historical_proof_cannot_terminalize_an_active_base_submission services/buyer-audit-api/tests/test_phase6_audit.py::test_legacy_finding_documents_still_parse_without_rewriting services/buyer-audit-api/tests/test_api.py::test_read_paths_never_change_the_evidence_head services/buyer-audit-api/tests/test_ai_inference_domain.py::test_a_candidate_without_reputation_evidence_scores_neutral_fifty services/buyer-audit-api/tests/test_ai_inference_domain.py::test_reputation_never_rescues_a_hard_filtered_candidate -q
```

정확한 19-node 명령:

```text
uv run --project services/buyer-audit-api pytest -q services/buyer-audit-api/tests/test_phase6_terminal.py::test_a_stale_persisted_audit_blocks_the_decision services/buyer-audit-api/tests/test_phase6_terminal.py::test_a_prepared_job_is_never_prepared_twice_with_another_transaction services/buyer-audit-api/tests/test_phase6_terminal.py::test_a_prepared_commitment_must_bind_the_decided_audit_bundle services/buyer-audit-api/tests/test_phase6_terminal.py::test_a_different_fingerprint_can_never_move_a_job services/buyer-audit-api/tests/test_phase6_terminal.py::test_a_confirmed_job_is_terminal_and_idempotent services/buyer-audit-api/tests/test_phase6_terminal.py::test_a_confirmed_publication_cannot_be_turned_into_a_conflict services/buyer-audit-api/tests/test_phase6_reputation.py::test_a_confirmed_proof_must_match_the_whole_prepared_commitment services/buyer-audit-api/tests/test_phase6_reputation.py::test_a_confirmation_cannot_reclassify_a_base_submission_as_historical services/buyer-audit-api/tests/test_phase6_reputation.py::test_a_confirmation_cannot_move_known_event_coordinates services/buyer-audit-api/tests/test_phase6_reputation.py::test_the_query_fingerprint_identifies_the_question_not_the_window services/buyer-audit-api/tests/test_phase6_reputation.py::test_a_resolved_window_must_end_at_a_real_head services/buyer-audit-api/tests/test_phase6_reputation.py::test_a_snapshot_cannot_hold_an_untrusted_or_mismatched_event services/buyer-audit-api/tests/test_phase6_reputation.py::test_a_snapshot_refuses_an_event_outside_the_queried_window services/buyer-audit-api/tests/test_phase6_reputation.py::test_a_snapshot_refuses_an_event_that_contradicts_its_own_reference services/buyer-audit-api/tests/test_phase6_reputation.py::test_a_fallback_never_reuses_another_agents_snapshot services/buyer-audit-api/tests/test_phase6_reputation.py::test_an_undeclared_allow_list_is_neutral_without_any_call services/buyer-audit-api/tests/test_phase6_reputation.py::test_an_answer_to_a_different_question_is_never_scored services/buyer-audit-api/tests/test_mongo_repository.py::test_preflight_precedes_every_new_phase6_unique_index services/buyer-audit-api/tests/test_mongo_repository.py::test_collision_report_covers_every_new_phase6_unique_index
```

정확한 추가 6-node 명령:

```text
uv run --project services/buyer-audit-api pytest -q services/buyer-audit-api/tests/test_phase6_projections.py::test_legacy_permit2_record_is_historical_and_not_base_verified services/buyer-audit-api/tests/test_phase6_payment_terminal.py::test_historical_permit2_intent_cannot_reconcile_or_close services/buyer-audit-api/tests/test_phase6_audit.py::test_legacy_finding_documents_still_parse_without_rewriting services/buyer-audit-api/tests/test_phase6_audit.py::test_persisted_audit_is_returned_by_the_reader_and_is_reused services/buyer-audit-api/tests/test_phase6_reputation.py::test_no_matching_evidence_is_neutral_fifty_and_labelled services/buyer-audit-api/tests/test_ai_inference_domain.py::test_reputation_never_rescues_a_hard_filtered_candidate
```

Focused Python 152건은 terminal 100/0/DEFER, existing AUDITED/current ruleset, stale audit 차단, finalize race, transaction reference single bind, receipt+decoded event confirmation, snapshot provenance/immutability, provider-level 공유, 10% scoring, hard filter, production Buyer composition, 모든 payment-ordering positive/negative를 포함한다. Gateway 37/88건은 PREPARED/SUBMITTED_UNKNOWN restart, confirmation proof, exact query, writer default-disabled 및 production fake 거부를 포함한다. Native 10건은 실제 transaction rollback/race와 신규 index를 실행한다.

### exact-base, protected path, teardown

| 정확한 명령 | 결과 |
|---|---|
| `git diff --name-status 66e72560a3ebbf06c18865d84a0712228d4ea1d7..ce6b403833c1e58240dfd02f9f286ac32ae97c6a` + 같은 range의 `--shortstat` + `--name-only \| awk 'END {print NR}'` | exit 0, 위 35 paths / 13,203+ / 437- |
| `git diff --exit-code 66e72560a3ebbf06c18865d84a0712228d4ea1d7..ce6b403833c1e58240dfd02f9f286ac32ae97c6a -- aidlc-docs/inception/requirements.md aidlc-docs/inception/design.md aidlc-docs/inception/tasks.md docs/ERC3009_DEPLOYMENT_GATE.md infra/contracts infra/aws apps/dashboard tools work-orders` | exit 0 |
| `git diff --name-only 888674533d7afbd27419b0037c03f6e4fdf892c6..ce6b403833c1e58240dfd02f9f286ac32ae97c6a -- <같은 protected paths>` | 승인 base 이전 planning/WO 파일만 출력 |
| `git diff --name-status c8afbdcf5a9ff82caca40c3b10845c1a4f9557ec..ce6b403833c1e58240dfd02f9f286ac32ae97c6a` | Coder r3 evidence 한 파일만 출력 |
| `lsof -nP -iTCP:27019 -sTCP:LISTEN` 및 `lsof -nP -iTCP:27021 -sTCP:LISTEN` | 종료 후 출력 없음 |
| `find /private/tmp -maxdepth 1 -type d \( -name 'pbl-mongo-test.*' -o -name 'pbl-mongo-r3-probe.*' \) -print` | 출력 없음 |
| `ps -axo pid=,command= | rg '[m]ongod.*(27019|27021)|[p]ytest.*PBL-coder|[n]ode --test.*commerce-gateway'` | 잔류 process 없음 |
| `git rev-parse HEAD; git status --short` in Coder | exact `ce6b4038...`, clean |

## r2 결함 독립 probe

모든 probe는 exact tip의 in-memory/fake 또는 별도 loopback Mongo만 사용했다. Coder가 보고한 임시 probe를 재사용하지 않고 stdin inline harness를 새로 구성했다.

### R2-H1 — payment identity/fingerprint/order

`uv run --project services/buyer-audit-api python -` inline probe 결과는 `R3_PAYMENT_PROBE_PASS 6`이다.

1. decision identity A 뒤 identity B의 `REPUTATION_RECORDED`: `load_payment_view`와 `claim` 모두 `PaymentEvidenceError`.
2. decision identity A 뒤 identity B의 `REPUTATION_PUBLICATION_CONFLICT`: view/claim 모두 거부.
3. conflict `existingFingerprint != decision.payloadFingerprint`: view/claim 모두 거부.
4. `RECORDED -> CONFLICT`와 `CONFLICT -> RECORDED`: 두 분기 모두 상호배타적으로 거부.
5. Phase 5 legacy `REPUTATION_RECORDED` without decision: view/기존 claim 성공, event hash 목록 불변. 그 뒤 decision을 붙인 recorded-before-decision chain은 view/claim 거부.
6. 정상 decision+recorded tail: view/기존 claim 성공, event 추가·reservation/spend 변화 없음.

Source에서 `core/payment.py:51-124`는 event 객체를 검사하며 decision의 nonblank identity/fingerprint, follower identity, conflict existing fingerprint, singleton/order, legacy branch를 구분한다. Type-only validation이던 R2-H1은 닫혔다.

### R2-H2/H4 — 409 분류와 conflict evidence

- Reviewer Gateway inline probe: `R3_GATEWAY_PROBE_PASS ordinary_reasons=8 recordConflict=0 true_mismatch=1 requested_ne_existing=true http_409_reasons=4`.
- `LEASE_MOVED`, `STALE_STATUS`, `ALREADY_TERMINAL`, `TRANSACTION_ALREADY_BOUND`, `DIFFERENT_CONFIRMATION`, `PREPARED_COMMITMENT_CHANGED`, `SAME_FINGERPRINT_NOT_A_CONFLICT`, `UNSPECIFIED` 각각은 `recordConflict=0`, chain write=0이었다.
- 오직 `PAYLOAD_FINGERPRINT_MISMATCH`에서 simulated persisted existing fingerprint와 requested fingerprint가 실제로 다를 때 `recordConflict`가 정확히 1회 호출됐다.
- Fake `fetch`로 HTTP 409 reason 4종을 통과시켜 Gateway client가 typed reason을 그대로 보존하는지 확인했다. 네트워크 I/O는 없었다.
- Reviewer Memory/API inline probe: same fingerprint, lost lease, stale status는 typed 409/no event/no terminalization이었다. Wrong transition fingerprint도 자체로 event를 쓰지 않았고, 이후 명시적 differing-fingerprint conflict만 event 1건을 만들었다. 동일 호출 retry는 같은 event hash를 반환하고 다른 논리 conflict는 별도 event로 보존됐다.

`core/reputation.py:117-154,1076-1119`, memory repository `:635-983`, Mongo repository `:1690-2078`, API `_outbox_error`/conflict route `api/app.py:281-296,1399-1423`, Gateway `adapters/http.ts:718-764`와 `erc8004.ts:506-533`을 대조했다. Backend same-fingerprint 차단과 Gateway conditional recording이 일치한다.

### R2-M1 — native Mongo 8-way identical conflict

별도 `127.0.0.1:27021`, single-node replica set `pblr3`, 고유 database에서 Reviewer inline probe를 실행했다. 결과:

```text
R3_NATIVE_MONGO_PROBE_PASS calls=8 logical_events=1 other_conflicts=1 index=unique-partial preflight_collision=reported
```

- same fingerprint는 `SAME_FINGERPRINT_NOT_A_CONFLICT`; event 0, job `PENDING` 유지.
- 동일 `(identityHash, existingFingerprint, requestedFingerprint, reasonCode)` 8개 동시 호출은 8개 모두 성공하고 distinct event hash 1개, DB conflict event 1건으로 수렴.
- 다른 requested fingerprint/reason은 두 번째 독립 conflict event로 보존.
- `conflictKey`는 네 필드의 deterministic hash와 일치.
- 실제 `unique_reputation_conflict_key`는 `payload.conflictKey: 1`, `unique: true`, `type=REPUTATION_PUBLICATION_CONFLICT` 및 string key의 partial filter였다.
- 중복 legacy-shaped fixture의 read-only preflight는 해당 index와 count 2를 보고했다. clean DB preflight는 빈 목록이었다.
- `_atomic_outbox_append`의 transaction 내부 dedupe read와 duplicate-key recovery를 source 및 실제 race로 확인했다.

## 이전 finding별 최종 disposition

| ID | r3 판정 | 독립 근거 |
|---|---|---|
| C1 | **FIXED / VERIFIED** | Memory lock/Mongo transaction이 `REPUTATION_RECORDED` append와 `CONFIRMED`를 원자 처리한다. same proof retry는 기존 event이고 tx-only legacy writer는 닫혔다. focused/native 통과. |
| C2 | **FIXED / VERIFIED** | 독립 restart probe에서 `PREPARED`와 `SUBMITTED_UNKNOWN`, ref 없음, find miss가 각각 `SUBMITTED_UNKNOWN`으로만 남았다: `finds=2`, `prepares=0`, `giveFeedback=0`, `submittedUnknown=2`. |
| H1 | **FIXED / VERIFIED** | Confirmation은 registry/client/agent/value/decimals/tags/hash/URI/source/known coordinates/exact transaction ref와 결합되고 full proof가 저장된다. |
| H2 | **FIXED / VERIFIED** | Query fingerprint는 chain/registry/agent/trusted clients/tags를 결합한다. latest-head window와 raw event client/tag/window/source/ref 좌표, cross-agent fallback이 fail closed다. |
| H3 | **FIXED / VERIFIED** | Buyer production composition에 provider가 연결된다. trusted-client env는 strict; absent/empty이면 I/O 없이 `NO_EVIDENCE/50`. Gateway가 bounded latest range를 결정한다. |
| H4 | **FIXED / VERIFIED** | Conflict state+event 원자성, reachable API/call site, typed reason 분류가 있다. Ordinary 409는 event 0; 실제 payload mismatch만 정확히 한 logical conflict를 기록한다. |
| M1 | **FIXED / VERIFIED** | `jobId`, `snapshotId`, `conflictKey`를 포함한 신규 unique index가 collision preflight와 drift coverage에 모두 있다. |
| M2 | **FIXED / VERIFIED** | Native Mongo가 terminal finalize, confirmed publication atomicity/rollback/race, snapshot/index/history, identical-conflict concurrency를 실제 replica set에서 실행한다. |
| R2-H1 | **FIXED / VERIFIED** | identity A 뒤 identity B recorded/conflict, wrong existing fingerprint, branch 혼합, recorded-before-decision을 Reviewer probe가 모두 거부했다. Legacy no-decision branch만 격리 수용한다. |
| R2-H2/H4 | **FIXED / VERIFIED** | Gateway/API/memory/Mongo의 typed 409와 same-fingerprint refusal을 독립 확인했다. False terminal conflict가 재현되지 않는다. |
| R2-M1 | **FIXED / VERIFIED** | 별도 native replica-set 8-way race가 exactly one event로 수렴했다. deterministic key/partial unique/preflight/different conflict도 확인했다. |

## findings by severity

### Critical

없음.

### High

없음.

### Medium

없음.

### Low / informational

- Gateway는 `ERC8004_REPUTATION_REGISTRY` override를 허용하지만 Buyer composition은 canonical registry를 사용한다. 비canonical override에서는 query/publish가 mismatch로 fail closed하고, 프로젝트 invariant도 canonical registry를 요구하므로 승인 차단 사유가 아니다. 두 process의 설정을 장래 가변화할 때는 같은 config source로 묶는 것이 바람직하다.
- Product 결함이 아닌 Reviewer harness 정정이 있었다. 최초 `npm run lint:ruff`는 존재하지 않는 script라 exit 1이었고 즉시 Work Order의 실제 Ruff 명령으로 고정 순서를 처음부터 실행했다. `shasum -a 256`은 host `C.UTF-8` locale 문제로 exit 9였으며 `sha256sum`으로 성공했다. 첫 custom Mongo command는 실행기 안전 규칙이 destructive cleanup 구문을 process 시작 전에 거부했다. `trash` cleanup으로 바꾼 첫 실행은 Reviewer가 collision report 키를 `indexName`으로 잘못 가정해 exit 1이었고 listener/tempdir를 정리했다. Source의 실제 `index` 키를 사용한 두 번째 실행은 위 결과로 통과했다. 어느 경우도 product/repository 파일을 바꾸거나 live system에 연결하지 않았다.

## 요구 영역별 최종 판정

| 영역 | 판정 | 근거 |
|---|---|---|
| terminal AUDITED → 100/0/DEFER, stale/ruleset | 통과 | deterministic policy/finalize tests; unknown/preview no-job; stale/current audit guards |
| audit+decision+job 및 confirmed event+state 원자성 | 통과 | memory races + native Mongo transaction/rollback |
| PREPARED/SUBMITTED_UNKNOWN restart | 통과 | 독립 fake probe에서 두 상태 모두 second prepare/broadcast 0 |
| transaction ref single bind / conflict / confirmed proof | 통과 | exact source/ref/coordinates/receipt+decoded-event 결합 |
| payment tail identity/fingerprint/order/read-side | 통과 | Reviewer 6-case probe 및 focused amendment tests |
| conflict classification/idempotency | 통과 | Gateway/Memory/API/native Reviewer probes |
| snapshot provenance/trusted client/exact query/immutability | 통과 | focused 19 + broad/native |
| provider-level sharing / no evidence 50 / hard filter 뒤 10% / legacy benchmark 미사용 | 통과 | workflow/domain focused + broad |
| production composition / writer guard | 통과 | provider wiring; writer default disabled; production fake 거부 |
| historical evidence / Permit2 read-only | 통과 | exact 7-node history suite; no backfill/mutation/delete path |
| scope / protected paths | 통과 | exact base protected diff 0; 35-path 회계 일치 |

## 보존·외부 경계 및 통합 권고

- Existing `REPUTATION_RECORDED`, successful PBLC/ERC-3009 evidence, historical Permit2 settlements, legacy audit/Mongo documents를 수정·backfill·삭제하지 않았다. 읽기 호환과 Permit2 execute/reconcile/terminalization 차단이 유지된다.
- Review 동안 Atlas 또는 canonical/history DB를 열지 않았다. Mongo는 고립된 loopback 임시 database만 사용해 drop/종료했고 listener/process/tempdir가 남지 않았다.
- Public RPC, facilitator, ERC-8004, provider, AWS, Atlas, public chain read/write를 하지 않았다. Node chain/provider는 전부 fake였다.
- Secret, private key, raw prompt/response를 읽거나 출력하지 않았다.
- Coder worktree, product code/tests, planning/Work Order, 이전 review, canonical untracked `.githooks/`를 수정하지 않았다.
- 통합 권고: exact reviewed tip `ce6b403833c1e58240dfd02f9f286ac32ae97c6a`만 canonical feature 흐름에 통합할 수 있다. SHA가 바뀌면 재검토가 필요하다.

APPROVE
