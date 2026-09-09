# WO-P6-04: Read-only UI and final gate — actual browser LOCAL E2E·문서·AWS 직전 정지

- 상태: 대기
- 작성: Planner / 실행: Coder
- relay target: `work-orders/WO-P6-04-read-only-ui-final-gate.md`
- coder worktree: `../PBL-coder` 재사용
- branch gate: `wo/P6-04`
- predecessor: WO-P6-03 Reviewer `APPROVE` 및 Orchestrator 통합 SHA
- terminal gate: focused Reviewer + final broad matrix `APPROVE` 뒤 `AWAITING_AWS_DEPLOYMENT_APPROVAL`; 다음 implementation WO 없음
- primary commit message: `feat(phase6): complete read-only local e2e gate`
- exact-evidence follow-up 형식(필요한 경우만): `docs(phase6): record local e2e readiness`

## 목표

Phase 6 canonical projection, structured audit, reputation provenance를 기존 read-only dashboard 전 화면에 일관되게 표시한다. root `npm run test:e2e:local` 한 명령이 native isolated Mongo, 실제 loopback Buyer/Seller/Gateway/Next.js services, 실제 SIWE, Playwright Chromium을 통해 정상 1+비정상 11 scenarios를 검증하고, outbound/history/cleanup/process evidence를 machine-readable manifest로 남긴다. 구현·운영·architecture 문서와 AWS deploy-readiness를 그 증거에 맞춘 뒤 실제 AWS mutation 직전에서 정확히 멈춘다.

이 WO는 `P6-DES-WO-04`를 구현하고 Phase 6 local scope를 닫는다. live provider, Base Sepolia/ERC-8004 write, AWS/Atlas 배포, retention 최종 결정은 외부 gate다.

## 승인된 upstream 계약

- Requirements SHA-256: `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f`
- Design SHA-256: `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768`
- Tasks expected SHA-256 after exact relay: `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe`
- Design decisions: `P6-DES-08`, `P6-DES-10`, `P6-DES-11`; delivery packet `P6-DES-WO-04`; WO-P6-01..03의 모든 승인 decision.
- Requirements: final UI/E2E evidence portion of `P6-AC-02.6`, `P6-AC-02.7`, `P6-AC-03.4`, `P6-AC-04.5`, `P6-AC-05.6`, `P6-AC-05.7`, `P6-AC-06.2`, `P6-AC-06.5`, `P6-AC-07.4`; `P6-US-08`, `P6-AC-08.1`, `P6-AC-08.2`, `P6-AC-08.3`, `P6-AC-08.4`, `P6-AC-08.5`; `P6-US-09`, `P6-AC-09.1`, `P6-AC-09.2`, `P6-AC-09.3`, `P6-AC-09.4`, `P6-AC-09.5`, `P6-AC-09.6`; `P6-US-10`, `P6-AC-10.1`, `P6-AC-10.2`, `P6-AC-10.3`, `P6-AC-10.4`, `P6-AC-10.5`, `P6-AC-10.6`; §12.5의 12 scenario oracle; `P6-NFR-SEC-01`, `P6-NFR-SEC-02`, `P6-NFR-SEC-03`, `P6-NFR-SEC-04`, `P6-NFR-AUD-01`, `P6-NFR-AUD-02`, `P6-NFR-AUD-03`, `P6-NFR-IDEM-01`, `P6-NFR-PRIV-01`.

## 설계 결정 — 변경 금지

