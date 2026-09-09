# AEGIS AA 모델 구매 전환 요구사항

Status: 사용자 확정 요청 2026-09-09에 따른 구현 기준. 기존 요구와 충돌하면 본 문서가 신규 흐름에 우선한다. 과거 거래·원본 문서 이력은 보존한다.

## 범위와 운영 모델

- product-quality: high; engineering-assurance baseline: standard; security-sensitive: yes; has-ui: yes.
- 단일 사용자 로컬 데모. 지갑 서명·내부 API·감사 무결성·민감 원문 경계는 기존 scoped high-assurance를 유지한다.
- 사용자 → `/request` → 구매 에이전트 → Provider Gateway → 결과 반환. OpenAI, Anthropic Claude, Google Gemini 세 Gateway는 선택된 정확한 모델 ID/version을 실행하는 일반 코드이며 AI 판단을 하지 않는다.
- Mock Provider/Facilitator로 로컬 검증한다. 실제 Provider 키 연결, 토큰 배포/이동/실제 테스트넷 결제, AWS 실제 배포는 실행하지 않는다. AA 키가 있으면 별도의 실제 API 계약 검증은 가능하나 키는 서버 환경변수로만 사용한다.
- 신규 실행에서 판매자 견적 협상/counteroffer, SIWE, ERC-8004, 평판 점수, Evidence Anchor, 애플리케이션 독립 RPC 결제 검증을 제외한다. 과거 조회 기능과 거래·MongoDB 증거·평판 구현 커밋은 보존한다.

## 사용자 이야기와 수용 기준

### AEGIS-US-01 요청과 우선순위

사용자로서 요청·예산·선택적 우선순위를 제출하고 하나의 구매를 재개할 수 있다.

- GIVEN `/request` WHEN 실행 동의 후 제출 THEN 고유 purchaseId로 요청을 저장하고 분석·AA·선택·결제·응답·감사를 연결한다. tx hash는 별도 필드다.
- GIVEN 명시적 priority WHEN 처리 THEN default/price/speed/intelligence를 최우선 적용한다. 미지정이면 “싸게/비용 최소화”→price, “빨리/즉시/실시간”→speed, “정확하게/복잡한 추론/성능 중요”→intelligence, 무매치/서로 다른 조건 매치→default로 분류한다. 이유와 매치 근거를 저장한다.
- GIVEN priority WHEN 점수 계산 THEN 가격/완료시간/성능 가중치는 default=(.4,.3,.3), price=(.6,.2,.2), speed=(.2,.6,.2), intelligence=(.2,.2,.6)다. LLM 제안은 숫자를 변경하지 않는다. 추가 가중치 승인 단계는 없다.
- GIVEN 과거 balanced/quality/performance WHEN 읽기 THEN 저장된 당시 정책·숫자를 그대로 표시한다. 신규 API는 구 명칭을 명시적 오류로 거부하고 네 명칭을 안내한다. 과거 값을 신규 정책으로 자동 재해석하지 않는다.

### AEGIS-US-02 검증 가능한 AA 증거

구매 에이전트로서 AA 원본 출처와 정확한 모델 mapping을 고정하여 같은 근거로 비교한다.

- GIVEN 서버 adapter WHEN 조회 THEN `GET https://artificialanalysis.ai/api/v2/language/models/free`, `x-api-key` 서버 헤더, 공식 page/page_size pagination을 사용한다. 모든 페이지를 수집하고 page,total_pages,has_more 일관성과 반복 페이지를 검사한다.
- GIVEN 응답 WHEN snapshot 생성 THEN root intelligence_index_version을 snapshot에 보존하며 모델별 필드로 가정하지 않는다. id/name/slug/model_creator, input/output price, median end-to-end seconds, AA Intelligence Index를 보존한다.
- GIVEN snapshot WHEN 기록 THEN 감사 증거 API를 통해 MongoDB에 purchaseId, fetchedAt, 원본 응답 hash, 페이지 원본 또는 검증 가능한 원본 참조, 각 모델 id/slug, index version, 출처 및 fixture/live 구분을 저장한다.
- GIVEN 필수 값 누락/null, 정확한 mapping 실패, 중복 ID, 혼합 index version, 비정상 수치 WHEN 발견 THEN 결제 전 중단한다. 임의 점수나 fuzzy matching을 쓰지 않는다.
- GIVEN AA 키 없음 WHEN 검증 THEN fixture 계약 테스트를 수행하고 실제 AA API 검증은 미완료 외부 게이트로 표시한다. `.env.example`에는 빈 키 변수만 추가한다.

