# 구현 로드맵

> **채팅 UX 교정 완료:** 단일 대화·하단 입력창·요청별 실행 동의·결과 유지·반복 요청 및 실패 재시도 구분. dashboard 38 tests, 브라우저 핵심 경계 11개 묶음, 격리 Mock 연속 구매 2건, lint/build 통과. [검증 원본](DECISION_OBSERVER_VERIFICATION.md#최신-채팅-ux-교정-검증).

> **최신 추가:** 채팅형 구매와 별도 Qwen 결정/감사 관찰 및 두 checkpoint 구현. [설계/체크리스트](DECISION_OBSERVER_PLAN.md), [검증/외부 승인](DECISION_OBSERVER_VERIFICATION.md). 신규 Anchor 배포·체인 쓰기는 아직 실행하지 않았다. 이전의 신규 Anchor 제외는 이 기능에 한해 대체된다.

> **2026-09-12 우선 상태:** AEGIS 신규 배포·초기 발행, 지정 PBLC DB 48문서 삭제 완료. 로컬 Qwen 자동 priority 및 AEGIS Mock E2E 검증은 [최신 전환 기록](AEGIS_QWEN_MIGRATION.md)을 따른다. 아래 PBLC 상태는 과거 단계다. AEGIS 외부 실결제는 아직 미검증이며 AWS는 진행하지 않는다.

> 최신 사용자 지시: 발표용 로컬 데모를 유지하면서, 사용자 소유 PBLC로 `/request`에서 실제 x402 결제를 시도할 수 있게 한다. 유료 Provider API·AWS 배포는 범위 밖이다. 제외한 추가 검증은 미해결 후속 과제이며 이번 완료 조건이 아니다.

## 현재 전환 — 2026-09-10 사용자 소유 PBLC / AA

- AA adapter·snapshot·세 점수 정책과 로컬 계약 준비: 검증 완료. 상세 측정값은 [검증 기록](AEGIS_VERIFICATION.md)을 따른다.
- 결제 런타임: `823f3bd` + `9744d8d`에서 재시작·중복 결제 경계 수정 및 독립 리뷰 완료.
- UI: `9f6e23f` 구현·집중 검증 완료. 원문 priority 감사 집중 리뷰 통과. 2026-09-10 발표용 데모의 세 Provider E2E·예산 초과·중복 결제5건, 402 불일치2건, dashboard build·기본 lint 및 최종 리뷰 통과.
- AWS 실제 배포는 진행하지 않는다. 사용자 소유 PBLC 계약 배포·초기 mint는 완료됐고, live 실행은 `/request` 동의와 서버 승인 플래그로만 실제 Facilitator 정산을 시도한다.
- 기존 PBLC V2 owner는 사용자 지갑이 아니다. 신규 지급 자산은 사용자 소유 PBLC `0xe750…ace8`이며, 과거 계약·거래는 읽기 전용으로 보존한다. [실제 PBLC 요청 흐름](LIVE_PBLC_REQUEST_FLOW.md)을 따른다.
- AA free endpoint를 읽어 확인했지만 세 고정 모델의 exact ID/slug mapping이 없었다. 현재 검증된 기본 경로는 fixture이며, live AA/API Provider 호출은 결제 흐름과 분리된 후속 작업이다.

아래 완료 목록은 당시 정책의 구현 이력이다. SIWE·평판·Anchor·독립 RPC 검증 등 과거 체크가 신규 AEGIS 실행에 포함된다는 뜻은 아니다. 신규 수용 조건은 `aidlc-docs/inception/tasks.md`를 따른다.

이 문서는 승인된 `aidlc-docs/inception/tasks.md`의 실행 상태만 추적한다. 세부 요구사항과 완료 조건은 tasks가 단일 원본이다.

## 완료

- [x] **1.1 재사용 코어와 신뢰 데이터 기반**
  - [x] 워크스페이스와 core/domain import 경계
  - [x] SIWE 인증과 owner/buyer wallet binding
  - [x] Evidence Repository와 append-only hash chain
  - [x] 민감 원문 암호화와 안전한 키 handoff
  - [x] focused unit/integration/security tests
- [x] **2.1 AI 추론 구매 도메인**
  - [x] request normalization·clarification·수동 benchmark snapshot
  - [x] deterministic hard filter·4개 weight preset·선택 설명
  - [x] Gemini/Nemotron 공통 seller 계약과 실제/mock provider adapter
  - [x] EIP-712 seller quote·1회 counteroffer·x402 payment-gate shell
  - [x] `REQUESTED → QUOTED → DECIDED` evidence E2E와 Buyer-side signature 검증
  - [x] Latest broad suite: Python 49, TypeScript 18, native Mongo 1, schema boundary

- [x] **Verification boundary 1 독립 리뷰** — `Approve Phase 3`
  - 리뷰 계약: `docs/reviews/verification-boundary-1.md`
  - 리뷰 결과: `docs/reviews/verification-boundary-1-result.md`
  - 재리뷰 패킷: `docs/reviews/verification-boundary-1-rereview.md`
  - 재리뷰 결과: `docs/reviews/verification-boundary-1-rereview-result.md`
  - 재재리뷰 패킷: `docs/reviews/verification-boundary-1-rereview-2.md`
  - 재재리뷰 결과: `docs/reviews/verification-boundary-1-rereview-2-result.md`
  - 세 번째 재리뷰 패킷: `docs/reviews/verification-boundary-1-rereview-3.md`
  - 세 번째 재리뷰 결과: `docs/reviews/verification-boundary-1-rereview-3-result.md`

## 로컬 완료

- [x] **3.1 결제·체인 검증·ERC-8004 로컬 신뢰 경계 (과거 V1 구현; 실행 경로 폐기)**
  - [x] 원자적 예산 예약·DecisionAuthorization·정확히 한 번 settlement
  - [x] x402 v2/Permit2 조건 결합·고정 nonce·replay 방지·reconciliation
  - [x] 독립 receipt/Transfer 검증과 MongoDB 원자적 spent/reserved 정산
  - [x] 6-decimal 자체 ERC-20·EvidenceAnchor 계약과 Foundry 테스트
  - [x] 환경 주입형 ERC-8004 identity/reputation adapter·self-feedback 방지
  - [x] fake-domain 구매도 같은 공통 payment claim 경계를 통과
  - [x] 제출 tx hash 결속·확정 revert만 실패 처리·hash 없는 모호 상태에서 결제 재생성 금지와 seller durable journal 복구
  - [x] buyer ETH 0 상태의 실제 Base Sepolia Permit2·x402 Facilitator 결제와 정확한 PBLC Transfer 영수증

- [x] **Verification boundary 2/3 독립 재리뷰** — `Approve local completion`, P0–P3 없음
  - 최종 리뷰 계약: `docs/reviews/verification-boundary-3.md`
  - 최종 리뷰 결과: `docs/reviews/verification-boundary-3-result.md`

- [x] **4.1 감사 대시보드·로컬 E2E·AWS handoff**
  - [x] SIWE, 체인 잔액, 거래 목록/상세, AI 선택 근거, 평판, 감사 경고
  - [x] 구매 에이전트 지갑은 내부 관리자 API로만 사전 배정하고 로그인 화면의 수동 주소 입력 제거
  - [x] 인증 SSE, stale/degraded 표시, 민감 필드 redaction
  - [x] audit bundle→ERC-8004 100/0 feedback→tx evidence
  - [x] AI 추론 renderer와 공통 shell 분리
  - [x] MongoDB/API/dashboard/Gemini Seller/Nemotron Seller/Gateway Compose와 비용 차단 Terraform handoff
  - [x] 모든 5개 애플리케이션 Docker 이미지 build
  - [x] authenticated `/run`, durable seller/payment/provider-attempt recovery, 전달-선택 무결성 감사
  - [x] broad suite: Python 71/1 skipped, Seller 29, Gateway 33, Solidity 6, native Mongo 1
  - [x] Base Sepolia 결제·ERC-8004 feedback·EvidenceAnchor 실거래 증거
  - [ ] 실제 provider/AWS 증거와 대시보드 화면 캡처 (외부 게이트)

- [x] **4.2 읽기 전용 감사 모니터 개편**
  - [x] 개요의 구매 실행 폼과 가짜 시뮬레이터 없이 PBLC·거래·감사·경고를 실제 API 데이터로 요약
  - [x] 제공 시안의 고밀도 감사 콘솔 구조를 현재 상세 화면과 반응형 UI에 적용
  - [x] production build와 실행 중인 로컬 HTTP 응답 검증 (인증 계정 거래 화면 캡처는 외부 게이트 유지)

- [x] **4.3 사용자 대시보드와 분리된 정상 거래 실험 실행기**
  - [x] `/experiments`를 사용자 메뉴·공용 셸에서 제거하고 실제 결제 고지·확인 후 고정 `0.1 PBLC` 정상 거래 실행
  - [x] 요청 생성 뒤 `purchaseId`를 동일 출처 탭 간 유지하고 실행 중 중복 제출 차단
  - [x] canonical `pbl_audit`에 결제·전달·감사 증거를 연결하고 읽기 전용 개요·상세에 자동 반영
  - [x] Base Sepolia receipt/Transfer, PBLC 잔액 변화, MongoDB 10-event hash chain 교차 검증

- [x] **5.1 구매 요청·감사 조회 경계 개편**
  - [x] `/request`에 요청·선택 예산·우선순위·명시적 결제 확인·동일 purchase 재개 구현
  - [x] `/`와 `/dashboard`를 읽기 전용으로 유지하고 `/experiments`를 `/request`로 리다이렉트
  - [x] Buyer Agent, Buyer/Seller SDK Wrapper, Payment Executor, Audit Evidence API 용어와 책임 갱신
  - [x] priority/weights/최고 점수/설명 일치 감사 규칙 추가

- [x] **5.2 PBLC V2 ERC-3009 전환 경로**
  - [x] 별도 비업그레이드 `DemoTokenV2`와 정상·오서명·시간·replay·잔액 부족 테스트
  - [x] seller 402, Payment Executor 서명/payload, Mongo nonce, Transfer+AuthorizationUsed 검증
  - [x] 전환 중 기존 Permit2 실거래·미완결 기록 보존
  - [x] 배포 전 예상 주소·가스만 계산하는 승인 차단 스크립트
  - [x] 사용자 승인 후 PBLC V2 배포와 0.1 PBLC verify/settle/replay 실거래
  - [x] 공식 x402 클라이언트와 동일한 ERC-3009 `validAfter=0` 적용 및 Facilitator 시뮬레이션 경계 검증
  - [x] 같은 UTC 날짜에 PBLC V1/V2 지갑 정책이 공존하는 Mongo 인덱스 마이그레이션

- [x] **5.3 ERC-3009 단일 신규 결제 경로 확정**
  - [x] `PAYMENT_TRANSFER_METHOD` 선택 설정과 Permit2 signer/challenge/payload 실행 분기 제거
  - [x] 신규 intent는 ERC-3009 nonce만 생성하고 PBLC V2만 사용
  - [x] 과거 Permit2 intent는 대시보드·감사 조회만 허용하고 execute/reconcile 거부
  - [x] 기존 Permit2 배포 스크립트·로컬 프로세스 중지와 기본 Compose·환경 예시의 PBLC V2 전환
  - [x] 최종 검증: lint/typecheck, Python 75 passed/1 skipped, Seller 30, Payment Executor 34, Solidity 11, native replica-set Mongo 1, dashboard production build

- [x] **5.4 중단 결제와 감사 미실행 상태를 사용자 화면에서 분리**
  - [x] `PAYMENT_RECONCILIATION_REQUIRED`를 `결제 확인 필요`로 표시하고 실제 진행 중인 작업처럼 보이던 문구 제거
  - [x] 감사 결과가 없는 거래는 `감사 미실행`으로 표시
  - [x] 정산 완료 lifecycle과 거래 해시가 모두 확인될 때만 `온체인 결제 확인`, 그 외에는 `예정 금액·결제 미확정`으로 구분

## 외부 게이트

- [x] 개인 비공개 GitHub 저장소 `codenameVien/ai-agent-payment-audit` 생성 및 SSH remote 연결
- [x] MVP 결제·감사 검증은 `PROVIDER_MODE=mock`으로 확정; 실제 provider/API key 입력은 최종 provider smoke까지 연기
- [x] Base Sepolia wallet·token·facilitator 실거래
  - [x] 전용 deployer/buyer/seller 지갑 생성, 공개 RPC 연결, x402.org v2 exact·EIP-2612 gas sponsorship 지원 확인
  - [x] PBLC EIP-2612 permit과 exact-amount gas-sponsored payment payload 로컬 검증
  - [x] deployer 지갑에 1회 계약 배포용 Base Sepolia ETH 준비
  - [x] DemoToken·EvidenceAnchor 배포, buyer에 1,000,000 PBLC 직접 mint, buyer writer 등록
  - [x] Gemini/Nemotron 판매 에이전트 ERC-8004 identity 등록과 seller wallet 결속
  - [x] buyer ETH 없이 실제 x402 결제 및 Transfer receipt 증거 확보
  - [x] 정상 감사 뒤 ERC-8004 `NewFeedback(9154, 100, ...)` 실거래
  - [x] MongoDB 11-event head를 EvidenceAnchor에 기록하고 12번째 evidence로 tx 결속
- [ ] 실제 Gemini/Nemotron provider API 응답 smoke
- [ ] AWS 리소스 생성·비용 발생 승인
- [ ] 공개 배포 전 민감 원문 보존/삭제 정책 재승인
