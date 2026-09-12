[한국어](README.md) | [English](README.en.md)

# AEGIS — AI 모델 구매와 감사

> **현재 추가 기능:** 채팅형 구매 요청, 별도 로컬 Qwen 판단/감사 관찰자, 결제 전·감사 후 두 증거 체크포인트. MongoDB 원문에 대응하는 해시를 온체인에 제출하는 코드를 연결했다. 새 Anchor 배포·온체인 쓰기는 승인 전이며 아래 데모에서는 **Mock 체크포인트**다. [설계·검증·배포 경계](docs/DECISION_OBSERVER_PLAN.md).

### 채팅·관찰자 미리보기

```bash
ollama list # qwen3.5:4b 및 로컬 Ollama 서버 확인
npm run aegis:observer:demo
```

`http://127.0.0.1:3100/request` 하단 입력창에서 메시지를 보내고, 대화에 나타난 요청별 확인 카드에서 구매 실행에 명시적으로 동의한다. **메시지 전송 자체는 구매·결제 API를 호출하지 않는다.** 결과는 대화 안에 남으며 완료 후 같은 문구를 다시 보내도 새 요청으로 실행할 수 있다. 실패 재시도는 기존 purchaseId를 유지한다. 미전송 문구 때문에 이미 보낸 카드를 실행하지 못하는 제한은 없다. 예산·우선순위는 보내기 전에 접이식 설정에서 바꾼다.

요청 확인 카드는 로컬 UI이며 자유 대화 LLM 서비스가 아니다. Qwen은 실행 시 priority 분류 및 두 시점의 별도 증거 관찰에 사용한다. Provider의 본문 답변은 여전히 Mock이다.

실행 버튼이 잠기면 카드 옆의 연결/저장 요청 확인 사유를 확인한다. 데모 명령을 실행한 터미널을 유지하고, 서버가 켜진 뒤 **연결 다시 확인**을 누르면 같은 카드에서 계속할 수 있다. 재확인은 조회만 하며 자동 구매하지 않는다. 조회가 8초 내 완료되지 않아도 이유와 재확인 버튼을 표시한다. 저장된 거래를 찾지 못하면 ID를 지워 우회하지 않고 거래 상세를 확인한다.

이 명령은 기존 `.env.local`·실거래 DB를 사용하지 않는다. Qwen 호출만 실제 로컬 실행, AA/Provider/정산/Anchor는 모의이며 임시 DB는 종료 때 정리된다. 실거래 설정은 바꾸지 않는다.

![채팅 요청 — 대화 안에서 실행 동의](docs/images/chat-request.png)

> 2026-09-12: 신규 AEGIS 계약 배포·1,000,000 AEGIS 초기 발행 완료. 로컬 Qwen으로 자동 priority를 분류하고 가격·점수·결제 검사는 고정 코드 정책을 유지한다. 기존 PBLC DB 기록은 사용자 지시로 삭제했다. 새 AEGIS 외부 실결제는 아직 검증하지 않았다. [현재 상태·실행·언어별 역할](docs/AEGIS_QWEN_MIGRATION.md). 아래 PBLC 명령명은 호환용이며 과거 실거래 증거는 역사 기록이다.

## 왜 만들었나

사용자 요청에 맞춰 구매 에이전트가 모델을 비교하고, PBLC 고정 가격 결제 요청과 결과를 같은 `purchaseId`로 연결하는 시스템입니다. 기본은 Mock 로컬 데모이고, 명시 동의로만 Base Sepolia PBLC 실제 결제를 시도할 수 있습니다. Provider 결과는 계속 Mock이며, 실제 유료 모델 호출·AWS 배포는 포함하지 않습니다.

## 주요 기능

요청별 모델 비교, 고정 가격 결제 요청, 결과 반환과 선택 근거 감사를 하나의 기록으로 연결합니다. 구매는 `/request`, 감사 조회는 `/dashboard`에서 수행합니다.

## 구조

```mermaid
flowchart LR
  U[사용자] --> R[구매 요청 /request]
  R --> B[구매 에이전트]
  B -->|자동 priority만| Q[로컬 Qwen]
  Q -->|프리셋| B
  B --> P[결제 실행 모듈 · 키 격리]
  P --> G[세 Provider Mock Gateway]
  G --> F[Facilitator\nMock 또는 외부 x402]
  G -->|결과| B
  B -->|결과| R
  B --> E[감사 증거 기록 API]
  B --> OBS[별도 로컬 Qwen · 결정 및 감사 관찰]
  OBS --> E
  E -->|확정된 증거 hash/count 조회| CP[체크포인트 실행 · 격리 키]
  CP -. live 별도 승인 필요 .-> AN[Solidity EvidenceAnchor]
  CP -->|Mock 또는 확인된 Anchor 증거| E
  P --> E
  G --> E
  E --> M[(MongoDB)]
  U --> D[읽기 전용 대시보드]
  D --> E
```

