## Summary

- 배경: 폼 속 채팅 초안과 미전송 경고가 사용자가 원한 챗봇 UI·반복 구매를 방해했다.
- 구현: 대화 중심 /request, 하단 입력창, 메시지별 불변 확인 카드와 명시 실행 동의, 대화 안 결과·실패·거래 링크.
- 완료 후 동일 문구도 새 요청으로 실행한다. 실패 재시도는 같은 purchaseId, 동시 클릭은 한 create/run으로 제한한다. 결제 전 실패와 정산 미확정은 구분한다.

## Test plan

- [x] Node26 dashboard 38/38 tests
- [x] 브라우저 핵심 11개 fixture 묶음, 실제 API 쓰기 없음
- [x] 실제 격리 HTTP/Mongo·로컬 Qwen + Mock 연속 구매 2건, 각 정산 1·checkpoint 2·감사 기록, 대화 화면 유지
- [x] 전체 lint·타입 검사·dashboard build
- [x] 모바일 390×844, 설정 확장 시 입력창 노출 및 가로 overflow 없음
- [ ] 광범위 backend/계약 재검증·실결제·새 Anchor·AWS·유료 Provider: 이번 범위 밖, 실행하지 않음

## Core-flow review

사용자 지정대로 Mock 데모 핵심 사용자 흐름만 blocker로 평가했다. Terra 구현 후 주 작업에서 코드 및 독립 브라우저 동작을 대조했다. 결제 전 실패도 새 요청을 막던 blocker를 발견해 안전한 미시작/전송 없음과 미확정 상태를 구분하도록 수정했다. HTTP200 미정산을 성공으로 오표시하지 않고 pending을 보존한다. 최종 핵심 blocker 없음.

기존 사용자 DB·지갑 설정·다른 worktree 변경 없음. 테스트 ID·Mock/실제 범위·생략 목록은 [검증 기록](../DECISION_OBSERVER_VERIFICATION.md)의 최신 절이 원본이다.
