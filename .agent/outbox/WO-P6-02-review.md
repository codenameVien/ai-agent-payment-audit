# WO-P6-02 독립 리뷰

## 최종 판정 요약

`REJECT`다. 고정 suite와 broad regression은 모두 통과했지만, 승인 계약의 핵심인 at-most-once publish와 append-only confirmed evidence가 구현되지 않았다. 복구된 `PREPARED`가 새 transaction을 제출하며, outbox `CONFIRMED`는 `REPUTATION_RECORDED`와 원자적으로 기록되지 않는다. 기존 tx-only endpoint는 receipt/event proof 없이도 `REPUTATION_RECORDED`를 append한다. 추가로 confirmation binding, snapshot provenance, production wiring, conflict evidence, Mongo collision preflight에 blocking 결함이 재현됐다.

## 검토 식별자와 입력

- Reviewer 역할: 독립 검토 전용. 제품 코드, 테스트, planning/Work Order, coder worktree는 수정하지 않았다.
- Coder worktree: `/Users/vien/MyProjects/PBL-coder`
- branch: `wo/P6-02`
- 승인 packet base: `b8f3f3fd92a4f1d13ccad539899a5ff6fb3599ce`
- 구현 commit: `c201b391b77128086663e3c047c2fcf737b179cd`
- 요청 및 실제 검토 tip: `72e9611f70132bd63b268fd806ada327f4253981`
- `git merge-base b8f3f3f... 72e9611...`: `b8f3f3fd92a4f1d13ccad539899a5ff6fb3599ce`
- `git merge-base 72e9611... main`: `888674533d7afbd27419b0037c03f6e4fdf892c6`
- 검토 전·검증 후 coder 상태: clean, `## wo/P6-02`
- canonical checkout의 기존 상태: `## feature/phase6-audit-e2e`, 기존 untracked `.githooks/` 존재. 이를 건드리지 않았고 이 보고서만 생성했다.

입력 SHA-256:

| 파일 | SHA-256 |
|---|---|
| `work-orders/WO-P6-02-reputation-loop.md` | `b8385701c290d93d7e647c6c3364274ec5409e6dd81c9fa37678c179d3b4fc53` |
| planner amendment | `e49af8385885b676d83930e5a073430dff52cb2cfb6f70e9877936f1d8190e19` |
| coder evidence | `7d57801846d90edb42311b0a18bec972980f8cb1ce71c1a959540ce515612723` |
| canonical requirements | `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f` |
| canonical design | `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768` |
| canonical tasks | `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe` |

Work Order, amendment, coder evidence, Phase 6 canonical requirements/design/tasks 및 base-to-tip source/tests를 직접 읽었다. Coder self-report는 주장으로만 취급했다.

## commit 및 scope 회계

승인 base 이후 commit은 정확히 두 개다.

```text
c201b391b77128086663e3c047c2fcf737b179cd feat(phase6): automate reputation loop
72e9611f70132bd63b268fd806ada327f4253981 docs(phase6): record WO-P6-02 coder evidence
```

`git diff --name-status b8f3f3f...72e9611...`는 Work Order allowed-write에 포함된 30개 경로만 반환했다. 총 diff는 8,302 insertions/256 deletions이다. 중앙 `EventType` 변경은 amendment가 허용한 두 enum member 추가뿐이다. `.agent/TURN_LOG.md`는 72 insertions/0 deletions이다.

`c201b39...72e9611`은 `.agent/outbox/WO-P6-02-coder.done.md`만 269줄 추가한다. evidence-only follow-up 자체는 allowed scope라 수용한다. 다만 coder report의 “29 paths”는 실제 30 paths와 불일치하는 non-blocking evidence 오기다.

Packet base 기준 canonical requirements/design/tasks, deployment gate, contracts, AWS/infra, dashboard, tools에는 diff가 없다. Dashboard/scenario controller/catalog/reputation UI 범위 누출도 없다.

`main` merge-base `8886745...` 기준 protected diff에는 requirements/design/tasks가 보이지만, 세 파일은 모두 `8886745...b8f3f3f`의 승인된 Phase 6 planning 변경이다. Packet boundary `b8f3f3f...72e9611`에서는 변경이 0이므로 coder에게 misattribute하지 않는다.

