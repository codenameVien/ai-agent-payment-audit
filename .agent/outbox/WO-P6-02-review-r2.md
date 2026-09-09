# WO-P6-02 독립 리뷰 — correction round 2

## 최종 판정 요약

`REJECT`다. 이전 C1, C2, H1, H2, H3, M1, M2의 주된 구현 결함은 수정됐고 모든 고정/broad/native suite도 통과했다. 그러나 H4 correction은 여전히 안전하지 않다. Payment Executor가 lease/CAS 409를 immutable-payload 충돌과 구분하지 않고 **기존과 같은 fingerprint**를 conflict endpoint에 보내며, repository는 이를 terminal `CONFLICT`로 받아들인다. 또한 Mongo에서 동일 conflict의 동시 retry가 한 건으로 수렴하지 않았다. 승인된 payment-ordering amendment도 event type 순서만 검사할 뿐 decision/outbox publish identity를 확인하지 않아, 다른 identity의 conflict와 recorded publication을 정상 history로 수용한다.

## 검토 식별자와 입력

- Reviewer 역할: 독립 검토 전용
- Coder worktree: `/Users/vien/MyProjects/PBL-coder`
- branch: `wo/P6-02`
- 요청 및 실제 검토 tip: `f7deffa582d5762ad19783b1872955082a578b7a`
- canonical feature base / amended WO commit: `411e475f728344bad728c81c72bb7437a30da15e`
- correction implementation commit: `2d5318548c4b937b65f80f2e48f685f63b98f11a`
- `git merge-base 411e475f... f7deffa...`: `411e475f728344bad728c81c72bb7437a30da15e`
- `git merge-base f7deffa... main`: `888674533d7afbd27419b0037c03f6e4fdf892c6`
- 검토 전·후 coder worktree: clean
- canonical checkout: `feature/phase6-audit-e2e` at `411e475f728344bad728c81c72bb7437a30da15e`; 기존 untracked `.githooks/`는 보존

검토 입력 SHA-256:

| 파일 | SHA-256 |
|---|---|
| amended `work-orders/WO-P6-02-reputation-loop.md` | `813b4577aab0d24673a808a781c1008713eb3e0dc71a6dde16dae3ef81397561` |
| `.agent/outbox/WO-P6-02-planner-amendment-r2.done.md` | `e91112e3eb4d5c91e3ed028811e7510c60e9b2b1243e972877bd4b5c938e200e` |
| preserved `.agent/outbox/WO-P6-02-review.md` | `a9bb81fbe9252373f4c3c6e4a1150cce71e45da5ca191172ac6b45b18aeb3dae` |
| coder `.agent/outbox/WO-P6-02-coder-r2.done.md` | `6b13d98b8df0192101f09f0add9d446dc11d8badfeedbece34c5d2f7a3e03e9e` |
| canonical requirements | `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f` |
| canonical design | `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768` |
| canonical tasks | `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe` |

Work Order, r2 amendment, 원 rejection, coder r2 evidence, Phase 6 requirements/design/tasks, base-to-tip source/tests를 직접 읽었다. Coder self-report는 주장으로만 취급했다.

## commit 및 scope 회계

`git log --reverse 411e475f...f7deffa...`에는 merge ancestry로 기존 blocker/최초 구현/evidence까지 8개 commit이 보인다. r2 고유 correction 흐름은 다음과 같다.

```text
2d5318548c4b937b65f80f2e48f685f63b98f11a fix(phase6): bind reputation proofs and atomic publication evidence
b2597049d71032b7d2502fae6cf776fab8f4d4a0 merge: integrate canonical phase6 planner amendment into wo/P6-02
7afe4655d77ceb38c52db2433baeca541ea2ee87 docs(phase6): record WO-P6-02 round-2 correction evidence
f7deffa582d5762ad19783b1872955082a578b7a test(phase6): assert payment surfaces at every post-payment stage
```

