# Artificial Analysis 계약 확인

- 확인일: 2026-09-09
- 공식 원본: https://artificialanalysis.ai/data-api/docs
- 적용 endpoint: `GET https://artificialanalysis.ai/api/v2/language/models/free`
- 확인 범위: 공식 문서 확인. 인증된 실제 응답 검증은 아직 하지 않았다. 서버 키가 설정된 뒤에만 수행한다.

서버에서 `x-api-key`를 보내며 키는 클라이언트로 전달하지 않는다. 목록은 `page` 쿼리와 `pagination.page/page_size/total_pages/has_more`로 순회한다. 응답의 `tier`, 최상위 숫자형 `intelligence_index_version`, `data` 배열을 파싱한다. 버전은 모델 안의 필드가 아니다.

무료 응답의 입력·출력 단가, median 완료시간, Intelligence Index를 사용한다. null은 미측정 또는 해당 없음이며 0으로 해석하지 않는다. 완료시간은 기본적으로 답변 500토큰인 벤치마크 조건의 참고치다. 사용자 요청의 SLA나 실제 사용량 정산 근거로 표현하지 않는다.

공식 Free endpoint의 응답 예제에서 재확인한 정확한 모델별 경로는 `pricing.price_1m_input_tokens`, `pricing.price_1m_output_tokens`, `performance.median_end_to_end_response_time_seconds`, `evaluations.artificial_analysis_intelligence_index`다. 루트 `median_end_to_end_seconds`는 이 계약의 필드가 아니다. 루트 `intelligence_index_version`은 `4.1` 같은 소수형 숫자도 허용하며, 정수로 잘라 보존하지 않는다. 공식 예제의 `model_creator`는 `id`와 `name`을 포함한다. 모델 `slug`와 별개인 creator slug를 필수로 요구하지 않는다.

화면에 Artificial Analysis 출처 링크를 표시한다. 모든 페이지와 index version을 보존하고 모델 설정과 정확히 mapping한다. 문서 확인만으로 fixture 수치 또는 fixture 모델 ID가 실측·실제 API ID라고 주장하지 않는다.

PBLC 재사용 결정: `infra/contracts/src/DemoTokenV2.sol`의 name/symbol은 constant이고 setter 및 upgrade 기능이 없다. 따라서 새 토큰으로 개명하지 않고, 배포된 PBLC V2 계약을 신규 흐름의 결제 조건으로 사용한다. PBLC 과거 증거는 기존 주소·이름 그대로 읽으며, 실제 자산 이동·테스트넷 결제는 별도 승인 전 수행하지 않는다.

## 실제 AA snapshot 연결 절차

1. 사용자 로컬 터미널에서 `bash scripts/set_aa_api_key.sh`를 실행해 `.env.local`에만 키를 저장한다. 키는 채팅이나 git에 넣지 않는다.
2. 인증된 `/language/models/free` 응답에서 실제 `id`와 `slug`를 확인한 뒤, `model-catalog.live.example.json`을 복사해 provider 모델 ID·버전과 **정확히** 대응시킨 catalog를 만든다.
3. `.env.local`에 `AA_MODEL_CATALOG_PATH=/절대/경로/실제-catalog.json`을 설정한다. fixture catalog는 live key와 함께 사용할 수 없다.
4. 서비스는 pagination을 모두 읽고 mapping·필수 지표·index version을 검사한다. 하나라도 없으면 결제 전 중단하며, 임의 점수를 만들지 않는다.

이 프로젝트는 AA snapshot만 실제 API에서 읽을 수 있게 준비한다. Provider 응답과 x402 Facilitator 정산은 여전히 Mock이며, 테스트넷 전송·AWS 배포는 이 절차에 포함하지 않는다.
