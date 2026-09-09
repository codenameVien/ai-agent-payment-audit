# WO-P6-02: Reputation loop — terminal audit 기반 durable ERC-8004 자동화와 선택 provenance

- 상태: 대기
- 작성: Planner / 실행: Coder
- relay target: `work-orders/WO-P6-02-reputation-loop.md`
- coder worktree: `../PBL-coder` 재사용
- branch gate: `wo/P6-02`
- predecessor: WO-P6-01 Reviewer `APPROVE` 및 Orchestrator 통합 SHA
- successor gate: focused Reviewer `APPROVE` 및 Orchestrator 통합 전 WO-P6-03 시작 금지
- commit message: `feat(phase6): automate reputation loop`

## 목표

persisted terminal `AUDITED`에서 seller-attributed 100/0 또는 DEFER를 결정하고, Mongo transaction + durable outbox + lease/prepared/recovery로 한 publish identity의 외부 효과를 최대 한 번으로 제한한다. ERC-8004 query provenance를 immutable snapshot으로 저장하고, provider-level reputation을 hard filter가 끝난 후보의 기존 10% score component에만 반영한다.

이 WO는 `P6-DES-WO-02`를 구현한다. scenario control/fake service surface는 WO-P6-03, dashboard/actual-browser/root E2E는 WO-P6-04에서 완성한다. 실제 ERC-8004 write나 live-success 증명은 이 WO의 목표가 아니다.

## 승인된 upstream 계약

- Requirements SHA-256: `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f`
- Design SHA-256: `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768`
- Tasks expected SHA-256 after exact relay: `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe`
- Design decisions: `P6-DES-06`, `P6-DES-07`, `P6-DES-08`; delivery packet `P6-DES-WO-02`.
- Requirements: terminal orchestration portion of `P6-AC-03.3`, `P6-AC-03.5`; `P6-US-05`, `P6-AC-05.1`, `P6-AC-05.2`, `P6-AC-05.3`, `P6-AC-05.4`, `P6-AC-05.5`, `P6-AC-05.6`, `P6-AC-05.7`; `P6-US-06`, `P6-AC-06.1`, `P6-AC-06.2`, `P6-AC-06.3`, `P6-AC-06.4`, `P6-AC-06.5`, `P6-AC-06.6`; `P6-NFR-SEC-02`, `P6-NFR-SEC-04`, `P6-NFR-AUD-01`, `P6-NFR-AUD-02`, `P6-NFR-AUD-03`, `P6-NFR-IDEM-01`.
- Final E2E obligations prepared here: concurrent/retry/restart feedback count 1, sequential score influence, hard-ineligible reputation-100 rejection. Service/browser proof는 후속 WO가 소유한다.

## 설계 결정 — 변경 금지

