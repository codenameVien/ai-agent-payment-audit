# Phase 6 요구사항 개정 릴레이 아티팩트

- 작성 역할: Planner lane
- 기준 저장소: `/Users/vien/MyProjects/PBL`
- 기준 시점: 2026-09-04, `main@8886745`
- 의미상 대상 파일: `aidlc-docs/inception/requirements.md`
- 이 파일의 용도: 기존 요구사항을 보존한 채 Phase 6 요구사항을 전달하는 단일 소스
- 승인된 실행 범위: 완전한 LOCAL E2E까지 자율 진행하고, 실제 AWS 배포 직전에 정지

## 릴레이 계약

릴레이 담당자는 아래 `BEGIN`과 `END` 사이의 Markdown을 **바이트 단위로 그대로** `aidlc-docs/inception/requirements.md`의 마지막 줄 뒤에 한 번만 추가한다. 기존 본문, 기존 요구사항 ID, 승인 문구를 수정·이동·삭제하거나 아래 내용을 요약·의역하지 않는다.

릴레이 전 다음 조건을 확인한다.

1. 대상 파일이 현재 `## 11. Requirements Approval Gate`를 포함한다.
2. 대상 파일에 `P6-US-01` 또는 `BEGIN PHASE6 REQUIREMENTS APPEND`가 아직 없다.
3. 기준선이 달라 충돌하거나 이미 릴레이된 경우에는 자동 병합하지 않고 Planner에게 반환한다.

릴레이 후에는 `P6-US-*`, `P6-AC-*`, `P6-NFR-*` ID의 중복 여부와 기존 파일 앞부분의 byte hash 불변을 확인한다. 이 아티팩트 자체는 릴레이 대상 파일이 아니다.

<!-- BEGIN PHASE6 REQUIREMENTS APPEND -->

## 12. Phase 6 — Local E2E, Abnormal Audit Scenarios, and Reputation Automation

### 12.1 Amendment status and precedence

이 절은 기존 Phase 1–5 요구사항의 **추가 개정**이다. 기존 `US-*`, `AC-*`, `NFR-*` ID와 불변조건은 모두 유지된다. 충돌처럼 보이는 경우 더 엄격한 보안·지불·감사 불변조건을 적용하며, 이 절을 근거로 기존 요구사항을 삭제하거나 약화할 수 없다.

Phase 6의 종료점은 정상 및 모든 승인된 비정상 시나리오가 실제 로컬 서비스 경계와 브라우저 UI를 통과하는 완전한 LOCAL E2E, 그리고 배포 준비 체크리스트의 작성 완료다. 실제 AWS 리소스 생성·변경과 실제 공개 체인 쓰기는 Phase 6 범위 밖이며 명시적 후속 승인 전까지 금지한다.

### 12.2 Repository-grounded current-state evidence

이 판정의 직접 근거는 `aidlc-docs/inception/{requirements,design,tasks}.md`, `aidlc-docs/{aidlc-state,audit}.md`, `README.md`, `docs/ROADMAP.md`, `.agent/{CURRENT_STATE,DECISIONS}.md`, root/workspace package manifests, `services/buyer-audit-api/src/buyer_audit_api/{core,api,domains,adapters}`, `services/commerce-gateway/src`, `services/seller-service/src`, `apps/dashboard/src`, `infra/contracts`, `infra/aws/terraform`, 각 workspace의 tests다.

#### 12.2.1 Implemented and preserved baseline

| 영역 | 현재 구현·증거 | Phase 6 보존 조건 |
|---|---|---|
| 구매 증거 사슬 | MongoDB 및 메모리 저장소가 `purchaseId`별 append-only 이벤트, 이전 해시/현재 해시, 원자적 head 갱신, singleton 이벤트 제약을 제공한다. | 기존 성공·실패·불완전 행과 이벤트를 수정·삭제·재작성하지 않는다. |
| 선택 | buyer core가 hard filter 후 가중 점수로 선택하며 benchmark snapshot, quote, decision evidence를 저장한다. | 모델별 분기 대신 현재 adapter/port 경계를 유지한다. |
| 결정론 감사 | `core/audit.py`가 예산, 후보, 점수, 설명, 결제, 전달, 해시 사슬을 규칙으로 검사하고 `AUDITED`를 기록한다. | 규칙 결과가 권위 있고 semantic/LLM 결과는 advisory라는 기존 불변조건을 유지한다. |
| 결제 | Commerce Gateway만 ERC-3009 authorization을 서명·제출하고 receipt의 정확한 `Transfer` 및 `AuthorizationUsed`를 독립 검증한다. | Permit2는 신규 실행 경로로 복원하지 않는다. 지갑·체인 쓰기는 Gateway 밖으로 이동하지 않는다. |
| 라이브 증거 | PBLC V2 `0xDed7F4992D98eF31453dCebbB8c2A6b50d0284B3`, 배포 tx `0x5e5e6b1acde5d51e0d11f4c3784d64fc738fb6daa814f3bfe14afae1c8d4e83f`, ERC-3009 구매 `451f8657-cbc0-4469-acb6-a7037b4d4865`의 tx `0x5b555e3c50629430cdee30c528db8f45f56441a3a058888e89ab0fb4797892ee`와 block `46349821`, exact `Transfer`/`AuthorizationUsed`, replay 거부 증거가 존재한다. | 이 값을 합성 시나리오가 덮어쓰거나 재사용하지 않는다. 라이브 성공이라고 보고할 때는 저장된 receipt/log 또는 실제 조회 결과를 근거로 한다. |
| 역사 호환성 | Permit2 성공 구매 `378beb23-e352-49f0-b450-87da88791292`와 tx `0x32562decbafa3c670280501bafbce01b72ce698d0391c63f4e3c5113f070a0a8`, 불완전 구매 `14b7dd10-fba1-4ea0-afc9-fb44500d6b4b`가 읽기 가능하다. | 역사 자료는 immutable이다. 불완전 행을 자동 종결·삭제·backfill하지 않는다. |
| ERC-8004 | feedback publisher가 evidence preparation, on-chain lookup/recovery, receipt 확인, `REPUTATION_RECORDED`를 지원하며 in-process 중복 실행 방지 테스트가 있다. `/reputation` 및 reputation 조회 경계가 있다. | 실제 Phase 6 E2E에서는 fake registry를 사용하고 기존 온체인 feedback을 건드리지 않는다. |
| 공급자 | 결정론적 mock provider와 Gemini/Nemotron 실제 adapter가 provider mode 뒤에 있다. 기본 조합은 mock이다. | 이 단계의 필수 E2E는 mock으로 완결한다. 실제 자격증명과 외부 호출은 별도 gate다. |
| UI 경계 | dashboard navigation에는 overview, purchases, agents, alerts만 있고 `/experiments`는 `/request`로 redirect된다. | dashboard는 read-only를 유지하며 scenario runner를 노출하지 않는다. |

