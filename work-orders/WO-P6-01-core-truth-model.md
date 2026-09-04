# WO-P6-01: Core truth model — 직교 상태·append-only terminal payment·결정론 감사

- 상태: 대기
- 작성: Planner / 실행: Coder
- relay target: `work-orders/WO-P6-01-core-truth-model.md`
- coder worktree: `../PBL-coder` (네 Phase 6 WO가 순차 재사용)
- branch gate: `wo/P6-01`
- predecessor: relayed Phase 6 requirements/design/tasks
- successor gate: focused Reviewer `APPROVE` 및 Orchestrator 통합 전 WO-P6-02 시작 금지
- commit message: `feat(phase6): implement canonical truth model`

## 목표

승인된 event history를 수정하지 않고 `lifecycleStatus`, `paymentStatus`, `auditStatus`, `evidenceSource`를 직교 projection으로 계산한다. 실제 proof 기반 mismatch/no-transfer terminal을 원자적으로 append하고, 감사 평가와 저장을 분리해 모든 read API가 완전한 no-write가 되게 한다.

이 WO는 `P6-DES-WO-01`만 구현한다. terminal audit→reputation 자동화는 WO-P6-02, scenario harness는 WO-P6-03, dashboard/Playwright/final gate는 WO-P6-04가 소유한다.

## 승인된 upstream 계약

- Requirements SHA-256: `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f`
- Design SHA-256: `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768`
- Tasks expected SHA-256 after exact relay: `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe`
- Design decisions: `P6-DES-04`, `P6-DES-05`, `P6-DES-08`, `P6-DES-09`; delivery packet `P6-DES-WO-01`.
- Requirements: `P6-REQ-STATUS-01`, `P6-REQ-STATUS-02`; `P6-US-03`, `P6-AC-03.1`, `P6-AC-03.2`, `P6-AC-03.3`, `P6-AC-03.4`, `P6-AC-03.5`, `P6-AC-03.6`; `P6-US-04`, `P6-AC-04.1`, `P6-AC-04.2`, `P6-AC-04.3`, `P6-AC-04.4`, `P6-AC-04.5`; `P6-NFR-SEC-02`, `P6-NFR-SEC-03`, `P6-NFR-SEC-04`, `P6-NFR-AUD-01`, `P6-NFR-AUD-02`, `P6-NFR-IDEM-01`.
- Scenario oracle support owned here: A04/A05/A06 mismatch classification, A08/A09 no-transfer classification, A11 confirmed failure, A09 intermediate unknown/pending projection. Scenario injection 자체는 만들지 않는다.

## 설계 결정 — 변경 금지

1. `core/projections.py`는 verified events와 optional payment intent만 받아 I/O 없이 상태를 계산한다. persisted `AUDITED`가 없으면 preview finding이 있어도 `PENDING_AUDIT`다.
2. terminal payment state는 `SETTLED | FAILED | MISMATCH_CONFIRMED | RECONCILED_NO_TRANSFER`이며 서로 배타적이다. `PAYMENT_CONFIRMATION_UNKNOWN`은 terminal이 아니다.
3. 신규 `PAYMENT_RECONCILIATION_CHECKED`, `PAYMENT_MISMATCH_CONFIRMED`, `PAYMENT_RECONCILED_NO_TRANSFER`는 기존 event를 update/delete/backfill하지 않고 append한다.
4. same proof retry는 기존 결과를 반환한다. 같은 purchase의 다른 terminal proof는 `PaymentConflictError`와 HTTP 409이며 두 번째 terminal event나 budget delta를 만들지 않는다.
5. mismatch는 실제 outflow를 기록하며 자동 retry/refund하지 않는다. same-token은 quoted reservation을 actual spend로 한 번 교체하고, wrong-token은 quoted reservation을 release한 뒤 immutable `confirmedOutflows`에 actual token outflow를 기록한다.
6. no-transfer/confirmed failure는 reservation을 정확히 한 번 release한다. 순간적인 receipt/log 부재만으로 no-transfer를 확정하지 않는다.
7. transaction reference는 `EVM`과 `LOCAL` discriminated union이다. `localtx:`는 `transactionHash`가 될 수 없고 EVM regex 값은 `localTransactionId`가 될 수 없다.
8. `AuditEvaluator`는 pure draft를 만들고 persisted audit reader는 parse만 한다. 이 WO에서는 terminal finalize/outbox orchestration을 구현하지 않는다.
9. `AUD-QUOTE-PAYMENT-MISMATCH`는 claimed intent가 아니라 verified actual proof의 amount/token/recipient와 quote를 비교해 exact `mismatchedFields`와 expected/observed/evidenceRefs를 남긴다.
10. 기존 API compatibility fields는 read-time projection에서 유지하고 역사 문서를 수정하지 않는다.

