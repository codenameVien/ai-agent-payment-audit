# AEGIS AA three-factor v1 설계

2026-09-09 확정 요구 우선. 구현 위치 `/Users/vien/MyProjects/PBL-aegis`, base d084388. 과거 문서를 삭제하지 않고 본 신규 정책을 구분하여 병합한다.

## 구조

```mermaid
flowchart LR
 U[사용자] --> R[/request 구매 요청 페이지] --> B[구매 에이전트]
 B --> AA[Artificial Analysis 서버 adapter]
 B --> G[Provider Gateway: OpenAI / Claude / Gemini Mock]
 B --> P[결제 실행 모듈: 일반 코드·격리 서명]
 P -->|x402 승인 전달| G
 G -->|verify / settle| F[x402 Facilitator Mock]
 G -->|결제 확인 후 모의 결과| B
 B --> R
 B --> E[감사 증거 기록 API]
 P --> E
 G --> E
 E --> M[(MongoDB)]
 D[읽기 중심 감사 대시보드] --> E
```

실제 모드 미래 경계에서 Facilitator가 AEGIS ERC-3009 전송을 제출한다. 앱은 별도 receipt/Transfer/AuthorizationUsed 교차 검증을 하지 않는다. Gateway와 결제 실행 모듈은 AI가 아니며 Mongo 직접 접근도 없다. 기존 seller-service/commerce-gateway 패키지 경로를 재사용해도 사용자 역할 명칭은 Provider Gateway/결제 실행 모듈로 맞춘다.

## 신뢰 경계와 로컬 구성

SIWE 실행 경로를 제거하고 서버 설정의 단일 local owner/buyer identity를 사용한다. 이는 다중 사용자 인증 구현이 아니다. 앱은 loopback에서 실행하며 브라우저는 same-origin Next 서버 경유로 API를 호출한다. 기존 서버 간 내부 인증 헤더/비밀값 검증을 유지하고 브라우저 번들에 내부 토큰을 넣지 않는다. 변경 API의 origin/host 보호를 유지한다. 결제 키는 서명 프로세스에만 있고 구매 분석/LLM prompt에는 없다. 기존 민감 원문 암호화/구조화 로그 redaction을 유지한다.

새 로컬 composition은 mock provider/facilitator만 구성하고 live chain/provider 쓰기 adapter가 실행되지 않게 fail-closed한다. 주소·잔액·실행 모드는 fixture로 표시한다. 기존 운영 .env와 기존 DB를 덮어쓰지 않는다. 테스트는 sentinel을 가진 별도 DB를 사용하고 cleanup은 그 DB만 대상으로 한다.

## AA 계약과 모델 catalog

공식 계약 근거: https://artificialanalysis.ai/data-api/docs . Root `intelligence_index_version` numeric, `data` array, pagination `page`, `page_size`, `total_pages`, `has_more`, `x-api-key` 헤더. adapter는 첫 페이지부터 has_more false까지 읽되 page progression/total consistency를 검증한다. 페이지마다 version이 동일해야 한다. 데이터 root/index 버전이 바뀌면 기존 snapshot을 수정하지 않는다. 공식 end-to-end 측정은 별도 설명이 없으면 500 answer tokens를 가정한다. null은 미측정이며 0이 아니다. UI/문서에 Artificial Analysis attribution과 이 벤치마크 조건을 표시한다. 실제 조회 근거는 구현 worktree의 docs/AA_API_CONTRACT_CHECK.md를 참조한다.

Catalog entry: providerId, providerModelId, modelVersion, aaModelId, aaSlug, explicit capabilities, recipient. Provider 모델 ID는 실제 API용 정확한 ID이고 AA ID와 다를 수 있다. mapping은 명시적 쌍으로만 허용한다. 초기 Mock fixture용 모델 ID를 사용한다면 fixture mapping임을 명시하고 실제 AA mapping 검증으로 주장하지 않는다. 실제 AA 응답에 명시 catalog ID가 하나라도 없으면 구매를 중단한다. 검색/유사 이름/모델 계열 추정은 금지한다.

Snapshot: snapshotId, purchaseId, sourceUrl, mode(fixture/live), fetchedAt UTC, intelligenceIndexVersion, rawResponseHash, rawPages(또는 원본 보존 참조), catalogVersion, models[]. Hash는 응답 페이지 순서와 원본 bytes를 결속하고 hash algorithm/encoding을 기록한다. 원본 bytes를 보존하기 어렵다면 canonical JSON 방식으로 고정하고 `rawResponseHash`의 실제 해시 정의를 문서화한다. Evidence API는 immutable snapshot을 저장하고 purchase가 이를 참조한다.

## 숫자와 정책

