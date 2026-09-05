# WO-P6-01 3차 독립 리뷰

## 검토 식별자와 입력

- Reviewer 역할: 독립 검토 전용. 제품 코드, 테스트, planning/Work Order, coder worktree는 수정하지 않았다.
- 검토 repository/worktree: `/Users/vien/MyProjects/PBL-coder`
- branch: `wo/P6-01`
- 승인 packet base: `685472c2178fac1ed1d16fedd4dde4dda7d3ed64`
- correction implementation anchor: `2d3b500ba9ef511417826cc2bf9ad45660b23edf`
- 요청 및 실제 검토 tip: `ca037c9c9e54a1701db995c90dd5d976df83dd4e`
- `git merge-base 685472c... ca037c9...`: `685472c2178fac1ed1d16fedd4dde4dda7d3ed64`
- `git merge-base ca037c9... main`: `888674533d7afbd27419b0037c03f6e4fdf892c6`
- `git merge-base --is-ancestor 2d3b500... ca037c9...`: exit 0
- coder worktree 검토 전·검증 후 상태: clean, `## wo/P6-01`
- reviewer repo 검토 전 상태: `## feature/phase6-audit-e2e`, 기존 untracked `.githooks/` 존재. 이를 건드리지 않았고 이 보고서만 새로 만들었다.

입력 및 보호 문서 SHA-256:

| 파일 | SHA-256 |
|---|---|
| `work-orders/WO-P6-01-core-truth-model.md` | `a93814a1e60c5d6c1fcc839365c75acbda3eb753400c3341e13c48a26cc48a6e` |
| 기존 1차 review | `939578fc09897113fc67cdc63777d448845e87a51b363a9d29f3082415773853` |
| 기존 2차 review | `6a249875364b874fe4371af6835af07b9cd036fbeae7456bfd408844344401fe` |
| coder evidence | `670c577ed1cb9b1b858e2a1872533997e6207fa176d1aaec0179f136d149dcc0` |
| canonical requirements | `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f` |
| canonical design | `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768` |
| canonical tasks | `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe` |
| `docs/ERC3009_DEPLOYMENT_GATE.md` | `62fa20da465b450daf8e92cdbc084192470232fe36fec24b52cda028d5cd3208` |

Work Order, 두 prior report, coder evidence, Phase 6 canonical requirements/design/tasks와 base-to-tip source/tests를 직접 읽었다. Coder 보고서는 주장으로만 취급하고 아래 명령과 probe를 독립 실행했다.

## commit 및 scope 회계

승인 base 이후 history:

```text
449090805948f095f1d35373ffb7852f8f43a1c6 feat(phase6): implement canonical truth model
198ee43de2a142e70a0f50c73d3d755d47628383 docs(phase6): record WO-P6-01 coder evidence SHAs
c9e05e7c2b72378c5b706010cf6a70640891fbd5 docs(phase6): record WO-P6-01 coder evidence SHAs
0c2f5c959e4d5b09ffde45eb754a43f055b933e0 docs(phase6): record WO-P6-01 coder evidence SHAs
16451a7817236e7b708835db7349a4557287de31 fix(phase6): harden terminal truth model per review
50a34d4de646aa6f53b7abc516f6d7700ffc2779 docs(phase6): record WO-P6-01 correction evidence
2d3b500ba9ef511417826cc2bf9ad45660b23edf fix(phase6): bind evidence provenance and audit currency
ca037c9c9e54a1701db995c90dd5d976df83dd4e docs(phase6): record WO-P6-01 round-2 correction evidence
```

`git diff --name-status 685472c...ca037c9...`는 Work Order가 허용한 21개 경로만 반환했다: `.agent/TURN_LOG.md`, coder evidence, Buyer/Audit의 지정된 9개 source와 6개 test, Gateway의 지정된 2개 source와 2개 test다. 총 diff는 9,462 insertions/560 deletions이며 TURN_LOG는 183 insertions/0 deletions다.