1. feedback decision 입력은 persisted terminal `AUDITED`와 같은 audit bundle/ruleset에 묶인 payment/delivery/seller identity뿐이다. preview와 `PAYMENT_CONFIRMATION_UNKNOWN`은 job을 만들지 않는다.
2. provisional policy는 요구사항 그대로다: buyer selection/explanation-only risk라도 seller가 quote대로 전달하면 `PUBLISH(100)`; confirmed mismatch/no-transfer/seller-attributed confirmed failure/delivery integrity failure는 `PUBLISH(0)`; semantic-only는 감점하지 않으며 attribution/identity/proof 불충분은 `DEFER`.
3. publish identity는 `(chainId, registryAddress, purchaseId, sellerAgentId, tag1, tag2)`다. identity hash unique와 immutable payload fingerprint를 모두 검증한다.
4. `AUDITED + REPUTATION_DECIDED + outbox/deferred state`는 가능한 한 같은 Mongo transaction/CAS에서 만들어진다. race/restart가 decision이나 job을 중복 생성하지 않는다.
5. outbox state는 `PENDING|LEASED|PREPARED|SUBMITTED_UNKNOWN|CONFIRMED|DEFERRED|CONFLICT`다. lease는 operational mutable state지만 decision/conflict/confirmed evidence는 append-only event다.
6. publish 전에 matching trusted feedback을 먼저 조회한다. PREPARED 이후에는 새 nonce/payload를 만들지 않고 동일 prepared transaction을 조회 또는 재방송한다. corroboration 없는 두 번째 transaction은 금지한다.
7. `REPUTATION_RECORDED`는 confirmed receipt와 matching feedback event 뒤에만 append한다. tx-shaped 문자열만으로 성공 처리하지 않는다.
8. LOCAL test transaction은 `LOCAL/localtx:` variant다. production root의 writer 기본은 `disabled`; `fake`는 production 설정으로 허용하지 않는다.
9. snapshot은 raw values, decimals, trusted clients, tags, from/to block, query time, freshness, aggregation version/hash를 포함한다. matching evidence가 없으면 `50 + NO_EVIDENCE`다.
10. 동일 seller agent의 여러 model은 provider-level snapshot을 공유한다. model benchmark와 agent reputation은 별도 필드다.
11. reputation은 budget/capability/availability/identity/quote validity hard filter 뒤에만 score에 들어간다. reputation 100도 hard-ineligible 후보를 복원하지 못한다.
12. `REPUTATION_DECIDED`와 `REPUTATION_PUBLICATION_CONFLICT`는 payment state transition이 아니라 **검증된 terminal payment 뒤의 post-payment auxiliary event**로만 payment view가 수용한다. 전자는 terminal `AUDITED` 뒤 singleton이고, 후자는 해당 decision/outbox identity 뒤 반복 가능한 append-only conflict다. terminal 전, audit/decision 전, 또는 다른 불법 순서에서는 기존 `PaymentEvidenceError` fail-closed를 유지한다.

## 컨텍스트 — 작업 전 필독

- `AGENTS.md`
- `aidlc-docs/inception/requirements.md` §12.4 `P6-US-05..06`, §12.7
- `aidlc-docs/inception/design.md` §18.4.2–18.4.3, §18.5.3–18.5.4, §18.6.4, §18.8–18.9, §18.12.2, §18.14–18.19
- `aidlc-docs/inception/tasks.md` `P6-02`
- `work-orders/WO-P6-01-core-truth-model.md`과 Reviewer approval — 제공된 API/state 계약
- `work-orders/WO-P6-02-reputation-loop.md` — 현재 명령서; Coder read-only
- `.agent/{CURRENT_STATE,DECISIONS,HANDOFF}.md`, `.agent/TURN_LOG.md` 최근 항목
- `services/buyer-audit-api/src/buyer_audit_api/core/{audit,payment,ports}.py`
- `services/buyer-audit-api/src/buyer_audit_api/domains/ai_inference/{models,ports,workflow,selection}.py`
- `services/commerce-gateway/src/{contracts,erc8004,main}.ts`, `src/adapters/{http,viem-erc8004}.ts`
- 기존 `test_ai_inference_workflow.py`, `test_ai_inference_domain.py`, `test_api.py`, `test_mongo_repository.py`, `erc8004.test.ts`, `runtime.test.ts`
- `/Users/vien/.claude/lib/agent-share/templates/work-orders/README.md`

## 시작 전 게이트

1. `../PBL-coder`, branch `wo/P6-02`, clean worktree인지 확인한다.
2. upstream hash와 Phase 6 task marker를 확인하고, `git log`에서 Orchestrator가 지정한 WO-P6-01 approved integration SHA를 base에 포함하는지 확인한다.
3. `git rev-parse HEAD`를 `baseSha`로 기록하고 predecessor Reviewer artifact의 verdict/tip이 정확한지 읽는다. 불일치하면 시작하지 않는다.
4. live write mode, real RPC, real ERC-8004 registry, wallet private key, provider/AWS credential을 source/print하지 않는다. 테스트는 in-memory fake와 native loopback Mongo만 사용한다.
5. TCP 27019가 이미 사용 중이면 기존 프로세스를 죽이지 않고 blocked report로 반환한다.

## Allowed writes — 이 목록 밖 수정 금지

### Buyer/Audit source