- 요청 입력 토큰 추정의 최소 결정적 정책: UTF-8 prompt byte 길이를 4로 나눈 올림, 최소 1; method=`utf8-bytes-div4-v1`. 추정치임을 표시한다. maxOutputTokens는 명시적 요청 값 또는 기존 고정 서버 기본값을 양의 정수로 확정하고 정확한 기본값을 설정/증거에 기록한다. 추가 생성되는 prompt가 있으면 모델에 전달되는 전체 입력 bytes를 산정 대상으로 포함한다.
- 가격은 JSON 수를 십진 문자열로 보존하여 Decimal/동등 정밀 연산한다. amountUnits는 `ceil(inputTokens*inputPrice + maxOutputTokens*outputPrice)`와 동치다. 서로 다른 언어 구현은 공유 fixture로 교차 검증한다. uint256 범위 밖 값은 거부한다.
- input/output 단가는 유한하고 >=0, 시간과 AA index는 유한하고 >0, token 수는 양의 안전 정수. amountUnits가 0이면 `zero_payment_amount_unsupported`로 중단한다. 음수 index에 임의 offset을 더하지 않는다.
- 필수 수치/mapping 불량은 전체 구매를 중단한다. 정상 수치 후보의 요청 부적합은 filter rejection이며 모든 이유를 남긴다. budget 비교는 올림 이후 amountUnits를 사용한다. 허용시간은 변환한 ms와 비교한다.
- 필터를 통과한 집합만 정규화 분모로 사용한다. 내부 점수는 반올림하지 않고 충분한 정밀도(Decimal 또는 rational)로 비교하며 UI에서만 소수 표시한다. tie는 동일 정밀값 기준이며 임의 epsilon을 사용하지 않는다. providerId/modelId는 locale 비의존 문자열 오름차순이다.
- 과거 이름은 history read에 한정한다. 신규 priority 필드는 네 값만 받는다. 명시적 default도 분류를 우회한다. 서로 다른 분류 키워드가 함께 있으면 default다.

## 신규 증거와 이벤트

신규 schema는 `scoringPolicyVersion: aa-three-factor-v1`을 판별자로 쓰고 과거 schema reader는 그대로 둔다. 저장 문서 일괄 migration 없음. Event sequence는 REQUESTED → AA_SNAPSHOT_RECORDED → DECIDED → PAYMENT_AUTHORIZED → PAYMENT_PENDING → PAYMENT_CONFIRMED → DELIVERED → AUDITED를 표현하며 기존 허용 이벤트 이름과 대응시켜도 동일 의미와 순서 검사를 유지한다. 실패/불명확/전달 실패는 그 발생 지점에 기록한다. 구 QUOTED 이벤트를 쓴다면 협상 견적이라고 위장하지 말고 고정 가격/x402 terms 용도로 schema를 명시한다.

Decision evidence는 request/tokens/estimationMethod, originalPriority/effectivePriority/reason, weights/policyVersion, snapshotId/hash, 전체 catalog candidates와 source 값, 필터 이유, eligible 점수, winner, amountUnits, token identity(이름/심볼/decimals/address/chain), terms binding hash를 포함한다. Gateway/실행 모듈은 API로 이 고정 evidence를 읽는다.

Gateway는 body의 amount를 믿지 않고 evidence API에서 같은 snapshot과 token budget/정책을 가져와 다시 계산한다. 402는 purchaseId, modelId/version, snapshot hash, decision hash와 token/recipient/amount/network/expiry를 결속한다. buyer/결제 실행 모듈은 이 모두를 대조한 후 서명한다. 서버 catalog recipient/token 설정이 authoritative다.

결제 상태는 기존 원자 intent/예산 예약/terminal uniqueness를 재사용한다. 성공 저장과 예약 확정은 원자적이다. 같은 성공 증거 재전달은 idempotent, 상충 증거는 거부한다. pending/unknown timeout에서는 기존 nonce/intent로 조회·복구만 하며 새 settle 시도를 생성하지 않는다. Facilitator 증거에 `verificationBasis=facilitator_response`, `executionMode=mock`를 저장한다. 성공 후 전달 실패에도 결제는 성공으로 보존한다.

## AEGIS 계약

`infra/contracts/src/DemoTokenV2.sol`의 name/symbol constant와 non-upgradeable 선언을 확인했다. 기존 PBLC 변경은 불가능하다. 별도 AEGIS 계약은 이름/심볼 AEGIS, decimals=6, ERC-3009 domain name 및 version을 명시하고 기존 검증된 authorization 로직을 재사용한다. 배포 스크립트는 plan을 기본으로 하며 deploy는 명시 실행/승인 경계다. 실제 주소를 fixture 주소로 대체하거나 PBLC 주소에 AEGIS 라벨을 붙이지 않는다.

## 감사 및 UI

신규 audit는 저장된 전체 후보와 snapshot으로 필터·산식·점수·rank를 독립 재계산한다. 분류 이유/weights/amount/402/Facilitator proof와 중복 기록을 대조한다. estimatedCompletionMs와 observedExecutionMs 차이는 모의/실제 구분과 함께 보고하고 보장 위반으로 단정하지 않는다. hash chain은 저장 증거 내부 검증이며 외부 온체인 기준점에 의한 무결성 보장으로 표현하지 않는다.

UI는 policy discriminator에 따라 신규/과거 renderer를 선택한다. PBLC 역사 필드는 그대로, 누락 역사 필드는 unavailable, 새 거래에는 Mock Provider/Mock Facilitator·AA fixture 또는 live·명목 환산·선결제 고정액·벤치마크 참고 시간 표시. SIWE가 없는 local demo에서 기존 history 읽기 범위를 서버 고정 owner 정책으로 처리하고 public multi-user 완성 주장 금지.
# 2026-09-12 설계 변경

최신 확정 설계와 영향 범위: [Decision Observer 계획](../../docs/DECISION_OBSERVER_PLAN.md). 원문 MongoDB, 두 단계 해시 체크포인트, 분리된 Qwen 관찰, 메시지와 결제 동의 분리.