OpenAI·Anthropic Claude·Google Gemini Gateway는 선택된 모델의 결과를 반환하는 일반 코드입니다. 구매 에이전트가 요청 분석, AA 데이터 조회, 가격 계산, 필터·비교·선택을 담당합니다. 별도 프로세스의 **결제 실행 모듈**이 키를 격리하고 x402 v2 exact + ERC-3009 승인에 서명하며, Gateway가 Facilitator의 verify/settle 확인 후 결과를 제공합니다.

기본 실행은 Provider·Facilitator 모두 Mock입니다. `live` 모드는 사용자 소유 PBLC로 외부 x402 Facilitator 정산을 시도하며, Provider 결과는 명확히 Mock으로 유지합니다. 서버에 `AA_API_KEY`와 정확한 `AA_MODEL_CATALOG_PATH`를 함께 설정할 때만 실제 Artificial Analysis snapshot을 조회합니다. [구조도와 책임 경계](docs/AEGIS_ARCHITECTURE.md) · [실제 PBLC 요청 흐름](docs/LIVE_PBLC_REQUEST_FLOW.md)

## 시작하기

Node.js 20 이상, npm 의존성, Python 3.12/uv, 로컬 `mongod`·`mongosh`가 필요합니다. 저장소 루트에서 실행합니다.

```bash
npm run setup:python
npm run aegis:stack
```

runner는 전용 임시 MongoDB replica set과 증거 API, 세 Gateway, Mock Facilitator, 결제 실행 모듈을 시작합니다. 기존 환경 파일·MongoDB를 사용하지 않고 임시 테스트 키를 생성합니다. **Ctrl-C 종료 시 이 실행의 임시 DB와 기록이 제거됩니다.** 기존 PBLC 거래나 사용자 MongoDB 기록에는 접근하지 않습니다.

출력의 `evidence API` 주소를 복사하고 다른 터미널에서 다음을 실행합니다. `API_ORIGIN`에는 출력된 실제 주소를 넣습니다.

```bash
API_ORIGIN="http://127.0.0.1:출력된포트" npm run dev --workspace @pbl/dashboard -- --hostname 127.0.0.1 --port 3000
```

`http://localhost:3000/request`에서 요청·예산·선택적 우선순위와 실행 동의를 입력합니다. `/dashboard`는 읽기 중심 감사 화면입니다. SIWE 로그인은 신규 흐름에 없습니다. 단일 로컬 사용자 범위이며 공개 다중 사용자 인증 완성을 의미하지 않습니다. 내부 API 보호와 원문 암호화는 유지합니다.

## 사용 예시

실제 임시 MongoDB·로컬 HTTP 서비스에서 `allowed_providers: ["openai"]`인 요청은 AA fixture 기반 OpenAI 선택 → Mock 결제 → 결과 반환 → `AUDITED` 기록까지 통과했습니다. Claude·Gemini도 각각 같은 경로를 검증했습니다. 아래는 2026-09-10 집중 E2E 실제 출력입니다.

```text
ok 1 - openai is selected, paid once and delivered
ok 2 - anthropic is selected, paid once and delivered
ok 3 - google is selected, paid once and delivered
ok 4 - a budget below every candidate leaves no eligible model and no payment
ok 5 - two concurrent runs of one purchase settle exactly once
# tests 5
# pass 5
# fail 0
```

세 Provider 경로는 각각 허용 목록으로 선택 대상을 제한한 통합 검증입니다. 실제 모델 성능 비교 실험이나 실거래 결과가 아닙니다.

`aa-three-factor-v1`의 가격·완료시간·성능 가중치는 고정입니다.

| priority | 가격 | 완료시간 | 성능 |
|---|---:|---:|---:|
| default | 40% | 30% | 30% |
| price | 60% | 20% | 20% |
| speed | 20% | 60% | 20% |
| intelligence | 20% | 20% | 60% |

명시적 priority가 우선이며, 미지정이면 요청 문구를 분류합니다. 예산·기능·최대 허용시간·허용 Provider를 먼저 필터링한 뒤 통과 후보의 세 점수를 비교합니다. 평판·freshness·수동 품질 점수는 신규 선택에 쓰지 않습니다.

`quotePBLC = (예상 입력 토큰 × 입력 단가 + 최대 출력 토큰 × 출력 단가) / 1,000,000`

markup 없이 6-decimal 정수 단위로 올림합니다. 최대 출력량을 반영한 사전 고정 결제액이며 사용량 사후 정산이 아닙니다. **1 PBLC = 1 USD는 명목 환산이고 달러 담보·상환 약속이 아닙니다.**

