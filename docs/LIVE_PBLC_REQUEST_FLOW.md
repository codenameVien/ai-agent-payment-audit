# 실제 PBLC 구매 요청 흐름

이 문서는 **Mock Provider 응답은 유지하면서**, `/request` 한 건에서 선택된 모델의 고정 PBLC 금액을 Base Sepolia에서 실제로 결제하는 로컬 시연 경로다. 유료 OpenAI·Claude·Gemini API 호출은 포함하지 않는다.

## 안전 경계

- 사용 지갑: `PBLC_USER_ADDRESS`와 같은 주소로부터 유도되는 `PBLC_USER_PRIVATE_KEY`만 결제 실행 모듈 프로세스에 둔다. 브라우저·구매 에이전트 프롬프트·MongoDB에는 전달하지 않는다.
- 결제는 사용자가 `/request`에서 실행 동의를 체크하고 제출한 뒤에만 시도된다. 런타임 시작이나 이 문서의 명령만으로는 서명·`/verify`·`/settle`·전송을 하지 않는다.
- 한 `purchaseId`의 성공 결제는 최대 한 번이다. 402 조건(금액·토큰·수신자)이 구매 결정과 다르면 서명 전에 중단한다.
- 결과 텍스트는 **Mock Provider 실행**이다. 실제 PBLC 전송 성공은 유료 모델 실행 성공을 뜻하지 않는다.
- 외부 Facilitator의 성공 응답을 결제 상태 근거로 저장한다. 앱이 독립 RPC로 Transfer를 재검증하거나 온체인 Anchor를 쓰지는 않는다.

## 고정 지급 대상과 최대 작업 가격

| Provider / 정확한 모델 ID | 수신 지갑 | 표준 작업 최대 가격 |
|---|---|---:|
| OpenAI / `gpt-4.1-mini-2025-04-14` | `0xF00E8e0ecEF3B405250242F932849edB409497a0` | 0.012810 PBLC |
| Anthropic / `claude-haiku-4-5-20251001` | `0xC77492d3AD5c97B09C6529284e71c417Be8926d8` | 0.040025 PBLC |
| Google / `gemini-2.5-flash` | `0x5363903850f7AD4bF5FdbceFe9769A9e5b7080E4` | 0.020008 PBLC |

가격은 표준 작업의 예상 입력과 최대 출력 8,000 tokens로 계산한 **사전 고정 결제액**이며, 실제 사용량 사후 정산이 아니다. 1 PBLC = 1 USD는 명목 계산 규칙일 뿐 담보·상환 약속이 아니다.

## 실행 순서

1. `.env.local`에 이미 설정된 사용자 PBLC 지갑과 MongoDB/internal token 값을 확인한다. 개인키는 화면이나 채팅에 붙여넣지 않는다.
2. 읽기 전용 점검을 실행한다.

   ```bash
   npm run pblc:payment:preflight
   ```

   `balance`이 0보다 크고 `supportsV2ExactBaseSepolia`가 `true`인지 확인한다. 이것은 PBLC 커스텀 토큰 수락을 증명하지 않는다.

3. 실제 모드를 명시적으로 켠다. 이 명령은 `.env.local`에 모드 플래그만 쓰며 결제를 보내지 않는다.

   ```bash
   npm run pblc:live:enable
   # 프롬프트에 ENABLE_LIVE_PBLC_PAYMENT 입력
   ```

4. 첫 터미널에서 실제 결제 런타임을 시작한다.

   ```bash
   npm run aegis:live-payment
   ```

   출력의 `api` 주소를 기억한다. 이 프로세스는 기존 MongoDB를 지우거나 초기화하지 않는다.

5. 둘째 터미널에서 대시보드를 연다.

   ```bash
   API_ORIGIN=http://127.0.0.1:8100 npm run dashboard
   ```

6. `http://127.0.0.1:3000/request`에서 예산을 `0.05 PBLC` 이상으로 입력하고, 실제 결제 고지를 읽고 동의한 뒤 한 번 제출한다. 화면 배지가 `실제 PBLC 결제 · Mock Provider`인지 먼저 확인한다.

7. 응답 뒤 `/dashboard`에서 해당 `purchaseId`의 선택 모델·고정 금액·Facilitator 응답·감사 결과를 확인한다. 실패·확인 불가 상태에서 같은 요청을 새 `purchaseId`로 재시도하지 않는다.

## 외부 게이트

기본 Facilitator는 `https://x402.org/facilitator`다. `/supported`가 Base Sepolia의 x402 v2 `exact`를 표시해도 이 사용자 정의 PBLC를 실제로 받는다는 보장은 아니다. 첫 실제 요청의 `verify`가 거부되면 전송 없이 실패 기록을 남기고 중단한다. CDP Hosted Facilitator를 선택하는 경우에는 공식 CDP 인증 방식으로 서버 전용 자격증명을 구성해야 한다.

AWS 배포와 유료 Provider API 호출은 이 경로에 포함되지 않는다.
