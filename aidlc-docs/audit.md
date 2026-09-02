# AI-DLC Audit Log

## 2026-09-01 — Intent and delivery calibration approved

- The user confirmed the shared product definition after a numbered grilling process.
- Confirmed goal: audit whether the buyer agent's selection follows user requirements, budget, and policy, and cross-check the actual Base Sepolia payment.
- Confirmed MVP domain: autonomous purchase of one AI inference from provider-level Gemini or Nemotron seller agents.
- Confirmed delivery: product quality high; project baseline standard; high-assurance scoped to authentication, payment/wallet policy, audit integrity, and sensitive payload encryption.
- Confirmed system flags: `security-sensitive: yes`, `has-ui: yes`.
- Confirmed execution budget: four coherent implementation packets, two independent review boundaries, and two broad-suite runs, with re-scoping before exceeding the Standard 90-minute milestone checkpoint or after two repeated fix cycles in one finding category.
- Confirmed retention decision: option B, no automatic deletion of encrypted raw prompts/responses during MVP. Reconsider TTL/manual deletion before public AWS deployment.

## 2026-09-01 — Requirements drafted

- Artifact: `aidlc-docs/inception/requirements.md`
- State: awaiting explicit **Approve and Continue** or **Request Changes**.
- No design or implementation work started.

## 2026-09-02 — Requirements approved

- User decision: **Approve and Continue**.
- The requirements baseline is frozen at 10 user stories, 43 acceptance criteria, interaction surfaces, recovery flows, and applicable NFRs.
- Next checkpoint: disposable dashboard mockup review before design.
- No design or implementation work started.

## 2026-09-02 — Dashboard mockup drafted

- Reviewed flows represented: sign-in context, overview, new request, transaction list, transaction detail, seller reputation, and audit alerts.
- The transaction detail connects buyer reasoning, candidate scores, payment proof, response proof, and audit findings.
- Browser render checks completed at 1024px desktop width and 390px mobile width.
- State: awaiting explicit visual **Approve and Continue** or **Request Changes**.
- No design or implementation work started.

## 2026-09-02 — Dashboard mockup approved

- User decision: **Approve and Continue**.
- Approved flows: overview/new request, transaction list/detail, seller reputation, and audit alerts.
- The mockup checkpoint is closed and design work may begin.

## 2026-09-02 — Design drafted

- Artifact: `aidlc-docs/inception/design.md`
- Primary decisions: separate MetaMask owner and programmatic buyer wallet, provider-level sellers, FastAPI Evidence Repository as the only MongoDB boundary, Commerce Gateway as the only application signing/write-request boundary, custom ERC-20 Permit2 with limited pre-approval, deterministic audit authority, and ERC-8004 feedbackHash anchoring.
- Current protocol facts were rechecked against Coinbase, MetaMask, EIP-8004, and the official ERC-8004 contracts repository.
- State: awaiting explicit **Approve and Continue** or **Request Changes**.
- No implementation work started.

## 2026-09-02 — Design approved with domain-reuse clarification

- User decision: **Approve and Continue**.
- User clarification: the folder structure should preserve the audit/payment foundation when the purchase domain changes later.
- Design amendment: reusable authentication, evidence, payment, chain verification, and reputation cores are separated from `domains/ai_inference` and domain-specific dashboard renderers.
- Scope guard: this is a replaceable domain seam, not a multi-domain plugin marketplace in the MVP.
- No implementation work started.

## 2026-09-02 — Tasks drafted

- Artifact: `aidlc-docs/inception/tasks.md`
- Shape: four coherent work packets, two independent review boundaries, and two broad-suite runs.
- Reuse proof requires a fake-domain conformance path and a verified no-import boundary from common cores to `ai_inference`.
- State: awaiting explicit **Approve and Continue** or **Request Changes**.
- No implementation work started.

## 2026-09-02 — Tasks approved; Construction started

- User decision: **Approve and Continue** and requested goal-based continuation.
- The four-packet execution contract is frozen.
- Construction starts with packet 1.1: reusable core boundary, SIWE, Evidence Repository, and sensitive-payload encryption.
- External secrets, paid calls, blockchain writes, AWS deployment, and GitHub publication remain gated.

## 2026-09-02 — Packet 1.1 completed

