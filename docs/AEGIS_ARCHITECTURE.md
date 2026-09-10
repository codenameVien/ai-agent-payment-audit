# AEGIS 단일 구매 구조

2026-09-10 기준: 기본 실행은 세 Provider Mock 응답·AA fixture·Mock Facilitator다. 별도 `live` 서버 모드는 사용자 PBLC 지갑으로 외부 x402 Facilitator의 Base Sepolia `exact + ERC-3009` 정산을 시도한다. Provider 결과는 어느 모드에서도 Mock이다. 실제 PBLC 전송 성공 여부는 Facilitator 응답으로만 기록하며, 독립 RPC Transfer 검증이나 유료 모델 호출을 뜻하지 않는다. [실제 PBLC 요청 흐름](LIVE_PBLC_REQUEST_FLOW.md)을 따른다.

## 실행 경로와 조회 경로

```mermaid
flowchart TB
  U[사용자 · 단일 사용자 로컬 데모] --> R[구매 요청 페이지 /request]
  R -->|요청 · 예산 · 선택적 priority · 실행 동의| B
  subgraph BUYER[구매 에이전트의 논리적 책임]
    B[요청 분석 · AA 조회 · 가격 계산 · 필터 · 모델 선택]
    W[Buyer SDK Wrapper · 외부 API 연결 코드]
    P[결제 실행 모듈 · 별도 프로세스에서 키 격리]
    B --> W
    B -->|purchaseId · 모델 · 예상 결제 조건| P
  end
  W --> AA[Artificial Analysis adapter · 현재 synthetic fixture]
  W --> G[Provider Gateway · AI 판단 없는 일반 코드]
  G --> O[OpenAI Mock]
  G --> C[Anthropic Claude Mock]
  G --> GG[Google Gemini Mock]
  G -->|동일 snapshot으로 계산한 402 조건| P
  P -->|조건 대조 후 ERC-3009 서명| G
  G -->|verify / settle| F[Facilitator · Mock 또는 외부 x402]
  F -->|결제 확인 응답| G
  G -->|확인 후 모의 모델 결과| B
  B -->|결과 반환| R
  B --> E[감사 증거 기록 API]
  P --> E
  G --> E
  E --> M[(MongoDB · purchaseId별 증거)]
  D[감사 대시보드 · 읽기 전용] -->|거래 · 판단 · 결제 · 감사 조회| E
  U --> D
```

Gateway와 결제 실행 모듈은 MongoDB에 직접 접속하지 않는다. 감사 증거 기록 API가 필수 필드, 이벤트 순서, 해시 체인과 내부 조회 경계를 담당한다. 대시보드에는 구매 입력 폼이나 실험 실행기를 넣지 않는다.

## 선택과 감사

```mermaid
flowchart LR
  Q[요청 · 토큰 산정 · priority 근거] --> S[고정 가중치 정책]
  A[동일 AA snapshot · 명시 catalog 전체] --> H[예산 · 기능 · 시간 · 허용 목록 필터]
  H --> V[가격 · 완료시간 · 성능 점수]
  S --> V
  V --> K[가중합 · 결정적 동점 처리 · 모델 선택]
  K --> X[고정 결제액 · 402 조건 대조]
  Q --> AU[감사 · 원본 snapshot과 전체 후보로 재계산]
  A --> AU
  K --> AU
  X --> AU
```

- 정책은 `aa-three-factor-v1`이다. 가격·완료시간·성능의 비중은 default 40/30/30, price 60/20/20, speed 20/60/20, intelligence 20/20/60이다.
- 결제액은 예상 입력 토큰과 최대 출력 토큰의 사전 고정액이다. 6자리 정수 단위로 올림하며 실제 사용량 사후 정산이 아니다.
- AA 완료시간은 벤치마크 참고치이고 실제 요청의 완료 보장이 아니다. Mock 실행시간은 모의 실행시간이다.
- 해시 체인은 저장 증거 내부의 연결 검사다. 온체인 Anchor 기준점이나 독립 Transfer 확인에 의한 보장을 주장하지 않는다.