#### 12.2.2 Missing or insufficient for Phase 6

| Gap | 현재 한계 | Phase 6에서 필요한 결과 |
|---|---|---|
| 상태 의미 | API/UI가 마지막 lifecycle status와 audit severity를 중심으로 보여 `pending audit`, 결제 실패, 결제 확인 불명, 감사 완료 상태가 직교하지 않는다. | 결제·감사·증거 출처를 분리한 canonical projection과 일관된 API/UI label을 제공한다. |
| reconciliation 종결 | `PAYMENT_RECONCILIATION_REQUIRED`가 exact 성공이나 confirmed revert가 아니면 terminal no-transfer로 끝날 수 없다. | 증거에 근거한 append-only `reconciled-no-transfer` 종결 경로와 예약 해제를 추가한다. |
| 실제 결제 mismatch 감사 | 현재 `AUD-QUOTE-PAYMENT-MISMATCH`는 주로 quote와 claimed intent를 비교하며 실제 receipt의 token/recipient/amount mismatch를 terminal classification으로 표현하지 못한다. | actual payment proof와 quote를 비교하고 mismatch field 및 증거 reference를 남긴다. |
| 읽기 부작용 | purchase detail과 alerts read path가 audit service를 호출해 조건에 따라 `AUDITED`를 append할 수 있다. | GET/read API는 audit, feedback, scenario 실행을 포함한 어떤 쓰기도 발생시키지 않는다. |
| reputation 자동화 | 정상 구매 orchestration이 terminal audit 후 feedback을 자동 publish하지 않는다. | terminal audit 기반의 durable, cross-process, exactly-once 효과를 갖는 자동화가 필요하다. |
| reputation 영향/출처 | 선택은 정적 benchmark reputation 값을 사용하고 `/agents`는 저장된 개별 기록 중심이라 조회 집계의 provenance/freshness가 불충분하다. | 신뢰된 ERC-8004 집계와 provenance를 저장·노출하고, hard constraint 이후의 제한된 점수 성분으로 사용한다. |
| scenario harness | 허용 목록 기반의 격리된 비정상 시나리오 runner와 synthetic local ledger가 없다. | dev/demo-only harness, 별도 임시 DB, fake payment/registry, deterministic seed와 정리 절차가 필요하다. |
| complete E2E | root test와 service/unit/integration test는 있으나 모든 시나리오를 실제 HTTP 경계와 브라우저 UI로 검증하는 단일 local E2E gate가 없다. | 정상+전체 비정상 catalog를 API와 브라우저까지 검증하는 명령과 machine-readable 결과가 필요하다. |
| AWS stop artifact | Terraform은 `enable_services=false`여도 apply 시 cluster, logs, IAM, security group, task definitions 등을 만들 수 있다. | 어떤 AWS mutation도 없이 deploy-readiness만 작성·검증하고 정지하는 gate가 필요하다. |

### 12.3 Canonical Phase 6 status model

**P6-REQ-STATUS-01 — Orthogonal projections.** 모든 purchase read model과 API는 원본 `lifecycleStatus`를 보존하면서 다음 세 축을 별도 필드로 제공해야 한다. 한 축의 값을 다른 축의 대용으로 사용하지 않는다.

1. `paymentStatus`
   - `PAYMENT_NOT_STARTED` — 결제 의도 전
   - `PAYMENT_PENDING` — authorization/submission 진행 중이며 아직 확인 불명 상태는 아님
   - `PAYMENT_CONFIRMATION_UNKNOWN` — submission identity 또는 성공 claim은 있으나 authoritative receipt/log로 성공·실패·no-transfer를 확정하지 못함
   - `PAYMENT_SETTLED` — exact authoritative payment proof가 있음
   - `PAYMENT_MISMATCH_CONFIRMED` — authoritative 또는 명시적으로 synthetic인 local ledger proof가 quote와 다른 실제 전송을 확정함
   - `PAYMENT_FAILED` — authoritative revert/rejection으로 실패가 확정됨
   - `RECONCILED_NO_TRANSFER` — reconciliation 결과 transfer가 없었음이 terminal evidence로 확정됨
2. `auditStatus`
   - `PENDING_AUDIT` — persisted terminal `AUDITED` event가 없음. preview severity가 있어도 이 상태를 감사 완료로 표시하지 않는다.
   - `AUDITED_NORMAL` — persisted terminal `AUDITED`의 verdict가 `NORMAL`
   - `AUDITED_WARNING` — persisted terminal `AUDITED`의 verdict가 `CAUTION`; 외부 표시명은 “audited warning”으로 통일
   - `AUDITED_RISK` — persisted terminal `AUDITED`의 verdict가 `RISK`
3. `evidenceSource`
   - `BASE_SEPOLIA_VERIFIED` — 해당 사실이 저장된 또는 현재 검증된 Base Sepolia receipt/log로 입증됨
   - `HISTORICAL_ON_CHAIN` — 읽기 전용 역사 evidence이며 신규 실행에 사용하지 않음
   - `SYNTHETIC_LOCAL` — 격리된 scenario harness에서 생성됨

