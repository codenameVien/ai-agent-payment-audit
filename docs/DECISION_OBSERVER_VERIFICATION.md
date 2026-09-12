# Decision 관찰·채팅·체크포인트 검증 (2026-09-12)

## 구현 상태

- 채팅 초안은 브라우저 안에서만 갱신된다. 빈 대화 또는 미전송 메시지가 있으면 실행 불가. 편집 시 동의 해제. 동의와 실행 버튼 이후에만 purchaseId/구매 실행 API를 호출한다.
- Python이 DECIDED/AUDITED 각각에 별도 Qwen 관찰을 기록하고 해당 event prefix를 고정한다. 관찰자는 자문이며 실패는 OBSERVER_UNAVAILABLE로 기록한다. 고정 정책·결제 권한은 바꾸지 않는다.
- protected Evidence API GET은 읽기 전용. POST checkpoint는 동일 phase/hash/count/mode의 재실행만 허용한다. TypeScript는 MongoDB에 직접 접근하지 않는다.
- 결정 checkpoint가 필요한 모드에서는 결제 서명 전에 확인한다. 감사 checkpoint 실패는 이미 완료된 결제를 다시 만들지 않는다. Mock proof에는 transaction hash를 만들지 않는다.
- 실제 Anchor 모드는 기존 Solidity EvidenceAnchor 및 확인 어댑터를 재사용한다. 저장된 decision proof는 live 결제·재실행·audit anchor 전에 체인의 동일 purchaseId/count/hash/tx와 대조한다. 새 계약 배포나 체인 쓰기는 실행하지 않았다.

## 실제 실행한 검증

| 범위 | 결과 |
|---|---|
| Mock 3 Provider 전체 구매+관찰+두 checkpoint+재실행 | OpenAI/Anthropic/Google 각 1회 정산·checkpoint 2건·observer 2건, 재실행 증가 없음 |
| gateway 전체 단위/통합 | 130 통과 (Mongo proof 조작 거부 포함) |
| Python checkpoint/API·Qwen 구조화 adapter 집중 | 6 통과 |
| Solidity EvidenceAnchor 권한·연속성 | 2 통과, 로컬 EVM만 |
| dashboard 경계 테스트 | 36 통과 (Node26) |
| lint / 타입 검사 / dashboard production build | 통과 |
| schema 경계 / 가격 비교 | 통과, 가격 8case 일치 |
| script 안전 경계 | 24 통과 |
| 브라우저 채팅 전송 | 구매 API 쓰기 0건, 390px 가로 overflow 없음 |
| 브라우저 명시 구매 실행 | 실제 preview3100에서 POST 2건(create/run), 결과 상세 이동·Mock checkpoint 2건 표시 |
| 실제 로컬 Qwen 전체 흐름 | qwen3.5:4b로 세 Provider 각 decision/audit 관찰 완료, replay 중 관찰/정산/checkpoint 추가 없음 |

독립 리뷰는 사용자 요청대로 Mock 데모의 핵심 흐름만 blocker로 평가했다. wire naming 문제 수정 후 blocker 없음. 이후 발견한 preview3100 origin 누락은 임시 stack의 명시 loopback allowlist에 추가했다. 실제 live 환경의 origin 정책은 완화하지 않았다.

실제 Qwen 검증은 `node scripts/decision_observer_smoke.mjs --qwen`이 exit0으로 완료됐다. 초기 시도의 timeout/잘린 JSON은 unavailable로 남겼고, 한국어 UTF-8 전송·observer timeout30초·최대2finding/출력768토큰으로 조정 후 새 구매에서 검증했다. 이는 대표 정상 구매 3건의 호출·스키마·연결 검증이며, 이상행위 탐지 정확도 평가를 완료했다는 뜻은 아니다.

브라우저 Mock 구매 예시: `92492734-639c-4caf-99a8-4b4d69dfb726` (격리 preview 임시 데이터, 실거래 아님).

![실제 실행 후 Qwen/Mock 체크포인트 표시](images/decision-observer-result.png)

## 모의와 실제의 구분

- 실제: 로컬 HTTP 서비스, 임시 replica-set MongoDB, 임시 테스트 키 서명, 브라우저, 로컬 Qwen, 로컬 Solidity 테스트.
- 모의: AA fixture, 세 Provider 응답, Facilitator 정산, Anchor 체크포인트.
- 기존 live `.env.local`, AEGIS 자산, 사용자 거래/감사 DB는 변경하지 않았다. 테스트 종료 때 삭제되는 것은 이 실행이 만든 임시 저장소뿐이다.
- 새 토큰 배포/민팅/송금, AEGIS 실제 결제, 새 EvidenceAnchor 배포/쓰기, 유료 모델 호출, AWS 배포 없음.

## Known limitations

- Qwen은 두 사건 시점의 자문이다. 매 토큰의 사고과정이나 누락된 모든 행위를 관찰하는 상시 프로세스가 아니다. 결과의 정확도는 별도 평가가 필요하다.
- 입력은 안전한 필드로 제한되고 원 요청은 최대 4,000자로 잘린다. 요청 재인용 금지 지시가 있어도 자유문 finding에 원문 일부가 포함될 수 있으므로 민감 정보 없는 데모 요청을 사용한다.
- Model timeout/잘못된 JSON/존재하지 않는 eventId는 unavailable. “추가 의심 없음”과 “감사 정상 보증”은 다르다.
- Anchor가 없는 Mock는 블록체인 무결성 보장이 없다. 실제 Anchor도 등록 전 조작·미기록·기록의 최초 진실성·삭제 원문 복구를 보장하지 않는다. DB 전체 삭제 탐지에는 체인 기록과 외부 기준 purchaseId의 대조가 필요하다.
- 신규 Anchor 배포·체인 쓰기·체인 장애 복구는 외부 승인 게이트다. 대시보드는 저장된 Anchor 확인 기록이지 페이지를 열 때마다 새 RPC 감사를 수행한 결과가 아니다. 주기적 전수 대조 및 미확정 제출의 장시간 복구 스트레스는 미수행이다.
- Anchor 재대조 어댑터는 최근 최대 10,000블록의 로그만 조회한다. 오래된 checkpoint를 찾지 못하면 성공으로 추정하지 않고 중단한다. 장기 이력의 receipt 기반 조회/범위 확장은 후속 과제다.
- 사용자 축소 범위에 따라 Python 완전 outbound 계측, 광범위 과거 자료 zero-write 회귀, Mongo failure stress, 광범위 전체 테스트 반복, 추가 보안·성능 강화는 생략했다.

## 배포 승인 계획 (읽기 전용 추정)

사용 지갑 후보: `0x043D966B3f30Ff9FAC08FD6b5eFeDa6ac895a0a3`.
Solidity `EvidenceAnchor(initialOwner=동일지갑)`의 CREATE 배포, pending nonce=6 당시 예상 주소:
`0x169358B1F8404353456F6B287F5bE9648aB23Af8`.
읽기 전용 추정 gas=350,998, maxFeePerGas=7,000,000wei, 실행 수수료 상한 추정=0.000002456986ETH + L1 데이터 수수료. 체크포인트당 gas cap=200,000, 구매당 2건.
토큰 이동 금액은 0이며 별도 승인 없이 실행하지 않는다. nonce/네트워크 수수료가 바뀌므로 실제 승인 전에 `node scripts/evidence_anchor_preflight.mjs <공개지갑주소>`로 다시 확인해야 한다.