- NEW `services/buyer-audit-api/src/buyer_audit_api/core/reputation.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/core/terminal.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/core/models.py` — `EventType`에 `REPUTATION_DECIDED = "REPUTATION_DECIDED"`와 `REPUTATION_PUBLICATION_CONFLICT = "REPUTATION_PUBLICATION_CONFLICT"` 두 멤버만 추가
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/core/payment.py` — 두 reputation event를 검증된 terminal payment 뒤의 post-payment auxiliary로만 해석하도록 `load_payment_view` ordering validation 확장; payment state/accounting/authorization semantics 변경 금지
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/core/ports.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/memory.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/mongo.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/adapters/reputation_gateway.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/domains/ai_inference/models.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/domains/ai_inference/ports.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/domains/ai_inference/workflow.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/domains/ai_inference/selection.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/api/schemas.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/api/app.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/composition.py`

### Payment Executor source

- MODIFY `services/commerce-gateway/src/contracts.ts`
- MODIFY `services/commerce-gateway/src/erc8004.ts`
- MODIFY `services/commerce-gateway/src/adapters/http.ts`
- MODIFY `services/commerce-gateway/src/adapters/viem-erc8004.ts`
- MODIFY `services/commerce-gateway/src/main.ts`

### Tests

- NEW `services/buyer-audit-api/tests/test_phase6_reputation.py`
- NEW `services/buyer-audit-api/tests/test_phase6_terminal.py`
- MODIFY `services/buyer-audit-api/tests/test_payment_service.py`
- MODIFY `services/buyer-audit-api/tests/test_ai_inference_workflow.py`
- MODIFY `services/buyer-audit-api/tests/test_ai_inference_domain.py`
- MODIFY `services/buyer-audit-api/tests/test_api.py`
- MODIFY `services/buyer-audit-api/tests/test_mongo_repository.py`
- NEW `services/commerce-gateway/tests/phase6-reputation-loop.test.ts`
- MODIFY `services/commerce-gateway/tests/erc8004.test.ts`
- MODIFY `services/commerce-gateway/tests/runtime.test.ts`

### Handoff evidence

- APPEND ONLY `.agent/TURN_LOG.md`
- NEW `.agent/outbox/WO-P6-02-coder.done.md` 또는 실패 시 NEW `.agent/outbox/WO-P6-02-blocked.md`

WO-P6-01 implementation은 compatibility가 필요한 최소 수정도 이 allowed-write 안에서만 허용한다. WO-P6-01 acceptance를 약화하는 변경은 금지한다.

## 구현 단계

1. `ReputationDecisionPolicy`, publish identity/hash/fingerprint, typed `Publish|Defer`, snapshot/aggregation value objects를 pure core로 작성한다. 요구사항의 100/0/DEFER table을 unit test로 먼저 고정한다.
2. `TerminalAuditCoordinator.finalizeIfEligible(purchaseId)`가 terminal evidence/head를 재검증하고 persisted audit을 finalize한 뒤 같은 transaction/CAS에서 `REPUTATION_DECIDED`와 unique outbox 또는 DEFER를 만든다. recovery sweep은 terminal evidence가 있으나 audit/decision이 없는 항목만 대상으로 한다. 이 append 뒤 `load_payment_view`와 기존-intent `claim`이 동일 payment binding을 계속 읽되, reputation event를 terminal 전이나 audit/decision 전에서 전역 무시하지 않도록 ordering validation을 함께 확장한다.
3. Mongo outbox에 exact index 이름, unique identity, lease CAS, immutable fingerprint, conflict append, transaction variant partial unique를 구현한다. memory adapter도 동일 observable semantics를 갖게 한다.
4. Evidence API에 finalize/claim/get/prepared/submitted-unknown/confirmed typed endpoints를 추가한다. malformed=422, stale head/lease/conflict=409, missing=404, disabled=503, same-proof retry=200 semantics를 test한다.
5. Payment Executor publisher는 immutable job/audit evidence를 다시 읽고 matching feedback을 조회한다. already-found recovery, PREPARED crash, SUBMITTED_UNKNOWN lookup/rebroadcast, receipt+event confirmation을 구현하되 unit fake 외 실제 broadcast는 하지 않는다.
6. `REPUTATION_RECORDED`에 registry/chain/block/log/client/agent/tags/value/decimals/URI/hash/transaction/proof/identity를 보존한다. tx hash만 있거나 identity/tags가 다르면 confirm을 거부한다.
7. reputation query adapter가 trusted client allow-list, matching tags, block range, raw events와 provenance를 반환하고 aggregation이 decimals를 정규화해 mean 또는 neutral 50을 만든다.
8. AI workflow가 quote identity 확인 뒤 provider-level snapshot을 저장/참조하고, selection은 hard filter 뒤 snapshot score를 기존 reputation component에만 사용한다. legacy benchmark reputation은 역사 read 전용으로 남긴다.
9. two-worker finalize/outbox, concurrent publish, restart/recovery, conflicting fingerprint, multiple-model shared snapshot, sequential score influence, reputation-100 hard-ineligible rejection을 Python/Node/native Mongo로 검증한다.
10. focused 명령을 모두 실행하고 TURN_LOG, allowed-write diff, coherent commit, coder report 순서로 handoff한다.