1. Dashboard의 `/`, `/dashboard`, `/purchases`, `/purchases/[purchaseId]`, `/alerts`, `/agents`는 GET/SSE persisted evidence만 읽는다. scenario/audit/reconciliation/feedback start/stop/retry/cleanup/publish control을 추가하지 않는다.
2. `/experiments`는 데이터 mutation 없이 `/request` redirect를 유지한다. Nav에는 overview/purchases/agents/alerts만 있고 scenario runner entry가 없다.
3. `StatusTriplet`은 lifecycle/payment/audit를 별도 badge로 표시한다. pending audit, audited normal/warning/risk, payment failed, reconciled-no-transfer, confirmation-unknown을 합치지 않는다.
4. synthetic surface마다 정확한 문구 `Synthetic local scenario — not an on-chain transaction`을 표시한다. `TransactionReference`만 explorer link를 만들며 `kind=EVM + chainId=84532 + verified/history source + 0x64-byte hash`일 때만 허용한다.
5. synthetic/live/history aggregate와 filter는 분리한다. `localtx:`는 text만 표시하고 legacy `transaction_hash`는 null이다.
6. Phase 6 E2E의 유일한 user-invoked runner는 private `tools/phase6-e2e` workspace와 root `npm run test:e2e:local`이다. Dashboard/user request API에는 runner가 없다.
7. Playwright Chromium single worker/retry 0을 사용한다. source regex/unit tests는 actual browser/API E2E를 대체하지 않는다.
8. port block은 deterministic allocation+loopback bind probe, 최대 32회 이동을 사용한다. parallel Phase 6 run은 거부한다.
9. process manager는 process group을 추적해 SIGTERM→bounded poll→남은 child만 SIGKILL 순서로 종료한다. 기존 pbl-stack/점유 프로세스를 임의 kill하지 않는다.
10. E2E child env에는 AWS/provider/production wallet/real RPC/facilitator/registry/Atlas secret이 없다. non-loopback attempt는 차단되고 provider/RPC/facilitator/ERC-8004/AWS counters는 모두 0이어야 한다.
11. history snapshot은 read-only capability로 before/after exact digest를 비교한다. scenario DB/temp만 WO-P6-03 exact guard로 cleanup한다.
12. `scripts/generate_aws_deploy_readiness.mjs`는 local files만 읽는 pure renderer다. AWS SDK/CLI/Terraform execution capability가 없으며 `enable_services=false`도 apply 권한이 아니다.
13. public AWS sensitive-payload retention은 unresolved `BLOCKED`다. 모든 local gate 뒤 자동화는 `AWAITING_AWS_DEPLOYMENT_APPROVAL`을 기록하고 종료한다.

## 컨텍스트 — 작업 전 필독

- `AGENTS.md`
- `aidlc-docs/inception/requirements.md` `P6-US-08..10`, §12.5–12.10
- `aidlc-docs/inception/design.md` §18.4.4, §18.5.2, §18.10, §18.12–18.19
- `aidlc-docs/inception/tasks.md` `P6-04`와 final broad matrix
- WO-P6-01..03 및 세 Reviewer approval — 구현된 backend/scenario contracts
- `work-orders/WO-P6-04-read-only-ui-final-gate.md` — 현재 명령서; Coder read-only
- `.agent/{CURRENT_STATE,DECISIONS,HANDOFF}.md`, `.agent/TURN_LOG.md` 최근 항목
- `apps/dashboard/src/lib/{types,api}.ts`
- `apps/dashboard/src/components/{Nav,status,overview,purchase-list,purchase-detail,alerts,agents}.tsx`
- `apps/dashboard/tests/request-boundary.test.mjs`
- root/dashboard/service package manifests와 tsconfig
- `scripts/test_mongo_local.sh`, `infra/docker/compose.yml`, `infra/aws/terraform/*.tf` (read-only)
- `README.md`, `README.en.md`, `docs/{HANDOFF,ROADMAP,ERC3009_DEPLOYMENT_GATE}.md`
- `/Users/vien/.claude/lib/agent-share/templates/work-orders/README.md`

## 시작 전 게이트