- Exact base-to-tip diff: 34 paths, 11,943 insertions / 437 deletions.
- 30 product/test paths는 amended WO의 allowed-write와 정확히 일치한다.
- 나머지는 append-only `.agent/TURN_LOG.md`와 보존된 blocker/최초 coder evidence/r2 coder evidence다. Merge ancestry가 보존 파일을 packet diff에 포함한 것이며 protected planning 변경이나 scope leakage는 아니다.
- `7afe465...` evidence-only commit과 `f7deffa...`의 test/evidence 갱신은 허용 범위다. 다만 coder report의 “33 paths”는 실제 34 paths와 다른 non-blocking 회계 오기다.
- Dashboard, scenario controller/catalog, reputation UI, AWS/infra/contracts/tools 변경은 없다.

Packet base 기준 requirements/design/tasks, deployment gate, infra/contracts, infra/aws, dashboard, tools diff는 0이다. `main` merge-base 기준 보이는 requirements/design/tasks는 `8886745...411e475f`의 승인된 Phase 6 planning/amendment history이므로 coder 변경으로 귀속하지 않는다.

## 독립 실행 명령과 결과

### Work Order 고정 순서

| # | 정확한 명령 | 결과 |
|---|---|---|
| 1 | `uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests` | exit 0, `All checks passed!` |
| 2 | `uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src` | exit 0, 47 source files 오류 없음 |
| 3 | `uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_reputation.py services/buyer-audit-api/tests/test_phase6_terminal.py services/buyer-audit-api/tests/test_payment_service.py services/buyer-audit-api/tests/test_ai_inference_workflow.py services/buyer-audit-api/tests/test_ai_inference_domain.py services/buyer-audit-api/tests/test_api.py -q` | exit 0, `142 passed, 2 warnings in 1.86s` |
| 4 | `npm run build --workspace @pbl/commerce-gateway` | exit 0 |
| 5 | `node --test services/commerce-gateway/dist/tests/erc8004.test.js services/commerce-gateway/dist/tests/phase6-reputation-loop.test.js services/commerce-gateway/dist/tests/runtime.test.js` | exit 0, 34/34 pass |
| 6 | `npm run test:mongo:local` | exit 0, native loopback replica set `9 passed, 1 warning in 13.57s` |
| 7 | `git diff --check` | exit 0 |

### broad, gateway, history 보존

| 정확한 명령 | 결과 |
|---|---|
| `npm run test:unit` | exit 0, non-Mongo `293 passed, 9 deselected, 6 warnings in 2.55s` |
| `npm test --workspace @pbl/commerce-gateway` | exit 0, build 후 85/85 pass |
| legacy Permit2/historical audit/read-side/no-evidence/hard-filter 7-node pytest command | exit 0, 7/7 pass |
| C1/H1/H2/H3/H4/M1 집중 17-node pytest command | exit 0, 17/17 pass |
| payment amendment positive/negative 7-node pytest command | exit 0, 7/7 pass |

위 7-node history 명령은 다음 test를 직접 지정했다: legacy Permit2 projection, Permit2 terminal 거부 2건, legacy finding parse, read evidence-head 불변, no-evidence 50, hard-filter 우선. 17-node 명령은 stale audit, prepared/bundle/fingerprint/proof/source/transaction bind, confirmed/conflict append, exact query/window/reference/fallback, env allow-list, M1 preflight/index coverage를 지정했다. Payment 7-node 명령은 finalize/recorded/conflict positive와 conflict-before-decision, decision-before-audit, pre-terminal event, duplicate decision negative를 지정했다.

### exact-base protected diff와 teardown

| 정확한 명령 | 결과 |
|---|---|
| `git diff --check 411e475f...f7deffa...` | exit 0 |
| `git diff --name-status 411e475f...f7deffa...` | exit 0, 위 34 paths |
| `git diff --exit-code 411e475f...f7deffa... -- aidlc-docs/inception/requirements.md aidlc-docs/inception/design.md aidlc-docs/inception/tasks.md docs/ERC3009_DEPLOYMENT_GATE.md infra/contracts infra/aws apps/dashboard tools` | exit 0 |
| `git diff --name-only 8886745...f7deffa... -- <동일 protected paths>` | requirements/design/tasks만 출력; exact packet base 이전 승인 planning history |
| `git status --short` in coder worktree | 출력 없음, clean |
| `lsof -nP -iTCP:27019 -sTCP:LISTEN` after native tests/probe | 출력 없음 |
| `find /private/tmp -maxdepth 1 -type d -name 'pbl-mongo-r2-probe.*' -print` | 출력 없음 |
| `pgrep -af 'mongod.*27019\|pytest.*PBL-coder\|node --test.*commerce-gateway'` | 장기 실행 test process 없음; 명령 자체 PID만 일시 출력 |