### AEGIS-US-03 가격과 세 요인 선택

사용자로서 모델별 확정 선결제 금액과 정책 계산 근거를 확인한다.

- GIVEN 요청 WHEN 후보 가격 산정 THEN estimatedInputTokens와 maxOutputTokens를 먼저 확정하고 산정 알고리즘·입력 hash·버전을 기록한다. `quoteAEGIS=(inputTokens*inputPrice+maxOutputTokens*outputPrice)/1e6`, markup=0, `amountUnits=ceil(quoteAEGIS*1e6)`를 십진 정밀 연산으로 계산한다.
- GIVEN AEGIS 가격 WHEN 표시 THEN 1 AEGIS=1 USD는 명목 환산이며 담보·상환 가치가 아니다. 최대 출력 토큰 기준 사전 고정 결제액이며 사용량 사후 정산이 아니다.
- GIVEN AA seconds WHEN 변환 THEN estimatedCompletionMs=seconds*1000이며 벤치마크 조건 참고 추정치로 표시한다. 실제 완료시간을 보장하지 않는다.
- GIVEN 전체 후보 WHEN 비교 THEN 예산/필수 기능/최대 허용시간/판매자 허용 목록 hard filter를 먼저 적용하고 모든 탈락 이유를 보존한다. 기능 정보는 명시적 모델 설정 출처이며 AA 측정값이 아니다.
- GIVEN 통과 후보 WHEN 점수 계산 THEN priceScore=min(amount)/amount*100, completionTimeScore=min(time)/time*100, performanceScore=index/max(index)*100 및 고정 비율 가중합을 적용한다.
- GIVEN 동점 WHEN 선택 THEN 낮은 금액→짧은 시간→providerId→modelId의 결정적 순서를 사용한다. 신규 거래에 scoringPolicyVersion=`aa-three-factor-v1`을 저장한다.
- GIVEN 0 금액/0 이하 시간/0 이하 성능/음수·NaN·Infinity 또는 통과 후보 없음 WHEN 평가 THEN 명시적 사유로 구매 중단한다. 0 단가 자체는 허용하되 총 amountUnits가 0이면 이 정책에서는 결제 불가다. 은밀한 기본 점수는 없다.

### AEGIS-US-04 결제 결속과 중복 방지

사용자로서 선택한 모델·금액만 최대 한 번 결제되기를 원한다.

- GIVEN buyer의 선택 모델/금액/purchaseId WHEN 결제 실행 모듈 호출 THEN 저장된 결정과 같은 snapshot/policy/token budget을 확인하고 예산·수신자·토큰·중복을 검사한 뒤 격리된 키로 ERC-3009 승인에 서명한다. 키는 브라우저/LLM/로그에 노출하지 않는다.
- GIVEN Gateway 402 WHEN 발급 THEN Gateway는 신뢰 가능한 동일 snapshot과 정책으로 금액을 재계산하며 클라이언트 금액을 신뢰하지 않는다. buyer는 402 금액·토큰·수신자·모델·purchaseId·snapshot/decision binding을 대조한다.
- GIVEN 서명 WHEN 전달 THEN x402 v2 exact verify/settle은 Facilitator가 수행하고 결제 확인 이후에만 결과를 제공한다. Facilitator 자체 블록체인 처리는 유지한다.
- GIVEN 재시도/경합/timeout WHEN 발생 THEN 한 purchaseId에서 최대 한 번 성공 결제한다. 동일 intent/nonce를 재사용하고 불명확 상태에서는 새 결제를 만들지 않는다.
- GIVEN 결제 상태 WHEN 감사/표시 THEN 근거는 Facilitator 응답이며 독립 receipt/Transfer/AuthorizationUsed 검증 완료로 표현하지 않는다. 잔액 미조회는 실제 온체인 잔액으로 표현하지 않는다.

