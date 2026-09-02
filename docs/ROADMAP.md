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
  - [ ] 실제 Base Sepolia deploy·Permit2 allowance·CDP Facilitator smoke (외부 게이트)

- [x] **Verification boundary 2/3 독립 재리뷰** — `Approve local completion`, P0–P3 없음
  - 최종 리뷰 계약: `docs/reviews/verification-boundary-3.md`
  - 최종 리뷰 결과: `docs/reviews/verification-boundary-3-result.md`

- [x] **4.1 감사 대시보드·로컬 E2E·AWS handoff**
  - [x] SIWE, 체인 잔액, 거래 목록/상세, AI 선택 근거, 평판, 감사 경고
  - [x] 인증 SSE, stale/degraded 표시, 민감 필드 redaction
  - [x] audit bundle→ERC-8004 100/0 feedback→tx evidence
  - [x] AI 추론 renderer와 공통 shell 분리
  - [x] MongoDB/API/dashboard/Gemini Seller/Nemotron Seller/Gateway Compose와 비용 차단 Terraform handoff
  - [x] 모든 5개 애플리케이션 Docker 이미지 build
  - [x] authenticated `/run`, durable seller/payment/provider-attempt recovery, 전달-선택 무결성 감사
  - [x] broad suite: Python 68, Seller 29, Gateway 29, Solidity 4, native Mongo 1
  - [ ] 실제 provider/Base Sepolia/ERC-8004/AWS 증거와 화면 캡처 (외부 게이트)

## 외부 게이트

- 팀 GitHub 저장소 URL 확정 및 SSH remote 연결
- 실제 provider/API key 입력
- Base Sepolia wallet·token·facilitator 실거래
- AWS 리소스 생성·비용 발생 승인
- 공개 배포 전 민감 원문 보존/삭제 정책 재승인