현재 표준 작업은 최대 출력 8,000 tokens를 결제 전에 고정합니다. 모델 ID·seller 수신 지갑·단가와 짧은 요청 기준 0.01~0.04 PBLC 예시는 [모델별 표준 작업 결제 조건](docs/MODEL_TASK_PRICING.md)을 참고하세요.

데이터 계약 출처는 [Artificial Analysis](https://artificialanalysis.ai/data-api/docs)입니다. 완료시간은 기본 500 answer tokens 조건의 벤치마크 참고값이며 실제 완료 보장이 아닙니다. snapshot·정확한 mapping·필수 값 검증에 실패하면 결제 전 중단합니다.

## 감사와 검증

감사 증거 API가 MongoDB의 요청·snapshot·전체 후보·선택·결제·응답을 연결하고 이벤트 순서와 해시 체인을 검사합니다. Gateway와 결제 실행 모듈은 MongoDB에 직접 접근하지 않습니다. 신규 결제 상태 근거는 Facilitator 응답이며 독립 RPC Transfer 검증이나 온체인 Anchor 보장을 주장하지 않습니다. Mock 실행시간과 조회하지 않은 잔액은 실제 측정값으로 표시하지 않습니다.

```bash
npm run build --workspace @pbl/commerce-gateway
node --test --require ./scripts/aegis_outbound_guard.cjs --test-name-pattern='(openai is selected|anthropic is selected|google is selected|budget below every candidate|two concurrent runs)' scripts/aegis_phase6_scenarios.test.mjs
node --test --test-name-pattern='tampered 402|gateway refuses a payment payload' services/commerce-gateway/dist/tests/aegis-runtime.test.js
npm run lint
npm run build --workspace @pbl/dashboard
```

집중 E2E 5건·402 불일치 2건, dashboard build·기본 lint가 통과했습니다. Python 전체 outbound 계측, 과거 증거 광범위 zero-write 회귀, Mongo 실패 정리 스트레스, 전체 테스트 반복 및 추가 보안·성능 강화는 후속 과제입니다. 이를 완료하거나 운영 보안을 보장한다고 주장하지 않습니다.

## 로드맵

[작업 상태](docs/ROADMAP.md)와 [검증 기록](docs/AEGIS_VERIFICATION.md)에서 남은 구현과 검증을 추적합니다.

### 과거 기록과 외부 게이트

과거 PBLC·Permit2·ERC-3009 거래, 평판·Anchor·독립 RPC 증거는 당시 이름과 주소 그대로 보존합니다. [과거 증거와 인계](docs/HANDOFF.md)

신규 기본 Mock 실행은 사용자 소유 PBLC ERC-3009 계약([`0xe75013d333bebb90b321dd658440c10b5a0face8`](https://base-sepolia.blockscout.com/address/0xe75013d333bebb90b321dd658440c10b5a0face8), Base Sepolia, 6 decimals)을 결제 조건으로 사용합니다. 사용자 지갑이 owner이며 초기 `1,000,000 PBLC` mint는 완료됐습니다. 과거 PBLC V2와 거래는 읽기 전용으로 보존합니다. `npm run pblc:live:enable` 뒤 `npm run aegis:live-payment`을 사용하면 `/request` 동의 한 건에서 실제 x402 결제를 시도합니다. 실행 전 절차·제약은 [실제 PBLC 요청 흐름](docs/LIVE_PBLC_REQUEST_FLOW.md)을 따르세요. AA 무료 목록에는 현재 세 Provider 정확 모델 mapping이 없어 fixture AA를 유지합니다. **AWS 배포는 진행하지 않습니다.**

현재 로컬 실행 catalog의 수신 지갑은 OpenAI→seller1 `0xF00E…97a0`, Anthropic→seller2 `0xC774…26d8`, Google→seller3 `0x5363…80E4`로 설정했다. 기본 Mock 모드에서는 이 매핑을 증거로만 기록하고, live 모드에서는 선택된 수신자에게 고정 PBLC 금액을 지급 조건으로 제시한다.

### 지갑 결제 사전점검 (읽기 전용)

실제 전송 준비 상태만 확인하려면 `.env.local`의 `PBLC_USER_ADDRESS`를 사용합니다. 이 단계에서는 개인키를 읽거나 사용하지 않습니다.

```bash
npm run pblc:payment:preflight
```

사전점검은 지갑 공개 주소, ETH/PBLC 잔액, PBLC V2의 이름·심볼·decimals, Facilitator의 `exact + Base Sepolia` 지원만 읽습니다. 서명·`/verify`·`/settle`·트랜잭션 전송을 하지 않습니다. PBLC 커스텀 토큰의 실제 Facilitator 수락은 별도 승인 후에만 `verify/settle` smoke로 확인합니다.