다음 상태는 절대로 합치지 않는다: `PENDING_AUDIT`, `AUDITED_NORMAL`, `AUDITED_WARNING`, `AUDITED_RISK`, `PAYMENT_FAILED`, `RECONCILED_NO_TRANSFER`, `PAYMENT_CONFIRMATION_UNKNOWN`. `transactionHash` 문자열 또는 facilitator의 success claim만으로 `PAYMENT_SETTLED`이나 `BASE_SEPOLIA_VERIFIED`를 만들 수 없다.

**P6-REQ-STATUS-02 — Terminal lifecycle events.** 신규 lifecycle event `PAYMENT_RECONCILED_NO_TRANSFER`와 `PAYMENT_MISMATCH_CONFIRMED`를 정의한다.

- 둘 다 기존 events를 수정하지 않고 append한다.
- `PAYMENT_RECONCILED_NO_TRANSFER`는 `PAYMENT_RECONCILIATION_REQUIRED` 뒤에만 올 수 있으며, bounded retry/조회 정책이 끝났고 no-transfer를 뒷받침하는 proof 또는 local fake proof가 있을 때만 기록한다.
- `PAYMENT_MISMATCH_CONFIRMED`는 실제 전송 proof가 quote의 amount/token/recipient 중 하나 이상과 다를 때만 기록한다.
- 동일 proof의 반복 처리는 동일 결과를 반환하고 event, reservation release, audit, feedback을 중복 생성하지 않는다. 충돌 proof는 거부하고 보안 사건으로 기록한다.
- no-transfer 및 confirmed failure는 reserved budget을 정확히 한 번 해제한다. mismatch transfer는 실제 지출액을 rolling/user budget에 반영하며 자동 재시도·자동 환불을 하지 않는다.
- Phase 6 도입 전에 존재하던 incomplete MongoDB row는 자동 전이시키지 않는다. 별도 승인된 corroborating evidence와 명시적 reconciliation operation이 없는 한 `PAYMENT_CONFIRMATION_UNKNOWN`으로 읽고 원본 history를 그대로 둔다.

### 12.4 User stories and acceptance criteria

#### P6-US-01 — Auditable abnormal-scenario catalog

개발자와 검토자는 공개 체인이나 역사 데이터를 훼손하지 않고 buyer/audit/payment/reputation의 비정상 동작을 반복 검증할 수 있어야 한다.

- **P6-AC-01.1:** catalog는 versioned, allow-listed data schema이며 최소 `scenarioId`, `catalogVersion`, `seed`, `frozenClock`, `injections`, `expectedOracle`를 가진다.
- **P6-AC-01.2:** `scenarioId + catalogVersion + seed`가 같으면 purchase/event 식별자, 입력, 점수, fake receipt, 규칙 verdict, balance delta가 재현 가능하다.
- **P6-AC-01.3:** runner 입력은 catalog에 등록된 injection만 허용한다. 임의 코드, 임의 URL/RPC, shell, secret, wallet key, production DB name, expected verdict override를 받지 않는다.
- **P6-AC-01.4:** 아래 12.5의 정상 1개와 비정상 11개 항목을 모두 독립 실행할 수 있고, machine-readable manifest에 actual과 expected를 대조한다.
- **P6-AC-01.5:** production buyer/payment path는 동일한 injection 필드를 수락하지 않으며, harness 설정으로 production budget·binding·idempotency guard를 우회할 수 없음을 negative test로 입증한다.

#### P6-US-02 — Isolated development/demo scenario harness

개발자와 데모 운영자는 dashboard와 공개 체인으로부터 격리된 harness에서 시나리오를 실행하고 안전하게 정리할 수 있어야 한다.

- **P6-AC-02.1:** harness는 CLI 또는 인증된 loopback-only internal dev API로만 실행한다. `apps/dashboard` navigation, route, button, API client, user-facing request flow에 runner를 추가하지 않는다. 기존 `/experiments` redirect도 유지한다.
- **P6-AC-02.2:** payment executor, facilitator, receipt verifier, ledger 및 ERC-8004 registry는 in-process 또는 local-network fake boundary를 사용한다. harness 시작 시 real RPC/facilitator URL, real wallet/private key, production environment 또는 non-local host가 활성화되어 있으면 fail closed한다.
- **P6-AC-02.3:** E2E manifest는 RPC, facilitator, provider, ERC-8004, AWS outbound call count가 모두 `0`임을 보인다. 허용된 loopback/Mongo local 연결은 별도로 표시한다.
- **P6-AC-02.4:** 각 run은 명시적 allow-list prefix와 `runId`를 가진 별도 ephemeral MongoDB database/namespace를 사용한다. canonical/역사 DB와 동일한 이름, URI, database를 거부한다.
- **P6-AC-02.5:** cleanup은 해당 `runId` namespace만 exact match로 제거한다. 실행 전후 역사 DB의 row count, purchase ID set, event head/hash snapshot이 같음을 read-only pre/post check로 증명한다.
- **P6-AC-02.6:** 모든 synthetic event, API response, exported manifest, UI card/detail/alert/agent row는 `SYNTHETIC_LOCAL`, `scenarioId`, `catalogVersion`, `runId`를 명시한다. synthetic transaction identifier는 `localtx:` namespace를 쓰고 EVM transaction hash 필드나 BaseScan link로 렌더링하지 않는다.
- **P6-AC-02.7:** cleanup 후에도 test report artifact는 입력 secret이나 raw prompt/response 없이 oracle, rule IDs, hashes, balance deltas, suite result를 남긴다.

#### P6-US-03 — Deterministic terminal audit

감사자는 정상·비정상 구매의 사실 관계와 terminal classification을 append-only evidence로 재현할 수 있어야 한다.