## 독립 실행 명령과 결과

### Work Order 고정 순서

| # | 정확한 명령 | 결과 |
|---|---|---|
| 1 | `uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests` | exit 0, `All checks passed!` |
| 2 | `uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src` | exit 0, 47 source files 오류 없음 |
| 3 | `uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_reputation.py services/buyer-audit-api/tests/test_phase6_terminal.py services/buyer-audit-api/tests/test_ai_inference_workflow.py services/buyer-audit-api/tests/test_ai_inference_domain.py services/buyer-audit-api/tests/test_api.py -q` | exit 0, `95 passed, 2 warnings in 1.58s` |
| 4 | `npm run build --workspace @pbl/commerce-gateway` | exit 0 |
| 5 | `node --test services/commerce-gateway/dist/tests/erc8004.test.js services/commerce-gateway/dist/tests/phase6-reputation-loop.test.js services/commerce-gateway/dist/tests/runtime.test.js` | exit 0, 26/26 pass |
| 6 | `npm run test:mongo:local` | exit 0, 실제 loopback replica set에서 `8 passed, 1 warning in 10.10s` |
| 7 | `git diff --check` | exit 0 |

### broad regression 및 보존 집중 실행

| 정확한 명령 | 결과 |
|---|---|
| `npm run test:unit` | exit 0, Python non-Mongo `265 passed, 8 deselected, 6 warnings in 2.35s` |
| `npm test --workspace @pbl/commerce-gateway` | exit 0, build 후 77/77 pass |
| `uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_projections.py::test_legacy_permit2_record_is_historical_and_not_base_verified services/buyer-audit-api/tests/test_phase6_payment_terminal.py::test_historical_permit2_intent_cannot_reconcile_or_close services/buyer-audit-api/tests/test_phase6_payment_terminal.py::test_historical_proof_cannot_terminalize_an_active_base_submission services/buyer-audit-api/tests/test_phase6_audit.py::test_legacy_finding_documents_still_parse_without_rewriting services/buyer-audit-api/tests/test_api.py::test_read_paths_never_change_the_evidence_head services/buyer-audit-api/tests/test_ai_inference_domain.py::test_a_candidate_without_reputation_evidence_scores_neutral_fifty services/buyer-audit-api/tests/test_ai_inference_domain.py::test_reputation_never_rescues_a_hard_filtered_candidate -q` | exit 0, 7/7 pass |

### exact-base/protected/teardown

| 정확한 명령 | 결과 |
|---|---|
| `git diff --check b8f3f3f...72e9611...` | exit 0 |
| `git diff --name-status b8f3f3f...72e9611...` | exit 0, allowed 30 paths only |
| `git diff --exit-code b8f3f3f...72e9611... -- aidlc-docs/inception/requirements.md aidlc-docs/inception/design.md aidlc-docs/inception/tasks.md docs/ERC3009_DEPLOYMENT_GATE.md infra/contracts infra/aws apps/dashboard tools` | exit 0 |
| `git diff --name-only 8886745...72e9611... -- <같은 protected paths>` | requirements/design/tasks만 출력; 모두 packet base보다 오래된 승인 planning 변경 |
| `git diff --numstat c201b39...72e9611...` | coder evidence만 `269 0` |
| `lsof -nP -iTCP:27019 -sTCP:LISTEN` | native suite 종료 후 exit 1/출력 없음 |
| `find /private/tmp -maxdepth 1 -type d -name 'pbl-mongo-test.*' -print` | exit 0/출력 없음 |

SHA 계산의 첫 `shasum -a 256 ...`은 환경의 지원되지 않는 `C.UTF-8` locale 때문에 실패했다. 파일 변경은 없었고 `openssl dgst -sha256 ...`로 위 값을 확인했다.

## targeted negative probes

모두 coder worktree의 exact tip에서 local in-memory/fake 또는 loopback만 사용했다. Probe의 exit 1은 안전 조건을 assertion했는데 구현이 그 조건을 어겼다는 뜻이다.

