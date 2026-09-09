# AEGIS-01 결과

2026-09-09. branch `feature/aegis-aa-v1`. 로컬 구현만 수행했고 실제 AA/Provider/체인/AWS 호출은 하지 않았다.

## 구현 파일

신규
- `core/aa_policy.py` 결정적 정책 원시함수: 네 priority·고정 가중치·키워드 분류·utf8-bytes-div4-v1 토큰 추정·Fraction 정확 ceiling·세 요인 점수·tie-break·hard filter 사유
- `core/aa_audit.py` snapshot 기반 독립 재계산
- `core/documents.py` content-addressed immutable 문서와 원본 bytes hash
- `adapters/artificial_analysis.py` live(page 전용 query)·fixture capture adapter
- `domains/ai_inference/aa_models.py|aa_catalog.py|aa_request.py|aa_selection.py|aa_ports.py|aa_workflow.py`
- `packages/schemas/domains/ai_inference/aa-snapshot.schema.json`, `aa-decision.schema.json`
- `packages/schemas/fixtures/aa/` 2페이지 AA fixture, fixture catalog, 교차언어 pricing 케이스
- 테스트 `tests/test_aegis_{policy,snapshot,workflow,audit,api,schema_contract,mongo}.py`

변경
- `core/models.py` `AA_SNAPSHOT_RECORDED`, `ImmutableDocument`
- `core/ports.py` `ImmutableDocumentPort` 및 repository 계약
- `core/errors.py` `EvidenceImmutabilityError`/`ExternalEvidenceError`/`SelectionAbortedError`
- `core/audit.py` policy discriminator 분기와 신규 rule set, snapshot 원본 로딩
- `adapters/repositories/memory.py|mongo.py` insert-only 문서 저장과 `unique_aa_snapshot_per_purchase`
- `api/app.py|api/schemas.py` `/purchases/{id}/decide` 신규 분기, `GET /internal/purchases/{id}/aegis-decision`
- `composition.py|settings.py|.env.example` fail-closed 배선, AA 키는 서버 환경변수만
- `domains/ai_inference/module.py` `requestSchema: aegis-aa-v1` 분기, 과거 정규화 보존
- `scripts/validate_schemas.mjs` 스키마 경계와 JS 재계산 교차검증
- `tests/test_mongo_repository.py` 신규 collection recorder 1줄

## 리뷰 지적 처리

1. **P1 snapshot/전체 후보 재계산**: 재계산 입력을 snapshot immutable 문서로 바꿨다. capture 시점 catalog 전체를 `catalogEntries`로 snapshot에 보존하고, 후보 집합·AA 수치·capabilities·recipient를 모두 snapshot에서 재구성한다. `AuditService`가 문서를 읽어 hash를 검증한 뒤 전달하며, 문서가 없으면 `AUD-AA-SNAPSHOT-BODY-UNAVAILABLE`로 미검증을 명시한다. 재현 테스트: `test_a_candidate_erased_from_the_comparison_is_detected`(초과예산 후보를 candidates·rejected에서 동시에 삭제), `test_a_price_rewritten_together_with_its_derived_numbers_is_detected`(단가와 금액을 함께 정합적으로 변조), `test_a_recipient_swapped_after_capture_is_detected`, `test_audit_service_loads_the_snapshot_body_it_recomputes_from`. 원본 페이지 hash도 재계산한다(`AUD-AA-RAW-RESPONSE-HASH-MISMATCH`).
2. **P2 우선순위 근거 검증**: `original_priority`/`priority_reason`/matched 근거/분류 방법/가중치를 전부 대조하고 사유별 정합성(explicit↔original 일치, keyword_match는 단일 매치, conflicting은 default 등)과 사전 소속을 검사한다. 원문은 증거에 없으므로 키워드 분류는 `AUD-AA-PRIORITY-CLASSIFICATION-UNVERIFIABLE`(CAUTION)로 재도출 불가를 명시하고 검증 완료로 표기하지 않는다. 재현 테스트: `test_an_invented_explicit_priority_is_detected`, `test_a_fabricated_match_keyword_is_detected`, `test_keyword_classification_is_reported_as_not_re_derivable`.
3. **P3 Decimal 무손실 직렬화**: `decimal_text`에서 context 반올림을 유발하는 `normalize()`를 제거하고 문자열 포맷으로 바꿨다. `completion_ms_from_seconds`도 곱셈 대신 지수 shift로 정확하게 변환한다. 재현 테스트: `test_decimal_text_preserves_every_published_digit`(28자리 초과 값 round trip과 금액 3 units), `test_completion_ms_conversion_is_an_exact_exponent_shift`.
4. **AA wire 계약**: 완료시간 경로를 `performance.median_end_to_end_response_time_seconds`로 수정하고 root 값만 있는 응답은 중단한다. 미문서화 `page_size` 쿼리를 제거했고 응답 `pagination.page_size`는 계속 검증한다. mocked HTTP 계약 테스트로 query·헤더·키 비노출을 확인한다. 분수 index version과 slug 없는 creator 회귀도 추가했다.

## 검증

- `pytest -m 'not mongo'` 432 passed
- `bash scripts/test_mongo_local.sh` 11 passed (실제 로컬 mongod, 임시 DB만 사용, 기존 기록 미접근)
- `ruff check scripts services/buyer-audit-api` 통과, `mypy services/buyer-audit-api/src` 통과
- `npm run test:schemas` 통과, pricing 8케이스 Python/JS 동일 결과

## 한계와 남은 게이트

- AA 실제 API 검증 미완료(서버 키 없음). fixture 계약 통과는 실측이 아니며 snapshot `mode=fixture`, catalog `mappingProvenance=fixture`로 기록된다.
- AEGIS 토큰은 `status=prepared`, 주소는 zero address. 배포·이동·실결제는 승인 경계로 남는다.
- 결제/402/Facilitator/전달 규칙과 UI는 AEGIS-02·03 범위다. 현재 신규 구매 감사는 정산 증거가 없어 `AUD-PAYMENT-PENDING`(CAUTION)으로 끝난다.
- `docs/AA_API_CONTRACT_CHECK.md`는 오케스트레이터 소유라 수정하지 않았다. 구현이 의존하는 정확한 필드 경로는 그 문서의 갱신 내용과 일치한다.

## AEGIS-02 연결

- `AegisEvidenceReader.decision_evidence` / `GET /internal/purchases/{id}/aegis-decision`가 고정 결정·snapshot 원본·`termsBindingHash`를 제공한다. Gateway와 결제 실행 모듈은 Mongo에 직접 접근하지 않고 이 증거로 금액을 재계산하면 된다.
- `terms_binding_hash`는 purchaseId·provider/model/version·amountUnits·recipient·token identity·snapshotHash·policy version을 묶는다. 402 발급과 서명 대조에 그대로 쓸 수 있다.
- `core/aa_policy.rejection_reasons`/`rank_candidates`/`amount_units`는 재사용 가능한 순수 함수이며 `packages/schemas/fixtures/aa/pricing-cases.json`이 TypeScript 구현과의 교차검증 기준이다.