## Error handling과 설계 차이

- allowed-write 밖 설정/module, live writer 활성화, different publish identity, mutable audit rewrite, hard-filter 순서 변경이 필요해 보이면 구현을 멈춘다.
- 기존 ERC-8004 implementation 편의 때문에 durable idempotency를 in-process lock으로 대체하거나 receipt 없는 tx hash를 성공으로 처리하지 않는다.
- `.agent/outbox/WO-P6-02-blocked.md`에 base SHA, predecessor SHA/verdict, 관측 code path, requirement/design ID, 필요한 최소 Planner 결정을 기록한다.
- identity/fingerprint/proof 충돌은 재제출이 아니라 `CONFLICT` + append-only evidence로 fail closed한다.

## 검증 명령 — 순서 고정

```bash
uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests
uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src
uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_reputation.py services/buyer-audit-api/tests/test_phase6_terminal.py services/buyer-audit-api/tests/test_payment_service.py services/buyer-audit-api/tests/test_ai_inference_workflow.py services/buyer-audit-api/tests/test_ai_inference_domain.py services/buyer-audit-api/tests/test_api.py -q
npm run build --workspace @pbl/commerce-gateway
node --test services/commerce-gateway/dist/tests/erc8004.test.js services/commerce-gateway/dist/tests/phase6-reputation-loop.test.js services/commerce-gateway/dist/tests/runtime.test.js
npm run test:mongo:local
git diff --check
```

commit 후 Reviewer:

```bash
git diff --check "$(git merge-base HEAD main)"..HEAD
git diff --name-only "$(git merge-base HEAD main)"..HEAD
git diff --exit-code "$(git merge-base HEAD main)"..HEAD -- aidlc-docs/inception/requirements.md aidlc-docs/inception/design.md aidlc-docs/inception/tasks.md docs/ERC3009_DEPLOYMENT_GATE.md infra/contracts infra/aws apps/dashboard tools
```

## Preservation checks

- [ ] WO-P6-01의 state/projection/payment/audit tests가 regression 없이 유지된다.
- [ ] 기존 `REPUTATION_RECORDED`와 historical on-chain feedback parser는 backfill 없이 읽힌다.
- [ ] live publisher default는 disabled이고 production composition에 fake registry/write mode가 없다.
- [ ] actual ERC-8004/RPC/provider/facilitator/AWS calls와 public-chain writes가 0이다.
- [ ] buyer/seller feedback identity, tags, chain, registry, bundle binding negative tests가 통과한다.
- [ ] hard-ineligible candidate는 reputation 100이어도 복원되지 않는다.
- [ ] finalize, confirmed publication, publication conflict 뒤에도 payment view/기존-intent claim은 같은 terminal payment binding과 accounting을 반환하고 새 claim/reservation/spend/event를 만들지 않는다.
- [ ] reputation event의 terminal 전 배치, `REPUTATION_DECIDED`의 `AUDITED` 전·중복 배치, conflict의 decision/outbox identity 전 배치는 계속 fail closed하며 payment intent나 wallet policy를 변경하지 않는다.
- [ ] canonical history DB와 live/historical documents를 연결·수정·cleanup하지 않았다.
- [ ] raw feedback calldata/signature/private key/secret/raw prompt-response가 report/log에 없다.

## 완료 기준

