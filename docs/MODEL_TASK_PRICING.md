# 모델별 표준 작업 결제 조건

상태: **Mock Provider/Mock Facilitator 검증용. 실제 Provider API 호출·온체인 전송은 미실행.**

판매자는 회사가 아니라 정확한 모델 ID 하나로 고정한다. 구매 에이전트가 후보를 비교한 뒤 한
모델을 선택하면, 그 모델의 seller 수신 지갑에 x402 `exact` 금액을 한 번만 승인한다.

| Provider | 고정 API model ID | seller 수신 지갑 | 공개 표준 단가 (입력/출력, $/1M) |
|---|---|---|---:|
| OpenAI | `gpt-4.1-mini-2025-04-14` | `0xF00E8e0ecEF3B405250242F932849edB409497a0` | $0.40 / $1.60 |
| Anthropic | `claude-haiku-4-5-20251001` | `0xC77492d3AD5c97B09C6529284e71c417Be8926d8` | $1.00 / $5.00 |
| Google | `gemini-2.5-flash` | `0x5363903850f7AD4bF5FdbceFe9769A9e5b7080E4` | $0.30 / $2.50 |

단가는 각 Provider의 공개 문서를 기준으로 catalog fixture에 반영했다. [OpenAI GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini), [Anthropic Claude Haiku 4.5](https://www.anthropic.com/claude/haiku), [Gemini 2.5 Flash](https://ai.google.dev/gemini-api/docs/pricing?hl=en)를 확인했다. AA의 benchmark time/intelligence 값은 여전히 **fixture**이며 위 가격표도 live AA snapshot이라고 부르지 않는다.

## 표준 작업 견적

`standard-answer-v1`은 최대 출력 `8,000` tokens를 결제 전에 고정한다. 금액은 아래 식으로
정확히 계산한 뒤 PBLC 6-decimal raw unit으로 올림한다.

`amountUnits = ceil(estimatedInputTokens × inputPricePerMillion + 8,000 × outputPricePerMillion)`

이는 `1 PBLC = 명목 1 USD`, 6 decimals와 1M-token 가격 기준이 상쇄된 raw-unit 식이다. markup은
없고, 실제 사용량 사후 정산도 아니다. 입력 토큰은 요청과 시스템 프롬프트의 UTF-8 바이트/4
추정치라 약간 달라질 수 있지만, 선택 전 후보별 금액을 모두 확정해 증거에 기록한다.

짧은 요청(추정 입력 25 tokens)에서의 상한 예시는 다음과 같다.

| 모델 | raw units | PBLC (명목 USD) |
|---|---:|---:|
| OpenAI GPT-4.1 mini | 12,810 | 0.012810 |
| Claude Haiku 4.5 | 40,025 | 0.040025 |
| Gemini 2.5 Flash | 20,008 | 0.020008 |

## 실제 AA API 결과

2026-09-10에 서버 환경의 AA 키로 `GET /api/v2/language/models/free`를 읽기만 했다. 4페이지의
현재 응답에는 위 세 정확한 provider model ID/slug가 없어, 이 endpoint만으로 live AA mapping을
만들 수 없었다. 정확한 mapping이 없으면 시스템은 live AA 모드에서 결제 전에 중단해야 한다.
따라서 현재 실행은 fixture AA + Mock Provider/Facilitator로 유지한다.
