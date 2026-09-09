# 원문 기반 priority 감사 — 완료 조건 판단

**권고: 최종 로컬 완료 전 기존 서버 복호화 경계에서 원문을 재분류한다.** 현재 CAUTION은 정직한 제한 표시로는 적절하지만 `requirements.md:65`의 “요청에 따른 priority 타당성” 검사 전체를 충족하지 않는다. 이는 새 기능/보안 범위가 아니라 이미 승인된 감사 요구의 남은 연결 작업이다. 별도 승인 단계는 필요 없다.

## 현재 연결 가능한 근거

- `core/purchase_service.py:39-59`: raw_request를 kind=`request`로 암호화하여 sensitivePayloads에 저장한다. REQUESTED에는 `sensitivePayloadId`, `rawRequestHash`, purchaseId와 normalizedRequest가 연결된다. 따라서 원문 부재가 아니라 현재 감사 입력 경로의 미연결이다.
- `core/ports.py:270-272,305`: 기존 EvidenceRepository.get_sensitive_payload와 PayloadCipher.decrypt_json 포트가 있다. `api/app.py`의 기존 서버 처리도 container.repository/container.cipher를 사용하므로 새 브라우저 원문 API나 권한 확장이 필요하지 않다.
- `adapters/crypto/local_aes_gcm.py::decrypt_json`: purchaseId/kind를 AES-GCM AAD로 사용하고 복호화 plaintext의 content hash를 확인한다. 감사 연결 시 추가로 REQUESTED의 payload ID/purchaseId/kind=`request`/rawRequestHash가 저장 payload와 일치하는지 검사해야 한다.
- `core/audit.py:1218`: AuditService는 현재 repository/clock/semantic_advisor만 받는다. `_referenced_documents`는 snapshot만 로드한다. 이 서비스 또는 기존 composition에서 좁은 request-classification reader를 주입할 수 있다. core가 domain 구현을 import하지 않도록 기존 순수 함수 `core/aa_policy.py:197::classify_priority`를 재사용한다.
- `core/aa_audit.py:432`: 현재는 normalized/DECIDED끼리 일관성을 검사하고 원문 재도출이 불가능하다고 CAUTION을 낸다. 저장된 두 결과가 함께 잘못돼도 원문과의 불일치를 확정할 수 없는 상태다.

## 최소 연결

감사 서버가 REQUESTED의 암호화 요청을 조회하고 연결·무결성을 확인한 뒤 메모리에서만 `classify_priority(prompt=raw prompt, explicit=raw priority)`를 호출한다. prompt 자체를 evaluator/public_events/LLM semantic advisor/감사 finding·로그로 전달하지 않는다. 결정적 분류 결과(정책 이름, effective priority, reason, 매치 근거)만 기존 normalized/DECIDED와 대조한다. 명시 priority도 raw_request에서 재확인한다.

맞으면 이 부분의 미검증 CAUTION을 없앨 수 있다. 다르면 분류 불일치 RISK를 낸다. payload 누락/키 사용 불가/복호화 실패는 기존 역사 호환을 유지하며 CAUTION 또는 명시 미검증으로 남기고, hash/purchase binding 위반은 무결성 오류로 구분한다. 오류 메시지에 plaintext를 포함하지 않는다. 이미 저장된 과거 audit를 이 작업을 이유로 덮어쓰지 않는다.

## 필요한 focused 검증

정상 keyword/충돌/default/명시 override; normalized와 DECIDED를 동시에 변조해도 원문 비교에서 검출; 다른 purchase payload 또는 rawRequestHash 치환 검출; payload 누락/복호화 실패의 명시 미검증; 감사 출력/semantic advisor 입력에 원문 비노출. 기존 encrypted store를 사용한 로컬 integration 한 건을 포함한다.

AEGIS-01 scoped 리뷰 승인을 소급 취소할 사안은 아니다. 최종 AEGIS-02/03 연결에서 기존 감사 수용 조건을 마무리하고 제한 기록을 실제 검증 결과로 갱신하면 된다.
