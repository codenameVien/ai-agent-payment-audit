# AEGIS 전환 검증 기록

> 2026-09-10: 사용자 축소 범위인 발표용 로컬 데모 검증 완료. Coder Terra, Reviewer Astra Light. 아래 과거 광범위 검증 결과는 당시 증거이며 재실행 요구가 아니다. 원래 전체 goal이나 운영 환경 완료를 뜻하지 않는다.

## 최종 축소 범위 결과 — 2026-09-10

| 검증 | 결과 | 실제 확인 범위 |
|---|---|---|
| OpenAI·Claude·Gemini 각각 전체 흐름 | 3/3 통과 | 허용 목록으로 각 Provider 선택, AA fixture 결정, 1회 Mock 정산, 결과, AUDITED 이벤트 |
| 예산 초과 | 1/1 통과 | HTTP409, 선택·결제 이벤트 없음 |
| 동시 동일 purchaseId | 1/1 통과 | 1회 intent·정산·전달 |
| 402 조건·결정 불일치 | 2/2 통과 | 변조 조건은 서명 전 거부, 결정과 다른 payload는 정산0 |
| dashboard production build | 통과 | Next.js 10 routes |
| 기본 root lint | 통과 | Ruff, mypy59 source files, 세 workspace TypeScript |

Coder 기록: `../.agent/outbox/aegis-demo-terra-result.md`. 오케스트레이터가 집중 HTTP/Mongo E2E5건(5.83초)·402검사2건을 직접 재실행해 모두 통과했다. 최종 Astra Light 기능 리뷰: `../.agent/outbox/aegis-demo-final-review.md`, approve. 핵심 런타임 수정 없이 기존 구현으로 통과했다.

실제 실행한 것은 Gateway·결제 실행 모듈, 임시 로컬 키의 ERC-3009/EIP-712 서명, HTTP 통신, 소유한 임시 MongoDB replica set 저장이다. AA 값은 synthetic fixture, Provider 응답과 Facilitator 정산은 Mock이다. `x402mock:`는 블록체인 tx hash가 아니다. 테스트 종료 시 해당 임시 DB·프로세스를 정리했다.

오케스트레이터 실행 purchaseId: OpenAI `7518edd8-0ae6-43fd-9af9-365953f8ea77`, Claude `d766c802-92ff-40ae-a874-a304c011ddde`, Gemini `e32a1dcc-3337-4109-9496-7d0d8006143b`, 예산 초과 `cac8126c-e451-4049-8940-d55bb69b5447`, 동시 실행 `cf1b3e9e-f79a-4d93-a2e5-774a2c91c52e`. 모두 일회성 테스트 기록이며 현재 DB에 남아 있다는 뜻은 아니다.

기존 P6-03 파일25개 해시와 HEAD는 기준선과 동일했다. 기존 사용자 MongoDB·과거 PBLC 거래는 테스트 쓰기 대상으로 사용하지 않았다.

### 생략·유예한 검증과 한계

- Python 경로 전체 outbound 차단 계측: 미완료. Node guard를 전체 언어 차단의 증명으로 쓰지 않는다.
- PBLC·Anchor·평판 전체의 광범위 zero-write 회귀: 이번 완료 범위에서 제외. 기존 자료 보존과 포괄적 테스트 통과는 다르다.
- Mongo 실패 정리 경로 추가 스트레스: 미완료. 이전 리뷰의 해당 지적을 해결된 것으로 처리하지 않는다.
- 전체 광범위 테스트 반복·추가 보안/성능 강화: 수행 대상에서 제외. Terra의 최초 옵션 위치 실수로 기존14건 하니스가 한 번 실행되어 통과했으나, 이 결과로 유예한 보증 범위를 확대하지 않는다. 추가 반복은 하지 않았다.
- 최종 브라우저 재캡처는 이번 축소 검증에서 생략했다. 아래 UI35건·브라우저 결과는 당시 검증이며 새 검증처럼 재표기하지 않는다.

실제 AA API·Provider API 호출, 자산 이동, 실제 테스트넷 결제 및 AWS 배포는 모두 미실행이다. 2026-09-10부터 신규 실행의 결제 조건은 배포된 PBLC V2를 재사용하도록 전환했지만, Facilitator는 Mock이므로 실제 전송 증거가 아니다. 공개 다중 사용자 인증·운영 보안 검증도 완료가 아니다.

## PBLC V2 재사용·AA 실조회 준비 — 2026-09-10

- 신규 결제 조건: PBLC V2 `0xDed7F4992D98eF31453dCebbB8c2A6b50d0284B3`, Base Sepolia, ERC-3009, 6 decimals. 기존 PBLC 실거래·감사 기록은 수정하지 않았다.
- `AA_API_KEY`와 `AA_MODEL_CATALOG_PATH`가 모두 있어야 live AA adapter를 선택한다. fixture catalog·누락 metric·정확한 mapping 실패는 결제 전 중단한다.
- 이번 변경 검증: root lint, Commerce Gateway build, dashboard build, Python AA workflow/schema 29건, PBLC V2 조건의 Mock Facilitator/runtime 19건, 임시 MongoDB를 사용하는 로컬 E2E 14건 통과.
- 실제 AA 키를 아직 설정하지 않았으므로 인증 API 결과·live snapshot 저장은 미검증 외부 게이트다. Provider와 Facilitator Mock 범위는 유지한다.

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

