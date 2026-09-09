# WO-P6-03: Safe scenario surfaces — versioned catalog·loopback fakes·격리 Mongo·12-scenario HTTP

- 상태: 대기
- 작성: Planner / 실행: Coder
- relay target: `work-orders/WO-P6-03-safe-scenario-surfaces.md`
- coder worktree: `../PBL-coder` 재사용
- branch gate: `wo/P6-03`
- predecessor: WO-P6-02 Reviewer `APPROVE` 및 Orchestrator 통합 SHA
- successor gate: focused Reviewer `APPROVE` 및 Orchestrator 통합 전 WO-P6-04 시작 금지
- commit message: `test(phase6): add isolated scenario harness`

## 목표

정상 1개와 승인된 비정상 11개를 versioned allow-list catalog로 고정하고, production validation을 먼저 확인한 뒤 dev-only composition/test doubles에서만 비정상 decision/payment evidence를 만든다. 실제 loopback service HTTP를 통해 actual oracle를 수집하며 run-scoped native Mongo와 exact cleanup attestation으로 canonical/historical DB를 구조적으로 보호한다.

이 WO는 `P6-DES-WO-03`을 구현한다. actual browser/process orchestration/root `test:e2e:local`과 최종 manifest/readiness는 WO-P6-04가 소유한다. Dashboard navigation/routes/components는 이 WO에서 수정하지 않는다.

## 승인된 upstream 계약

- Requirements SHA-256: `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f`
- Design SHA-256: `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768`
- Tasks expected SHA-256 after exact relay: `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe`
- Design decisions: `P6-DES-01`, `P6-DES-02`, `P6-DES-03`, `P6-DES-08`, `P6-DES-09`; delivery packet `P6-DES-WO-03`.
- Requirements: `P6-US-01`, `P6-AC-01.1`, `P6-AC-01.2`, `P6-AC-01.3`, `P6-AC-01.4`, `P6-AC-01.5`; `P6-US-02`, `P6-AC-02.1`, `P6-AC-02.2`, `P6-AC-02.3`, `P6-AC-02.4`, `P6-AC-02.5`, `P6-AC-02.6`, `P6-AC-02.7`; `P6-US-07`, `P6-AC-07.1`, `P6-AC-07.2`, `P6-AC-07.3`, `P6-AC-07.4`; service/API foundation for `P6-AC-09.3`, `P6-AC-09.5`; `P6-NFR-SEC-01`, `P6-NFR-SEC-02`, `P6-NFR-SEC-03`, `P6-NFR-SEC-04`, `P6-NFR-AUD-01`, `P6-NFR-AUD-02`, `P6-NFR-AUD-03`, `P6-NFR-IDEM-01`.

## 설계 결정 — 변경 금지

1. scenario harness는 production feature flag가 아니다. `buyer_audit_api.scenarios.main`과 Node `tests/support/scenario-server.ts`만 local-only entrypoint이며 production roots는 scenario module을 import하지 않는다.
2. catalog loader는 `{fixture,injections}`와 `expectedOracle`을 즉시 분리한다. actual-producing API/Seller/Gateway/fakes에는 expected oracle를 전달하지 않고 comparator만 sealed actual과 비교한다.
3. production hard filter/payment/quote/identity/idempotency guard의 거부 또는 정상 계산을 먼저 assert한다. bad buyer decision이나 payment evidence는 그 뒤 attested `SyntheticLifecycleWriter`, decision/explanation decorator, fake facilitator/receipt/ledger에서만 만든다.
4. production request/config/runtime validation은 약화하지 않는다. reserved body fields는 422, reserved env/fake/localtx config는 startup failure다.
5. control API는 random bearer token을 요구하고 `127.0.0.1`에만 bind한다. production FastAPI/OpenAPI/dashboard API client에는 `/__scenario`가 없다.
6. scenario DB는 `pbl_phase6_<executionIdHex>`만 허용하며 regex, deny-list, exact expected name, in-memory attestation, DB sentinel이 모두 일치할 때만 drop한다.
7. `HistorySnapshotPort`는 read-only digest capability이고 `ScenarioRunStore`는 ephemeral write/drop capability다. 같은 client/object/DB로 조합하지 않는다.
8. child process에는 AWS/provider/real wallet/RPC/facilitator/registry credential/URL을 전달하지 않는다. base URL은 loopback만 허용하고 outbound category counters를 분리한다.
9. 모든 synthetic evidence는 `SYNTHETIC_LOCAL + runId + scenarioId + catalogVersion + catalogHash`를 hash-bound payload에 가진다. transaction은 `LOCAL/localtx:` variant이며 EVM hash/BaseScan truth가 아니다.
10. Gemini/Nemotron adapters에는 scenario branch를 추가하지 않는다. 기존 deterministic `MockProviderAdapter`와 같은 provider port를 재사용한다.
11. cleanup 또는 history digest가 실패하면 suite는 non-zero다. history mismatch를 고치기 위해 canonical row를 수정하지 않는다.