`2d3b500...ca037c9...`는 `.agent/outbox/WO-P6-01-coder.done.md`만 바꾼다. 이 파일을 제외한 post-anchor diff는 exit 0/empty다. Dashboard, scenario catalog/runner, reputation core/outbox/publisher, packages/tools, seller, contracts, infra, deployment에는 exact-base diff가 없다.

## 독립 명령과 결과

### Work Order 고정 순서 suite

| # | 정확한 명령 | 결과 |
|---|---|---|
| 1 | `uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests` | exit 0, `All checks passed!` |
| 2 | `uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src` | exit 0, 44 source files 오류 없음 |
| 3 | `uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_projections.py services/buyer-audit-api/tests/test_phase6_audit.py services/buyer-audit-api/tests/test_phase6_payment_terminal.py services/buyer-audit-api/tests/test_payment_service.py services/buyer-audit-api/tests/test_api.py -q` | exit 0, `145 passed, 6 warnings in 1.49s` |
| 4 | `npm run build --workspace @pbl/commerce-gateway` | exit 0, TypeScript build 성공 |
| 5 | `node --test services/commerce-gateway/dist/tests/gateway.test.js services/commerce-gateway/dist/tests/phase6-truth-model.test.js` | exit 0, 43/43 pass |
| 6 | `npm run test:mongo:local` | exit 0, `6 passed, 1 warning in 6.86s` |
| 7 | `git diff --check` | exit 0 |

Native Mongo 전 `lsof -nP -iTCP:27019 -sTCP:LISTEN`은 exit 1/출력 없음이었다. 스크립트는 `127.0.0.1:27019`, 임시 `/private/tmp/pbl-mongo-test.*` replica set만 사용했다.

### broad regression

| 명령 | 결과 |
|---|---|
| `uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests -q -m 'not mongo'` | exit 0, `195 passed, 6 deselected, 6 warnings in 1.86s` |
| `npm test --workspace @pbl/commerce-gateway` | exit 0, build 성공, 58/58 pass |

### exact-base/protected 검사

| 명령 | 결과 |
|---|---|
| `git diff --check 685472c...ca037c9...` | exit 0 |
| `git diff --name-status 685472c...ca037c9...` | exit 0, 허용 21개 경로만 출력 |
| `git diff --exit-code 685472c...ca037c9... -- aidlc-docs/inception/requirements.md aidlc-docs/inception/design.md aidlc-docs/inception/tasks.md docs/ERC3009_DEPLOYMENT_GATE.md infra/contracts infra/aws` | exit 0 |
| `git diff --exit-code 685472c...ca037c9... -- work-orders package.json package-lock.json .gitignore .agent/CURRENT_STATE.md .agent/DECISIONS.md .agent/HANDOFF.md apps services/seller-service contracts infra docs` | exit 0 |
| `git diff --exit-code 685472c...ca037c9... -- apps tools packages services/buyer-audit-api/src/buyer_audit_api/scenarios services/buyer-audit-api/src/buyer_audit_api/core/reputation.py services/buyer-audit-api/src/buyer_audit_api/core/terminal.py services/buyer-audit-api/src/buyer_audit_api/adapters/reputation_gateway.py services/commerce-gateway/src/erc8004.ts` | exit 0 |
| `git diff --exit-code 2d3b500...ca037c9... -- . ':(exclude).agent/outbox/WO-P6-01-coder.done.md'` | exit 0 |
| `git diff --check "$(git merge-base HEAD main)"..HEAD` | exit 0 |
| `git diff --name-only "$(git merge-base HEAD main)"..HEAD` | exit 0; pre-packet Phase 6 planning relay와 현재 packet을 함께 출력 |
| Work Order의 main-relative protected diff | exit 1; 변경은 `8886745...685472c`의 승인된 Phase 6 planning append |

마지막 exit 1은 coder에게 귀속하지 않는다. `main`과의 merge-base `8886745...`가 사용자가 지정한 승인 planning base보다 오래됐다. Packet 경계인 `685472c...ca037c9`에서 canonical planning, deployment gate, infra 및 나머지 보호 경로 diff는 모두 0이다.

