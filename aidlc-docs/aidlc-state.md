# AI-DLC State

> Current packet (2026-09-12): `/request` chatbot UX and repeat-purchase correction locally verified, branch `fix/chat-purchase-flow`. Design: `docs/DECISION_OBSERVER_PLAN.md` → 채팅 UX 교정. Evidence: `docs/DECISION_OBSERVER_VERIFICATION.md` → latest UX section (38 dashboard tests, 11 browser groups, 2 isolated Mock purchases, lint/build). Existing AEGIS/Qwen/checkpoint implementation is preserved. No chain writes or deployments. Older lifecycle details below are historical, not the active token/runtime status.

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