## AA 정책·증거 기록 — `e67e0b0`

- Coder 실행 로그 확인: Python non-Mongo **432 passed**, 실제 격리 Mongo **11 passed**, ruff·mypy(57 source files) 통과, schema 및 Python/JS 가격 교차 사례8 통과.
- 독립 Reviewer 직접 실행: audit/policy/snapshot **94 passed**, 수정 재검토 approve.
- snapshot 원본·전체 catalog 기반 재계산, 후보 누락/원가 변조, 우선순위 근거 불일치, 고정밀 Decimal 손실, 공식 시간 경로/페이지 요청 계약 회귀를 확인했다.
- 키워드 분류는 원문 재도출이 없는 경우 CAUTION으로 남는다. 구조화 근거 검사를 원문 재검증 완료로 과장하지 않는다.
- 결제·Gateway·UI 전체 흐름은 이 검증 범위에 포함되지 않는다. 실제 AA 인증 API도 미검증이다.

## 결제 런타임 중간 검증 — 최종 승인 전

- Coder 실행 출력 확인: 세 Provider 각각의 요청→402→Mock 정산→결과, 동시 중복 요청, 내부 무인증 거부, 구 경로 제외, cross-site POST 차단을 포함한 **격리 Mongo/HTTP smoke 7/7** 통과(35.90초).
- Python payment20, Seller30, Payment Executor112 통과. Reviewer의 terms/Facilitator focused14 통과.
- 그러나 Reviewer가 같은 purchaseId에 서로 다른 유효 nonce를 새 Gateway 인스턴스에서 제출하여 두 번 성공하는 경로를 순수 로컬 재현했다. durable intent 결속, 재시작 후 결과만 재조회, Facilitator 응답 대조 및 임시 DB 강제 종료 정리를 수정 중이다. **정상 smoke 통과를 최종 E2E 완료로 취급하지 않는다.**
- 위 수정으로 영향을 받는 검증은 다시 수행한다. 실제 AA API·Provider API·AEGIS 온체인 결제·AWS는 실행하지 않았다.

### 런타임 리뷰 수정 후

- 런타임 기본 구현 commit `823f3bd`, 후속 응답 보존 수정 `9744d8d`.
- 후속 수정 결과: Python458 non-Mongo(11 mongo deselected), Seller30, Payment Executor120, schema 가격 사례8, Foundry26, 실제 격리 Mongo11, 전체 프로세스 smoke8 통과. 전체 결과와 명령은 `.agent/outbox/aegis-02-result.md`에 있다. 최종03 변경으로 영향을 받는 검증은 다시 수행한다.
- Reviewer가 같은 구매의 다른 nonce 거부, 재시작 후 서명/verify/settle 없는 결과 회수, 강제 종료 확인을 검토했다. 마지막 모순 응답 수정은 TS durability8, Python 관련6을 직접 실행해 통과했다.
- 모순된 `success:true`는 실패로 덮어쓰지 않고 `RECONCILIATION_REQUIRED`로 보류한다. 예약을 유지하고 원본 success/amount/network/payer/reference를 구조화해 보존하며 재결제를 막는다.
- 최종 scoped AEGIS02 리뷰 **approve**. 원문 priority 감사·신규 UI·개정 이상 시나리오 전체 E2E는 다음 단계이며 아직 완료로 주장하지 않는다.

## AEGIS03 중간 확인 — 최종 통합 검증 전

- UI `9f6e23f`: 오케스트레이터 직접35/35 테스트 통과. 독립 pending 경계 리뷰13/13 통과·approve. 최초 조회 중 실행 차단, 과거 두 PBLC pending key 읽기 전용, 변경 요청의 기존 ID 재사용 방지를 확인했다.
- 실제 CUA 브라우저의 `/request`→`/run`→상세 이동 성공. 자동 price 분류·27입력/1024출력·619units·세 후보 점수·Mock 표시 확인. 이 임시 실행은03 감사 변경 전 서버여서 CAUTION을 보존했고, 소유 runner 종료로 임시 DB만 정리했다. 기존 사용자 DB는 수정하지 않았다.
- 모바일360px에서 overview/request/detail 문서 폭345/345/360, 데스크톱1440에서1425로 페이지 가로 넘침 없음. 메뉴 전용 행과 과거 판매 에이전트 표기 확인. 실제 최종 backend로 새 구매와 캡처를 다시 검증한다.
- 원 요청 복호화·결속·priority 재분류 집중 리뷰12/12 통과·approve. 실제 Mongo/HTTP 전체 E2E와 최종 전체 검사 결과는 아직 진행 중이다.