- **P6-AC-03.1:** 감사 finding은 `ruleId`, `rulesetVersion`, `severity`, `authority`, `expected`, `observed`, `mismatchedFields`, `evidenceRefs`를 구조화해 저장한다. deterministic rule의 `authority`는 `DETERMINISTIC`; semantic advisory는 `SEMANTIC_ADVISORY`다.
- **P6-AC-03.2:** `AUD-QUOTE-PAYMENT-MISMATCH`는 claimed intent뿐 아니라 verified actual receipt/local-ledger proof의 amount, token, recipient를 quote와 비교하며 `mismatchedFields`를 정확히 열거한다.
- **P6-AC-03.3:** terminal audit는 terminal payment/delivery evidence가 append된 뒤 orchestrator가 명시적으로 한 번 요청한다. GET/list/detail/alerts/agents 등 read handler는 audit·reconciliation·feedback event를 append하지 않는다.
- **P6-AC-03.4:** nonterminal preview는 `PENDING_AUDIT`로 표시하고 persisted `AUDITED`로 위장하지 않는다. `PAYMENT_CONFIRMATION_UNKNOWN`에는 terminal feedback을 publish하지 않는다.
- **P6-AC-03.5:** 동일 evidence head와 ruleset에 대한 재감사는 기존 terminal audit를 반환한다. evidence head가 terminal audit 후 달라지면 silent overwrite가 아니라 별도 correction/re-audit 정책을 요구한다.
- **P6-AC-03.6:** semantic finding만으로 deterministic `NORMAL`을 `RISK`로 강제하거나 deterministic `RISK`를 낮출 수 없다. semantic-only 경고는 `AUDITED_WARNING`까지만 만든다.

#### P6-US-04 — Append-only payment reconciliation

운영자는 불완전 결제를 history 삭제 없이 terminal no-transfer 또는 confirmed outcome으로 종결하고 budget reservation을 정확히 정리할 수 있어야 한다.

- **P6-AC-04.1:** bounded reconciliation policy는 attempt count, first/last checked time, checked chain, transaction/submission identity, provider result, proof reference를 append-only로 남긴다.
- **P6-AC-04.2:** no receipt/log라는 순간적 부재만으로 종결하지 않는다. configured finality와 bounded retry가 끝났고 authoritative lookup이 no-transfer를 확정하거나, `SYNTHETIC_LOCAL` fake oracle가 이를 확정해야 한다.
- **P6-AC-04.3:** terminal `PAYMENT_RECONCILED_NO_TRANSFER` 뒤에는 `AUDITED`가 append되며 `AUD-PAYMENT-RECONCILED-NO-TRANSFER`를 포함한다. 상태는 `RECONCILED_NO_TRANSFER`와 `AUDITED_RISK`이며 `PAYMENT_FAILED`로 표시하지 않는다.
- **P6-AC-04.4:** 기존 incomplete purchase를 migration/fixture cleanup 대상으로 삼지 않는다. 역사 row의 종결은 별도 operator authorization 및 corroborating evidence가 있을 때만 가능하다.
- **P6-AC-04.5:** `P6-A09` E2E는 reconciliation 직후 일시 정지해 API/UI가 `PAYMENT_CONFIRMATION_UNKNOWN + PENDING_AUDIT`, terminal audit/feedback 0건을 보이는지 확인한 후 bounded reconciliation을 재개한다. 종결 뒤에는 같은 purchase가 `RECONCILED_NO_TRANSFER + AUDITED_RISK`로 바뀌고 이전 event가 남아 있어야 한다.

#### P6-US-05 — ERC-8004 terminal feedback automation

시스템은 terminal audit 후 결정론적 reputation feedback을 자동 처리하되, 동일 구매의 on-chain feedback이 중복되지 않도록 해야 한다.

- **P6-AC-05.1:** persisted terminal `AUDITED` event만 feedback decision의 입력이 된다. decision은 `PUBLISH(value, reasonCodes)` 또는 `DEFER(reasonCode)`이며 audit bundle hash와 ruleset version을 포함한다.
- **P6-AC-05.2:** provisional mapping은 다음과 같다.
  - seller가 signed quote의 amount/token/recipient대로 전달을 완료했고 finding이 buyer selection/explanation에만 귀속되면 `PUBLISH(100)`.
  - confirmed payment mismatch, no-transfer, seller-attributable confirmed failure 또는 delivery integrity failure이면 `PUBLISH(0)`.
  - semantic advisory만으로 seller 점수를 낮추지 않는다.
  - `PAYMENT_CONFIRMATION_UNKNOWN`, attribution 불충분, identity 불명, conflicting proof는 `DEFER`하며 chain write를 하지 않는다.
- **P6-AC-05.3:** 한 purchase의 durable publish identity `(chainId, registryAddress, purchaseId, sellerAgentId, tag1, tag2)`에는 MongoDB unique constraint/CAS를 적용해 process restart, 다중 worker, 재감사에서도 on-chain feedback을 최대 한 건으로 제한한다. `auditBundleHash`, ruleset version, value, reason codes는 immutable payload fingerprint에 포함한다. 같은 publish identity에 다른 fingerprint가 오면 새 transaction을 보내지 않고 conflict/correction 검토 대상으로 append한다.
- **P6-AC-05.4:** publish timeout/unknown 전 재시도는 먼저 trusted on-chain feedback event를 조회해 동일 idempotency evidence를 복구한다. corroboration 없이 두 번째 transaction을 제출하지 않는다.
- **P6-AC-05.5:** `REPUTATION_RECORDED`는 confirmed receipt/event 뒤에만 append한다. tx hash만으로 성공 처리하지 않으며 record에는 registry, chain, block, log index, client, agent ID, tags, value, evidence URI/hash가 포함된다.
- **P6-AC-05.6:** Phase 6 LOCAL E2E는 fake registry와 `localtx:` identifier를 사용한다. 실제 ERC-8004 contract write는 금지하며 기존 온체인 feedback을 보존한다.
- **P6-AC-05.7:** concurrent terminal orchestration, retry, process restart를 포함한 E2E에서 한 purchase당 fake-registry accepted feedback과 `REPUTATION_RECORDED`가 각각 최대 1개임을 입증한다.

#### P6-US-06 — Reputation query, aggregation, provenance, and selection influence

사용자와 감사자는 reputation 수치의 근거를 확인할 수 있고, buyer는 이를 hard constraint를 침해하지 않는 제한된 선택 신호로 사용할 수 있어야 한다.