## 원 rejection finding별 독립 disposition

| ID | r2 판정 | 독립 근거 |
|---|---|---|
| C1 | **FIXED** | Memory lock와 Mongo transaction이 `REPUTATION_RECORDED` append + `CONFIRMED`를 한 atomic unit으로 처리한다. 같은 proof retry는 기존 event를 반환한다. tx-only Phase 5 route는 404다. focused/native atomic tests 통과. |
| C2 | **FIXED** | Entry status가 `PREPARED` 또는 `SUBMITTED_UNKNOWN`이고 ref가 없으면 find 후 `SUBMITTED_UNKNOWN`으로만 남고 prepare/broadcast를 하지 않는다. 독립 Node probe: 두 상태 모두 `finds=2`, `prepares=0`, `giveFeedback=0`, `submittedUnknown=2`. |
| H1 | **FIXED** | confirmation이 registry/client/agent/value/decimals/tags/hash/URI/source/known coordinates/exact transaction ref에 결합되고 full proof가 저장된다. 다른 hash/source/ref probe가 fail closed. |
| H2 | **FIXED** | query fingerprint가 chain/registry/agent/trusted clients/tags를 묶고, answer window는 reported latest head와 exact lookback을 만족해야 한다. raw event의 trusted client/tag/window/source/ref 좌표와 cross-agent fallback이 거부된다. |
| H3 | **FIXED** | Buyer production composition에 gateway reputation provider가 연결됐다. `PBL_AUDIT_FEEDBACK_CLIENTS`는 strict parse되고 unset/empty이면 I/O 없이 `NO_EVIDENCE/50`; configured query는 gateway가 latest head 기반 bounded range를 결정한다. |
| H4 | **NOT FIXED** | Conflict event/API/call site와 atomic state+event 자체는 추가됐지만 정상 CAS/lease 경쟁을 false terminal conflict로 만들며, native Mongo 동일-conflict retry도 한 event로 수렴하지 않는다. 아래 High/Medium finding 참조. |
| M1 | **FIXED** | `jobId`, `snapshotId`를 포함한 모든 신규 unique index가 collision preflight source와 coverage test에 포함된다. |
| M2 | **FIXED** | native Mongo 9-test suite가 terminal finalize atomic unit과 confirmed publication atomic unit, rollback/races를 실제 replica set에서 실행한다. |

## 독립 targeted probes

모두 exact tip에서 fake/in-memory 또는 격리된 `127.0.0.1:27019` Mongo만 사용했다.

1. PREPARED/SUBMITTED_UNKNOWN restart probe: 두 상태, `transactionRef=null`, matching find 없음, live mode를 직접 drive했다. 두 결과 모두 `SUBMITTED_UNKNOWN`; `finds=2`, `prepares=0`, `giveFeedback=0`, `submittedUnknown=2`로 C2 correction 확인.
2. Same-fingerprint CAS probe (Node): `markPrepared`가 `ReputationOutboxConflictError("lease moved")`를 반환하게 했다. Publisher는 `recordConflict`를 호출했고 `requestedFingerprint === job.payloadFingerprint`가 `true`; external write는 0.
3. Same-fingerprint repository probe (Python memory): 기존 fingerprint와 같은 requested fingerprint로 `record_conflict` 호출. 결과 `CONFLICT`, `existingFingerprint == requestedFingerprint`, conflict event 1.
4. Payment identity probe (Python memory): decision identity A 뒤 conflict identity B를 append. `load_payment_view`와 existing `claim` 모두 성공했고 intent는 `SETTLED`; 새 claim event는 없었다. 같은 방식으로 decision A 뒤 recorded identity B도 view가 성공했다.
5. Native Mongo concurrent identical-conflict probe: 독립 임시 replica set에서 동일 `(identity, requestedFingerprint, reasonCode)`를 8회 동시 호출. 8회 모두 성공하고 동일 logical conflict event가 7개 append됐다. DB/process/temp directory는 종료·삭제됨.
6. 원 H1/H2/stale probe를 좁힌 17-node suite로 다시 실행: wrong commitment/proof/source/transaction, wrong query agent/client/registry/chain/tag/window/ref, cross-agent fallback, old ruleset가 모두 fail closed.

