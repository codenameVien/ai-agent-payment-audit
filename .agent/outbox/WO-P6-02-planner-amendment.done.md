# WO-P6-02 Planner Amendment — DONE

- 상태: **READY_FOR_CODER**
- Planner checkout: `/Users/vien/MyProjects/PBL`
- branch: `feature/phase6-audit-e2e`
- base SHA: `feb13f94d3a7934dec3a21a56c2cd3477afac179`
- Coder blocker commit: `0c10b91ce1067ffe3f245a3aef11b032cff9f249`
- blocker report: `/Users/vien/MyProjects/PBL-coder/.agent/outbox/WO-P6-02-blocked.md`
- blocker report SHA-256: `2f114f92e7395187b5b8561ace2e375b11d8b916c370fd200b7991d86a765a90`
- original WO SHA-256: `8ccb09351a28abd06d6337d259ab8651d30c728597505938cc0656e5648dd720`
- amended WO SHA-256: `b8385701c290d93d7e647c6c3364274ec5409e6dd81c9fa37678c179d3b4fc53`

## 확인된 packet-boundary omission

승인 설계 `aidlc-docs/inception/design.md` §18.6.1은
`REPUTATION_DECIDED`와 `REPUTATION_PUBLICATION_CONFLICT`를 append-only purchase event로
요구하고, §18.6.4는 `REPUTATION_DECIDED` 이름에 의존하는 singleton index와 durable event
chain 보존을 고정한다. 현재 구현의 유일한 appendable event 선언점은
`services/buyer-audit-api/src/buyer_audit_api/core/models.py`의 closed `EventType(StrEnum)`이며,
원래 WO-P6-02 allowed-write 목록에 그 파일만 누락됐다.

## 승인된 유일 교정

WO-P6-02의 Buyer/Audit allowed writes에 다음 범위를 추가했다.

> MODIFY `services/buyer-audit-api/src/buyer_audit_api/core/models.py` — `EventType`에
> `REPUTATION_DECIDED = "REPUTATION_DECIDED"`와
> `REPUTATION_PUBLICATION_CONFLICT = "REPUTATION_PUBLICATION_CONFLICT"` 두 멤버만 추가

중앙 `EventType` 밖의 별도 parallel event enum 추가와 기존 event type overload는 명시적으로
금지했다. 기존 enum member/value/order, payload contract, 구현 단계, 완료 기준, 검증 명령,
security/preservation/reviewer gate는 변경하지 않았다.

## 보존 및 검증

- Requirements SHA-256: `65824ffab8bbc11a23d29fba7610b0c72e4426409c88eced1c0b8d109f4e5f4f`
- Design SHA-256: `e89ab0566592ae2b997729097eed35a2530d6daddeee76c4ad7921621962e768`
- Tasks SHA-256: `97eedc61b14dffab77dbd022a68d2404a8244f73b61c337982316e182c49d7fe`
- 허용된 planning write는 `work-orders/WO-P6-02-reputation-loop.md`, append-only
  `.agent/TURN_LOG.md`, 이 receipt뿐이다.
- requirements/design/tasks, WO-P6-01/03/04, 제품 source/test, historical evidence/data,
  `.githooks`는 수정하지 않는다.
- actual ERC-8004/RPC/provider/facilitator/AWS/Atlas/public-chain 호출이나 mutation은 없다.
- coherent commit message: `docs(phase6): amend WO-P6-02 event-type scope`
- 이 artifact는 그 commit 자체에 포함되므로 exact commit SHA는 commit 생성 후 Planner가
  handoff에서 보고한다.

## Coder resume contract

Orchestrator가 planning commit을 `wo/P6-02`에 반영한 뒤 Coder는 기존 packet을 재개한다.
`core/models.py`에서는 위 두 enum member만 추가할 수 있으며, 다른 설계 차이가 발견되면
기존 stop-and-report 규칙을 그대로 따른다. WO-P6-03은 WO-P6-02 Reviewer `APPROVE`와 통합 전
시작하지 않는다.

READY_FOR_CODER