## 컨텍스트 — 작업 전 필독

- `AGENTS.md`
- `aidlc-docs/inception/requirements.md` §12.2, `P6-US-01..02`, `P6-US-07`, §12.5–12.7
- `aidlc-docs/inception/design.md` §18.3–18.4, §18.5.1–18.5.3, §18.6, §18.10–18.11, §18.13–18.15, §18.17–18.19
- `aidlc-docs/inception/tasks.md` `P6-03`
- WO-P6-01/02와 Reviewer approvals — truth/reputation ports
- `work-orders/WO-P6-03-safe-scenario-surfaces.md` — 현재 명령서; Coder read-only
- `.agent/{CURRENT_STATE,DECISIONS,HANDOFF}.md`, `.agent/TURN_LOG.md` 최근 항목
- `scripts/validate_schemas.mjs`, `scripts/test_mongo_local.sh`
- `services/buyer-audit-api/src/buyer_audit_api/{composition,main}.py`
- `services/commerce-gateway/src/{main,gateway,contracts}.ts`
- `services/seller-service/src/{main,seller-engine,application,http}.ts`, `src/adapters/mock.ts`
- `apps/dashboard/src/components/Nav.tsx`, `apps/dashboard/src/app/experiments/page.tsx` — read-only 확인만; 수정 금지
- `/Users/vien/.claude/lib/agent-share/templates/work-orders/README.md`

## 시작 전 게이트

1. `../PBL-coder`, branch `wo/P6-03`, clean worktree, predecessor approved integration SHA를 확인한다.
2. upstream hash/marker를 확인하고 base SHA, catalog source 부재, `test:e2e:local` 부재를 기록한다. 기존에 Phase 6 파일이 있거나 설계와 다르면 덮어쓰지 않고 report한다.
3. 실제 `pbl_audit` 및 보존 purchase IDs `451f8657-cbc0-4469-acb6-a7037b4d4865`, `378beb23-e352-49f0-b450-87da88791292`, `14b7dd10-fba1-4ea0-afc9-fb44500d6b4b`를 cleanup/write 대상으로 구성하지 않는다.
4. `mongod`, `mongosh`와 필요한 local ports를 read-only probe한다. 점유 프로세스를 kill하지 않는다.
5. secret/env 파일을 source하지 않는다. real provider/RPC/facilitator/registry/AWS/Atlas URL 또는 key가 필요한 설계라면 즉시 중단한다.

## Allowed writes — 이 목록 밖 수정 금지

### Shared schemas/catalog

- NEW `packages/schemas/common/phase6-scenario-envelope.schema.json`
- NEW `packages/schemas/domains/ai_inference/phase6-scenario.schema.json`
- NEW `tools/phase6-e2e/catalog/phase6.v1.json`
- MODIFY `scripts/validate_schemas.mjs`

### Buyer/Audit scenario-only and production guards

- NEW `services/buyer-audit-api/src/buyer_audit_api/scenarios/__init__.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/scenarios/models.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/scenarios/catalog.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/scenarios/injections.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/scenarios/repository.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/scenarios/mongo_guard.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/scenarios/composition.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/scenarios/control.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/scenarios/main.py`
- NEW `services/buyer-audit-api/src/buyer_audit_api/core/runtime_guard.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/composition.py`
- MODIFY `services/buyer-audit-api/src/buyer_audit_api/main.py`