- [ ] persisted terminal audit만 deterministic decision/outbox를 만든다; unknown/preview는 0 job이다.
- [ ] decision, outbox, lease, prepared, submitted-unknown, confirmed, deferred, conflict state가 typed/복구 가능하다.
- [ ] process restart, worker race, re-audit에서 같은 publish identity의 accepted fake effect와 `REPUTATION_RECORDED`가 각각 최대 1개다.
- [ ] different fingerprint는 new transaction 없이 conflict evidence를 만든다.
- [ ] receipt와 matching feedback event 전에는 confirmed/recorded가 되지 않는다.
- [ ] snapshot provenance/aggregation/no-evidence/freshness가 immutable evidence로 저장·조회된다.
- [ ] provider-level snapshot 공유, hard-filter precedence, sequential score influence가 검증된다.
- [ ] `load_payment_view`와 `claim`이 (a) finalize 직후, (b) confirmed `REPUTATION_RECORDED` 직후, (c) 한 개 이상 append-only `REPUTATION_PUBLICATION_CONFLICT` 직후 각각 성공하고 기존 payment intent를 그대로 반환하며, 대응하는 illegal-order negative cases는 `PaymentEvidenceError`로 실패한다.
- [ ] allowed-write 밖 변경이 없고 focused suite/native Mongo가 모두 exit 0다.
- [ ] coherent commit과 Coder handoff evidence가 있다.

## Coder evidence contract

`.agent/outbox/WO-P6-02-coder.done.md` 필수 내용:

- base/tip SHA, branch, predecessor approved SHA, commit message
- changed files
- 모든 명령/exit code/test count
- decision mapping table 결과
- two-worker/restart/PREPARED/SUBMITTED_UNKNOWN/conflict/confirmed-only 결과
- snapshot provenance/mean-neutral/hard-filter/sequential-selection 결과
- finalize/confirmed/conflict 각각의 payment view/claim 회귀와 pre-terminal/decision-before-audit/conflict-before-decision illegal-order negative 결과
- live/outbound/write 0 근거와 protected diff
- 미실행 항목/이유, process teardown
- `READY_FOR_REVIEW`

Reviewer는 exact tip에서 독립 재실행 후 `.agent/outbox/WO-P6-02-review.md`에 `APPROVE|REJECT`, verified SHA, command 결과를 남긴다.

## Commit, reviewer, rollback/handoff gate

1. commit 전 append-only TURN_LOG에 완료 header와 모든 commands/results를 기록한다.
2. `wo/P6-02`에만 coherent commit한다. main/push/merge 금지.
3. Reviewer는 changed-file allow-list, atomic Mongo behavior, recovery, provider selection regression, no-live-write를 독립 확인한다.
4. 실패/reject 시 reset/stash/rebase/amend하지 않는다. branch를 미통합 상태로 보존하고 bounded correction 또는 Planner 회송을 기다린다.
5. rollback은 database/event 삭제가 아니라 branch 미통합이다. 운영 outbox/history에 cleanup을 수행하지 않는다.
6. Orchestrator가 Reviewer-approved tip을 통합하고 coder worktree를 clean `wo/P6-03`로 준비한 뒤에만 다음 WO를 시작한다.

## 금지 사항

- canonical planning/Work Order 수정
- 중앙 `EventType` 밖의 별도 parallel event enum 추가 또는 `REPUTATION_DECIDED`/`REPUTATION_PUBLICATION_CONFLICT` 대신 기존 event type을 overload
- 두 reputation event를 unconditional/global auxiliary set에서 순서 검증 전에 제거하거나, terminal·audit·decision prerequisite 없이 무시
- live ERC-8004 feedback/query 성공 주장 또는 transaction 제출
- 실제 RPC/provider/facilitator/AWS/Atlas 접속
- production root의 fake mode/injection 허용
- tx hash만으로 `REPUTATION_RECORDED` append
- in-process mutex만으로 durable exactly-once 주장
- reputation으로 hard constraint 우회 또는 model-level identity 날조
- 기존 audit/reputation/payment/history mutation/backfill/delete
- scenario controller/catalog/dashboard/Playwright/AWS readiness 구현
- secret/env/private key/raw prompt-response 읽기·출력
- main commit, push, merge, reset, stash, rebase, amend
