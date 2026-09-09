# Artificial Analysis 계약 확인

- 확인일: 2026-09-09
- 공식 원본: https://artificialanalysis.ai/data-api/docs
- 적용 endpoint: `GET https://artificialanalysis.ai/api/v2/language/models/free`
- 확인 범위: 공식 문서 확인. 인증된 실제 응답 검증은 아직 하지 않았다.

서버에서 `x-api-key`를 보내며 키는 클라이언트로 전달하지 않는다. 목록은 `page` 쿼리와 `pagination.page/page_size/total_pages/has_more`로 순회한다. 응답의 `tier`, 최상위 숫자형 `intelligence_index_version`, `data` 배열을 파싱한다. 버전은 모델 안의 필드가 아니다.

무료 응답의 입력·출력 단가, median 완료시간, Intelligence Index를 사용한다. null은 미측정 또는 해당 없음이며 0으로 해석하지 않는다. 완료시간은 기본적으로 답변 500토큰인 벤치마크 조건의 참고치다. 사용자 요청의 SLA나 실제 사용량 정산 근거로 표현하지 않는다.

화면에 Artificial Analysis 출처 링크를 표시한다. 모든 페이지와 index version을 보존하고 모델 설정과 정확히 mapping한다. 문서 확인만으로 fixture 수치 또는 fixture 모델 ID가 실측·실제 API ID라고 주장하지 않는다.

신규 PBLC 명칭 변경 검토: `infra/contracts/src/DemoTokenV2.sol`의 name/symbol은 constant이고 setter 및 upgrade 기능이 없다. `scripts/pblc_v2_setup.mjs`는 직접 계약 배포 경로다. AEGIS에는 새 계약이 필요하며 PBLC 과거 증거는 기존 주소·이름 그대로 읽는다. 새 배포는 승인 전 수행하지 않는다.