## 컨텍스트 — 작업 전 필독

- `AGENTS.md` — 제품·결제·보안 불변조건
- `aidlc-docs/inception/requirements.md` §12.3–12.7 — 상태·terminal·감사·보존 요구
- `aidlc-docs/inception/design.md` §18.4–18.7, §18.12, §18.15, §18.17–18.19 — exact module/data/index/test 계약
- `aidlc-docs/inception/tasks.md` Phase 6 `P6-01`
- `work-orders/WO-P6-01-core-truth-model.md` — relay된 현재 명령서; Coder는 읽기만 한다
- `.agent/CURRENT_STATE.md`, `.agent/DECISIONS.md`, `.agent/HANDOFF.md`, `.agent/TURN_LOG.md` 최근 항목 — active intent-lock과 history 보호
- `services/buyer-audit-api/src/buyer_audit_api/core/{models,payment,audit,ports}.py`
- `services/buyer-audit-api/src/buyer_audit_api/adapters/repositories/{memory,mongo}.py`
- `services/buyer-audit-api/src/buyer_audit_api/api/{schemas,app}.py`
- `services/commerce-gateway/src/{contracts,gateway}.ts`
- 기존 `test_payment_service.py`, `test_api.py`, `test_mongo_repository.py`, `gateway.test.ts`
- `/Users/vien/.claude/lib/agent-share/templates/work-orders/README.md` — branch/commit/reviewer 운영 규칙; 이 문서의 더 엄격한 제한이 우선

## 시작 전 게이트

1. 현재 경로가 `../PBL-coder`, branch가 정확히 `wo/P6-01`, worktree가 clean인지 확인한다. main에서 작업하지 않는다.
2. upstream 세 hash와 Phase 6 task marker/ID를 확인한다. 하나라도 다르면 작업하지 않고 blocked report를 쓴다.
3. `git rev-parse HEAD`를 `baseSha`로 기록한다. `docs/ERC3009_DEPLOYMENT_GATE.md`와 canonical planning 3파일 SHA-256을 기록한다.
4. `MONGODB_URI`, `TEST_MONGODB_URI`, `RPC_URL`, `FACILITATOR_URL`, AWS/provider/wallet secret env를 source/print하지 않는다. native Mongo test는 `scripts/test_mongo_local.sh`가 만든 loopback replica set만 사용한다.
5. TCP 27019가 이미 사용 중이면 프로세스를 죽이지 말고 blocked report로 반환한다.

## Allowed writes — 이 목록 밖 수정 금지

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
- NEW `.agent/outbox/WO-P6-01-coder.done.md` 또는 실패 시 NEW `.agent/outbox/WO-P6-01-blocked.md`

Planning docs, Work Order 본문, package manifest/lock, dashboard, seller, infra, contracts, deployment scripts는 allowed write가 아니다.

## 구현 단계