- **P6-AC-06.1:** reputation query snapshot은 `chainId`, `registryAddress`, `agentId`, trusted client allow-list, `tag1`, `tag2`, from/to block, queriedAt, event count, raw values, decimals, aggregation method/version, derived score, freshness, evidence source를 저장·반환한다.
- **P6-AC-06.2:** `/agents`, purchase detail 및 decision evidence는 aggregate score와 provenance/freshness를 제공한다. UI는 값·표본 수·출처·조회 시점을 함께 보여 숫자를 임의의 신뢰 점수로 위장하지 않는다.
- **P6-AC-06.3:** Phase 6 provisional aggregation은 trusted, matching-tag confirmed feedback의 산술 평균을 `0..100`으로 사용한다. 유효 feedback이 없으면 neutral `50`을 사용하고 `NO_REPUTATION_EVIDENCE`로 표시한다. 이 기본값은 12.10의 후속 제품 결정으로 교체 가능하다.
- **P6-AC-06.4:** derived reputation은 기존 preset의 reputation weight 범위 안에서, budget/capability/availability/identity/quote validity 등 hard filter가 끝난 후보끼리만 점수에 영향을 준다. reputation 단독으로 hard constraint를 우회하거나 부적격 후보를 복원할 수 없다.
- **P6-AC-06.5:** 두 개의 otherwise-equal eligible 후보를 사용한 sequential local E2E는 첫 terminal feedback 이후 다음 purchase의 stored snapshot과 선택 점수가 기대 방향으로 변함을 보인다. 별도 negative E2E는 reputation 100인 hard-ineligible 후보가 선택되지 않음을 보인다.
- **P6-AC-06.6:** ERC-8004 reputation은 provider-level `sellerAgentId`에 귀속한다. 같은 seller agent가 제공하는 여러 model은 동일 provenance snapshot을 참조하되, model-level benchmark score와 agent-level reputation을 별도 필드로 저장·표시한다. model별 온체인 identity를 임의 생성하지 않는다.

#### P6-US-07 — Provider boundary for complete local E2E

개발자는 외부 모델 자격증명 없이도 Phase 6 전체를 검증하고, 실제 provider adapter의 준비 상태를 거짓 성공 보고 없이 유지할 수 있어야 한다.

- **P6-AC-07.1:** 정상 및 모든 abnormal E2E의 필수 inference/quote 응답은 seed 가능한 deterministic mock provider로 충분하다.
- **P6-AC-07.2:** Gemini와 Nemotron adapter는 기존 provider port, config validation, typed contract 및 startup readiness test를 유지한다. scenario-specific branch를 실제 adapter에 추가하지 않는다.
- **P6-AC-07.3:** live credential 또는 call이 이미 명시적으로 구성된 경우에도 optional external-gate smoke test로만 분리하며 Phase 6 합격 조건이나 synthetic 결과에 섞지 않는다. 실제 response evidence가 없으면 live integration 성공이라고 보고하지 않는다.
- **P6-AC-07.4:** harness는 Gemini/Nemotron endpoint 호출을 차단하고 outbound-call manifest로 `0`을 입증한다.

#### P6-US-08 — Read-only user dashboard evidence

사용자는 구매·감사·reputation 증거를 읽을 수 있어야 하지만 실험 또는 쓰기 동작을 실행할 수 없어야 한다.

- **P6-AC-08.1:** overview, purchases list/detail, alerts, agents 화면은 12.3의 `paymentStatus`, `auditStatus`, `evidenceSource`를 동일한 label/color semantics로 표시한다.
- **P6-AC-08.2:** synthetic row와 화면에는 눈에 띄는 “Synthetic local scenario — not an on-chain transaction” 표시가 있다. BaseScan link, real chain badge, live provider badge를 만들지 않는다.
- **P6-AC-08.3:** UI와 dashboard API client에는 scenario 시작/중지/재실행/정리, audit trigger, reconciliation trigger, feedback publish action이 없다. browser E2E는 navigation 및 주요 DOM에 runner control이 없음을 검증한다.
- **P6-AC-08.4:** abnormal finding은 rule ID, expected/observed 요약, evidence source를 보여주되 raw prompt/response, secret, wallet material을 노출하지 않는다.
- **P6-AC-08.5:** 역사 on-chain row와 synthetic row가 함께 조회될 경우 출처별 필터/label을 제공하고 서로 aggregate하거나 transaction link를 혼동하지 않는다.

#### P6-US-09 — Complete LOCAL E2E gate

검토자는 한 명령으로 실제 로컬 HTTP 서비스와 브라우저를 통해 정상 및 전체 abnormal catalog의 계약을 검증할 수 있어야 한다.

- **P6-AC-09.1:** root script `npm run test:e2e:local`은 격리 DB와 fake boundaries를 준비하고, API/service를 실제 loopback port에 실행하고, 인증된 API 호출과 실제 browser automation으로 12.5 전 항목을 실행·검증한 뒤 자기 namespace만 정리한다.
- **P6-AC-09.2:** 단순 source regex 또는 함수 직접 호출만으로 browser/API E2E를 대체할 수 없다. unit/integration test는 별도 gate로 유지한다.
- **P6-AC-09.3:** 각 scenario는 request, candidates/benchmark, quotes, decision, payment evidence, delivery 또는 terminal absence, persisted audit, feedback decision/record를 같은 `purchaseId`로 조회 가능해야 한다.
- **P6-AC-09.4:** API evidence는 최소 purchase list, purchase detail, audit alerts, agents/reputation provenance를 검증한다. UI evidence는 overview, purchases list/detail, alerts, agents와 status/source label을 검증한다.
- **P6-AC-09.5:** suite 종료 manifest는 commit, config hash, catalog version, seed, service versions, scenario별 oracle diff, event-chain verification, fake balance before/after, accepted/rejected transaction count, feedback count, outbound-call count, cleanup 결과, history pre/post snapshot을 기록한다.
- **P6-AC-09.6:** 하나의 scenario, status, rule, balance, transaction, UI/API assertion 또는 cleanup이 실패하면 전체 명령은 non-zero로 종료한다.

#### P6-US-10 — AWS deployment stop boundary

운영자는 실제 배포를 수행하지 않고도 다음 승인에 필요한 자료를 받아야 하며, 시스템은 승인 전 AWS mutation을 일으켜서는 안 된다.

