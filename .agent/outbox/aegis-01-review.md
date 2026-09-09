# AEGIS-01 provisional focused review

2026-09-09. Coder 작업 중 파일을 읽은 잠정 결과이며 최종 commit 재확인이 필요하다. runtime02/UI03·실제 API·DB·키는 검사하지 않았다. 아래 재현은 테스트 fixture와 in-memory repository만 사용했다.

## 차단 발견

1. **[P1] snapshot 원본과 전체 후보를 감사 재계산의 입력으로 사용하지 않는다.** `core/aa_audit.py::recalculate_decision`은 `DECIDED.candidates[].source`를 신뢰하고 `_snapshot_binding_issues`는 ID/hash 문자열만 대조한다. `core/audit.py::_aa_deterministic_findings`에 전달되는 snapshot도 원본 문서가 아닌 reference event다. 같은 snapshot에서 일부 후보를 제외하거나 원본 수치와 계산값을 함께 바꾼 결정이 정상 판정될 수 있다. 재현: `test_aegis_audit._decided_events(budget_units=3000)`의 초과예산 후보를 `candidates`와 `rejected`에서 모두 제거하면 감사 rule은 `AUD-PAYMENT-PENDING` 하나뿐이다. 검증한 snapshot 원본 및 당시 고정 catalog 전체 집합에서 수치·mapping·후보 완전성을 재구성해야 한다.

2. **[P2] 우선순위 분류 타당성 검사가 없다.** `core/aa_audit.py::_weights`는 저장된 `effective_priority`와 가중치만 비교하며 `original_priority`, `priority_reason`, matched 근거를 검증하지 않는다. 재현: 기존 default 요청의 REQUESTED normalized 값을 `original_priority='price'`, `priority_reason='explicit_priority'`로 변경해도 감사 rule은 `AUD-PAYMENT-PENDING` 하나뿐이다. 명시 우선순위 불일치와 분류 근거 변조를 검출하고, 원문 기반 분류 확인이 불가능한 경우 검증 완료로 간주하지 않도록 해야 한다.

3. **[P2] Decimal 직렬화가 올림 이전 단가를 바꾼다.** `core/aa_policy.py::decimal_text`의 `value.normalize()`는 기본 Decimal context 정밀도 28자리로 반올림한다. 허용된 유한 Decimal `1.0000000000000000000000000001`이 저장 시 `1`이 된다. input=1, output=1, outputPrice=0에서 원값의 amountUnits는 2, 저장 후 값은 1이다. snapshot 모델 수치가 이 함수로 저장되므로 실제 결정 경로에도 원본과 다른 금액이 들어갈 수 있다. 정밀 손실 없는 십진 문자열 보존 및 경계 round-trip 검증이 필요하다.

## 확인한 정상 경계

- 기본 시간 field path는 공식 nested `performance.median_end_to_end_response_time_seconds`로 수정돼 있다.
- root index version은 Decimal로 보존해 분수 버전을 잘라내지 않는다. 정확한 AA ID/slug mapping, 중복 ID/페이지/version 및 필수 null/비정상값 중단이 구현돼 있다.
- 가격 본 연산은 Fraction으로 정확한 ceiling을 수행하며, filter 이후 후보만 비율의 분모에 쓰고 exact score로 동점을 정렬한다.
- snapshot 별도 문서 저장은 insert-only이고 기존 history reader 분기를 유지한다. 외부 Mongo 회귀 실행은 이번 리뷰 범위에서 하지 않았다.
- 알려진 진행 중 AA wire 관찰: `adapters/artificial_analysis.py`는 아직 `page_size` 쿼리를 보낸다. 기존 `.agent/outbox/aegis-01-contract-check.md` 조치 후 최종 재확인 필요.

최종 commit 전 잠정 판정.

## 수정 재검토

기존 세 발견 및 HTTP page_size 관찰을 수정 후 현재 코드로 재확인했다.

- P1 해결: AuditService가 참조 immutable 문서를 로드하고 purchase/hash 무결성을 검사한다. 재계산은 snapshot에 캡처한 catalogEntries 전체와 models 수치에서 시작하며 결정의 후보 집합·source·수신자·기능을 대조한다. 후보 삭제, 원가와 파생 수치 동시 변경, snapshot body 변조에 대한 회귀가 통과한다.
- priority P2 해결: 명시 priority/사유/매치 근거와 결정의 전체 분류 payload를 대조하고 사전 밖 키워드를 탐지한다. keyword 분류의 원문 재도출은 수행하지 못하는 한계로 CAUTION을 남기며 검증 완료로 주장하지 않는다.
- Decimal P2 해결: normalize 호출을 제거하고 format 및 문자열 trailing-zero 처리로 원본 자릿수를 보존한다. 고정밀 단가 snapshot round-trip과 경계 올림 회귀가 통과한다.
- HTTP 관찰 해결: 실제 adapter 쿼리는 `page`만 전송하고 response의 page_size는 검증한다. mocked HTTP wire 회귀가 통과한다.

Reviewer 실행: `.venv/bin/python -m pytest tests/test_aegis_audit.py tests/test_aegis_policy.py tests/test_aegis_snapshot.py -q` → **94 passed**, 기존 websockets deprecation warning 1건. 외부 API/DB/네트워크 및 runtime02/UI03는 실행·검토하지 않았다.

위 scoped 발견은 모두 해결됐다. 이는 AEGIS-01 현재 diff의 focused 승인으로 실제 AA 계약 검증이나 전체 시스템 완료 승인이 아니다.

VERDICT approve
