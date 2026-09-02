# Verification Boundary 2 — 최초 결과

Date: 2026-09-02
Decision: `Request changes`

독립 리뷰는 P1 네 건과 P2 한 건을 발견했다.

1. 판매자 서비스에 Facilitator `/verify`·`/settle`과 `PAYMENT-RESPONSE` 성공 경로가 없었다.
2. ERC-8004 신원이 호출자 Boolean으로 들어오며 결제 흐름의 온체인 signer 검증과 연결되지 않았다.
3. MongoDB wallet policy 전체 교체가 동시 claim의 `reservedUnits` 증가를 잃을 수 있었다.
4. EvidenceAnchor 제출 hash를 성공 receipt와 정확한 이벤트 확인 전에 기록했다.
5. `AUTHORIZED` 직후 중단되면 고정 nonce 결제를 재개하지 못했다.

## 수정 결과

- 판매자 `FacilitatorPaymentGate`가 PAYMENT-SIGNATURE를 견적과 대조하고 `/verify → provider → /settle` 순서를 지킨 뒤 PAYMENT-RESPONSE를 반환한다.
- `erc8004AgentId`가 EIP-712 견적의 서명 필드가 됐고, Commerce Gateway가 결제 전 registry agent wallet과 signer를 다시 대조한다.
- wallet policy 교체는 `spentUnits`/`reservedUnits` CAS를 사용하며 실제 Mongo 경합 테스트가 예약 보존을 확인한다.
- EvidenceAnchor는 성공 receipt와 정확한 `EvidenceAnchored` event를 확인한 뒤에만 off-chain evidence를 기록한다.
- 기존 payment intent는 terminal 상태까지 다시 읽을 수 있고, AUTHORISED/RECONCILIATION 상태는 동일 DecisionAuthorization·Permit2 nonce로 재개한다.
- audit bundle hash→ERC-8004 100/0 feedback→확정 receipt→`REPUTATION_RECORDED` 연결을 추가했다.

최종 결정은 새 컨텍스트 재리뷰 결과 문서에서 갱신한다.