- **P6-AC-10.1:** Phase 6에서 허용되는 infra 작업은 local static inspection, formatting, validation, rendered config 검토와 `docs/AWS_DEPLOY_READINESS.md` 작성뿐이다.
- **P6-AC-10.2:** 다음은 모두 금지한다: `terraform apply/import/destroy`, AWS CLI/SDK/Console의 create/update/delete, ECR push, Amplify connect/deploy, ECS service/task 실행, Secrets Manager secret 생성/값 변경, IAM/SG/VPC/ALB/WAF/Route53/CloudWatch 리소스 변경, MongoDB Atlas network/user/cluster 변경.
- **P6-AC-10.3:** `enable_services=false`는 안전한 no-op으로 간주하지 않는다. 현재 Terraform은 이 값에서도 cluster, log group, IAM role/policy, security group, task definition을 생성할 수 있으므로 어떤 workspace/flag에서도 `terraform apply`하지 않는다.
- **P6-AC-10.4:** deploy-readiness checklist는 최소 다음을 포함한다: LOCAL E2E manifest와 pass commit, immutable image/build artifact digest 계획, 서비스별 env/secret **이름만** 포함한 매트릭스, ap-northeast-2 topology, ECR/ALB/HTTPS/WAF/Atlas connectivity/alarms gaps, IAM least privilege 검토, PBLC/Base Sepolia 및 ERC-8004 주소/chain verification plan, retention-policy blocker, backup/rollback/runbook, observability/SLO, cost owner/budget, live provider external gate, exact future mutation commands와 예상 resource list, approver 및 승인 시점.
- **P6-AC-10.5:** 공개 AWS 배포는 raw sensitive payload의 indefinite retention을 재검토하고 명시적으로 승인하기 전까지 `BLOCKED`로 표시한다.
- **P6-AC-10.6:** LOCAL E2E와 checklist가 완료되면 자동화는 성공적으로 정지하고 `AWAITING_AWS_DEPLOYMENT_APPROVAL`을 보고한다. 배포 명령을 제안서에 적을 수는 있으나 실행하지 않는다.

### 12.5 Provisional configurable scenario catalog and exact E2E oracle

아래 catalog는 Phase 6 구현을 막지 않는 provisional baseline이다. `Q`는 signed quote amount, `B`는 user budget, `A`는 실제 synthetic transfer amount다. `PBLC Δ`는 buyer의 fake PBLC V2-compatible 6-decimal local-ledger delta다. 모든 항목은 `SYNTHETIC_LOCAL`이고 real Base Sepolia write가 `0`이어야 한다. `feedback`은 fake ERC-8004 accepted feedback이다.

| ID | Injection 및 선행조건 | terminal 상태 | 필수 verdict / rule IDs | balance·transaction·feedback oracle |
|---|---|---|---|---|
| `P6-N00-NORMAL` | 모든 hard constraint 통과, 최상 eligible 후보 선택, 설명 일치, exact quote/transfer/delivery | `PAYMENT_SETTLED` + `AUDITED_NORMAL` | `NORMAL`; risk/warning finding 없음 | buyer PBLC `-Q`, selected seller `+Q`; accepted transfer 1, rejected 0; feedback `100` 1개 |
| `P6-A01-BUDGET-IGNORED` | `B < Q`; 결제 guard 이후 synthetic evidence injection으로 unauthorized outflow를 표현 | `PAYMENT_SETTLED` + `AUDITED_RISK` | `AUD-BUDGET-EXCEEDED`, `AUD-HARD-FILTER-MISMATCH` | buyer PBLC `-Q`, seller `+Q`; accepted 1; 동일 injection이 production path에서는 결제 전 거부됨; seller feedback `100` 1개 |
| `P6-A02-BETTER-ELIGIBLE-EXCLUDED` | 두 후보 모두 eligible이고 동일 preset 기준 excluded 후보의 objective total score가 selected보다 큼; 후보가 부당하게 hard-filter/exclusion 처리됨 | `PAYMENT_SETTLED` + `AUDITED_RISK` | `AUD-ELIGIBLE-CANDIDATE-EXCLUDED`, `AUD-HARD-FILTER-MISMATCH` | buyer PBLC `-Q`, selected seller `+Q`; accepted 1; feedback `100` 1개 |
| `P6-A03-EXPLANATION-CONTRADICTS-SELECTION` | 저장된 점수/selected model과 explanation의 claimed winner 또는 비교 근거가 불일치 | `PAYMENT_SETTLED` + `AUDITED_RISK` | `AUD-EXPLANATION-SELECTION-MISMATCH` | buyer PBLC `-Q`, seller `+Q`; accepted 1; feedback `100` 1개 |
| `P6-A04-WRONG-AMOUNT` | actual local proof에서 `A != Q`, token/recipient는 일치 | `PAYMENT_MISMATCH_CONFIRMED` + `AUDITED_RISK` | `AUD-QUOTE-PAYMENT-MISMATCH`, `mismatchedFields=["amount"]` | buyer PBLC `-A`, seller `+A`; accepted 1, automatic retry 0; feedback `0` 1개 |
| `P6-A05-WRONG-TOKEN` | actual local proof token이 quote/PBLC V2 token과 다름, amount/recipient는 일치 | `PAYMENT_MISMATCH_CONFIRMED` + `AUDITED_RISK` | `AUD-QUOTE-PAYMENT-MISMATCH`, `mismatchedFields=["token"]` | buyer PBLC `0`, seller PBLC `0`; alternate fake token buyer `-Q`, seller `+Q`; accepted 1; feedback `0` 1개 |
| `P6-A06-WRONG-RECIPIENT` | actual local proof recipient가 quoted seller recipient와 다름 | `PAYMENT_MISMATCH_CONFIRMED` + `AUDITED_RISK` | `AUD-QUOTE-PAYMENT-MISMATCH`, `mismatchedFields=["recipient"]` | buyer PBLC `-Q`, selected seller `0`, wrong recipient `+Q`; accepted 1; feedback `0` 1개 |
| `P6-A07-DUPLICATE-OR-REUSED-NONCE` | 동일 purchase에 두 번째 payment attempt 및 동일 ERC-3009 nonce replay | `PAYMENT_SETTLED` + `AUDITED_RISK` | `AUD-DUPLICATE-PAYMENT-ATTEMPT`, `AUD-ERC3009-NONCE-REUSE`; 기존 event 중복이면 `AUD-DUPLICATE-PAYMENT-EVENT`도 포함 | buyer PBLC `-Q`, seller `+Q`; accepted 1, duplicate/replay rejected 1 이상; feedback `100` 정확히 1개 |
| `P6-A08-FACILITATOR-SUCCESS-NO-TRANSFER` | synthetic facilitator success claim/tx identity는 있으나 fake Base-Sepolia-proof verifier에 matching `Transfer`/`AuthorizationUsed`가 없음; real chain claim으로 표시 금지 | `RECONCILED_NO_TRANSFER` + `AUDITED_RISK` | `AUD-FACILITATOR-SUCCESS-WITHOUT-TRANSFER`, `AUD-PAYMENT-RECONCILED-NO-TRANSFER` | 모든 token balance `0` delta; accepted transfer 0; reserved budget 1회 해제; feedback `0` 1개 |
| `P6-A09-INCOMPLETE-RECONCILIATION-TERMINAL` | `PAYMENT_RECONCILIATION_REQUIRED`를 먼저 append하고 bounded local reconciliation이 no-transfer를 확정 | `RECONCILED_NO_TRANSFER` + `AUDITED_RISK` | `AUD-PAYMENT-RECONCILED-NO-TRANSFER`; 원본 reconciliation event 보존 | 모든 balance `0` delta; accepted transfer 0; reservation 1회 해제; feedback `0` 1개; 기존 역사 incomplete row 변화 0 |
| `P6-A10-SEMANTIC-WARNING` | deterministic rule은 정상이고 explanation에 advisory-only 불확실성이 있음 | `PAYMENT_SETTLED` + `AUDITED_WARNING` | `SEM-REQUEST-RATIONALE-UNCERTAIN`; deterministic risk 없음 | buyer PBLC `-Q`, seller `+Q`; accepted 1; feedback `100` 1개 |
| `P6-A11-CONFIRMED-PAYMENT-FAILURE` | selected seller/payment endpoint에 귀속되는 fake authoritative receipt가 revert/rejection을 확정 | `PAYMENT_FAILED` + `AUDITED_RISK` | `AUD-PAYMENT-FAILED` | 모든 balance `0` delta; accepted transfer 0; reservation 1회 해제; feedback `0` 1개 |