1. `../PBL-coder`, branch `wo/P6-04`, clean worktree, predecessor three approvals/integrated SHA를 확인한다.
2. upstream hashes/markers와 base SHA를 기록한다. WO-P6-03 service manifest가 12 IDs, historyEqual, outbound-zero, cleanup pass인지 독립 확인한다.
3. `mongod`, `mongosh`, Node 20+, uv, Chromium availability를 확인한다. Chromium이 없으면 skip/substitute하지 않는다. approved dev dependency 설치가 필요한 경우 `npm install --include=dev`와 `npx playwright install chromium`만 사용하고 결과를 기록한다.
4. 실행 전 history digest를 얻을 수 있는 approved loopback read-only source가 없거나 required port/process를 안전하게 격리할 수 없으면 기존 프로세스를 kill하거나 live Atlas로 대체하지 말고 blocked report를 쓴다.
5. AWS/provider/wallet/real RPC/facilitator/registry/Atlas credential/env 값을 source/print하지 않는다. production secret을 child env에서 명시적으로 제거한다.

## Allowed writes — 이 목록 밖 수정 금지

### Dashboard

- MODIFY `apps/dashboard/src/lib/types.ts`
- MODIFY `apps/dashboard/src/lib/api.ts`
- MODIFY `apps/dashboard/src/components/status.tsx`
- NEW `apps/dashboard/src/components/evidence-source.tsx`
- MODIFY `apps/dashboard/src/components/overview.tsx`
- MODIFY `apps/dashboard/src/components/purchase-list.tsx`
- MODIFY `apps/dashboard/src/components/purchase-detail.tsx`
- MODIFY `apps/dashboard/src/components/alerts.tsx`
- MODIFY `apps/dashboard/src/components/agents.tsx`
- MODIFY `apps/dashboard/src/components/Nav.tsx`
- MODIFY `apps/dashboard/tests/request-boundary.test.mjs`
- NEW `apps/dashboard/tests/phase6-read-model.test.mjs`

### Private E2E workspace and root orchestration

- NEW `tools/phase6-e2e/package.json`
- NEW `tools/phase6-e2e/tsconfig.json`
- NEW `tools/phase6-e2e/playwright.config.ts`
- NEW `tools/phase6-e2e/src/orchestrator.ts`
- NEW `tools/phase6-e2e/src/process-manager.ts`
- NEW `tools/phase6-e2e/src/port-allocator.ts`
- NEW `tools/phase6-e2e/src/network-policy.ts`
- NEW `tools/phase6-e2e/src/manifest.ts`
- NEW `tools/phase6-e2e/tests/phase6-dashboard.spec.ts`
- MODIFY `package.json`
- MODIFY `package-lock.json`
- MODIFY `.gitignore`

### Readiness and user/operator documentation

- NEW `scripts/generate_aws_deploy_readiness.mjs`
- NEW `docs/AWS_DEPLOY_READINESS.md`
- MODIFY `README.md`
- MODIFY `README.en.md`
- MODIFY `docs/HANDOFF.md`
- MODIFY `docs/ROADMAP.md`

Architecture deliverable은 새 speculative architecture 문서를 만들지 않고 `README.md`, `README.en.md`, `docs/HANDOFF.md`의 구조도/data-flow/process-boundary를 실제 Phase 6 composition과 일치시키는 것으로 고정한다.

### Project state/log and evidence

- MODIFY `.agent/CURRENT_STATE.md`
- MODIFY `.agent/HANDOFF.md`
- APPEND ONLY `.agent/TURN_LOG.md`
- NEW `.agent/outbox/phase6-local-e2e-manifest.json`
- NEW `.agent/outbox/WO-P6-04-coder.done.md` 또는 실패 시 NEW `.agent/outbox/WO-P6-04-blocked.md`
- runtime only, gitignored: `artifacts/phase6-e2e/<executionId>/**`

Canonical planning, Work Orders, buyer/gateway/seller implementation, schema/catalog, infra/contracts/Terraform/Compose, deployment evidence는 read-only다. WO-P6-01..03 defect로 이 목록 밖 변경이 필요하면 이 WO에서 고치지 않고 Planner에게 반환한다.

## 구현 단계