### Payment Executor/Seller local support and production guards

- NEW `services/commerce-gateway/src/runtime-guard.ts`
- MODIFY `services/commerce-gateway/src/main.ts`
- NEW `services/commerce-gateway/tests/support/local-ledger.ts`
- NEW `services/commerce-gateway/tests/support/fake-boundaries.ts`
- NEW `services/commerce-gateway/tests/support/scenario-server.ts`
- NEW `services/seller-service/src/runtime-guard.ts`
- MODIFY `services/seller-service/src/main.ts`
- NEW `services/seller-service/tests/support/scenario-server.ts`

### Tests

- NEW `services/buyer-audit-api/tests/test_phase6_catalog.py`
- NEW `services/buyer-audit-api/tests/test_phase6_runtime_guard.py`
- NEW `services/buyer-audit-api/tests/test_phase6_mongo_guard.py`
- NEW `services/buyer-audit-api/tests/test_phase6_scenario_http.py`
- MODIFY `services/buyer-audit-api/tests/test_mongo_repository.py`
- NEW `services/commerce-gateway/tests/phase6-scenario-boundaries.test.ts`
- MODIFY `services/commerce-gateway/tests/runtime.test.ts`
- NEW `services/seller-service/tests/phase6-scenario-server.test.ts`
- MODIFY `services/seller-service/tests/runtime.test.ts`

### Handoff evidence

- APPEND ONLY `.agent/TURN_LOG.md`
- NEW `.agent/outbox/WO-P6-03-service-scenarios.json`
- NEW `.agent/outbox/WO-P6-03-coder.done.md` 또는 실패 시 NEW `.agent/outbox/WO-P6-03-blocked.md`

Root package/lock, `tools/phase6-e2e`의 catalog 외 runner files, dashboard, docs, infra, contracts는 이 WO에서 수정하지 않는다.

## Catalog와 oracle — 구현 기준