첫 Node CAS probe 작성 시 임의 tag를 사용해 canonical tag guard에서 먼저 거부됐다. `FEEDBACK_TAG1/2` export를 사용해 같은 probe를 즉시 재실행했으며 위 2번이 유효 결과다.

## findings by severity

### Critical

없음.

### High

#### R2-H1 — Payment ordering이 decision/outbox publish identity를 검증하지 않는다

- `core/payment.py:51-87`의 `_post_payment_tail_is_valid` 입력은 `list[EventType]`뿐이다. 따라서 `REPUTATION_DECIDED`, `REPUTATION_PUBLICATION_CONFLICT`, `REPUTATION_RECORDED` payload의 `publishIdentityHash`/`payloadFingerprint`를 비교할 수 없다.
- 독립 probe에서 decision identity A 뒤 conflict identity B 및 recorded identity B가 모두 정상 payment history로 수용됐다. 기존 test도 conflict payload를 `{"reasonCode": ...}`만으로 만들고 성공을 기대해 이 결합 누락을 고정한다.
- 이는 amended WO 설계 결정 12와 추가 AC 3의 “해당 decision/outbox identity 뒤” 조건, 그리고 recorded publication이 자기 decision 뒤에 와야 한다는 fail-closed ordering을 위반한다. Hash-chain 자체가 유효해도 다른 publish identity의 event를 해당 purchase payment tail로 인정한다.
- 조치: ordering validator가 event 객체/payload를 보게 하고, decision이 있을 때 conflict/recorded의 identity를 그 decision의 `publishIdentityHash`에 결합해야 한다. Fingerprint도 존재하는 event끼리 일치시켜야 한다. Decision 없는 기존 Phase 5 `REPUTATION_RECORDED` read compatibility는 별도 legacy branch로 보존하고, mismatched/missing identity negative tests를 추가해야 한다.

#### R2-H2 / H4 재개방 — 모든 lease/CAS 409가 동일 fingerprint의 terminal conflict로 오기록된다

- `services/commerce-gateway/src/erc8004.ts:485-501`은 `ReputationOutboxConflictError`의 원인이 lease 이동, stale status, immutable fingerprint 변경 중 무엇인지 구분하지 않고 현재 job의 **동일한** `payloadFingerprint`로 `recordConflict`를 호출한다.
- `publication_conflict_payload`와 memory/Mongo `record_conflict`는 `requestedFingerprint == existingFingerprint`를 거부하지 않고 job을 terminal `CONFLICT`로 바꾼다 (`core/reputation.py:1024-1039`, `memory.py:918-971`, `mongo.py:1943-1999`). 독립 Node+memory probe로 그대로 재현됐다.
- 요구사항 P6-AC-05.3과 WO 완료 기준은 **같은 identity의 다른 fingerprint**만 conflict로 기록한다. 정상 lease 경쟁이나 idempotent same-payload retry는 payload conflict가 아니다.
- 특히 broadcast 뒤 confirmation 중 lease가 이동하면 losing worker의 409가 아직 유효한 다른 worker/job을 `CONFLICT`로 terminalize할 수 있다. 실제 external feedback은 존재하지만 `REPUTATION_RECORDED`를 남기지 못하는 상태가 가능하다.
- 조치: HTTP/API error를 typed reason으로 구분하고, publisher는 immutable fingerprint mismatch일 때만 conflict evidence를 요청해야 한다. Backend도 same-fingerprint conflict를 거부해야 하며, lease/status CAS loss는 원래 409로만 남겨 recovery worker가 계속 처리하게 해야 한다.