## 한 purchaseId의 처리와 중단 경로

```mermaid
flowchart TD
  R[요청 · 예산 · 선택적 우선순위 · 실행 동의] --> Q[원문 암호화 저장 · 요청 정규화]
  Q --> A{AA snapshot · 명시 모델 mapping 유효?}
  A -->|아니오| STOP[결제 전 중단 · 사유 기록]
  A -->|예| H[전체 후보 가격 산정 · hard filter]
  H --> C{통과 후보 있음?}
  C -->|아니오| STOP
  C -->|예| D[세 점수 가중합 · 동점 규칙 · 선택 기록]
  D --> P[결제 실행 모듈 · 예산 예약 · 중복 확인]
  P --> X{Gateway 402가 저장된 선택 조건과 일치?}
  X -->|아니오| STOP
  X -->|예| SIGN[격리된 키로 ERC-3009 승인 서명]
  SIGN --> F[Facilitator verify · settle]
  F --> S{결제 확인 응답}
  S -->|확정 거부| FAIL[결제 실패 기록]
  S -->|유실 · 모순 · 불명확| UNKNOWN[결제 확인 필요 · 예약 유지 · 재결제 금지]
  S -->|성공| G[선택된 Mock 모델 실행 · 결과 반환]
  G --> RESULT{결과 확보?}
  RESULT -->|아니오| PAID[결제 후 전달 실패 · 재결제 금지]
  RESULT -->|예| AUDIT[원 요청 분류 · snapshot 점수 · 금액 · 응답 감사]
  AUDIT --> VIEW[요청 결과 · 읽기 전용 감사 대시보드]
  STOP --> VIEW
  FAIL --> VIEW
  UNKNOWN --> VIEW
  PAID --> VIEW
```

각 단계 증거는 감사 증거 기록 API를 통해 같은 `purchaseId`에 연결된다. 정상·주의·위험은 감사 결과이고, 결제 확인 필요는 결제 상태다. 확인 필요를 “감사 진행 중”이나 정산 완료로 표시하지 않는다. 위 Facilitator와 모델 실행은 현재 로컬 Mock이다.

## 실제 실행과 과거 기록의 경계

| 구분 | 신규 로컬 실행 | 과거 PBLC 기록 |
|---|---|---|
| 모델 응답 | 세 Provider 모두 Mock | 당시 기록 그대로 |
| AA 데이터 | synthetic fixture 표시, 실제 인증 API 검증은 외부 게이트 | 당시 정책/점수 그대로 |
| 결제 상태 근거 | Facilitator 응답, Mock 여부 명시 | 당시 결제 방식과 증거 그대로 |
| 토큰 | 사용자 소유 PBLC `0xe750…ace8` 배포·초기 mint 완료; 기본은 Mock, 명시 live 모드에서 외부 Facilitator 전송을 시도 | PBLC 및 당시 계약 주소 유지 |
| 평판·Anchor·독립 RPC 결제 검증 | 실행·점수에서 제외 | 저장된 상세 증거 읽기 유지 |
| 인증 | 단일 사용자 loopback 데모, 내부 API 보호 유지 | 신규 SIWE 로그인 요구 없음 |

실제 모드에서는 Facilitator가 블록체인 전송을 제출한다. 현재 로컬 검증은 실제 PBLC 전송이 아니다. 잔액을 조회하지 않았다면 실제 온체인 잔액처럼 표시하지 않는다. 1 PBLC = 1 USD는 명목 산정 규칙이며 달러 담보·상환 가치를 뜻하지 않는다.

사용자가 AWS 배포는 아직 이르다고 명시했다. AWS 실제 배포는 진행하지 않는다. 토큰 배포·초기 mint는 완료됐으며, 실제 테스트넷 결제는 `/request`의 실행 동의와 `AEGIS_REAL_PAYMENT_APPROVED=yes`가 모두 있을 때만 시도한다.