1. Dashboard DTO를 canonical projection, structured finding, reputation provenance, EVM/LOCAL transaction union으로 전환한다. compatibility fields는 backend projection에서만 받고 raw event payload를 link에 전달하지 않는다.
2. `StatusTriplet`, `EvidenceSourceBadge`, `TransactionReference`를 구현하고 모든 overview/list/detail/alerts/agents에 같은 status/source semantics를 적용한다.
3. synthetic/history/live filters와 aggregation을 분리하고 ruleId/expected/observed/source/provenance/freshness를 redacted 형태로 표시한다. raw prompt/response, secret, signature, wallet material은 렌더하지 않는다.
4. Nav/request-boundary tests를 확장해 scenario route/control, audit/reconcile/feedback mutation, experiment runner, raw localtx→BaseScan interpolation이 source와 rendered DOM/network에 없음을 확인한다.
5. private `@pbl/phase6-e2e` workspace를 만들고 유일한 신규 외부 dev dependency `@playwright/test`만 추가한다. Ajv/ORM/process supervisor/AWS SDK를 추가하지 않는다.
6. deterministic port allocator와 single-run lock을 구현한다. 선택 port/collision count를 manifest에 남기고 점유 process를 종료하지 않는다.
7. process manager가 native single-node Mongo replica set, scenario API, two Seller servers, fake Gateway/boundaries, Next.js production server를 실제 child process로 시작하고 health-check/teardown한다.
8. ephemeral SIWE wallet을 memory-only로 생성해 실제 nonce/challenge/signature verify와 session cookie를 거친다. key/signature/env는 log/manifest에 쓰지 않는다.
9. 12 scenarios를 actual API로 실행한다. list/detail/alerts/agents와 event chain/payment/reputation/ledger를 검증하고 A09는 intermediate browser/API assertion 후 advance한다.
10. Playwright Chromium single worker가 overview, purchases list/detail, alerts, agents를 12 scenarios에서 실제 렌더링해 statuses, source labels, rule IDs, provenance, no control, no BaseScan/localtx를 검증한다.
11. network policy가 loopback/Mongo를 별도 계수하고 real provider/RPC/facilitator/ERC-8004/AWS attempts를 차단/0으로 증명한다. 테스트 code path가 fake counter 자체를 expected oracle에서 채우지 않게 한다.
12. actual manifest를 seal한 뒤 independent comparator로 catalog expected와 비교한다. 하나의 assertion/status/rule/balance/transaction/feedback/cleanup/process/history mismatch라도 root command를 non-zero로 만든다.
13. exact scenario cleanup, child teardown, temp cleanup 뒤 history after digest를 before와 비교한다. redacted manifest를 `.agent/outbox/phase6-local-e2e-manifest.json`에 쓰고 detailed runtime artifacts는 gitignore한다.
14. pure readiness renderer가 성공 manifest와 local Terraform/Docker/manifests/env key names만 읽어 `docs/AWS_DEPLOY_READINESS.md`를 만든다. future commands/resource list는 fenced documentation이며 실행하지 않는다.
15. README 한국어/영어, HANDOFF, ROADMAP을 실제 command/architecture/mock-live boundary/known external gates와 일치시킨다. 과거 live success와 Phase 6 synthetic result를 혼합하지 않는다.
16. `.agent/CURRENT_STATE.md`는 candidate가 local E2E complete/pending review임을, `.agent/HANDOFF.md`는 exact next external gate를 기록한다. TURN_LOG는 append-only다. 제품 결정/provisional default를 임의 확정하지 않는다.
17. focused tests와 root full matrix를 실행한다. executable tree를 primary commit으로 고정한 뒤 exact commit에서 E2E를 재검증해 manifest/readiness에 pass commit을 연결한다.
18. exact tested commit을 문서에 반영하려면 self-reference가 불가능하므로 **오직 이 경우만** docs/state/evidence-only follow-up commit을 허용한다. follow-up은 executable source/package/lock을 바꾸지 않으며 지정된 message를 쓴다. Reviewer는 primary tested SHA와 docs-only tip 둘 다 검증한다.
19. 모든 process를 종료하고 final status `AWAITING_AWS_DEPLOYMENT_APPROVAL`을 남긴 뒤 Reviewer에게 handoff한다. AWS command는 실행하지 않는다.