SHA 계산의 첫 `shasum -a 256 ...`은 실행 환경의 지원되지 않는 `C.UTF-8` locale 때문에 exit 9였다. 파일 변경은 없었고, locale 비의존 `openssl dgst -sha256 ...`로 위 표의 값을 독립 확인했다.

## prior r2 독립 probe 재실행

모든 probe는 로컬 process와 in-memory/fake repository만 썼다.

| prior finding / 입력 | 관측 결과 |
|---|---|
| H1 Node: BASE submission과 동일 hash의 `HISTORICAL_ON_CHAIN` receipt를 `classifyReceipt()`에 전달 | 거부. `TransactionRefError: receipt evidence source is not the submitted evidence source` |
| H1 Python: active Base intent의 bound hash를 HISTORICAL ref/source mismatch proof로 `confirm_mismatch()` | 거부. `PaymentEvidenceError`; terminal/budget mutation 전에 종료 |
| H2 Python: run-A `localtx:` submission에 run-B scenario를 단 3회 check와 no-transfer proof로 제출 | 첫 check에서 거부. `PaymentEvidenceError: ... scenario run does not own ...`; attempt count 0 |
| H4 Python: 같은 head에 `phase6.rules.old` audit을 저장한 뒤 `AuditService.audit()` | 거부. 현재 `phase6.rules.v1`과 달라 explicit re-audit policy 요구 |
| H4 Python: audit append race의 loser가 winner audit 뒤 추가 access event까지 읽는 상황 | stale 반환 없이 거부. 현재 마지막 event는 `SENSITIVE_PAYLOAD_ACCESSED` |
| H4 Python: predecessor와 무관한 `evidenceHeadEventHash`를 가진 `AUDITED` event projection | 거부. `EvidenceIntegrityError: stored audit does not describe the head it was appended to` |
| M2 fake Mongo call-order recorder | 첫 preflight position 16; 신규 8개 unique index position 24–31; 전부 preflight 뒤 생성 |

추가 회귀 확인:

- C1: 네 terminal state (`SETTLED`, `FAILED`, `MISMATCH_CONFIRMED`, `RECONCILED_NO_TRANSFER`) 각각 seller/signing/submission 0회 test가 독립 Node 실행에서 통과했다. `gateway.ts`의 terminal early return도 직접 확인했다.
- H3: confirmations 미제공(0으로 정규화) 상태에서 attempt bound에 도달해도 `RECONCILIATION_REQUIRED`이고 no-transfer를 호출하지 않는 Node test가 통과했다. Python은 recorded finality와 proof finality의 동일성 및 configured minimum을 검사한다.
- M1: `InternalConfirmMismatchRequest`에서 expected state/count/head 세 필드를 뺀 Pydantic probe는 3개 validation error로 거부됐고, `0x` + 64개 비-hex 문자의 settlement hash도 validation error로 거부됐다.

## prior finding별 disposition

| finding | 3차 판정 | 독립 근거 |
|---|---|---|
| C1 Critical | **RESOLVED** | 모든 terminal state가 seller/sign/submission 전 반환하며 4개 회귀 test 통과 |
| H1 High | **RESOLVED** | Gateway와 Python 모두 submission identity뿐 아니라 exact provenance/source를 결합; r2의 두 동일-hash wrong-source probe가 거부됨 |
| H2 High | **RESOLVED** | 원 reconciliation event의 complete scenario와 local run ownership을 check/mismatch/no-transfer 모두에 결합; foreign-run probe가 mutation 전 거부됨 |
| H3 High | **RESOLVED / REGRESSION 없음** | bounded attempt와 configured finality가 함께 필요; unknown/zero finality는 nonterminal |
| H4 High | **RESOLVED** | normal/race path가 공통 current reader로 head+ruleset을 검증하고 projection도 동일 persisted-audit parser를 사용; r2의 세 probe 모두 fail-closed |
| M1 Medium | **RESOLVED / REGRESSION 없음** | terminal proof guard가 필수이며 stale 값은 409, EVM hash는 schema/core에서 canonical hex 검증 |
| M2 Medium | **RESOLVED** | collision report가 두 singleton을 포함한 신규 unique index 8개 전부를 포괄하고 생성 전에 실행됨; fake order와 native Mongo 모두 통과 |