| ID | production guard 먼저 | scenario-only injection | terminal/rules | ledger/feedback oracle owner |
|---|---|---|---|---|
| `P6-N00-NORMAL` | 모든 hard/binding/exact 검증 통과 | 없음 | `PAYMENT_SETTLED + AUDITED_NORMAL`, finding 0 | ledger `-Q/+Q`, accepted 1, feedback 100×1 |
| `P6-A01-BUDGET-IGNORED` | real claim이 `B<Q` 거부 | attested lifecycle writer가 rejected guard ref 뒤 synthetic settlement/delivery append | `SETTLED+RISK`; `AUD-BUDGET-EXCEEDED`, `AUD-HARD-FILTER-MISMATCH` | ledger `-Q/+Q`, accepted 1, seller feedback 100×1 |
| `P6-A02-BETTER-ELIGIBLE-EXCLUDED` | real selection이 최고 eligible 계산 | decision decorator가 persistence 직전 후보를 부당 excluded로 이동 | `SETTLED+RISK`; `AUD-ELIGIBLE-CANDIDATE-EXCLUDED`, `AUD-HARD-FILTER-MISMATCH` | selected ledger `-Q/+Q`, feedback 100×1 |
| `P6-A03-EXPLANATION-CONTRADICTS-SELECTION` | real winner/score/preset 계산 | explanation decorator만 모순 문구 저장 | `SETTLED+RISK`; `AUD-EXPLANATION-SELECTION-MISMATCH` | ledger `-Q/+Q`, feedback 100×1 |
| `P6-A04-WRONG-AMOUNT` | exact authorization/402 통과 | fake facilitator가 alternate amount transfer journal | `MISMATCH_CONFIRMED+RISK`; quote mismatch fields `["amount"]` | ledger `-A/+A`, accepted 1, retry 0, feedback 0×1 |
| `P6-A05-WRONG-TOKEN` | exact PBLC binding 통과 | fake boundary가 alternate token transfer | `MISMATCH_CONFIRMED+RISK`; fields `["token"]` | PBLC delta 0, alternate `-Q/+Q`, accepted 1, feedback 0×1 |
| `P6-A06-WRONG-RECIPIENT` | recipient binding 통과 | fake boundary가 wrong recipient transfer | `MISMATCH_CONFIRMED+RISK`; fields `["recipient"]` | buyer `-Q`, seller 0, wrong recipient `+Q`, feedback 0×1 |
| `P6-A07-DUPLICATE-OR-REUSED-NONCE` | first execute exact; second execute returns existing result/seller call 0 | captured authorization을 fake replay port에 직접 1회 제출 | `SETTLED+RISK`; duplicate attempt + nonce reuse (+ duplicate event if present) | accepted 1, rejected ≥1, balance `-Q/+Q`, feedback 100 exactly 1 |
| `P6-A08-FACILITATOR-SUCCESS-NO-TRANSFER` | challenge/authorization 검증 통과 | fake success claim/local ID, receipt status 1 but no Transfer/AuthorizationUsed | `RECONCILED_NO_TRANSFER+RISK`; facilitator-no-transfer + reconciled-no-transfer | balance 0, accepted 0, release 1, feedback 0×1 |
| `P6-A09-INCOMPLETE-RECONCILIATION-TERMINAL` | authorization 후 reconciliation 진입 | controller pause 후 frozen clock/finality advance, unused/no-transfer proof | intermediate `CONFIRMATION_UNKNOWN+PENDING_AUDIT`; final `RECONCILED_NO_TRANSFER+RISK` + reconciliation rule | balance 0, accepted 0, release 1, feedback final 0×1; history unchanged |
| `P6-A10-SEMANTIC-WARNING` | deterministic finding 0 | allow-listed semantic caution 1 | `SETTLED+AUDITED_WARNING`; `SEM-REQUEST-RATIONALE-UNCERTAIN`, deterministic risk 0 | ledger `-Q/+Q`, feedback 100×1 |
| `P6-A11-CONFIRMED-PAYMENT-FAILURE` | submitted binding 통과 | fake authoritative receipt status 0 | `PAYMENT_FAILED+AUDITED_RISK`; `AUD-PAYMENT-FAILED` | balance 0, accepted 0, release 1, feedback 0×1 |

각 actual oracle의 소유자는 production core projection/audit, fake ledger/receipt/registry journal이다. Catalog의 expected 값은 comparator 이외 코드에서 접근할 수 없다.

## 구현 단계

1. common/domain JSON schema와 Pydantic models를 `additionalProperties/extra=forbid`로 작성한다. `scenarioId,catalogVersion,seed,frozenClock,injections,expectedOracle`과 각 injection conditional field를 검증한다.
2. `phase6.v1.json`에 위 12개 scenario를 정확히 한 번 정의하고 RFC8785 catalog hash를 계산한다. CLI/API 입력으로 catalog path, URL, DB, code/shell, secret, wallet, expected override를 받지 않는다.
3. deterministic `runId`, random `executionId`, UUIDv5/counter IDs, shared frozen clock/ticks, `localtx:` factory를 구현한다. same tuple repeat 결과와 concurrent execution namespace 분리를 test한다.
4. production roots에 reserved env/body/config fail-closed guard를 composition 전에 적용한다. production import graph와 OpenAPI에 scenario control/injection/fake mode가 없음을 test한다.
5. scenario repository decorator와 attested lifecycle/decision/explanation injection을 구현한다. 모든 write는 append-only이며 scenario metadata가 event hash에 들어간다.
6. 6-decimal per-token/per-wallet local ledger, ERC-3009 nonce set, accepted/rejected attempt journal, fake facilitator, receipt verifier, identity, registry를 구현한다. 이들은 `tests/support`에서 real core ports로 조합되고 public export/Docker entrypoint에 포함되지 않는다.
7. Seller scenario server는 existing `SellerEngine/Application/HttpTransport/MockProviderAdapter`를 seed/clock과 조합한다. Gemini/Nemotron source는 건드리지 않는다.
8. `ScenarioRunStore` attestation/sentinel/exact cleanup과 별도 `HistorySnapshotPort` digest를 구현한다. 모든 deny-list/host/name/sentinel mismatch가 drop 0으로 끝나는지 native Mongo에서 검증한다.
9. random bearer/loopback-only control API `prepare/advance/actual/delete`를 production app과 별개로 구현한다. A09 pause snapshot에서 audit/outbox/feedback 0을 확인한다.
10. actual service-HTTP test가 12 scenarios를 실행해 same purchaseId의 request→candidate→quote→decision→payment/delivery/terminal→audit→feedback evidence를 수집하고 `.agent/outbox/WO-P6-03-service-scenarios.json`에 redacted actual/expected diff, ledger counts, outbound counters, cleanup/history digests를 기록한다.
11. 각 run cleanup과 child teardown을 확인하고 focused suite, TURN_LOG, allowed diff, coherent commit, coder report 순서로 handoff한다.

