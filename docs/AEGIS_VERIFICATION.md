# AEGIS 전환 검증 기록

## 변경 전 기준선 — 2026-09-09

- 대상: 별도 작업 폴더 `/Users/vien/MyProjects/PBL-aegis`, 승인된 Phase 6 base `d084388` 위 설계 commit `44295ba`.
- 목적: 최종 통합 검증과 별개로, 전환 전 기존 회귀 테스트의 상태를 확보한다.
- `npm test`: exit 0. Python 313개 수집(실제 Mongo 연결이 필요한 8개 skip), Seller 30/30, Payment Executor 88/88, schema 검사 통과, Foundry 11/11.
- `npm test --workspace @pbl/dashboard`: 4/4 통과.
- `npm run lint`: ruff, mypy(기존 47 source files), 세 workspace TypeScript noEmit 통과.
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

## AEGIS 계약 준비 — `d889363`

- Coder Opus: 기존 계약 포함 Foundry 26/26, 오프라인 planner Node 테스트 11/11 통과.
- Reviewer Astra Light 직접 재검증: AEGIS 계약 15/15, planner 11/11 통과. 기존 PBLC 소스 diff 없음.
- 리뷰에서 승인 패킷 제시 순서의 문구를 수정한 후 approve. 코드 수정이 아니므로 동일 테스트를 다시 실행하지 않았다.
- 외부 네트워크가 차단된 프로세스에서 planner 실행 성공을 Coder가 확인했다.
- 실제 배포 지갑·nonce·예상 주소·라이브 가스는 미확정이다. 오프라인 샘플은 실제 배포 승인 자료가 아니다.
- P6-03 보존 중간 재검사: 25개 파일 해시 동일, 원래 HEAD 동일.

## Mongo 테스트 러너 — `8315830`

- `node --test scripts/test_mongo_runner.test.mjs`: 오케스트레이터 직접 8/8 통과. Coder의 별도 반복 실행도 8/8 통과.
- 점유 포트 거부, 소유 PID 확인, 기동 실패, 잘못된 서버 PID, pytest 실패 전파, 정상 정리, 포트 선택, SIGINT 정리를 확인했다.
- 이 테스트는 명령 스텁과 임시 loopback 리스너를 사용한다. 실제 MongoDB 저장 검증과 구분하며 사용자 DB에 접속하지 않았다.
- 테스트가 생성한 임시 파일과 프로세스는 정리됐다. 기존 P6-03 파일 25개 해시도 다시 동일함을 확인했다.
- 실제 Mongo 통합 검증은 AA/런타임 구현 안정화 뒤 실행한다.