## Manifest 필수 계약

`.agent/outbox/phase6-local-e2e-manifest.json`:

```text
commit, dirtyPaths, catalogVersion, catalogHash, runId, executionId,
frozenClock, serviceVersions, portMap, configHash,
historyBefore, historyAfter, historyEqual,
scenarios[{id,purchaseId,projection,ruleIds,eventChain,balanceDeltas,
           acceptedTransfers,rejectedTransfers,feedbacks,apiAssertions,uiAssertions}],
outboundCounters, cleanup, processTeardown, suiteResults, finalStatus
```

- scenario 배열은 exactly 12 unique IDs다.
- A09에는 intermediate `PAYMENT_CONFIRMATION_UNKNOWN + PENDING_AUDIT`와 final `RECONCILED_NO_TRANSFER + AUDITED_RISK` evidence가 모두 있다.
- 모든 scenario는 API/UI same `purchaseId`, exact status/rules/source, accepted successful transfer ≤1, terminal audit exactly 1, expected feedback count/value를 만족한다.
- `historyEqual=true`, cleanup/processTeardown pass, real provider/RPC/facilitator/ERC-8004/AWS counters 0, `finalStatus=AWAITING_AWS_DEPLOYMENT_APPROVAL`다.
- raw prompt/response, cookie, key, signature, nonce, credential, full environment는 없다.

## AWS readiness 필수 계약

`docs/AWS_DEPLOY_READINESS.md`는 최소 다음을 포함한다.

- verified local manifest path/hash와 executable pass commit
- immutable image/build digest 계획
- 서비스별 env/secret **이름만** 있는 matrix
- ap-northeast-2 topology와 ECR/ALB/HTTPS/WAF/Atlas connectivity/alarms gaps
- IAM least privilege review
- PBLC V2/Base Sepolia/ERC-8004 주소·chain 재검증 계획
- sensitive payload retention `BLOCKED`
- backup/rollback/runbook, observability/SLO, cost owner/budget
- live Gemini/Nemotron external gate
- 향후 mutation command/resource list, approver, approval timestamp placeholder
- `enable_services=false`도 apply 권한이 아니라는 경고
- exact final status `AWAITING_AWS_DEPLOYMENT_APPROVAL`

renderer와 tests에는 AWS SDK/CLI/Terraform executor가 없다. fake `aws`/`terraform` tripwire attempt count도 0이어야 한다.

## Error handling과 설계 차이

- WO-P6-01..03 API/schema가 design과 달라 dashboard/E2E가 requirement를 약화해야만 동작하면 즉시 중단한다. backend를 이 WO 범위에 몰래 수정하지 않는다.
- Chromium/Mongo/loopback/history snapshot이 없으면 skip, mock DOM, source regex, live Atlas 대체를 하지 않는다.
- history mismatch, cleanup attestation 실패, outbound attempt, child leak, AWS tripwire, localtx/BaseScan confusion은 hard failure다. canonical data나 기존 process를 고쳐서 pass로 만들지 않는다.
- `.agent/outbox/WO-P6-04-blocked.md`에 base/predecessor SHA, exact failure, command/exit, process/DB/outbound preservation, requirement/design ID, 필요한 최소 Planner/사용자 결정을 기록한다.
- product wording/retention/reputation policy가 미결정이면 provisional default를 유지하고 meeting decision으로 남긴다. Phase 6 구현을 임의 차단하거나 확정하지 않는다.

## Focused 검증 명령 — 순서 고정

