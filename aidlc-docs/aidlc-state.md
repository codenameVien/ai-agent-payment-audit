# AI-DLC State

> Current packet (2026-09-12): `/request` disabled-button readiness recovery verified, branch `fix/purchase-readiness-retry`. Design: `docs/DECISION_OBSERVER_PLAN.md` → 구매 실행 버튼 연결 복구. Evidence: `docs/DECISION_OBSERVER_VERIFICATION.md` → latest recovery section (38 dashboard tests, 15 browser groups, lint/build, actual preview readiness with zero purchase writes). Existing AEGIS/Qwen/checkpoint implementation and transaction storage are preserved. No chain writes or deployments. Earlier chatbot UX shipped in PR #15; older lifecycle details below are historical, not the active token/runtime status.

- **Lifecycle:** Integration — ERC-3009-only payment runtime
- **Current stage:** PBLC V2 deploy/verify/settle/receipt/replay evidence captured; active Permit2 path retired; AWS and real provider remain external gates
- **Requirements:** Approved on 2026-09-02; revised 2026-09-04
- **UI mockup checkpoint:** Approved on 2026-09-02
- **Design:** Approved on 2026-09-02; revised 2026-09-04 for `/request`, Buyer Agent ownership, SDK Wrappers, Audit Evidence API, and PBLC V2 ERC-3009
- **Tasks:** Original phases approved on 2026-09-02; migration packet added by explicit user direction on 2026-09-04
- **Construction:** `/request`, audit boundary, PBLC V2, x402 ERC-3009 single execution path, and historical Permit2 read compatibility complete
- **External gate:** real Gemini/Nemotron provider, AWS cost-bearing deployment, and public retention policy approval remain
- **Integration:** working branch `feature/erc3009-request-boundary` from `2e79ba0`
- **Remote:** `git@github.com:codenameVien/ai-agent-payment-audit.git` (private personal repository)
- **Last updated:** 2026-09-04