| Probe | 관측 결과 |
|---|---|
| Python in-memory: job을 `PREPARED(feedbackHash=0x11...)`로 만든 뒤 같은 agent/value이나 `feedbackHash=0x22...`인 proof로 `mark_confirmed` | exit 1. 다른 hash가 수용되어 job이 `CONFIRMED`; 저장 hash가 `0x22...`로 덮였고 purchase의 `REPUTATION_RECORDED` 수는 0 |
| Node fake: restart된 `PREPARED`, `transactionRef=null`, matching find 결과 없음, live mode에서 `drive(job)` | exit 1. `giveFeedbackCalls=1`, 결과 `SUBMITTED_UNKNOWN`; 복구가 새 제출을 만듦 |
| Python in-memory: Base-bound submitted ref와 같은 hash이나 `HISTORICAL_ON_CHAIN` source인 proof | exit 1. proof 수용, confirmed job source가 Base에서 historical로 변경됨 |
| Python fake query: 요청 agent `1`/trusted client A에 대해 응답 agent `999`/trusted client B, scope block `500..500`, event block `999`, nested ref 좌표 `3/4`, event 좌표 `999/7`, historical source | exit 1. 전부 수용되어 `derivedScore=100.0` snapshot 저장 |
| Python fallback: seller A의 agent `1` fresh score-100 snapshot 저장 후 같은 seller/agent `2` query 실패 | exit 1. agent `1` snapshot을 agent `2` 요청에 재사용해 `STALE/100.0` 반환 |
| Python persisted-audit: current head에 `phase6.rules.old` AUDITED를 append 후 coordinator 실행 | exit 0. 명시적 re-audit policy가 필요하다는 `EvidenceIntegrityError`; decision/job 0 |

추가 Mongo collision probe용 isolated `mongod` 명령은 실행기 안전 정책이 temp directory 삭제 구문을 거부해 process 생성 전에 중단됐다. 따라서 아래 M1은 실행됐다고 주장하지 않으며, actual native suite 결과와 source/index-set 대조로 판정한다.

## findings by severity

### Critical

#### C1 — `CONFIRMED`와 append-only `REPUTATION_RECORDED`가 분리되어 있고 tx-only 성공 writer가 열려 있다

- Memory/Mongo `mark_confirmed`는 outbox 문서만 `CONFIRMED`로 바꾼다 (`memory.py:822-866`, `mongo.py:1757-1802`). Evidence API confirmed endpoint도 이를 호출해 job DTO만 반환한다 (`api/app.py:1349-1374`). Purchase event append는 없다.
- Payment Executor의 outbox HTTP client는 `markConfirmed`만 호출하므로 정상 publisher flow가 `REPUTATION_RECORDED`를 만들 방법이 없다.
- 반대로 기존 `/internal/evidence/purchases/{purchase_id}/reputation`은 receipt proof, decoded event, block/log/client/tags/URI/publish identity를 받지 않고 tx-shaped hash만으로 event를 append한다 (`api/app.py:1535-1600`). 기존 API round-trip test도 임의 hash를 보내 200과 `REPUTATION_RECORDED`를 기대한다.
- 독립 probe는 valid-shaped proof로 outbox가 `CONFIRMED`였는데 event count가 0임을 확인했다. 즉 design §18.8의 “append REPUTATION_RECORDED + outbox CONFIRMED” 원자 단위도, WO의 confirmed-only gate도 성립하지 않는다.
- 영향: receipt/event 없이 성공 evidence를 만들 수 있고, 반대로 실제 confirmed publication은 canonical purchase history에 남지 않는다.

#### C2 — crash 후 `PREPARED`가 두 번째 on-chain transaction을 제출한다

- `PREPARED`에는 feedback hash만 저장되고 exact prepared/signed transaction reference는 없어도 된다 (`core/reputation.py:449-453`).
- Publisher는 `PREPARED`와 fresh `PENDING/LEASED`를 구분하지 않는다. find가 비어 있고 ref가 없으면 다시 `markPrepared`한 뒤 `submitObjectiveFeedback`를 호출한다 (`erc8004.ts:306-372`).
- broadcast 직후, returned hash를 `SUBMITTED_UNKNOWN`/`CONFIRMED`로 저장하기 전 crash가 나면 durable 상태는 바로 이 `PREPARED + ref null`이다. 재시작의 bounded log lookup이 아직 event를 찾지 못하면 새 transaction을 제출한다.
- 독립 Node probe에서 이 정확한 상태가 `giveFeedbackCalls=1`을 만들었다. Coder의 “PREPARED never second write” test는 ref가 이미 채워진 PREPARED만 사용해 crash window를 검사하지 않았다.
- 영향: “one user request / publish identity at most one external effect”와 WO-P6-02의 핵심 idempotency를 직접 위반한다.