1. 새 enum/value object와 transaction union을 먼저 추가한다. legacy parser/response fixture를 깨뜨리지 않는 compatibility test를 먼저 고정한다.
2. `PurchaseProjectionService`를 pure function으로 구현하고 terminal conflict, unknown/pending, persisted-audit-only, evidence source 혼합 오류를 table-driven test로 만든다.
3. payment intent에 reconciliation counters/proof/actual transfer와 신규 terminal state를 additive하게 추가한다. 기존 Permit2 intent는 계속 read-only/fail-closed다.
4. `PAYMENT_RECONCILIATION_CHECKED` append, `confirmMismatch`, `reconcileNoTransfer`를 expected state/head/proof CAS로 구현한다. transient not-found는 unknown으로 남긴다.
5. repository transaction 안에서 payment intent CAS, wallet reservation/spent/release, `confirmedOutflows`, terminal event, evidence head를 원자 처리한다. 설계에 명시된 index 이름과 partial expression을 그대로 사용한다.
6. `AuditFinding`을 `ruleId/rulesetVersion/severity/authority/expected/observed/mismatchedFields/evidenceRefs`로 확장하고 legacy `code` compatibility를 유지한다. `AuditEvaluator`와 persisted reader를 `AuditService.audit()`의 write 동작에서 분리한다.
7. list/detail/alerts/agents/SSE response path를 projection/reader로 전환한다. 읽기 전후 event count/head가 같은 API tests를 추가한다. terminal mutation endpoints만 typed proof와 status code를 소유한다.
8. memory repository로 unit reference semantics를 맞춘 뒤 native Mongo에서 terminal race, attempt uniqueness, partial-index old-row compatibility, exact reservation delta를 검증한다.
9. Payment Executor가 receipt/local-proof classification을 typed result로 Evidence API에 전달하도록 갱신하되, public chain adapter나 실제 URL은 호출하지 않는다.
10. focused 명령을 모두 실행하고 결과를 TURN_LOG에 append한다. allowed-write diff를 확인한 뒤 coherent commit 하나를 만든다. 그 후 actual tip SHA를 coder report에 기록하고 Reviewer에게 넘긴다.

## Error handling과 설계 차이

- 기존 구현이 승인 설계와 달라 allowed-write 밖 파일, 다른 state model, 기존 event mutation, weaker idempotency, 또는 API 의미 변경이 필요하면 **즉시 중단**한다.
- 요구사항을 기존 구현에 맞춰 조용히 낮추거나 test/oracle를 완화하지 않는다.
- `.agent/outbox/WO-P6-01-blocked.md`에 `baseSha`, 관측 파일/심볼/line, 위배되는 requirement/design ID, 시도한 read-only 확인, 보존 상태, 필요한 최소 Planner 결정을 기록한다.
- 증거가 모순되면 정상 상태를 생성하지 말고 typed integrity/conflict error로 fail closed한다.

## 검증 명령 — 순서 고정

```bash
uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests
uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src
uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_projections.py services/buyer-audit-api/tests/test_phase6_audit.py services/buyer-audit-api/tests/test_phase6_payment_terminal.py services/buyer-audit-api/tests/test_payment_service.py services/buyer-audit-api/tests/test_api.py -q
npm run build --workspace @pbl/commerce-gateway
node --test services/commerce-gateway/dist/tests/gateway.test.js services/commerce-gateway/dist/tests/phase6-truth-model.test.js
npm run test:mongo:local
git diff --check
```

commit 후 Reviewer가 같은 SHA에서 다음을 재실행한다.

```bash
git diff --check "$(git merge-base HEAD main)"..HEAD
git diff --name-only "$(git merge-base HEAD main)"..HEAD
git diff --exit-code "$(git merge-base HEAD main)"..HEAD -- aidlc-docs/inception/requirements.md aidlc-docs/inception/design.md aidlc-docs/inception/tasks.md docs/ERC3009_DEPLOYMENT_GATE.md infra/contracts infra/aws
```

## Preservation checks

