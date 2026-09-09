# AEGIS 구현 작업 — 2026-09-09

사용자 확정 요구로 로컬 구현 승인됨. Planner/Reviewer Astra Light, Coder Opus. 구현 worktree `/Users/vien/MyProjects/PBL-aegis`, branch `feature/aegis-aa-v1`, base d084388. 기존 `/Users/vien/MyProjects/PBL-coder` dirty P6-03은 그대로 둔다.

- packet-count: 3
- review-boundaries: AA/선택 증거, 결제/역사 보존, UI/최종 E2E 세 경계 focused independent review.
- broad-suite-count: 최종 1회; 이후 변경이 관련 증거를 무효화할 때만 이유를 기록해 재실행.
- baseline: Standard. scoped high-assurance: 결제 키·중복·조건 결속과 API/감사 무결성. exit: 해당 negative/concurrency/history 검증 통과.
- checkpoint: milestone active 90분 또는 관측 가능한 300k tokens에서 재계획 점검. 기존 정책의 active 3시간/750k continue-past 경계는 orchestration에서 관리한다. 같은 문제 두 fix cycle 이후 재분해한다. 2026-09-09 사용자가 예산 연장 질문에 승인하여 남은 로컬 Mock 결제 연결·UI·전체 E2E 완료까지 연장했다. AWS 실제 배포는 아직 원하지 않는다고 재확인했으며, 토큰 배포·자산 이동·실제 결제는 별도 승인 경계다.
- 순서: 설계 문서 수정 → 영향 범위 확정 → 아래 구현 → 검증 → 구조도·기록 갱신. 문서별 추가 승인 단계 없음.

## AEGIS-01 — AA snapshot과 가격/선택/감사 계약

- [ ] 요구/설계의 신규 정책을 canonical에 적용하고 과거 내용을 역사로 보존한다.
- [ ] 공식 AA adapter, pagination, snapshot/evidence API 저장, explicit catalog mapping, fixture/live provenance 구현.
- [ ] 네 priority, 토큰 추정 기록, decimal ceiling, hard filters, 세 점수, tie-break, version discriminator 구현.
- [ ] 신규 결정 감사 재계산과 과거 policy reader를 분리한다.
- Done when: 누락/null/잘못된 mapping/중복 ID/페이지 반복/혼합 index/NaN/Infinity/0/음수 중단, 경계 올림·시간 변환·명시 priority·충돌 문구·필터 분모·tie 테스트, Mongo snapshot round trip, 기존 history read 회귀 통과.
- likely scope: buyer-audit-api domains/ai_inference, core schema/events/audit, adapters/repository, packages schemas, fixtures/tests.

## AEGIS-02 — Mock Gateway, 결제 실행 모듈, local identity, AEGIS 준비

- [ ] OpenAI/Claude/Gemini Mock Gateway의 정확한 model mapping과 402 생성/결제 후 결과 반환 구현. 협상·counteroffer 신규 composition 제외.
- [ ] 동일 snapshot/decision/amount/token/recipient binding 재계산, ERC-3009 서명 경계, Facilitator verify/settle 응답 근거, 중복/timeout 복구 유지.
- [ ] SIWE 없이 단일 local identity로 실행하되 내부 API 보호/원문 암호화/키 격리 유지. ERC-8004/Anchor/RPC 검증을 신규 runtime에서 제거하고 과거 reader 보존.
- [ ] 별도 AEGIS 계약/fixture/plan-only 설정과 PBLC immutable 증거 작성. 배포·이동·실결제 미실행.
- Done when: 세 provider HTTP 구매→402→verify/settle→응답 E2E, 악성 client amount/terms/snapshot 치환 거부, concurrent duplicate/nonce replay/timeout/전달 실패, API 미인증 거부/키 비노출, PBLC 역사 읽기, AEGIS contract tests 통과.
- likely scope: seller-service, commerce-gateway, buyer API composition/payment, infra/contracts, local launch/env templates, focused integration tests.

## AEGIS-03 — 화면, 개정 Phase 6, 최종 handoff

- [ ] `/request` 신규 priority/모의 동의와 동일 purchaseId 재개; 읽기 dashboard에서 신규 세 요인/AA 출처/Facilitator 한계와 과거 policy 표시.
- [ ] 기존 dirty P6-03 diff를 검토해 isolation/read-only/outbound tripwire 등 새 구조에 맞는 부분만 재사용한다. blind merge 금지.
- [ ] 개정 Phase 6 시나리오: 세 provider 정상 구매; missing AA; mapping/version 불일치; no eligible; 점수/가중치 변조; 402 mismatch; concurrent duplicate; Facilitator timeout; 성공 후 응답 실패; history read zero-write; mock/live 혼동 방지. 모든 HTTP E2E oracle에 purchaseId/증거/settle count를 검사한다.
- [ ] 전체 `npm test`, `npm run lint`, 각 workspace typecheck(명령 확인), `npm run test:mongo:local`, dashboard build 및 E2E 실행. 외부 DB 사용 시 기존 기록을 건드리지 않으며 독립 test DB만 사용.
- [ ] README/README.en/설계·구조도/ROADMAP/AI-DLC state/audit/project log/HANDOFF를 검증 결과에 맞게 갱신.
- Done when: 전체 required suite 결과와 skip/failure 사유, 로컬 Mongo 실제 검증 vs Mock Provider/Facilitator vs AA fixture/live, 남은 외부 게이트가 명시되고 AWS 실제 배포 직전 정지한다.

## 최종 정지

로컬 구현을 외부 키/토큰 승인 없이 계속 완료한다. AEGIS 실제 배포 승인과 AWS 실제 배포는 별도 경계다. AA 키 없으면 실제 AA 검증 미완료를 표기한다. 공개 다중 사용자 인증/접근 제어와 원문 보존 정책을 AWS 전 결정 사항으로 남긴다. 실제 provider 키를 연결하지 않는다.
