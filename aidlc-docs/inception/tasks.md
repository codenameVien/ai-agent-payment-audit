# AEGIS 구현 작업 — 2026-09-09

## 현재 실행 계약 우선 — 2026-09-10

사용자가 목표를 발표용 로컬 데모로 축소했다. `.agent/DEMO_SCOPE.md`가 아래 기존 완료 조건·assurance·Opus 배정보다 우선한다. Coder는 Terra(`gpt-5.6-terra`), Planner/Reviewer는 Astra Light다. 이전 구현/검증 이력과 미커밋 변경은 보존하며 새 packet이나 추가 리뷰 단계를 만들지 않는다.

남은 완료 기준은 세 Mock Provider 각각의 요청→AA 선택→Mock x402 결제→결과→감사 E2E, 예산 초과 결제 전 중단, 402 조건 불일치 중단, purchaseId별 중복 성공 결제 방지, dashboard build·기본 lint, README 양 언어·구조도·project log·handoff 최소 갱신과 실제/모의/미검증 구분 보고다. 필요한 기존 focused tests만 실행한다.

전체 Python outbound 계측, 광범위 역사 zero-write 회귀, Mongo 실패 cleanup 스트레스, broad suite 반복, 추가 hardening은 유예한다. 아래 미완료 체크박스를 억지로 완료 표시하지 않으며 이번 데모의 차단 조건으로 재활성화하지 않는다. 기존 데이터는 보존하고 소유한 임시 저장소만 사용한다. 실제 AA/Provider 호출·토큰 배포·자산 이동·실제 테스트넷 결제·AWS 배포는 수행하지 않는다.

사용자 확정 요구로 로컬 구현 승인됨. Planner/Reviewer Astra Light, Coder Opus. 구현 worktree `/Users/vien/MyProjects/PBL-aegis`, branch `feature/aegis-aa-v1`, base d084388. 기존 `/Users/vien/MyProjects/PBL-coder` dirty P6-03은 그대로 둔다.

- packet-count: 3
- review-boundaries: AA/선택 증거, 결제/역사 보존, UI/최종 E2E 세 경계 focused independent review.
- broad-suite-count: 최종 1회; 이후 변경이 관련 증거를 무효화할 때만 이유를 기록해 재실행.
- baseline: Standard. scoped high-assurance: 결제 키·중복·조건 결속과 API/감사 무결성. exit: 해당 negative/concurrency/history 검증 통과.
- checkpoint: milestone active 90분 또는 관측 가능한 300k tokens에서 재계획 점검. 기존 정책의 active 3시간/750k continue-past 경계는 orchestration에서 관리한다. 같은 문제 두 fix cycle 이후 재분해한다. 2026-09-09 사용자가 예산 연장 질문에 승인하여 남은 로컬 Mock 결제 연결·UI·전체 E2E 완료까지 연장했다. AWS 실제 배포는 아직 원하지 않는다고 재확인했으며, 토큰 배포·자산 이동·실제 결제는 별도 승인 경계다.
- 순서: 설계 문서 수정 → 영향 범위 확정 → 아래 구현 → 검증 → 구조도·기록 갱신. 문서별 추가 승인 단계 없음.

## AEGIS-01 — AA snapshot과 가격/선택/감사 계약

- [x] 요구/설계의 신규 정책을 PBL-aegis 작업 브랜치에 적용하고 과거 내용을 역사로 보존했다. 원본 PBL은 d084388 그대로이며 통합 대기 상태다.
- [x] 공식 AA adapter, pagination, snapshot/evidence API 저장, explicit catalog mapping, fixture/live provenance 구현.
- [x] 네 priority, 토큰 추정 기록, decimal ceiling, hard filters, 세 점수, tie-break, version discriminator 구현.
- [x] 신규 결정 감사 재계산과 과거 policy reader를 분리한다.
- **Status:** `e67e0b0` 완료, `.agent/outbox/aegis-01-review.md` scoped review APPROVE. 검증: Python non-Mongo 432, 실제 격리 Mongo 11, schema 8 통과; Ruff 및 mypy 57 files 통과. 리뷰 focused suite 94 통과. 이는 packet01 로컬 검증이며 runtime02/전체 E2E/실제 AA API 완료를 뜻하지 않는다.
- **검증 한계:** keyword priority의 원문 재도출은 아직 완전 검증하지 못하므로 감사에 CAUTION을 유지한다. 명시 priority와 저장된 근거 일관성 검사를 원문 기반 분류 전체 보증으로 표현하지 않는다.
- Done when: 누락/null/잘못된 mapping/중복 ID/페이지 반복/혼합 index/NaN/Infinity/0/음수 중단, 경계 올림·시간 변환·명시 priority·충돌 문구·필터 분모·tie 테스트, Mongo snapshot round trip, 기존 history read 회귀 통과.
- likely scope: buyer-audit-api domains/ai_inference, core schema/events/audit, adapters/repository, packages schemas, fixtures/tests.

