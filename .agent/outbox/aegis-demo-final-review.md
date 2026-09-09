# Presentation demo — bounded final functional review

2026-09-10. `.agent/DEMO_SCOPE.md`의 최신 축소 기준으로 Terra 결과와 실제 harness/runtime assertions를 대조했다. suite를 재실행하지 않았으며 실행 성공 수치는 Terra 기록이고, main이 집중 E2E 5건을 독립 재확인한다.

## 기준별 근거

- 3 Provider: phase6 harness는 각 provider를 허용 목록으로 지정하고 HTTP run 성공, 정확한 provider model/version, DECIDED 금액 일치 및 INTENT/AUTHORIZED/SETTLED/DELIVERED/AUDITED 각 1건을 검사한다. Terra 집중 실행 3건 PASS 기록과 부합한다.
- 예산 초과: 모든 후보 미달 예산에서 409/no_eligible_candidate, DECIDED 및 PAYMENT_SETTLED 0건을 검사한다. 집중 실행 PASS 기록과 부합한다.
- 402 불일치: 기존 runtime test는 PAYMENT-REQUIRED 금액 변조 시 X402BindingError, settlement 0 및 CLAIMED 상태를 검사한다. 별도 payload/decision 불일치 test도 402/settlement 0을 확인한다. Terra 2건 PASS 기록과 부합한다.
- 중복 성공: 동일 purchaseId 동시 run의 최소 1개 성공 및 claimed/settled/delivered 각각 1건을 검사한다. Terra 집중 실행 PASS 기록과 부합한다.
- build/lint: Terra는 commerce build, dashboard production build, 루트 lint 성공을 기록했다. 루트 lint script 구성은 기록의 Ruff/mypy/3개 TypeScript 검사와 일치한다. 이 리뷰에서 재실행하지 않았다.

## 최종 보고 표현 정정

Terra 결과의 “Provider gateways, facilitator, payment execution ... were local mocks”는 결제 실행 자체를 stub으로 오해하게 한다. 정확한 표현은 **Gateway/결제 실행 모듈은 실제 일반 코드로 실행하고, 실행마다 생성한 임시 로컬 개인키로 실제 ERC-3009 서명을 생성·검사했으며, Provider 응답·AA 데이터·Facilitator 정산은 Mock/fixture**다. `main.ts`는 AegisPaymentExecutor/AegisAuthorizationSigner를 구성하고 runner는 임시 키를 executor child에만 전달한다. MongoDB는 실제 owned disposable replica set이다. `x402mock:`는 실제 블록체인 거래가 아니다. 이 표현을 최종 보고에서 사용해야 한다.

## 범위/판정

축소된 기능 검증 범위에서 구체적 blocker는 없다. 이전 Python outbound 전체 계측, 역사 zero-write 전체 회귀, Mongo 실패 cleanup stress 발견은 미해결/보류로 유지하며 이번 승인이 해결을 의미하지 않는다. 외부 Provider/AA API, 토큰 배포·자산 이동·실제 테스트넷 결제·AWS 배포는 검증/실행한 것으로 주장하지 않는다. 문서 최소 갱신은 main 최종 인계 범위다.

VERDICT approve
