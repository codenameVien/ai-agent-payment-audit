# AEGIS-03 original-priority focused review

현재 미커밋 original-priority 구현을 `aegis-priority-audit-note.md`의 승인 범위로 검토했다. 원문 reader, 정책 입력 공유, AA 재계산 연결, AuditService/API 주입 및 focused test만 대상이다. E2E harness·UI·전체 suite·외부 API/DB는 포함하지 않는다.

## 확인 결과

- StoredRequestClassifier는 REQUESTED의 sensitivePayloadId로 조회한 payload의 purchaseId, kind=request, content_hash/rawRequestHash를 복호화 전에 검사한다. 기존 cipher는 purchaseId/kind AAD 및 복호화 평문 hash 검사를 유지한다.
- request normalization과 감사가 동일 classification_inputs 및 classify_priority를 사용한다. 재도출한 effective/original priority, reason, 고정 사전 매치 근거를 normalized 결과와 대조하며, 기존 normalized/DECIDED 비교와 함께 두 기록을 공동 변조한 경우도 검출한다.
- 원문은 reader 내부 메모리에서만 사용한다. 반환은 고정 정책 결과뿐이며 cipher 예외 내용은 폐기한다. 누락/복호화 실패는 명시적 미검증, purchase/kind/hash 결속 불일치는 무결성 finding으로 구분한다.
- AuditService는 신규 AA 요청만 reader로 보내고 기존 audit가 있으면 재생성 전에 반환한다. semantic advisor에는 기존 public_events만 전달한다. API에는 원문 공개 endpoint를 추가하지 않는다.
- 직접 `.venv/bin/python -m pytest tests/test_aegis_priority_audit.py -q`: **12 passed**, 기존 dependency deprecation warning 2건. 실제 LocalEnvelopeCipher + in-memory repository 기반이며 keyword/conflict/default/explicit, 공동 변조, 다른 purchase/hash 치환, 누락/복호화 실패, audit/advisor/public chain 원문 비노출을 확인했다.

이 범위에서 구체적인 차단 결함을 발견하지 않았다. 판정은 현재 읽은 미커밋 구현 기준이며 이후 동작 변경 시 재확인이 필요하다.

VERDICT approve