각 scenario의 API/UI assertion은 다음 공통 계약을 만족해야 한다.

1. API list/detail/alerts/agents가 표의 상태, exact rule IDs, `SYNTHETIC_LOCAL`, 동일 `purchaseId`, expected/observed evidence를 반환한다.
2. browser overview/list/detail/alerts/agents가 같은 상태와 source label을 표시하고 synthetic transaction을 BaseScan에 연결하지 않는다.
3. event hash chain 검증이 통과하며 하나의 `purchaseId`에 successful transfer는 최대 1개다.
4. persisted terminal `AUDITED`가 정확히 1개이고 fake feedback은 표의 수량과 값에 일치한다.
5. scenario 종료 후 canonical/역사 MongoDB 행과 기존 Base Sepolia/Permit2 evidence는 byte-equivalent projection/hash snapshot 기준으로 변하지 않는다.

### 12.6 Required verification suites and quality gate

다음 명령은 깨끗한 local checkout에서 모두 성공해야 한다. 누락된 root script는 Phase 6 implementation task에서 추가하되 기존 script의 의미를 약화하지 않는다.

1. `npm run lint` — Ruff와 strict mypy, repository lint
2. `npm test` — Python unit/integration, Node service tests, contract tests
3. `npm run test --workspace @pbl/dashboard` — dashboard unit/source-boundary tests
4. `npm run test:mongo:local` — isolated native MongoDB integration 및 cleanup
5. `npm run build --workspace @pbl/seller-service`
6. `npm run build --workspace @pbl/commerce-gateway`
7. `npm run build --workspace @pbl/dashboard`
8. `npm run test:e2e:local` — 12.5 전체 catalog의 HTTP+browser E2E
9. `docker compose -f infra/docker/compose.yml config`
10. `npm audit --omit=dev`
11. `git diff --check`

`npm run test:e2e:local`은 MongoDB 사용 불가, browser 실행 불가, port 충돌, cleanup 불완전, outbound call 감지 시 skip 또는 성공으로 처리하지 않고 fail closed한다. 현재 Mac의 Docker Desktop Mongo kernel 제한 때문에 native local Mongo test path를 사용할 수 있으나, 그 사실과 실행 backend를 manifest에 기록한다.

### 12.7 Security, privacy, and evidence non-negotiables

- **P6-NFR-SEC-01:** raw prompt/response는 기존 sensitive store 경계를 유지하고 structured scenario/audit DB 또는 report에 평문으로 복제하지 않는다. local test secret과 fake key도 log/snapshot에 남기지 않는다.
- **P6-NFR-SEC-02:** authentication, wallet/payment authorization, audit integrity, sensitive payload 경계에는 negative, replay, concurrency, restart test를 포함한다.
- **P6-NFR-SEC-03:** quote의 amount, 6-decimal token, recipient, expiry, agent identity, `purchaseId`를 authorization 직전과 receipt 검증 시 각각 확인한다.
- **P6-NFR-SEC-04:** `purchaseId`는 request, candidates, benchmark, quote, decision, authorization, submission, receipt, delivery, audit, feedback, UI/API projection의 canonical correlation key다. synthetic run 식별자는 이를 대체하지 않는다.
- **P6-NFR-AUD-01:** 모든 correction/reconciliation/audit/reputation record는 append-only다. update/delete/backfill로 이전 사실을 고치지 않는다.
- **P6-NFR-AUD-02:** API/UI는 확인되지 않은 tx, receipt, block, Transfer, AuthorizationUsed, feedback을 만들거나 암시하지 않는다. mock proof는 항상 `SYNTHETIC_LOCAL`이며 실제 chain explorer link를 갖지 않는다.
- **P6-NFR-AUD-03:** public-chain anomaly injection, replay submission, wrong-token/recipient/amount transaction, 실제 ERC-8004 feedback 발행을 포함한 모든 Phase 6 chain write는 금지한다.
- **P6-NFR-IDEM-01:** 하나의 user request는 성공 transfer 최대 1개라는 기존 invariant를 유지한다. synthetic mismatch는 anomaly evidence이지 production retry 허가가 아니다.
- **P6-NFR-PRIV-01:** indefinite sensitive-payload retention은 local MVP의 기존 상태로만 보존하며 public AWS deployment 전에 명시적으로 재결정한다.

