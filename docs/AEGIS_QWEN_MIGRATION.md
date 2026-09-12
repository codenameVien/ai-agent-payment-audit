# AEGIS·로컬 Qwen 전환 — 2026-09-12

## 현재 실행과 검증 경계

- PR #12는 이미 main에 merge되어 있었다. 이번 작업은 그 main에서 분기했다.
- 신규 자산: Base Sepolia AEGIS, 이름/심볼 모두 AEGIS, decimals 6.
- 계약: `0x3440294d5fdc4849461c6f383a7fcf89af0c4a4b`
- owner 및 초기 수령자: `0x043D966B3f30Ff9FAC08FD6b5eFeDa6ac895a0a3`
- 초기 공급: 1,000,000 AEGIS. 기존 PBLC 이름만 바꾼 것이 아니라 신규 계약을 배포했다.
- 배포 tx: `0x933614256942ed1d54f557826e51bfa2164fc08e89d51662544e24a0d9e5f9dc`, block 46707909.
- 배포·초기 mint·RPC 잔액 확인은 실제 실행. **새 AEGIS의 외부 Facilitator verify/settle 및 seller 송금은 아직 검증하지 않았다.**
- Provider 결과와 이번 구매 E2E 결제는 Mock이다. AWS·유료 Provider 호출은 실행하지 않는다.
- 1 AEGIS = 1 USD는 가격 계산의 명목 환산 규칙이며 달러 담보/상환 보장이 아니다.

## Qwen 역할

`AEGIS_PRIORITY_CLASSIFIER=local-qwen`, `AEGIS_OLLAMA_MODEL=qwen3.5:4b`로 활성화한다.
Ollama는 loopback HTTP만 허용하고 구조화 JSON으로 네 priority 중 하나를 반환한다.
명시 priority는 LLM을 호출하지 않는다. 자동 선택 때만 원문 요청을 로컬 Qwen에 보낸다.
분류 실패/시간 초과는 구매·결제 생성 전에 중단한다. 조용한 키워드 fallback은 없다.
Qwen은 지갑 키나 결제 서명을 받지 않는다.

가중치·가격·hard filter·순위·동점 처리는 기존 고정 코드 정책이다.
default=40/30/30, price=60/20/20, speed=20/60/20, intelligence=20/20/60 (가격/시간/성능).
감사는 분류 방법·모델·정해진 근거 라벨과 정책의 일치를 확인한다.
별도 LLM으로 문장 해석의 정답을 독립 보장하는 것은 아니다.

## Solidity와 일반 코드

| 구간 | 구현 | 책임 |
|---|---|---|
| Base Sepolia 토큰 | Solidity: infra/contracts/src/AEGISToken.sol | 잔액·mint·Transfer·ERC-3009 서명/기간/nonce 검증 |
| 구매 판단·감사 | Python: services/buyer-audit-api | Qwen 호출, AA 조회, 가격/점수 계산, 감사·Mongo 기록 |
| 결제 실행 모듈 | TypeScript: services/commerce-gateway | 키 격리, 402 조건 대조, ERC-3009 승인 서명 |
| Gateway·Facilitator 연결 | TypeScript | Mock 결과 제공, x402 verify/settle 요청 |
| 요청·대시보드 | React/Next.js TypeScript | 실행 동의·입력·결과 조회 |

Facilitator가 실제 체인 전송을 제출한다. 앱의 별도 RPC 결제 교차검증은 없으며,
잔액 조회는 결제 독립검증과 구별한다.

## 기존 기록 정리

사용자의 이번 삭제 지시가 이전 DB 기록 보존 지시를 대체한다.
`aegis_live_payment`의 기존 PBLC 구매 3건에 연결된 44문서와
`pbl_audit`의 과거 정책·요청 payload 4문서, 총 48문서를 삭제했다.
백업은 만들지 않았으므로 이 작업에서 복원할 사본은 없다.
사용자 계정 2건 및 다른 DB는 보존했다. 이전 pending 브라우저 키 3개만 제거한다.
Git 이력·역사 문서·Base Sepolia 온체인 거래는 삭제하지 않는다.
과거 토큰 주소를 AEGIS 거래로 재표기하지 않는다.

## 실행

```bash
npm run aegis:payment:preflight
npm run aegis:live-payment
# 별도 터미널
API_ORIGIN=http://127.0.0.1:8100 npm run dashboard
```

기존 pblc 명령/환경변수 이름 일부는 호환용이다. 실제 자산은 TOKEN_ADDRESS와
PAYMENT_TOKEN_NAME/SYMBOL/VERSION(현재 AEGIS/AEGIS/1)으로 결정한다.
서버 시작 자체는 서명·송금하지 않으며 /request에서 명시적으로 실행해야 한다.

검증용 `node scripts/aegis_qwen_smoke.mjs`는 실제 로컬 Qwen + AA fixture +
임시 MongoDB + Mock Provider/Facilitator를 사용한다. 사용자 DB/키는 사용하지 않는다.

## 이번 검증 결과

- AEGISToken Solidity 15개 통과, 실제 배포 후 이름·심볼·소수점·잔액 확인.
- Python Qwen/AA 집중 검증 73개 및 후속 선택 설명 회귀 포함 집중 38개 통과(서로 중복되므로 합산하지 않음).
- commerce-gateway 124개, dashboard 35개(Node 26), runner script 24개 통과.
- 실제 Qwen 분류: 비용→price, 속도→speed, 복잡한 추론→intelligence, 충돌→default.
- 실제 Qwen + AA fixture + OpenAI/Claude/Gemini Mock 전체 E2E 3개 통과.
- 각 요청의 재실행도 성공하고 PAYMENT_SETTLED는 한 번만 기록됨. 임시 Mongo는 종료 후 정리됨.
- 재실행마다 달라지는 모의 관측시간을 결과 동일성 해시에서 제외해 delivery409를 수정함. 내용 변경은 여전히 거부함.
- dashboard build, 기본 lint/타입 검사, schema validator 통과.
- live 로컬 서버 시작과 /health의 AEGIS/live 확인. 시작 시 서명·송금 없음. AA는 현재 fixture, Provider는 Mock.
- 이번에는 광범위 전체 Python 반복·보안/성능 강화·전체 outbound 계측을 수행하지 않음.