## Error handling과 설계 차이

- production schema/guard를 약화해야만 scenario가 동작하거나 actual process가 expected oracle를 읽어야 한다면 즉시 중단한다.
- 설계와 다른 DB name/host/capability, 기존 app 안의 experiment route, non-loopback service, real provider requirement가 발견되면 임의 적응하지 않는다.
- cleanup attestation/history digest가 다르면 canonical data를 복구·수정·삭제하지 않는다. scenario-owned process만 안전하게 종료하고 `.agent/outbox/WO-P6-03-blocked.md`에 before/after digest와 exact mismatch를 기록한다.
- blocked report에는 base/predecessor SHA, 파일/심볼, requirement/design ID, outbound/process 상태, 필요한 최소 Planner 결정을 포함한다.

## 검증 명령 — 순서 고정

```bash
npm run test:schemas
uv run --project services/buyer-audit-api ruff check services/buyer-audit-api/src services/buyer-audit-api/tests
uv run --project services/buyer-audit-api mypy services/buyer-audit-api/src
uv run --project services/buyer-audit-api pytest services/buyer-audit-api/tests/test_phase6_catalog.py services/buyer-audit-api/tests/test_phase6_runtime_guard.py services/buyer-audit-api/tests/test_phase6_mongo_guard.py services/buyer-audit-api/tests/test_phase6_scenario_http.py -q
npm run build --workspace @pbl/commerce-gateway
node --test services/commerce-gateway/dist/tests/phase6-scenario-boundaries.test.js services/commerce-gateway/dist/tests/runtime.test.js
npm run build --workspace @pbl/seller-service
node --test services/seller-service/dist/tests/phase6-scenario-server.test.js services/seller-service/dist/tests/runtime.test.js
npm run test:mongo:local
git diff --check
```

commit 후 Reviewer:

```bash
git diff --check "$(git merge-base HEAD main)"..HEAD
git diff --name-only "$(git merge-base HEAD main)"..HEAD
git diff --exit-code "$(git merge-base HEAD main)"..HEAD -- aidlc-docs/inception/requirements.md aidlc-docs/inception/design.md aidlc-docs/inception/tasks.md docs/ERC3009_DEPLOYMENT_GATE.md apps/dashboard package.json package-lock.json infra/contracts infra/aws
```

Reviewer는 service-scenarios JSON schema/12 IDs/zero counters/history equality/cleanup을 실제 출력에서 재계산한다.

## Preservation checks

- [ ] catalog ID가 정상 1+비정상 11이고 duplicate/missing이 없다.
- [ ] same catalog/ID/seed 결과 ID/clock/receipt/rules/balance가 재현된다.
- [ ] production roots가 reserved env/body/fake/localtx를 fail closed하고 scenario import/API가 없다.
- [ ] anomaly는 production guard 결과 이후 test double에서만 생성됐다.
- [ ] run DB regex/deny-list/exact expected/sentinel/attestation mismatch 각각 drop 0이다.
- [ ] scenario cleanup은 현재 execution namespace만 제거했고 temp prefix/process를 exact 확인했다.
- [ ] canonical/historical history before/after count, purchase ID set, event/head/payment digest가 같다.
- [ ] 세 protected purchase ID와 PBLC V2/Permit2 tx evidence 변화가 0이다.
- [ ] real provider/RPC/facilitator/ERC-8004/AWS/Atlas attempted/accepted가 모두 0이다.
- [ ] synthetic event/API/report는 label/metadata를 갖고 localtx가 EVM hash/BaseScan link field에 없다.
- [ ] raw prompt/response, secret, private key, signature, nonce 원문이 report/log에 없다.