### AEGIS-US-05 토큰과 과거 증거

- GIVEN 기존 PBLC V2 소스 WHEN 검사 THEN non-upgradeable 및 constant name/symbol이므로 rename 불가능하다는 근거를 기록한다. 신규 AEGIS 이름/심볼·6 decimals·ERC-3009 계약/fixture/설정/배포 계획을 준비한다.
- GIVEN 실제 배포/자산 이동/테스트넷 결제 직전 THEN 사용 지갑·배포 방식·예상 주소·가스 추정·테스트 금액을 제시하고 사용자 승인까지 멈춘다.
- GIVEN 기존 PBLC 또는 Permit2 거래 WHEN 조회 THEN 당시 토큰명·계약 주소·점수·평판·anchor·결제 증거를 그대로 표시한다. 읽기가 저장 데이터/이벤트를 변경하지 않는다.
- GIVEN 새 코드 WHEN 통합 THEN 기존 history/dirty worktree를 보존한다. reset/rebase/amend/squash, 거래 삭제·덮어쓰기는 금지한다.

### AEGIS-US-06 감사와 사용자 화면

- GIVEN 신규 구매 WHEN 감사 THEN priority 타당성, 고정 가중치, AA 산식/amountUnits, 동일 snapshot+전체 후보 재계산, 402/Facilitator 대조, 예상/관측 시간 차이, 중복 방지/기록 일치를 검사한다.
- GIVEN 감사 기록 WHEN 저장 THEN 필수 필드·순서·hash chain과 민감 원문 분리 암호화를 유지한다. Gateway/결제 실행 모듈은 MongoDB에 직접 접근하지 않는다.
- GIVEN UI WHEN 신규 상세 조회 THEN 세 점수·최종합·priority·가중치·AA 출처·fixture/live·모의 실행시간을 표시한다. 과거 거래는 과거 정책 배지와 원래 증거를 표시한다.
- GIVEN 신규 UI WHEN 실행 THEN SIWE/ERC-8004/Anchor/RPC 독립검증을 요구하지 않는다. 내부 API 보호와 키 격리는 유지한다.

## Interaction contract

`/request`: prompt, budget, optional priority, 실행 동의, 실행/동일 purchaseId 재개. 상태는 초기/검증 실패/분석 중/데이터 불충분 중단/필터 전체 탈락/결제 pending 또는 불명확/결제 성공 및 전달 실패/성공이다. 결제 후 전달 실패는 재결제 버튼을 제공하지 않는다.

`/dashboard`, `/`, `/purchases`, 상세, alerts: 읽기 중심. 구매 폼·실험 실행기 없음. 빈 목록, 로딩, 조회 실패/재조회, 과거 증거 일부 누락, 신규 mock 상세를 구분한다. `/experiments`는 `/request`로 무변경 redirect를 유지한다. 기본 화면 keyboard/label/responsive 회귀를 유지한다.

## 검증과 외부 게이트

필수 완료: AA pagination/snapshot/mapping/missing 계약, 수치/올림/분류/filter/점수/tie, 402 결속/중복/race, 과거 schema/평판/증거 읽기, 세 Gateway HTTP E2E, 개정 Phase 6 비정상 시나리오 E2E, 전체 tests/typecheck/lint/dashboard build, README/설계/구조도/project log/handoff 갱신.

외부 게이트를 로컬 완료와 분리한다: AA 실제 계약(키 없으면 미완료), 실제 Provider(이번 범위 제외), AEGIS 배포·이동·실결제(승인 대기), 공개 다중 사용자 인증·접근 제어/원문 보존 정책(AWS 전 결정), AWS 실제 배포(항상 정지).
