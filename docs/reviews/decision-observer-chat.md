# Decision observer / chat review

## Summary

- 배경: 사용자가 구매 결정 및 감사의 변조 대조 기준점과 별도 로컬 Qwen 관찰을 요청했다. 채팅 메시지와 결제 실행은 분리해야 한다.
- 구현: 채팅 초안/동의 경계, Python 이벤트 관찰/두 prefix, protected checkpoint API, TS 격리 Anchor writer 및 live 증거 재대조, 명확한 Mock UI.
- 독립 리뷰: Terra 구현 레인 간 교차 검토 및 오케스트레이터 검증. 요청대로 Mock 핵심 사용자 흐름만 blocker 평가. 정확한 Astra Light alias는 현재 도구에 없어 동일 모델이라고 주장하지 않는다.

## Findings resolved

1. camelCase TS와 snake_case Python 응답 불일치: Pydantic aliases로 통일하고 실제 HTTP/Mongo E2E 확인.
2. 보내지 않은 채팅 입력이 구매에서 누락되는 문제: 미전송 초안 존재 시 실행 차단, 초기 가짜 사용자 메시지 제거.
3. preview3100 origin 차단: 임시 local stack에 정확한 loopback origin 추가. live 전역 정책은 유지.
4. live 재실행이 Mongo의 저장 proof만 신뢰하는 문제: 외부 Anchor의 purchaseId/count/hash/tx와 다시 대조하고 audit anchor 전에 decision 재확인. Mock는 RPC 없음.
5. 로컬 Qwen 관찰 오류 원인 추적: 고정 진단 코드, 별도 bounded timeout, 한국어 UTF-8 입력으로 조정. 실제 모델 응답 여부는 최종 E2E 결과를 별도로 보고한다.

최종 독립 판정: 위 수정 후 Mock 핵심 흐름 blocker 없음. 실제 배포/브로드캐스트는 승인 전 외부 게이트이며 완료로 평가하지 않았다.

## Test plan

- [x] TypeScript gateway 130 tests
- [x] Python checkpoint/observer focused 6 tests
- [x] Dashboard 36 tests / build / lint / typecheck
- [x] Solidity EvidenceAnchor 2 local tests
- [x] Schema pricing 8 cases and scripts 24 tests
- [x] Mock3Provider E2E 및 replay 단일 정산
- [x] 브라우저 메시지 전송 API 쓰기 0 / mobile overflow 없음
- [x] 실제 로컬 Qwen 3Provider E2E 및 브라우저 명시 실행(create/run) 결과 이동
- [ ] 실제 새 Anchor 배포·체인 쓰기 (별도 승인)
- [ ] 실제 AEGIS 정산·유료 Provider·AWS (이번 작업에서 하지 않음)

실제 Qwen 및 최종 브라우저 실행 결과·known limitations: [검증 기록](../DECISION_OBSERVER_VERIFICATION.md).