## findings by severity

### Critical / High / Medium

없음. 정확성, 보안, orthogonal state/event compatibility, read-side effect, transaction-reference type/provenance, concurrency/idempotency, budget/outflow accounting에서 integration을 막을 재현 가능한 결함을 찾지 못했다.

### Low / process — non-blocking

- Focused/broad Python 실행의 6 warnings 중 4개는 동기 parameterized test에 `pytest.mark.asyncio`가 붙은 test-hygiene 경고이고, 나머지는 dependency deprecation 경고다. Test 누락이나 skip은 아니며 145/195 결과에는 영향이 없다.
- evidence-only commit 다섯 개(`198ee43...`, `c9e05e7...`, `0c2f5c9...`, `50a34d4...`, `ca037c9...`)는 각각 coder evidence 한 파일만 수정했다. Original cadence에서 불필요하게 반복된 앞선 세 commit이라는 1차 review의 process 관찰은 남지만, executable scope/protected-path 위반은 아니다. History를 rewrite할 이유도 승인도 없다. 최신 `ca037c9...`는 `2d3b500...` correction 결과와 정확한 tip을 기록하기 위한 evidence-only follow-up으로 수용한다.

## scope 결정, 보존, 외부 경계

1. **Index subset: 수용.** 이 packet의 truth-model/terminal index만 포함하고 reputation outbox/snapshot과 scenario-run index는 WO-P6-02/03에 남겼다. M2 preflight는 현재 packet의 신규 8개 index를 전부 다룬다.
2. **Optional gateway terminal capability: 수용.** 이 packet에서 capability가 완전하지 않으면 terminal을 만들지 않는 fail-closed staged wiring이다. Production HTTP wiring은 후속 packet 범위다.
3. **`PAYMENT_ATTEMPT_REJECTED` support: 수용.** Enum/index/reader support만 있으며 scenario writer는 없다.
4. **Sensitive access: 수용.** 민감 원문 access는 201 `POST /purchases/{id}/sensitive/{payloadId}/access`라는 명시적 mutation이다. GET/list/detail/alerts/agents/SSE zero-write regression이 통과했다.

Native Mongo test는 old documents와 legacy Permit2 intent를 backfill 없이 읽고, Permit2 execute/reconcile/terminal 경로가 read-only로 거부됨을 확인했다. Collision preflight는 aggregate-only이며 기존 row를 수정·삭제하지 않는다. Payment-intent `replace_one`은 새로 요청된 active transition transaction 내부의 CAS이지 migration/backfill 경로가 아니다. 기존 event는 append-only다.

Terminal race, 동일 proof idempotency, conflicting proof, attempt uniqueness, cross-purchase transaction reuse, same/wrong-token accounting과 unique index가 memory/native Mongo에서 통과했다. Read projection은 malformed/conflicting source/ref/audit evidence를 fail-closed로 처리한다.

Mongo 종료 직후 한 번의 `pgrep -af 'mongod.*pbl-mongo-test'`가 자기 명령행을 PID `44607`로 일시 matching했으나 즉시 `ps -p 44607`은 exit 1이었다. Self-match를 배제해 다시 실행한 `lsof -nP -iTCP:27019 -sTCP:LISTEN`과 `pgrep -fl '[m]ongod.*pbl-mongo-test'`는 모두 exit 1/출력 없음, `find /private/tmp -maxdepth 1 -name 'pbl-mongo-test.*' -print`는 exit 0/출력 없음이었다. 기존 listener를 종료하지 않았고 임시 process/path가 남지 않았다.

이번 검토에서 public RPC, facilitator, ERC-8004, provider, Atlas, AWS, public chain에 호출하거나 쓰지 않았다. Secret/env/private key/authorization nonce를 읽거나 출력하지 않았다. Commit, merge, push, deploy도 하지 않았다.

APPROVE