```bash
npm run test --workspace @pbl/dashboard
npm run build --workspace @pbl/dashboard
npm run build --workspace @pbl/seller-service
npm run build --workspace @pbl/commerce-gateway
npm run build --workspace @pbl/phase6-e2e
npm test --workspace @pbl/phase6-e2e
npm run test:e2e:local
node scripts/generate_aws_deploy_readiness.mjs
git diff --check
```

## Final broad command matrix — 순서 고정, 모두 필수

```bash
npm run lint
npm test
npm run test --workspace @pbl/dashboard
npm run test:mongo:local
npm run build --workspace @pbl/seller-service
npm run build --workspace @pbl/commerce-gateway
npm run build --workspace @pbl/dashboard
npm run test:e2e:local
docker compose -f infra/docker/compose.yml config
npm audit --omit=dev
git diff --check
```

- E2E의 provider/RPC/facilitator/ERC-8004/AWS outbound-zero와 `npm audit`의 npm registry 조회는 별도 category다. registry audit를 live provider call로 오기록하지 않는다.
- Mongo/Chromium/port/cleanup/history/outbound 문제는 skip으로 성공 처리하지 않는다.
- broad suite 뒤 executable source/package/lock이 바뀌면 증거는 무효다. 무효화 이유를 기록하고 full matrix를 다시 실행한다.

commit 후 Reviewer:

```bash
git diff --check "$(git merge-base HEAD main)"..HEAD
git diff --name-only "$(git merge-base HEAD main)"..HEAD
git diff --exit-code "$(git merge-base HEAD main)"..HEAD -- aidlc-docs/inception/requirements.md aidlc-docs/inception/design.md aidlc-docs/inception/tasks.md work-orders infra/contracts infra/aws/terraform infra/docker/compose.yml docs/ERC3009_DEPLOYMENT_GATE.md
```

Reviewer는 exact primary executable commit에서 `npm run test:e2e:local`과 full matrix를 독립 재실행하고 manifest hash/commit을 대조한다. docs-only follow-up이 있으면 executable diff가 0인지 별도 확인한다.

## Preservation checks

- [ ] requirements/design/tasks/Work Orders와 PBLC V2/Permit2 deployment evidence diff가 0이다.
- [ ] infra/contracts, Terraform, Compose가 byte-identical이며 어떤 apply/import/destroy/deploy/push도 실행하지 않았다.
- [ ] historical successful/failed/incomplete Mongo counts/IDs/event heads/hashes/payment refs before=after다.
- [ ] synthetic DB/temp만 exact attestation으로 cleanup됐고 existing `pbl-stack`/Mongo/process를 kill하지 않았다.
- [ ] public-chain successful writes, ERC-8004 live writes, provider live calls, AWS/Atlas mutations가 0이다.
- [ ] dashboard의 GET/SSE/browser navigation으로 audit/reconciliation/feedback/scenario write가 0이다.
- [ ] synthetic 모든 surface가 label을 보이고 localtx/EVM/BaseScan type confusion이 0이다.
- [ ] one purchase accepted successful transfer ≤1, feedback/oracle가 catalog와 일치한다.
- [ ] logs/manifest/docs에 raw prompt/response, key, signature, cookie, nonce, credential/env value가 없다.
- [ ] child/native Mongo/Next/Playwright process와 allocated ports가 모두 teardown됐다.

## 완료 기준

- [ ] API fields와 dashboard five surfaces가 canonical status/source/finding/provenance를 같은 의미로 렌더한다.
- [ ] dashboard nav/routes/DOM/network에 scenario/audit/reconcile/feedback mutation control이 없다.
- [ ] actual Chromium이 12 scenarios와 A09 intermediate/final 상태를 검증한다.
- [ ] root `npm run test:e2e:local`이 실제 services/SIWE/native Mongo/browser를 한 번에 실행·정리한다.
- [ ] manifest 12 scenarios의 API/UI/event/ledger/feedback oracle가 모두 pass다.
- [ ] historyEqual, outbound-zero, cleanup/process teardown, synthetic link safety가 pass다.
- [ ] README 한국어/영어, HANDOFF, ROADMAP, architecture/data-flow, state/log가 실제 구현과 일치한다.
- [ ] AWS readiness가 local pure generation, retention BLOCKED, future plan only, exact stop status를 포함한다.
- [ ] focused suite와 final broad matrix 모두 exit 0이며 skip/fabrication이 없다.
- [ ] allowed-write 밖 변경이 없고 primary coherent commit, 필요 시 docs-only evidence commit, Coder handoff가 있다.
- [ ] 최종 자동화 상태가 `AWAITING_AWS_DEPLOYMENT_APPROVAL`이며 AWS/public-chain/provider process가 실행 중이지 않다.

