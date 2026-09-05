# WO-P6-02 Planner Amendment R2 — DONE

- 상태: **READY_FOR_CODER**
- Planner checkout: `/Users/vien/MyProjects/PBL`
- branch: `feature/phase6-audit-e2e`
- base SHA: `022313a65cfb7147dd34611127dd6ccefb4d4be9`
- rejected Coder tip reviewed: `72e9611f70132bd63b268fd806ada327f4253981`
- Reviewer artifact: `.agent/outbox/WO-P6-02-review.md`
- Reviewer artifact SHA-256: `a9bb81fbe9252373f4c3c6e4a1150cce71e45da5ca191172ac6b45b18aeb3dae`
- original WO SHA-256: `b8385701c290d93d7e647c6c3364274ec5409e6dd81c9fa37678c179d3b4fc53`
- amended WO SHA-256: `813b4577aab0d24673a808a781c1008713eb3e0dc71a6dde16dae3ef81397561`

## 확인된 결합 누락

`PaymentService.claim()`은 existing payment intent를 반환하기 전에도
`load_payment_view()`로 evidence ordering을 검증한다. 승인된 reputation flow가 terminal audit
뒤 `REPUTATION_DECIDED`, confirmed publication 뒤 `REPUTATION_RECORDED`, conflict에서
`REPUTATION_PUBLICATION_CONFLICT`를 append하므로, payment parser가 두 신규 event의 제한된
post-payment 위치를 모르면 정상 finalize 이후 payment view와 idempotent claim이 함께 실패한다.

## 승인된 bounded correction

- Buyer/Audit allowed writes에
  `services/buyer-audit-api/src/buyer_audit_api/core/payment.py`를 추가했다. 허용 범위는 두
  reputation event를 검증된 terminal payment 뒤의 post-payment auxiliary로만 해석하도록
  `load_payment_view` ordering validation을 확장하는 것뿐이다. Payment state, accounting,
  authorization, terminal proof semantics는 변경할 수 없다.
- Tests allowed writes에 `services/buyer-audit-api/tests/test_payment_service.py`를 추가했다.
  기존 허용된 `test_phase6_terminal.py`와 `test_api.py`를 함께 사용해 core/service/HTTP
  regression을 고정한다.
- 두 event를 unconditional/global auxiliary set에서 순서 검증 전에 제거하는 방식은
  금지했다. `REPUTATION_DECIDED`는 terminal `AUDITED` 뒤 singleton이고,
  `REPUTATION_PUBLICATION_CONFLICT`는 해당 decision/outbox identity 뒤 반복 가능한
  append-only finding이다.

## 추가 Acceptance Criteria

1. `load_payment_view`와 existing-intent `claim`은 terminal finalize 직후, confirmed
   `REPUTATION_RECORDED` 직후, 하나 이상의 valid conflict 직후 각각 기존 terminal payment
   binding을 그대로 반환한다.
2. 위 read/claim은 새 `PAYMENT_INTENT_CLAIMED`, reservation, spend, payment/reputation event를
   만들지 않는다.
3. reputation event의 pre-terminal 배치, decision-before-audit, duplicate decision,
   conflict-before-decision/outbox identity는 `PaymentEvidenceError`로 실패하고 payment intent와
   wallet policy를 변경하지 않는다.
4. 기존 payment lifecycle의 다른 illegal ordering과 WO-P6-01 acceptance는 그대로 fail
   closed다.

## 보존 및 검증 계약

- Requirements SHA-256: `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f`
- Design SHA-256: `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768`
- Tasks SHA-256: `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe`
- Requirements/design/tasks, WO-P6-01/03/04, product code/tests, historical records, and
  `.githooks` are not modified by this Planner commit.
- No live ERC-8004/RPC/provider/facilitator/AWS/Atlas/public-chain call or mutation is authorized.
- coherent commit message: `docs(phase6): amend WO-P6-02 payment ordering scope`
- This artifact is included in that commit; the exact commit SHA is reported after commit.

## Exact next step

Orchestrator applies the planning commit to `wo/P6-02`; Coder implements the bounded correction
and returns the amended fixed suite for independent review. WO-P6-03 remains blocked.

READY_FOR_CODER