### High

#### H1 — confirmation proof가 prepared commitment와 exact transaction provenance에 결합되지 않는다

- `assert_proof_matches_job`은 agent/tags/value와 EVM chain, kind+hash만 비교한다 (`core/reputation.py:768-797`). Prepared `feedback_hash`, evidence source, 이미 알려진 block/log 좌표, client, feedback URI/audit bundle을 비교하지 않는다. Proof에는 registry field 자체가 없어 docstring의 registry 검증도 불가능하다.
- Memory/Mongo 모두 accepted proof의 feedback hash/source로 기존 job을 덮는다. 같은 `receiptProofRef` retry는 나머지 proof가 달라도 기존 confirmed job을 반환한다.
- 독립 probe 두 개가 (a) prepared hash와 다른 hash의 confirmation, (b) Base-bound ref를 동일 hash historical proof로 재분류하는 confirmation을 모두 수용했다.
- 전체 confirmed proof 원문도 outbox에 보존되지 않고 client/tags/URI/registry가 유실된다. C1 때문에 extended event에도 남지 않는다.

#### H2 — snapshot provenance가 요청과 raw event를 결합하지 않아 임의 score가 들어간다

- Buyer adapter는 응답 scope 중 chain/registry만 configured 값과 비교한다 (`adapters/reputation_gateway.py:135-178`). 요청 agent ID, trusted-client allow-list, tags, 요청 block range를 응답과 비교하지 않는다.
- Snapshot validation은 client가 응답이 주장한 allow-list에 있고 tag가 응답 scope와 같은지만 본다. Event block이 scope 범위인지, nested transaction ref의 block/log가 event 좌표와 같은지, source가 Base인지 검증하지 않는다 (`core/reputation.py:573-591`, `626-673`).
- 독립 probe는 다른 agent/client, 범위 밖 block, 충돌 좌표와 historical ref를 모두 가진 event가 score 100으로 저장됨을 확인했다.
- `latest_snapshot`은 seller ID만 조회하고 fallback은 requested agent/scope 일치 확인 없이 fresh snapshot을 재사용한다. 독립 probe에서 agent `1`의 score 100이 agent `2` 요청에 반환됐다 (`reputation_gateway.py:119,180-203`; `core/reputation.py:690-707`).
- 영향: hard filter 뒤 10%라는 산술 경계는 지켜도 입력 reputation이 다른 agent/client/range의 증거일 수 있어 선택 무결성이 깨진다.

#### H3 — production Buyer composition에 reputation provider가 연결되지 않았고 query 범위도 block 0 하나다

- `build_container`의 `AiInferenceDecisionWorkflow(...)`에는 `reputation=` 인자가 없다 (`composition.py:140-159`). `GatewayReputationProvider`/`HttpReputationQueryClient`는 production source 어디에서도 조립되지 않는다. 따라서 실제 workflow는 항상 `None -> neutral 50` 경로이며 새 query/snapshot 점수가 선택에 반영되지 않는다.
- 설령 수동으로 연결해도 adapter는 `from_block=0, to_block=0`을 고정 전송한다 (`reputation_gateway.py:121-126`). Gateway는 이를 latest-range sentinel로 해석하지 않고 그대로 registry log query에 전달하므로 genesis block 외 feedback을 조회하지 않는다.
- Fake-provider unit tests는 workflow capability만 증명하며 production wiring을 증명하지 않는다.

#### H4 — publication conflict는 append-only evidence도, reachable workflow도 아니다

- `record_conflict`는 outbox status만 `CONFLICT`로 바꾸며 `REPUTATION_PUBLICATION_CONFLICT`를 append하지 않는다 (`memory.py:868-892`, `mongo.py:1804-1846`).
- Production source에서 `record_conflict()` call site가 없고 HTTP conflict endpoint도 없다. 검색 결과는 port, 두 repository 구현, unit tests뿐이다.
- Different fingerprint/transition conflict는 exception으로 끝나며 required append-only conflict finding을 남기지 않는다. 승인 amendment로 추가한 event enum은 소비되지 않는다.

