# AEGIS-03 harness provisional review

현재 작성 중인 Phase 6 harness, outbound guard, local stack diff, Mongo payment test를 읽기 전용 검토했다. 결과/커밋 전 판정이며 broad suite·실제 DB·API 실행은 하지 않았다.

## 남은 발견

1. **[P1] Python hop은 outbound 실측/차단 범위 밖이다.** `scripts/aegis_local_stack.mjs`의 evidence-api는 `baseEnv()`로 uv/Python을 실행한다. `aegis_outbound_guard.cjs`는 Node net/dns만 감싸므로 서버 AA/httpx 호출은 관측하지 않는다. Node transport의 실제 counter는 기존 constant zero보다 개선됐지만 “모든 hop” 검증으로 계산할 수 없다. 승인된 외부 게이트 검증 범위에 맞게 Python transport에도 실패 전 차단/계수를 연결하고 그 경로를 통과하는 거부 테스트가 필요하다. 또한 phase6 마지막 oracle은 `rejected.length >= 2`만 확인해 의도한 2건 외에 실제 flow의 잘못된 외부 시도도 통과시킨다. 의도적 probe와 flow를 구분해 flow의 추가 거부를 실패시켜야 한다.

2. **[P1] 역사 zero-write 이름에 해당하는 fixture 검증이 없다.** `scripts/aegis_phase6_scenarios.test.mjs`의 `read paths are zero-write for both new and historical evidence`는 createPurchase로 신규 AEGIS만 만든다. 기존 `test_aegis_payment.py::test_the_legacy_composition_still_serves_its_historical_surfaces`는 별도 legacy 모드의 route/auth 존재 검사이며 이 oracle의 대체가 아니다. `test_aegis_audit.py`의 과거 정책 제외도 evaluator 단위다. 신규 default 구성에서 저장된 PBLC/당시 계약/평판·anchor 감사 fixture를 읽고 원래 데이터/chain이 유지됨을 확인하는 isolated fixture 검증을 추가해야 한다. 기존 사용자 DB 사용은 필요하지 않다.

3. **[P2] 새 Mongo test의 실패 경로 cleanup 누락.** `test_aegis_payment_mongo.py::test_a_real_mongo_payment_attempt_survives_a_reopened_store`의 첫 try/finally는 repository.close만 한다. ensure_indexes/reserve/settle/assert 중 실패하면 아래 reopened try에 도달하지 않으므로 cleanup.drop_database 및 cleanup.close가 실행되지 않는다. 전체 테스트를 외부 cleanup finally로 감싸 생성한 UUID DB만 항상 회수해야 한다. 기존 사용자 DB 삭제 문제는 발견하지 않았다.

## 확인한 범위

- 3 provider별 HTTP run/amount/event 개수, mapping/null/index version/예산 중단, 동시 실행과 완료 재실행, 전체 서비스 재시작 후 Mongo chain 유지 시나리오가 있다.
- 기존02 TS tests에 tampered402, payload mismatch, timeout reconciliation, provider 실패 후 no-repay, unsafe integer, nonce/durable restart/contradictory success가 있어 같은 테스트를 여기 중복 요구하지 않는다.
- local stack의 저장소 옵션 금지와 기존 소유 PID/dbPath 검사는 유지되며 services/infra 분리로 restart가 같은 Mongo를 사용한다. 종료는 서비스 종료 뒤 owned infra 회수 순서다.
- 실제 실행 결과는 아직 받지 않았으며 위 발견은 현재 소스 기준이다.

## Provider execution blocker after review

Main relayed all three findings to the same Opus session. The resumed call exited1 on HTTP429 `rate_limit_error`, retry-after-ms8799000 (~2h27m), before completing those fixes. Backend03 working changes are preserved. This is a provider availability blocker, not an approved model substitution and not a successful final verification.

VERDICT block