## AEGIS-02 — Mock Gateway, 결제 실행 모듈, local identity, AEGIS 준비

- **Status:** Opus 구현 진행 중. 계약 하위 작업 `d889363`은 완료·리뷰됐으며 별도 packet으로 세지 않는다. 아래 runtime/결제 E2E 완료 조건은 아직 대기한다.
- [ ] OpenAI/Claude/Gemini Mock Gateway의 정확한 model mapping과 402 생성/결제 후 결과 반환 구현. 협상·counteroffer 신규 composition 제외.
- [ ] 동일 snapshot/decision/amount/token/recipient binding 재계산, ERC-3009 서명 경계, Facilitator verify/settle 응답 근거, 중복/timeout 복구 유지.
- [ ] SIWE 없이 단일 local identity로 실행하되 내부 API 보호/원문 암호화/키 격리 유지. ERC-8004/Anchor/RPC 검증을 신규 runtime에서 제거하고 과거 reader 보존.
- [x] 별도 AEGIS 계약/fixture/plan-only 설정과 PBLC immutable 증거 작성. 배포·이동·실결제 미실행. (`d889363`)
- Done when: 세 provider HTTP 구매→402→verify/settle→응답 E2E, 악성 client amount/terms/snapshot 치환 거부, concurrent duplicate/nonce replay/timeout/전달 실패, API 미인증 거부/키 비노출, PBLC 역사 읽기, AEGIS contract tests 통과.
- likely scope: seller-service, commerce-gateway, buyer API composition/payment, infra/contracts, local launch/env templates, focused integration tests.

## AEGIS-03 — 화면, 개정 Phase 6, 최종 handoff

- **Status:** 대기. 격리 Mongo runner 보완 `8315830` 및 해당 테스트 8개 통과는 검증 기반 준비이며 packet03/E2E 완료가 아니다. 사용자 승인 예산 연장 범위 안에서 남은 로컬 구현과 아래 전체 검증을 계속한다. AWS 배포는 현재 진행 대상이 아니다.
- [ ] `/request` 신규 priority/모의 동의와 동일 purchaseId 재개; 읽기 dashboard에서 신규 세 요인/AA 출처/Facilitator 한계와 과거 policy 표시.
- [ ] 기존 dirty P6-03 diff를 검토해 isolation/read-only/outbound tripwire 등 새 구조에 맞는 부분만 재사용한다. blind merge 금지.
- [ ] 개정 Phase 6 시나리오: 세 provider 정상 구매; missing AA; mapping/version 불일치; no eligible; 점수/가중치 변조; 402 mismatch; concurrent duplicate; Facilitator timeout; 성공 후 응답 실패; history read zero-write; mock/live 혼동 방지. 모든 HTTP E2E oracle에 purchaseId/증거/settle count를 검사한다.
- [ ] 전체 `npm test`, `npm run lint`, 각 workspace typecheck(명령 확인), `npm run test:mongo:local`, dashboard build 및 E2E 실행. 외부 DB 사용 시 기존 기록을 건드리지 않으며 독립 test DB만 사용.
- [ ] README/README.en/설계·구조도/ROADMAP/AI-DLC state/audit/project log/HANDOFF를 검증 결과에 맞게 갱신.
- Done when: 전체 required suite 결과와 skip/failure 사유, 로컬 Mongo 실제 검증 vs Mock Provider/Facilitator vs AA fixture/live, 남은 외부 게이트가 명시되고 AWS 실제 배포 직전 정지한다.

## 최종 정지

로컬 구현을 외부 키/토큰 승인 없이 계속 완료한다. AEGIS 실제 배포 승인과 AWS 실제 배포는 별도 경계다. AA 키 없으면 실제 AA 검증 미완료를 표기한다. 공개 다중 사용자 인증/접근 제어와 원문 보존 정책을 AWS 전 결정 사항으로 남긴다. 실제 provider 키를 연결하지 않는다.
# 2026-09-12 현재 작업

[Decision Observer 계획](../../docs/DECISION_OBSERVER_PLAN.md)의 실행 체크리스트로 이번 채팅/관찰/체크포인트 작업을 추적한다. 실제 배포 및 쓰기는 별도 승인 경계.