### Medium

#### M1 — collision preflight가 새 unique index 두 개를 누락한다

- `phase6_index_collision_report`에는 publish identity, EVM/local ref, snapshot query가 있으나 `jobId`와 `snapshotId` collision spec은 없다 (`mongo.py:603-631`).
- `ensure_indexes`는 이후 `unique_reputation_job_id`와 `unique_reputation_snapshot`을 unique로 생성한다 (`mongo.py:800-804`, `827-830`). Existing duplicates가 있으면 read-only preflight의 명시적 report가 아니라 index 생성 중 `DuplicateKeyError`와 partial index creation으로 실패한다.
- Coverage test의 `PHASE6_NEW_UNIQUE_INDEXES` 목록도 이 두 index를 제외해 drift test가 결함을 숨긴다.

#### M2 — native Mongo suite가 terminal/confirmation 원자 단위를 실행하지 않는다

- Native 8 tests는 기존 Phase 6 history/index, raw outbox claim/transition, snapshot immutability를 실제 replica set에서 검증해 해당 부분은 유효하다.
- 그러나 reputation native test는 outbox job을 raw insert한 뒤 lease/confirm만 검사한다. `TerminalAuditCoordinator -> finalize_atomic`의 audit+decision+job transaction과 required `REPUTATION_RECORDED + CONFIRMED` transaction을 실제 Mongo에서 실행하지 않는다. 후자는 C1처럼 구현도 없다.

## 요구 영역별 disposition

| 영역 | 판정 | 근거 |
|---|---|---|
| terminal audit → 100/0/DEFER | 부분 통과 | mapping/focused tests 통과; unknown/preview job 0 |
| 기존 AUDITED 호환, stale/ruleset 차단 | 통과 | persisted audit reuse와 stale test 통과; 독립 old-ruleset probe fail-closed |
| audit+decision+job 원자성 | source상 구현, native 증거 부족 | Mongo `with_transaction`; in-memory race 통과; M2 |
| lease/CAS/restart | 부분 통과 | single lease/expiry/cross-job tx unique는 통과; PREPARED restart는 C2 실패 |
| transaction ref single bind/conflict | 실패 | H1 source reclassification 및 H4 |
| confirmed receipt+event만 recorded | 실패 | C1/H1 |
| Mongo collision preflight/native | 부분 통과 | native 8 pass; M1/M2 |
| provider-level snapshot provenance | 실패 | H2/H3 |
| no evidence 50 / immutable snapshot | 통과 | unit/native tests 통과 |
| hard filter 후 reputation 10% | 통과 | filter `continue`가 component 계산보다 앞서며 모든 preset 10% |
| legacy benchmark reputation 미사용 | 통과 | no snapshot이면 benchmark 값과 무관하게 50 |
| live writer default disabled / production fake 거부 | 통과 | focused runtime tests 및 `resolveWriteMode` source 확인 |
| historical evidence / Permit2 read-only | 통과 | focused 7, broad suites, native history test 통과; backfill/delete 경로 없음 |
| read-side effects | 통과 | read head 불변 test 통과; reputation query는 read-only RPC adapter |
| scope/protected paths | 통과 | exact packet base protected diff 0; allowed 30 paths only |

## 보존·외부 경계

- Existing `REPUTATION_RECORDED` parser/read model과 legacy Permit2/history는 backfill 없이 계속 읽힌다. Permit2 execute/reconcile/terminalization은 거부되는 회귀가 통과했다.
- Selection core는 hard-ineligible reputation-100 후보를 복원하지 않고, legacy `BenchmarkSnapshot.reputation_score`를 점수에 쓰지 않는다.
- Production writer mode는 unset/empty에서 disabled이고 `fake`/unknown 값은 startup에서 거부된다.
- 이 리뷰에서는 public RPC/provider/facilitator/ERC-8004/AWS/Atlas/public-chain 호출 또는 쓰기를 하지 않았다. Native Mongo는 `127.0.0.1:27019` 임시 replica set만 사용했고 종료 후 listener/temp directory가 남지 않았다.
- Secret/env/private key/raw prompt-response를 읽거나 출력하지 않았다. Commit, merge, push, deploy도 하지 않았다.

REJECT
