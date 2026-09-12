# 채팅 요청·Decision 감사·온체인 체크포인트

## 확정 범위 — 2026-09-12

사용자가 원문 MongoDB + 해시 온체인, 판단 확정 후 결제 전 및 감사 후 두 체크포인트,
별도 로컬 Qwen 감사, 채팅형 구매를 승인했다. 숨겨진 사고과정이 아닌 입력·후보·점수·선택·행동 증거를 기록한다.
제품 품질 high, 검증은 기존 Mock 데모 핵심 흐름 기준. 키 격리·중복 결제·증거 무결성은 기존 보호를 유지한다.
실제 Anchor 배포/온체인 쓰기, AWS, 유료 Provider 호출은 이번 자동 실행 범위 밖이다.

## 요구사항 및 설계

1. /request는 대화 초안을 작성한다. 메시지 전송은 구매·서명·결제를 만들지 않는다.
   대화 초안과 예산/priority를 확인하고 기존 명시 실행 동의로만 purchaseId 생성/실행.
   편집/새 대화 시 기존 동의를 해제. 결과·실패·재실행은 기존 안전한 pending 경계를 유지.
2. 별도 Qwen 관찰자는 구매 결정 시점과 실행/규칙감사 완료 시점에 이벤트 기반으로 실행한다.
   priority 분류 역할과 분리된 시스템 프롬프트·구조화 결과·모델/대상 hash/이벤트 ID를 보존.
   매 토큰을 감시하는 daemon이 아니라 구매 이벤트 체크포인트 감시다.
   의심 사유는 자문이며 고정 규칙·결제 권한을 덮어쓰지 않는다. 오류/타임아웃은 미완료로 표시.
   규칙 감사와 LLM 검토를 구분하고 원문/키/자유문 보안 경계를 지킨다.
3. 판단 및 감사 완료 각각 verified event prefix(eventCount, headHash)에 결속된 체크포인트를 만든다.
   코드가 임의 hash를 쓰지 않고 감사 증거 API가 확인한 prefix를 별도 키 보유 서비스가 읽는다.
   Mongo 접근은 감사 증거 API만. 일반 코드가 Solidity EvidenceAnchor에 제출한다.
   기록은 phase(decision/audit), purchaseId, 대상 hash/count, mode, 상태, 실제일 때 tx/contract/chain을 보존.
   Mock 성공을 온체인 확인으로 표시하지 않는다. off/미설정은 미기록으로 표시.
   live 체크포인트가 실패/모호하면 결제 전에는 멈추고, 결제 후에는 재결제하지 않고 복구만 한다.
   동일 phase 재실행은 저장된 결과 반환; 내용이 달라지면 충돌. 이후 감사가 자신의 관찰 이벤트를 무한 재감사하지 않는다.
4. 온체인은 최초 기록의 진실성/삭제 원문 복구를 보장하지 않는다. 원문 해시 비교·누락 탐지 기준이다.
   결제 독립 RPC 검증은 복원하지 않는다. Anchor 쓰기의 확인은 Anchor 증거에만 한정한다.
5. 과거 기록·다른 worktree·AEGIS 자산 설정 보존. 이번 작업에서 DB 초기화/민팅/송금 금지.

## 영향 범위 및 순서

- Python: 관찰 adapter/service, 증거 이벤트·회귀 호환, 구매 orchestration·내부 체크포인트 API.
- TypeScript: 기존 EvidenceAnchor 어댑터를 재사용한 내부 checkpoint 실행, mock/live 분리 및 승인 게이트.
- React: 채팅 초안/구매 실행 확인, 판단·감사 관찰 및 Anchor 상태 읽기 표시.
- Solidity: 기존 EvidenceAnchor를 우선 재사용, 권한·연속성 테스트. 필요 없는 신규 토큰 배포 금지.
- 검증: 메시지 전송=결제0, 결정 체크포인트 선행, 감사 체크포인트 후행, Qwen 오류/구조검증,
  hash/phase 충돌, retry 중복 결제0, 기존 핵심 Mock E2E, lint/typecheck/build.
- 구조도: 별도 사이드 작업에서 현재/추가/미배포를 구분한 전체·파트별 그림.

## 실행 체크리스트

- [x] 현재 main/다른 worktree/기존 코드 확인, 설계 및 영향 범위 기록
- [x] 채팅형 요청
- [x] Qwen 관찰 및 증거 API
- [x] 두 단계 Anchor 실행 모듈 (Mock 통합, live는 승인 전)
- [x] 집중 테스트·Mock E2E·실제 로컬 Qwen 3Provider·build/lint
- [x] 구조도·기록·외부 승인 경계 문서화
- [ ] GitHub PR 전달

검증 결과 및 생략 범위의 원본: [DECISION_OBSERVER_VERIFICATION.md](DECISION_OBSERVER_VERIFICATION.md).