- Implemented the Python 3.12 workspace, protocol-neutral core/domain boundary, SIWE authentication, owner/buyer wallet binding, Evidence Repository, RFC 8785 hash chain, AES-256-GCM envelope encryption, owner-only raw access, and safe local key setup.
- Verification: root lint and strict mypy passed; 20 focused tests passed with one real-Mongo test skipped by default; the isolated native Mongo integration test passed separately.
- The real Mongo test covers atomic nonce consumption, unique buyer-wallet binding, 12-way concurrent event append, hash-chain verification, ciphertext-only storage, and decrypt/hash verification.
- A first integration run exposed an 8-retry contention limit; the repository now uses a bounded 64-attempt exponential backoff and the same automated integration test passes.
- Environment caveat: the current Docker Desktop kernel is in MongoDB's unsupported Linux 6.19–7.0.13 range. Native MongoDB 8.3 is used through an isolated auto-cleanup script until that kernel changes.
- Upstream `siwe 4.4.0` requires the upstream-tested `abnf 2.2.0` pin; the latest abnf failed during SIWE import.
- No real secret, provider call, blockchain write, AWS resource, or GitHub publication occurred.

## 2026-09-02 — Packet 2.1 implementation and Broad suite 1 completed

- Implemented deterministic AI request normalization, clarification, manual benchmark normalization, hard filters, four approved scoring presets, and reproducible selection evidence.
- Implemented a provider-level TypeScript seller engine shared by Gemini and Nemotron, real HTTP provider adapters behind the same mockable contract, one idempotent in-provider counteroffer, and protocol-neutral versus AI quote schemas.
- Implemented EIP-712 seller quotes in the Seller Service and independent Buyer-side signer recovery before a quote can enter the evidence chain. Amount, token, recipient, expiry, signer, chain, contract, and request-binding tamper paths are covered.
- Implemented the `/health`, `/internal/quotes`, and x402-gated `/v1/inference` transport shell. Actual x402 verification remains a Phase 3 `PaymentGate` adapter.
- Implemented the `REQUESTED → QUOTED → DECIDED` E2E path using only stored benchmark snapshots and verified signed quotes.
- Added local secret handoff scripts: internal and seller-wallet keys are generated without printing; external Gemini/NVIDIA keys are collected later with hidden terminal input. No key was created, entered, or read during this packet.
- Broad suite 1 result: Ruff and strict mypy passed for 36 Python source files; strict TypeScript passed; Python 38 passed and one isolated Mongo test skipped by default; TypeScript 13 passed; schema boundary passed; isolated native Mongo integration passed separately.
- Self-review finding fixed before closure: the initial Buyer workflow only checked a signature prefix. It now performs full EIP-712 address recovery and rejects invalid signatures before `QUOTED` is stored.
- Non-blocking warnings remain from upstream `websockets.legacy` and FastAPI TestClient/httpx2 migration notices.
- Verification boundary 1 independent new-context review is still pending. It is not recorded as complete, and Phase 3 has not started.
- No real provider call, blockchain write, AWS resource, or GitHub publication occurred.

## 2026-09-02 — Phase 3 remediation and Phase 4 local completion

- Verification boundary 2 최초 리뷰는 seller Facilitator 경로, ERC-8004 신원 근거, wallet-policy lost update, 미확정 anchor 기록, 승인 후 crash resume의 다섯 결함을 발견해 `Request changes`를 냈다.
- Seller Service에 실제 `/verify → provider → /settle` 순서와 `PAYMENT-RESPONSE`를 구현하고 관통 테스트를 추가했다.
- `erc8004AgentId`를 EIP-712 견적에 포함하고 Commerce Gateway가 결제 전 registry agent wallet과 signer를 확인한다.
- Mongo wallet policy는 예약/사용 금액 CAS를 사용하며 실제 replica-set 경합 테스트가 `reservedUnits` 보존을 확인한다.
- EvidenceAnchor와 ERC-8004 평판은 성공 receipt를 확인한 뒤에만 off-chain evidence에 기록한다.
- 동일 purchase 재시도는 terminal 상태를 반환하고, 승인 직후 중단은 동일 DecisionAuthorization과 Permit2 nonce로 재개한다.
- Next.js 대시보드에 SIWE, 체인 잔액, 거래/선택 이유, 감사 경고, provider-level 판매 에이전트 평판, SSE를 연결했다. AI 추론 renderer는 공통 shell에서 분리했다.
- 결정적 감사에 선택/벤치마크/견적-결제/중복 수명주기 검사를 추가하고 semantic advisor는 권위 판정을 해제할 수 없게 유지했다.
- Docker Compose와 비용 기본 차단 ECS/Secrets Manager/CloudWatch Terraform handoff를 추가했다. 실제 AWS apply는 하지 않았다.
- 민감 원문은 MVP 정책 B대로 자동 삭제하지 않는다. 공개 배포 전 TTL 또는 수동 삭제 정책과 시연 증거 보존을 다시 결정해야 한다.
- Broad suite 2: lint/typecheck 통과, Python 64 passed/1 skipped, Seller 19, Commerce Gateway 20, Solidity 4, native MongoDB 1 passed.

