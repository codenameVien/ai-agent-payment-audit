# AEGIS-02 provisional review

현재 완성된 `core/aegis_payment.py` 및 `api/app.py`/`api/schemas.py` 신규 internal route만 검토했다. Gateway/Signer/composition은 구현 중이므로 미작성 기능을 결함으로 판단하지 않는다. 최종 artifact/commit 승인 판정은 보류한다.

## 현재 발견

- **[P1] Facilitator network와 선택한 chain의 결속 누락.** `core/aegis_payment.py:356` (`AegisPaymentService.settle`)은 network가 비어 있지 않은지만 검사한다. `terms.token.chainId`와 다른 network의 응답도 SETTLED 전이에 전달된다. 선택 조건과 Facilitator 응답 대조 요구를 충족하려면 정확한 예상 CAIP network와 비교해야 한다.
- **[P2] Mock 실행을 요청값으로 live 표시 가능.** `core/aegis_payment.py:55,327`, `api/schemas.py:845,853,866` 및 `api/app.py:2185`는 settle/fail/delivery의 실행 모드를 클라이언트가 `live`로 지정하게 한다. 별도 서버 mode 결속이 없고 settle 증거는 동시에 `SYNTHETIC_LOCAL`이다. 이번 mock-only runtime에서는 서버에서 mock으로 고정하거나 불일치를 거부해야 한다.

순수 로컬 method 재현: 실제 fixture decision의 chainId=84532를 사용하고 `_transition_context`와 repository transition만 AsyncMock으로 대체했다. `settle(facilitator_network='eip155:1', execution_mode='live', ...)`를 호출하면 거부 없이 repository transition에 `facilitatorNetwork=eip155:1, executionMode=live, evidenceSource=SYNTHETIC_LOCAL`이 전달된다. DB·네트워크·키 접근 없음.

## 조건부 관찰 — Gateway 완료 시 확인

`api/schemas.py:815`의 AegisPaymentTermsResponse.amount_units/budget_units 및 기존 중첩 PaymentIntentResponse는 JSON integer다. Python에서 안전한 uint256도 JS JSON Number로 읽으면 2^53 이상에서 달라진다. 완료된 Gateway/Signer가 Number.isSafeInteger로 거부하거나 exact string/lossless parse 경로를 쓰는지 확인할 때까지 이 항목은 최종 결함으로 확정하지 않는다. Number로 반올림한 다음 BigInt로 바꾸는 처리는 불가하다.

## 현재 확인한 보존 경계

새 internal route마다 require_internal을 호출하며 server bearer 비교를 유지한다. reserve는 기존 repository의 원자 intent/budget/event claim을 사용하고 terminal 전이는 성공/실패 상태를 재개방하지 않는다. 실제 Mongo 경합 검증이나 전체 suite는 이번 provisional review에서 실행하지 않았다.

최종 판정 보류: 구현 진행 중.