## Coder evidence contract

`.agent/outbox/WO-P6-04-coder.done.md` 필수 내용:

- base SHA, predecessor approved SHA, primary tested SHA, optional docs-only tip, branch/messages
- changed files와 primary-vs-docs-only diff
- focused/final 11 commands의 exit code/count/duration
- local manifest path/hash, catalog/config hash, 12 scenario summary
- actual Chromium/browser evidence와 A09 intermediate/final evidence
- history before/after digest, cleanup/process/port teardown
- outbound counters와 AWS tripwire 0
- protected files/data hashes/diffs
- docs/readiness/state/log completeness와 unresolved decisions
- 미실행 항목/이유
- exact terminal marker `AWAITING_AWS_DEPLOYMENT_APPROVAL`
- `READY_FOR_REVIEW`

Reviewer는 `.agent/outbox/WO-P6-04-review.md`에 independently verified primary/tip SHA, full matrix, manifest/readiness/protected state, `APPROVE|REJECT`를 기록한다.

## Commit, reviewer, rollback/handoff gate

1. primary commit 전 TURN_LOG에 실행 내역을 append하고 unrelated change를 제거한다. `wo/P6-04`에만 commit한다.
2. exact tested SHA를 docs/readiness에 넣는 데 필요한 경우만 docs/state/evidence-only follow-up을 허용한다. executable source, manifests, lock, tests, catalog는 이 commit에서 변경 금지다.
3. Coder는 main/push/merge/deploy하지 않는다. Reviewer는 self-report가 아니라 exact commits와 actual output을 재검증한다.
4. 실패/reject 시 reset/stash/rebase/amend하지 않는다. branch와 evidence를 미통합 상태로 보존하고, safe scenario resources만 exact guard로 정리한다.
5. history/outbound/AWS/type-confusion 실패는 자동 보정하지 않는 hard stop이다. 승인 baseline 변경이 필요하면 Planner로 반환한다.
6. Reviewer `APPROVE` 뒤 Orchestrator만 통합·project state finalization을 수행한다. 그 다음 행동은 배포가 아니라 사용자에게 local-complete evidence를 보고하고 AWS deployment approval을 기다리는 것이다.

## 금지 사항

- canonical planning/Work Order 또는 WO-P6-01..03 구현 수정
- dashboard/user request flow의 experiment/scenario runner, audit/reconcile/feedback write control
- source regex/mock DOM만으로 browser E2E 대체
- test skip, assertion/oracle/status/rule/balance/feedback 약화
- synthetic를 EVM hash/BaseScan/live provider/on-chain truth로 표시
- history DB mutation/backfill/delete/drop/clone/seed/migration
- existing pbl-stack/Mongo/port process 강제 종료
- real Gemini/Nemotron/RPC/facilitator/ERC-8004 call 또는 public-chain write
- `terraform apply/import/destroy`, AWS CLI/SDK/Console create/update/delete, ECR push, Amplify/ECS 실행, Secrets/IAM/SG/VPC/ALB/WAF/Route53/CloudWatch/Atlas mutation
- `enable_services=false`를 apply 승인으로 해석
- secret/env/private key/cookie/signature/raw prompt-response 읽기·출력·커밋
- retention/reputation wording/product decision 임의 확정
- main commit, push, merge, reset, stash, rebase, amend
