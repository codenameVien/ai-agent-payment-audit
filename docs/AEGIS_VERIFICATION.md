# AEGIS 전환 검증 기록

## 변경 전 기준선 — 2026-09-09

- 대상: 별도 작업 폴더 `/Users/vien/MyProjects/PBL-aegis`, 승인된 Phase 6 base `d084388` 위 설계 commit `44295ba`.
- 목적: 최종 통합 검증과 별개로, 전환 전 기존 회귀 테스트의 상태를 확보한다.
- `npm test`: exit 0. Python 313개 수집(실제 Mongo 연결이 필요한 8개 skip), Seller 30/30, Payment Executor 88/88, schema 검사 통과, Foundry 11/11.
- `npm test --workspace @pbl/dashboard`: 4/4 통과.
- 이 기준선은 신규 AEGIS 구현 완료나 새로운 흐름의 E2E 통과를 뜻하지 않는다.
- 기존 테스트에 websockets/Starlette deprecation 및 일부 sync test의 asyncio marker 경고가 있다.
- 별도의 Mongo 검증과 신규 HTTP/UI E2E는 구현 후 실행한다. 기존 사용자 DB·거래는 검증 대상 쓰기 저장소로 사용하지 않는다.

## 계획 리뷰

- Astra Light(`gpt-6-astra`, low) 독립 계획 리뷰: approve. 구현 검토와는 구분한다.
- 증거: `.agent/outbox/aegis-plan-review.md`.

## 외부 실행 경계

- 실제 AA 인증 API 응답: 아직 검증하지 않음.
- Provider API: 세 종류 모두 Mock 대상으로 구현하며 실제 키는 연결하지 않음.
- 신규 AEGIS 계약: 배포 전 승인 필요. 실제 배포·자산 이동·테스트넷 결제 미실행.
- AWS 실제 배포 미실행. 공개 다중 사용자 인증/접근 제어는 별도 결정 사항.