## 2026-09-02 — Verification boundary 2 remediation

- Seller와 Commerce Gateway 실행 엔트리포인트, provider별 라우팅, Dockerfile, Compose `agents` profile을 추가했고 API·Dashboard·두 Seller·Gateway 이미지 5개를 실제 빌드했다.
- 정산 실패 `PAYMENT-RESPONSE`의 tx hash를 보존하고, Gateway는 Facilitator 제출 hash를 reconciliation evidence에 먼저 결속한다. hash 없는 모호 상태는 자동 재제출하지 않으며 저장된 hash와 일치하는 확정 revert만 예산을 해제한다.
- 기존 payment intent는 quote 만료·자정 이후에도 immutable binding 검증으로 재개하며 회귀 테스트를 추가했다.
- 비종결 감사는 임시 결과만 반환하고 `FAILED` 또는 `SETTLED + DELIVERED`에서만 불변 `AUDITED`를 기록한다.
- ERC-8004 평판은 Evidence API가 agent ID·객관 값 100/0·bundle hash를 쓰기 전에 결정하고, 정확한 `NewFeedback` event를 검증한 뒤에만 evidence로 기록한다.
- Broad suite 3: lint/typecheck 통과, Python 65 passed/1 skipped, Seller 21, Commerce Gateway 22, Solidity 4, native MongoDB 1 passed. npm production audit 0 vulnerabilities, 5개 Docker image build 통과.
- 실제 provider key 호출, custom ERC-20 Facilitator 호환 tx, Base Sepolia 배포/등록/평판/anchor, AWS apply, 화면 캡처는 외부 게이트로 유지한다.
- 실제 provider call, Base Sepolia/ERC-8004/EvidenceAnchor write, AWS resource, GitHub publication은 수행하지 않았다.

## 2026-09-02 — Verification boundary 1 review packet prepared

- Added `docs/reviews/verification-boundary-1.md` with a read-only fresh-context reviewer contract, required threat questions, direct-inspection targets, reproduction commands, baseline evidence, and result template.
- The packet explicitly forbids secrets, real provider calls, blockchain writes, AWS deployment, GitHub publication, and implementation changes during the review pass.
- This preparation is not an independent review and does not approve Phase 3.
- Independent reviewer execution still requires explicit authorization for a separate review agent/context.

## 2026-09-02 — Verification boundary 1 requested changes

- The user explicitly approved an independent review and resumed the goal.
- Fresh-context reviewer decision: `Request changes`; no P0/P3, five P1 and three P2 findings.
- The findings affect evidence-tail/reference integrity, plaintext prompt leakage, signed seller assertions, paid-model binding, decision reload verification, counteroffer scope, quote-request validation, and x402 version representation.
- Full evidence is recorded in `docs/reviews/verification-boundary-1-result.md`.
- Phase 3 remains blocked until all findings pass the broad suite and a fresh-context re-review approves the boundary.

## 2026-09-02 — Verification boundary 1 remediation submitted for rereview

- Fixed all five P1 findings: independent evidence heads and read verification, prompt redaction, complete signed seller assertions with separate Buyer identity evidence, payment-authorized model binding, and persisted QUOTED reload/chain/signature/provider-model-version verification.
- Fixed all three P2 findings: one quote lineage per purchase/provider process, strict quote-request validation, and removal of false x402 v1 representation.
- Added adversarial tests for reference mutation, tail deletion in memory/API/native Mongo, signed-assertion tampering, paid-model substitution, persisted evidence tampering, model-version mismatch, multiple request IDs, malformed quote fields, and x402 placeholder output.
- Added a TypeScript-produced EIP-712 wire fixture verified by the Python Buyer implementation.
- Final local evidence: Ruff/mypy/tsc pass; Python 44 passed and one native-Mongo test skipped by default; TypeScript 17 passed; schema boundary passed; isolated native Mongo 1 passed; key-handoff Python and shell syntax checks passed.
- Rereview manifest: `e0e49ffa62560bc02324942460d6421395af02a5a27949d08db5e7fb982b63ab`.
- Phase 3 remains blocked until a fresh-context reviewer approves `docs/reviews/verification-boundary-1-rereview.md`.