- [ ] 기존 successful/failed/incomplete fixture는 byte mutation/backfill 없이 parse된다.
- [ ] historical Permit2 intent execute/reconcile rejection이 유지된다.
- [ ] canonical history DB는 이 WO에서 연결·복제·cleanup하지 않았다.
- [ ] `docs/ERC3009_DEPLOYMENT_GATE.md`, canonical planning, `infra/contracts`, `infra/aws` diff가 0이다.
- [ ] public RPC/facilitator/ERC-8004/provider/AWS command와 write가 0이다.
- [ ] native Mongo temp DB/process는 test script가 exact temp path만 정리했고 기존 listener를 종료하지 않았다.
- [ ] raw prompt/response, signature, authorization nonce, secret 값이 source/test output/report에 없다.

## 완료 기준

- [ ] status projection의 일곱 비혼동 상태와 three-source semantics가 단위/API test로 고정됐다.
- [ ] local/EVM transaction union이 type confusion을 거부하고 synthetic는 legacy transaction hash를 만들지 않는다.
- [ ] mismatch/no-transfer/failed/settled terminal이 race에서도 하나뿐이며 same/conflicting proof semantics가 맞다.
- [ ] reservation/spent/confirmedOutflows가 amount/token/recipient mismatch와 no-transfer에서 정확히 한 번 반영된다.
- [ ] structured audit 규칙과 legacy finding parser가 모두 통과한다.
- [ ] 모든 GET/read path는 persisted evidence만 반환하고 event/head를 바꾸지 않는다.
- [ ] native Mongo indexes/CAS/concurrency와 old-document compatibility가 통과한다.
- [ ] allowed-write 목록 밖 tracked/untracked 변경이 없다.
- [ ] focused suite 전부 exit 0, coherent commit과 Coder handoff evidence가 있다.

## Coder evidence contract

`.agent/outbox/WO-P6-01-coder.done.md`에는 다음을 빠짐없이 기록한다.

- base SHA, tip SHA, branch, commit message
- changed files 전체
- 위 검증 명령 전체와 exit code/핵심 count
- terminal race/same proof/conflicting proof/GET zero-write/native Mongo 결과
- protected file before/after hashes와 diff 결과
- 실행하지 않은 명령 및 이유
- background process/port teardown 결과
- `READY_FOR_REVIEW`

이 파일은 구현 handoff report이며 planning revision이 아니다. Reviewer는 `.agent/outbox/WO-P6-01-review.md`에 독립 재검증 SHA와 `APPROVE|REJECT`를 기록한다.

## Commit, reviewer, rollback/handoff gate

1. commit 전 TURN_LOG에 완료 header, intent, files, 모든 commands/results, decisions, handoff를 append한다. 기존 log를 rewrite하지 않는다.
2. commit은 branch `wo/P6-01`에만 만들고 main/push는 금지한다. unrelated formatting/refactor를 섞지 않는다.
3. Reviewer는 exact tip과 allowed-write 범위, focused suites, native Mongo, protected diffs를 직접 확인한다. self-report만으로 승인하지 않는다.
4. `REJECT` 또는 blocker면 branch/worktree/history를 reset/stash/rebase/amend하지 않는다. 현 상태와 evidence를 보존하고, 승인된 acceptance 안의 correction만 별도 지시 후 수행한다.
5. rollback은 데이터 삭제가 아니라 branch를 미통합 상태로 유지하는 것이다. Orchestrator만 승인 tip을 main에 통합한다.
6. 승인·통합 뒤 Orchestrator가 같은 coder worktree를 clean `wo/P6-02`로 준비할 때만 다음 WO로 handoff한다.

## 금지 사항

- canonical requirements/design/tasks 또는 Work Order 수정
- 기존 Mongo row/event/payment intent/audit/feedback update, delete, backfill, migration
- production `pbl_audit` 또는 Atlas 접속·snapshot·cleanup
- Permit2 실행 복원 또는 public-chain anomaly/transaction
- real RPC/facilitator/ERC-8004/provider 호출
- AWS/Atlas/Terraform mutation, `terraform apply/import/destroy`
- dashboard/scenario/reputation-outbox 구현
- test skip, assertion 약화, fabricated receipt/hash/Transfer/AuthorizationUsed
- secret/env/private key 읽기·출력·커밋
- main commit, push, merge, reset, stash, rebase, amend