## 완료 기준

- [ ] versioned schema/catalog가 12 scenarios와 exact injections/oracles를 검증한다.
- [ ] actual producer와 expected comparator가 process/data dependency로 분리됐다.
- [ ] production validation/idempotency는 그대로이며 negative tests가 이를 입증한다.
- [ ] Buyer/Seller/Gateway 실제 core가 loopback HTTP와 fake ports로 조합됐다.
- [ ] A01–A11 injection placement와 actual oracle owner가 설계 표와 일치한다.
- [ ] service-HTTP 12-scenario evidence가 purchaseId, status, rule, ledger, attempts, feedback을 모두 대조한다.
- [ ] A09 intermediate/final state와 append-only event preservation이 통과한다.
- [ ] native Mongo isolation/cleanup refusal/history pre-post equality가 통과한다.
- [ ] allowed-write 밖 변경이 없고 모든 focused 명령이 exit 0다.
- [ ] coherent commit과 machine-readable/Coder handoff evidence가 있다.

## Evidence contract

`.agent/outbox/WO-P6-03-service-scenarios.json` 필수 필드:

`commit, catalogVersion, catalogHash, runId, executionId, scenarios[12], historyBefore, historyAfter, historyEqual, outboundCounters, cleanup, processTeardown, suiteResults`.

각 scenario는 `id,purchaseId,projection,ruleIds,mismatchedFields,eventChain,balanceDeltas,acceptedTransfers,rejectedTransfers,feedbacks,apiAssertions`를 가진다. raw payload/secret은 금지한다.

`.agent/outbox/WO-P6-03-coder.done.md`에는 base/tip/predecessor SHA, branch/message, changed files, 모든 commands/exit codes, catalog/repeatability, production rejection, HTTP result, cleanup/history/outbound evidence, 미실행 항목과 `READY_FOR_REVIEW`를 기록한다.

Reviewer는 `.agent/outbox/WO-P6-03-review.md`에 independently verified tip과 `APPROVE|REJECT`를 남긴다.

## Commit, reviewer, rollback/handoff gate

1. commit 전 TURN_LOG를 append하고 service JSON을 redaction 검사한다.
2. `wo/P6-03`에만 coherent commit한다. main/push/merge 금지.
3. Reviewer는 실제 service HTTP, production guards, native Mongo deny-list/cleanup/history, outbound counters를 재실행한다.
4. 실패/reject 시 reset/stash/rebase/amend하지 않는다. scenario-owned DB/process만 exact guard로 정리하고 branch를 미통합 상태로 보존한다.
5. historical digest mismatch면 자동 복구가 아니라 hard stop이다. Planner/사용자 승인 없이 history를 reconcile하지 않는다.
6. Orchestrator가 approved tip을 통합하고 clean `wo/P6-04`를 준비한 뒤에만 actual-browser/final gate로 handoff한다.

## 금지 사항

- canonical planning/Work Order/dashboard 수정
- dashboard nav/route/button/client에 scenario control 추가
- production app/module에서 scenario package import 또는 fake mode 허용
- guard/quote/budget/identity/idempotency validation 약화
- expected oracle를 actual producer/fake에 전달
- arbitrary URL/DB/path/code/shell/secret/wallet/expected override 입력
- canonical/Atlas/history DB clone/seed/migrate/update/delete/drop
- deny-list/non-loopback/sentinel mismatch cleanup
- public-chain anomaly, real provider/RPC/facilitator/ERC-8004/AWS/Atlas call
- Gemini/Nemotron adapter에 scenario branch
- fabricated EVM hash/receipt/Transfer/AuthorizationUsed/BaseScan link
- secret/env/private key/raw prompt-response 읽기·출력
- main commit, push, merge, reset, stash, rebase, amend