## 2026-09-02 — Verification boundary 1 rereview requested changes

- Fresh-context rereviewer reproduced the manifest and passed every required command, then returned `Request changes` with two P1 and two P2 findings.
- P1: concurrent same-purchase quote requests can race into two lineages; decision inputs can diverge from the persisted `REQUESTED` event or omit it entirely.
- P2: Mongo event/head writes are not transactional; whitespace-only quote identifiers pass validation.
- Previously remediated attacks were independently confirmed blocked.
- Full result: `docs/reviews/verification-boundary-1-rereview-result.md`.
- Phase 3 remains blocked pending correction, broad suite, and another fresh-context approval.

## 2026-09-02 — Second remediation submitted for fresh rereview

- Closed the concurrent quote race by registering a purchase-scoped in-flight promise before signing; simultaneous different request IDs now yield exactly one quote and one rejection.
- Made verified `REQUESTED` evidence the decision trust root and reject missing, duplicate, stale, or caller-divergent request/budget lifecycle inputs before quoting.
- Moved Mongo sequence read, event insert, and independent-head insert into one replica-set transaction; injected head failure rolls back both writes and retry starts at sequence one.
- Trimmed and rejected whitespace-only purchase, request, and preferred-model identifiers.
- Broad suite: Ruff/mypy/tsc pass; Python 46 passed/1 skipped; TypeScript 18 passed; schema boundary passed; native replica-set Mongo one passed.
- New rereview manifest: `a8274106b4bf1e9c8073a0bc2aa75053bccc7406c703bd207ab61629a7af0bb3`.
- Phase 3 remains blocked until `docs/reviews/verification-boundary-1-rereview-2.md` is independently approved.

## 2026-09-02 — Second fresh-context rereview requested changes

- Decision: `Request changes`; two P1 and one P2, no P0/P3.
- P1: later payment/delivery/audit states are not rejected before quote; concurrent decisions can race past precheck and leave a trailing `QUOTED`.
- P2: Mongo validates only the latest event/head pair and misses an interior anchor deletion.
- Previous remediation round was independently confirmed effective and all required commands passed.
- Result: `docs/reviews/verification-boundary-1-rereview-2-result.md`; Phase 3 remains blocked.

## 2026-09-02 — Third remediation submitted for fresh rereview

- Replaced ad-hoc quote checks with an explicit business-event projection: pre-quote must be exactly `REQUESTED`, and persisted selection must be exactly `REQUESTED → QUOTED`; only sensitive-access and correction records are auxiliary.
- Added expected event-count/head-hash CAS to the Evidence Repository contract. Memory enforces it under the append lock; Mongo enforces it inside the event/head transaction. Workflow binds both `QUOTED` and `DECIDED` to verified heads.
- Added `QUOTED`/`DECIDED` singleton defenses in both repositories and partial unique Mongo indexes.
- Mongo append now verifies the complete event chain and every immutable head anchor before writing, so an interior anchor deletion fails closed.
- Added regression attacks for later-state-before-quote, synchronized duplicate decisions, stale-head append, and interior Mongo head deletion.
- Verification: Ruff/mypy/tsc passed; Python 49 passed/1 skipped; TypeScript 18 passed; schema boundary passed; native replica-set Mongo one passed; key-handoff Python and shell syntax checks passed; no patch rejects.
- New rereview manifest: `2d0fbd118cf4266367c0df4461a1357575b9049cf3de73347876fa58a2756d5c`.
- Phase 3 remains blocked until `docs/reviews/verification-boundary-1-rereview-3.md` receives independent approval.

## 2026-09-02 — Verification boundary 1 approved