### 12.8 Deliverables required before the AWS stop

Phase 6 implementation이 완료되려면 다음 산출물이 repository에 존재해야 한다. 경로의 세부 설계는 Phase 6 design revision에서 확정하되 의미는 변경하지 않는다.

1. versioned scenario catalog와 schema
2. isolated harness 및 fake payment/receipt/ERC-8004/provider boundaries
3. canonical status projection과 append-only terminal events
4. deterministic audit rules 및 feedback decision policy
5. reputation provenance/aggregation 및 selection snapshot
6. read-only dashboard evidence views
7. 정상+전체 abnormal catalog의 machine-readable LOCAL E2E manifest
8. `docs/AWS_DEPLOY_READINESS.md`

이 산출물 완료 직후 `AWAITING_AWS_DEPLOYMENT_APPROVAL`에서 정지한다. AWS나 공개 체인의 현재 상태를 바꾸는 산출물은 포함하지 않는다.

### 12.9 Phase 6 approval gate

Phase 6는 다음이 모두 참일 때만 requirements-complete로 판정한다.

- 정상 1개와 비정상 11개 scenario의 exact oracle가 API, UI, event chain, local balance, transaction count, audit rule, feedback count에서 모두 일치한다.
- 기존 successful/failed/incomplete MongoDB evidence와 PBLC V2/Permit2/Base Sepolia evidence가 변경되지 않았다.
- read path와 dashboard에 mutation/runner가 없다.
- feedback 자동화가 durable idempotency, recovery, provenance, constrained selection influence를 입증했다.
- type/lint/build/unit/integration/Mongo/browser E2E 및 보안 negative suite가 모두 통과했다.
- outbound real provider/RPC/facilitator/ERC-8004/AWS call과 public-chain write가 모두 0이다.
- deploy-readiness checklist가 완성됐고 실제 AWS 리소스는 생성·변경되지 않았다.

### 12.10 Non-blocking product decisions for the later team meeting

다음 결정은 Phase 6 구현을 막지 않는다. 팀 결정 전에는 아래 provisional default를 사용하고 config/policy version으로 교체 가능하게 한다.

| 논의할 결정 | Phase 6 provisional default |
|---|---|
| seller feedback 값과 귀속 기준 | 12.4 `P6-AC-05.2`의 objective `100/0/DEFER` mapping |
| semantic warning의 reputation 영향 | seller feedback을 낮추지 않음 |
| reputation aggregation | trusted matching-tag confirmed feedback의 산술 평균, 무증거 neutral `50` |
| trusted reviewer/client allow-list governance | deployment config의 명시적 allow-list; unknown client 제외 |
| reputation freshness/decay | snapshot freshness 표시, Phase 6에서는 시간 감쇠 없음 |
| reputation selection weight | 기존 preset 안의 reputation component만 사용; hard filter 후 적용 |
| warning/risk 사용자 문구 및 색상 | canonical enum은 고정하고 표현 문구만 UX 회의에서 조정 |
| reconciliation finality/retry 수치 | local fake의 결정론적 catalog 값; live 수치는 배포 전 운영 결정 |
| confirmed mismatch 후 refund/compensation | 자동 환불 없음; 운영 정책 결정 전 evidence 보존과 risk alert만 수행 |
| historical incomplete row의 운영 종결 | 자동 migration 없음; 개별 evidence와 operator 승인 필요 |
| synthetic demo data의 보관 기간 | ephemeral run DB는 suite 종료 시 삭제, manifest만 비민감 정보로 보관 |
| public AWS sensitive-payload retention | 미결정이며 실제 배포 blocker 유지 |

<!-- END PHASE6 REQUIREMENTS APPEND -->

## Planner self-check

- [x] 기존 requirement ID를 삭제·재사용·약화하지 않고 새 `P6-*` namespace만 추가했다.
- [x] 저장소 증거를 implemented와 missing으로 분리했다.
- [x] 요청된 9개 abnormal 유형을 모두 포함하고, 정상·warning·confirmed failure까지 exact oracle를 정의했다.
- [x] `pending audit`, `audited normal`, `audited warning`, `audited risk`, `payment failed`, `reconciled-no-transfer`, `payment-confirmation-unknown`을 서로 다른 canonical 값으로 고정했다.
- [x] ERC-8004 terminal 자동화, durable idempotency, 중복 방지, provenance 집계, hard constraint 이후의 선택 영향 요구사항을 포함했다.
- [x] fake/local payment 경계, public-chain write 금지, synthetic label, 재현성, exact cleanup, 역사 DB 보존을 포함했다.
- [x] 모든 scenario의 API/UI/rule/balance/transaction/feedback oracle와 type/lint/build/unit/integration/E2E gate를 포함했다.
- [x] Gemini/Nemotron live 호출을 external gate로 남기고 deterministic mock으로 Phase 6를 완결했다.
- [x] `enable_services=false`도 mutation 가능함을 반영해 모든 AWS apply/mutation을 금지하고 정확한 stop boundary를 정의했다.
- [x] 팀 회의의 미결 결정을 provisional default와 분리해 구현을 막지 않게 했다.
- [x] 의미상 대상 파일과 byte-preserving relay 절차를 명시했다.

**정확한 다음 Planner 단계:** 위 블록이 `aidlc-docs/inception/requirements.md`에 의미 변경 없이 릴레이된 뒤, Planner가 그 릴레이 결과를 기준으로 `aidlc-docs/inception/design.md`의 Phase 6 설계 개정 아티팩트를 작성한다.
