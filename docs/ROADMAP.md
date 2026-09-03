# 구현 로드맵

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

- [x] **3.1 결제·체인 검증·ERC-8004 로컬 신뢰 경계**
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