- Fresh-context third rereviewer returned `Approve Phase 3` with no P0–P3 findings.
- Reviewer independently reproduced explicit lifecycle rejection, synchronized-decision CAS, direct stale-writer races, and Mongo interior-anchor deletion/mutation attacks.
- Earlier integrity, confidentiality, seller-signature, paid-model, quote-lineage, transaction, strict-input, and placeholder-version attacks remained blocked.
- Reviewer command evidence: Ruff/mypy/tsc pass; Python 49 passed/1 skipped; TypeScript 18 passed; schema boundary pass; native Mongo one pass; manifest matched `2d0fbd118cf4266367c0df4461a1357575b9049cf3de73347876fa58a2756d5c`.
- Result: `docs/reviews/verification-boundary-1-rereview-3-result.md`.
- Phase 3 may begin locally. Real secrets, provider calls, Base Sepolia writes, ERC-8004 writes, AWS deployment, and GitHub publication remain gated.

## 2026-09-02 — Final verification-boundary remediation

- Added authenticated `/purchases/{id}/run` orchestration and server-side prompt decryption so the dashboard never receives Gateway credentials or raw prompt material.
- Added `DELIVERY_STAGED` recovery and delivery checks that bind seller, provider, model, and version to the selected signed quote before the audit can report a safe outcome.
- Bound seller cache hits to both `paymentSignature` and an exact payment-proof fingerprint; unsigned or substituted proofs cannot reuse a paid result.
- Added the MongoDB-backed seller execution journal `CLAIMED → SUBMITTED → SETTLED → PROVIDER_SUBMITTED → DELIVERED`. Authorization, settlement, provider result, and prompt are stored as encrypted sensitive payloads, and a restart after Facilitator settlement resumes the same submitted authorization without creating a new payment.
- Hashless Gateway reconciliation invokes the authenticated seller recovery path, then binds the recovered exact transaction through a one-time `PAYMENT_SUBMISSION_IDENTIFIED` CAS before receipt verification; it never recreates or blindly resubmits a payment.
- A deterministic provider attempt and per-caller acquisition token are durably CAS-recorded before inference. Only the winning token may call the provider, including concurrent recovery. If result persistence becomes ambiguous, restart does not invoke the paid provider again and leaves delivery visibly unresolved for audit.
- Reputation and EvidenceAnchor publication now recover confirmed on-chain events after write-before-record crashes and use per-process single-flight for concurrent retries.
- Final broad suite before independent rereview: Ruff/mypy/tsc passed; Python 68 passed/1 skipped; Seller 29 passed; Commerce Gateway 29 passed; Solidity 4 passed; native replica-set Mongo 1 passed; production dependency audit reported 0 vulnerabilities; five application Docker images built.
- Deployment invariant: the MVP runs one active writer replica per seller and one Gateway writer. Active-active operation requires a distributed lease/nonce coordinator and is not claimed by this deliverable.
- Sensitive prompt/response retention remains MVP policy B. Before public deployment, re-decide TTL versus manual deletion and how much demonstration evidence must be retained.
- Real provider calls, Base Sepolia/ERC-8004/EvidenceAnchor writes, AWS apply, dashboard screenshot, and GitHub publication remain external credential/authority gates.

## 2026-09-02 — Verification boundary 3 approved

- Independent final decision: `Approve local completion`; no P0–P3 findings.
- The reviewer reproduced the prior concurrent `recover()` race. The provider acquisition-token CAS reduced two provider invocations to exactly one, while the losing recovery returned an explicit unresolved-delivery result.
- Seller payment-proof fingerprinting, settlement restart recovery, hashless `None → exact tx` binding, transaction substitution rejection, provider result-persistence failure handling, delivery-to-selected-quote binding, and one-writer deployment documentation were independently confirmed.
- Reviewer evidence: lint passed; Python 68 passed/1 skipped; Seller 29 passed; Commerce Gateway 29 passed; Solidity 4 passed; native Mongo 1 passed; dashboard production build, Compose config, and `git diff --check` passed.
- Result: `docs/reviews/verification-boundary-3-result.md`.
- Local implementation is complete. External keys, real provider calls, Base Sepolia/ERC-8004/EvidenceAnchor writes, AWS apply, dashboard transaction screenshot, and team GitHub publication remain explicit handoff gates.

## 2026-09-02 — 개인 GitHub 전달 시작

- 사용자가 팀 저장소 대신 개인 저장소 사용을 확정했다.
- 비공개 저장소 `codenameVien/ai-agent-payment-audit`를 생성하고 SSH remote를 연결했다.
- 검증 완료된 초기 구현은 최소 `main` bootstrap 위의 `feature/initial-mvp` PR로 전달한다.
- 실제 provider key, provider 호출, Base Sepolia/ERC-8004/EvidenceAnchor write, AWS apply는 계속 별도 외부 게이트로 유지한다.
