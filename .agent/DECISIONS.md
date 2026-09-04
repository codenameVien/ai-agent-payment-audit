# Coordination Decisions

This file records decisions about how multiple agents coordinate in this repository.
Product decisions remain in docs/planning/DECISIONS.md.

## Decisions

| Date | Decision | Reason | Impact |
|---|---|---|---|
| 2026-09-04 | Planner=`gpt-5.6-sol` xhigh, Coder=OMP `anthropic/claude-opus-5` xhigh, Reviewer=`gpt-5.6-sol` xhigh | User explicitly selected the heterogeneous three-role workflow | Keep planning and review independent from implementation; record actual model/runtime evidence. |
| 2026-09-04 | Autonomous execution continues through complete local E2E and stops before actual AWS deployment | User explicitly requested goal-style uninterrupted work with this endpoint | Do not create or mutate AWS resources; finish a deploy-ready checklist instead. |
| 2026-09-04 | Implement provisional abnormal scenarios before team agreement | User wants a concrete implementation to review with teammates later | Keep scenarios isolated from the user dashboard and public chain; make the catalog configurable and document that team review remains open. |

<!-- 위반 기록도 여기: 주체 확정 후에만 공식 기재, 회고 문서로 재캘리브레이션 -->

## Open coordination questions
- Team confirmation of the final abnormal-scenario catalog remains pending after the provisional implementation is demonstrated.
