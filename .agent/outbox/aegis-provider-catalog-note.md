# AEGIS-02 실행용 Provider catalog 연결

2026-09-09. 기존 정확한 모델 ID/version 요구의 통합 메모이며 새 packet·승인·Provider API 연결을 추가하지 않는다. 아래 공식 ID 근거는 오케스트레이터가 오늘 확인한 문서다. 이 메모 작성 과정에서 API/키를 사용하지 않았다.

| Provider | 실행용 Mock Gateway의 provider model ID | 버전 표현 | 근거 |
|---|---|---|---|
| OpenAI | `gpt-4.1-2025-04-14` | pinned snapshot `2025-04-14` | https://developers.openai.com/api/docs/models/gpt-4.1 |
| Anthropic | `claude-sonnet-5` | canonical pinned ID `claude-sonnet-5`; 존재하지 않는 날짜 suffix 추가 금지 | https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions |
| Google | `gemini-2.5-flash` | stable model `gemini-2.5-flash`; 문서 release `2025-06` | https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash |

Gemini의 release month는 감사용 문서 metadata이며 실제 immutable revision ID라고 표현하지 않는다. 별도 정확 revision이 확인되지 않았으므로 날짜 기반 suffix나 임의 hash를 만들지 않는다. 이 stable model ID만으로 백엔드 revision 고정을 보장한다고 주장하지 않는다.

## 적용

- `fixture-openai-alpha` / `fixture-anthropic-beta` / `fixture-google-gamma` 같은 generic fixture는 unit test의 독립적인 계약 검증에 남겨도 된다. 사용자가 실행하는 Mock demo catalog와 Gateway 응답/선택 증거에는 위 provider ID와 문서화된 version 표현을 사용한다.
- Provider ID와 AA ID는 별개의 이름 공간이다. runnable mock catalog의 AA ID/slug/가격/시간/index는 명시적 synthetic fixture로 유지하고 source/mode를 fixture로 기록한다. 실제 모델 이름을 붙였다는 이유로 이 숫자가 해당 모델의 실제 AA 측정값이 되는 것은 아니다.
- 화면과 감사 상세에는 `Mock Provider 실행`, `AA synthetic fixture — 실제 측정값 아님`을 함께 표시한다. Provider 문서가 검증한 것은 model identifier이고 AA 데이터/mapping 검증이 아님을 구분한다.
- live AA adapter는 synthetic fixture mapping을 거부한다. 운영 catalog에 실제 AA id/slug를 명시하고 authenticated AA 응답과 정확히 일치하는지 검증하기 전에는 구매를 중단한다. 이름 fuzzy matching이나 가공한 AA UUID로 이 게이트를 통과시키지 않는다.
- Gateway는 provider model ID/version과 snapshot/decision binding을 그대로 보존한다. 세 provider 각각 선택 가능한 고정 fixture 시나리오로 402→settlement→response E2E를 검증하되 실제 provider 호출·API 키 연결은 하지 않는다.

이 변화는 catalog/표시 fixture의 연결 정확도를 높이는 기존 범위 내 통합이며 AA 정책, 점수 산식, 결제 승인 경계를 변경하지 않는다.