### Medium

#### R2-M1 / H4 동시성 — Mongo의 동일 conflict retry가 append-only event 한 건으로 수렴하지 않는다

- `mongo.py:1952-1979`의 동일-event 조회가 transaction 밖에서 실행되고, 이후 transaction filter는 `status != CONFIRMED`만 검사한다. 먼저 append한 transaction이 job을 `CONFLICT`로 바꿔도 뒤 transaction들은 같은 filter를 계속 만족한다.
- 독립 native Mongo probe에서 동일 logical conflict 8회 동시 호출이 성공 8회, event 7개가 됐다. Source의 “same conflict recorded twice is one conflict” observable semantics와 idempotent retry 계약을 위반한다.
- 조치: 동일 conflict key를 transaction 안에서 다시 검사하거나 unique conflict key/event index 및 duplicate-to-existing-result 처리를 사용해 한 event로 수렴시킨다. Native Mongo에 concurrent identical conflict probe를 추가한다.

### Low / informational

- Coder r2 evidence의 changed-path 수가 33이지만 exact base-to-tip은 34다. 경로 자체는 모두 허용 범위다.
- Gateway는 `ERC8004_REPUTATION_REGISTRY` override를 지원하지만 Buyer production composition은 canonical registry constant를 사용한다. 현재 `.env.example`의 canonical 값에서는 일치한다. 향후 override를 지원할 의도라면 두 process가 동일 config를 공유하거나 noncanonical override를 명시적으로 거부해야 한다.

## 요구 영역별 최종 disposition

| 영역 | 판정 | 근거 |
|---|---|---|
| terminal AUDITED → 100/0/DEFER, stale/ruleset | 통과 | deterministic mapping, stale audit, unknown/preview no-job tests |
| audit+decision+job 원자성 | 통과 | memory race + native Mongo transaction/rollback |
| PREPARED/SUBMITTED_UNKNOWN restart no second submit | 통과 | 독립 Node probe에서 write 0 |
| transaction ref single bind / confirmed proof | 통과 | exact ref/source/coordinates/receipt+decoded-event 결합 |
| conflict classification / conditional ordering | **실패** | same-fingerprint CAS false conflict; payment identity mismatch 수용 |
| conflict concurrency/idempotency | **실패** | native 8 concurrent calls → 7 duplicate events |
| snapshot provenance / trusted client / exact query / immutable snapshot | 통과 | strict env parsing, exact scope/window/ref, cross-agent fallback 차단 |
| no evidence 50 / hard filter 뒤 10% / legacy benchmark 미사용 | 통과 | focused + broad regression |
| production composition / writer guard | 통과(정보성 config caveat) | provider wired; writer default disabled; fake production 거부 |
| historical evidence / Permit2 read-only / read side effects | 통과 | 7 focused + broad; backfill/mutation 없음 |
| scope / protected paths | 통과 | exact packet-base protected diff 0; dashboard/scenario/AWS leakage 0 |

## 보존 및 외부 경계

- 기존 `REPUTATION_RECORDED`, legacy audit documents, historical Permit2 settlements는 backfill/mutation/delete 없이 읽혔다. Permit2 execute/reconcile/terminalization은 계속 거부된다.
- Read paths는 evidence head, wallet reservation/spend, payment/reputation events를 만들지 않았다.
- 이번 리뷰는 public RPC, ERC-8004, provider, facilitator, Atlas, AWS, public chain에 연결하거나 쓰지 않았다. Node chain interactions는 fake뿐이며 Mongo는 loopback 임시 replica set만 사용했다.
- Secret/private key/raw prompt-response를 읽거나 출력하지 않았다.
- 제품 코드, 테스트, planning/Work Order, coder worktree는 수정하지 않았다. Rollback은 branch 미통합으로 보존해야 한다.

REJECT
