# AI-DLC Audit Log

## 2026-09-04 — 중단 거래의 결제·감사 UI 의미 분리

- 사용자는 `PAYMENT_RECONCILIATION_REQUIRED` 거래가 감사 `진행 중`으로 표시되고, 실제 송금되지 않은 `0.1 PBLC` 예약액이 결제액처럼 보이는 문제를 발견했다.
- 감사 결과가 없다는 하나의 조건을 `진행 중`으로 표시하던 fallback을 제거했다. lifecycle은 `결제 확인 필요` 또는 `결제 실패`, 감사는 `감사 미실행`으로 별도 표시한다.
- lifecycle이 정산 완료 상태이고 거래 hash도 있을 때만 온체인 결제 확인으로 표시한다. 정산 완료 전에는 hash가 있어도 `거래 해시 존재 · 결제 미확정`으로 구분한다.
- 기존 MongoDB·온체인 기록은 변경하지 않으며 사용자 화면의 의미와 테스트만 수정한다.

## 2026-09-04 — ERC-3009를 유일한 신규 결제 경로로 확정

- 사용자는 ERC-3009 실거래 성공 뒤에도 Permit2 실행 경로를 보존한 결정이 지나치게 보수적이라고 판단했고, Permit2를 다시 사용할 계획이 없음을 명확히 했다.
- 과거 Permit2 온체인 거래와 MongoDB 성공·실패·미완결 기록은 감사 증거로 그대로 보존한다. 삭제·덮어쓰기·재정산은 하지 않는다.
- `PAYMENT_TRANSFER_METHOD`, Permit2 signer/challenge/payload 분기와 기본 실행 설정을 제거한다. 신규 intent는 ERC-3009 nonce만 생성한다.
- legacy Permit2 intent는 사용자 화면과 내부 조회에서 읽을 수 있지만 execute/reconcile은 seller 또는 signer 호출 전에 fail-closed한다.
- 기본 PBLC 주소와 seller 402는 PBLC V2 `exact + eip3009`로 고정하며, 이전 Permit2 배포 스크립트와 실행 중인 로컬 프로세스도 제거·중지한다.
- 검증 결과: lint/typecheck 통과, Python 75 passed/1 skipped, Seller 30 passed, Payment Executor 34 passed, Solidity 11 passed, native replica-set Mongo 1 passed, dashboard production build 통과. 실제 `pbl_audit`의 과거 Permit2 성공 10-event/미완결 6-event와 ERC-3009 성공 10-event 기록도 변경 없이 확인했다.

## 2026-09-04 — PBLC V2 ERC-3009 deployment and real settlement approved and completed

- User explicitly approved the previously reported deployer, predicted address, initial holder/supply, gas estimate, and `0.1 PBLC` Base Sepolia smoke.
- Deployed PBLC V2 at `0xDed7F4992D98eF31453dCebbB8c2A6b50d0284B3` in tx `0x5e5e6b1acde5d51e0d11f4c3784d64fc738fb6daa814f3bfe14afae1c8d4e83f`, block `46349391`; buyer received `1,000,000 PBLC`.
- Two pre-settlement attempts were preserved as `RECONCILIATION_REQUIRED` with no tx hash and no token movement. The captured Facilitator reason was `invalid_exact_evm_transaction_simulation_failed`; comparison with the official x402 client identified `validAfter=now` instead of canonical `validAfter=0`.
- Added same-UTC-day V1/V2 wallet policy coexistence (`buyerWalletAddress + policyDate + token`), deterministic current-date selection, safe Facilitator error propagation, and a reusable isolated ERC-3009 smoke stack.
- Successful purchase `451f8657-cbc0-4469-acb6-a7037b4d4865` settled `100000` units to Gemini seller in tx `0x5b555e3c50629430cdee30c528db8f45f56441a3a058888e89ab0fb4797892ee`, block `46349821`; receipt contains `AuthorizationUsed` at log `196` and exact Transfer at log `197`.
- Independent replay verification returned `invalid_exact_evm_nonce_already_used`. Audit result is `NORMAL`, findings empty, bundle `sha256:3c4d3d5c53dbc275feb8bef02fd7eabd5a3c016468b9d1c2340a947dd17587d5`.
- Existing Permit2 success and incomplete records were not deleted or rewritten. This was the migration snapshot; the later ERC-3009-only decision above supersedes its temporary legacy-runtime availability.

## 2026-09-04 — ERC-3009 local implementation reached deployment approval gate

- Preserved PBLC V1, Permit2 runtime default, successful transaction `0x32562decbafa3c670280501bafbce01b72ce698d0391c63f4e3c5113f070a0a8`, and incomplete purchase `14b7dd10-fba1-4ea0-afc9-fb44500d6b4b` without mutation.
- Added `/request`, read-only `/` and `/dashboard`, and a data-neutral `/experiments` redirect. The pending legacy localStorage key remains readable.
- Added user-facing Buyer Agent/Payment Executor/Audit Evidence API and Buyer/Seller SDK Wrapper boundaries while preserving internal compatibility names.
- Added PBLC V2 ERC-3009 contract and exact EVM payload path behind `PAYMENT_TRANSFER_METHOD`; default remains `permit2`.
- Local plan only: predicted PBLC V2 `0xDed7F4992D98eF31453dCebbB8c2A6b50d0284B3`; deployer `0x5B2BC76a3e4DeA700309FD9D746180162bcAbec8`; holder/buyer `0xa45Cd1a41E1e548e2daB0123E7Cb4E3dB964cdaB`; estimated gas `848512`; estimated max fee `0.000005939584 ETH`; smoke `0.1 PBLC`. No transaction was sent.
- Deployment and real x402 verify/settle remain blocked until the user explicitly approves these values.

## 2026-09-04 — Request boundary and ERC-3009 migration revision approved

- User explicitly directed a material revision and ordered design → impact → implementation → tests → architecture update.
- `/request` becomes the only purchase input/execution surface. `/` and `/dashboard` remain read-only audit surfaces; `/experiments` redirects without deleting evidence.
- The Buyer Agent owns analysis, quote comparison, seller selection, purchase approval, payment-execution request, and result return. The existing Commerce Gateway implementation is presented as a separately deployed Payment Executor inside that logical boundary so FastAPI never receives the private key.
- User-facing `Evidence Repository` wording becomes `감사 증거 기록 모듈` or `Audit Evidence API`; it remains the sole MongoDB record/query boundary and is not an agent.
- Buyer and Seller SDK Wrapper responsibilities are restored at the architecture level while existing adapter files may remain.
- Target payment is PBLC V2 x402 v2 `exact + eip3009`. The proven PBLC Permit2/EIP-2612 route stays active until a separately deployed ERC-3009 token passes local negative tests and an explicitly approved Base Sepolia verify/settle/receipt/replay smoke.
- Live preservation baseline: `pbl_audit` contains canonical settled purchase `378beb23-e352-49f0-b450-87da88791292` with tx `0x32562decbafa3c670280501bafbce01b72ce698d0391c63f4e3c5113f070a0a8` and 10 events, plus incomplete `14b7dd10-fba1-4ea0-afc9-fb44500d6b4b` in `RECONCILIATION_REQUIRED` with no tx hash and 6 events. Neither may be mutated or deleted.
- Official check on 2026-09-04: x402 v2 exact EVM defines EIP-3009 as the recommended default for compatible tokens; Coinbase documents Base Sepolia v2 exact plus EIP-3009/Permit2 ERC-20 support; live x402.org `/supported` advertises `{x402Version:2, scheme:exact, network:eip155:84532}` but does not enumerate transfer methods or custom token addresses. Therefore custom PBLC V2 compatibility remains an external smoke proof obligation.
- Next irreversible gate: before PBLC V2 deployment, report wallets, intended deployment/mint/test values, predicted address when available, and gas/cost estimate, then wait for explicit approval.

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

## 2026-09-02 — Provider API key 게이트 연기

- 사용자는 현재 단계의 목적이 모델 품질이 아니라 결제·감사 흐름 검증이라는 점을 확인했다.
- Gemini/Nemotron은 `PROVIDER_MODE=mock`으로 실행하고 API key 입력과 실제 provider 호출은 최종 provider smoke까지 연기한다.
- 가짜 API key는 만들지 않으며 `GEMINI_API_KEY`와 `NVIDIA_API_KEY`는 빈 값으로 유지한다.
- 다음 외부 증거는 Base Sepolia 자체 ERC-20·Permit2·x402 testnet Facilitator 경로다.

## 2026-09-02 — 자체 토큰의 구매자 무가스 경로로 수정

- 사용자는 자체 ERC-20을 선택한 이유가 faucet 토큰 부족을 피하기 위한 것인데 buyer wallet에 다시 faucet funding을 요구하는 계획은 목적과 맞지 않는다고 지적했다.
- PBLC는 EIP-2612 `permit`을 지원하고 x402 `eip2612GasSponsoring`으로 결제액만 승인한다. buyer wallet의 native ETH와 수동 Permit2 approval은 제거한다.
- 계약 배포 가스는 분리된 Admin/Deployer wallet 한 곳이 한 번 부담하고, 초기 PBLC는 buyer wallet에 직접 mint한다.
- 실제 Facilitator가 자체 PBLC permit과 settlement를 처리하는지는 Base Sepolia transaction으로 확인하기 전까지 외부 미검증 게이트다.

## 2026-09-02 — Base Sepolia PBLC·EvidenceAnchor 배포

- PBLC `0x9DFFfdDcF5d7E526Bda60728e4c8F79dBA50CeD9` 배포 거래 `0x4c4c58711cb7d9437b118605ba7658d696af2a8543b1ee6ee89f2f8dff31f25e`가 성공했고 buyer `0xa45Cd1a41E1e548e2daB0123E7Cb4E3dB964cdaB` 잔액은 1,000,000 PBLC다.
- EvidenceAnchor `0x31691806C02ca6921a9Bac7AF0972302F8CfA101` 배포 거래 `0xddb7cb3008b6794c29cb4aa745ca7577a09a5c6ea73dcd82013a3ce5dfe77c24`가 성공했다.
- 최초 buyer writer 등록 거래 `0x287433e71e3903401fd8e0d1558ff50306811e81cd24d529b11aef65e5328629`는 RPC가 산정한 22,765 gas limit으로 out-of-gas 실패했다. 배포 주소는 env에 기록되지 않았고 중복 배포도 하지 않았다.
- 100,000 gas limit으로 재시도한 `0x217ccb4ef2ec1f08019b4d73f5e8d195c67f798b027972b94ab8eb488ebabd1b`가 성공했으며 `isWriter(buyer)=true`를 별도 RPC 조회로 확인했다.
- 복구 스크립트는 PBLC 이름·심볼·owner·buyer 초기 잔액과 EvidenceAnchor owner를 검증한 뒤에만 주소를 `.env.local`에 기록한다. 영수증 직후 공개 RPC의 latest 상태가 지연된 사례를 반영해 성공 영수증의 block number로 후속 상태를 읽는다.
- 실제 x402 Permit2 settlement와 token Transfer는 아직 실행하지 않았으므로 별도 외부 게이트로 남긴다.

## 2026-09-02 — ERC-8004 판매 에이전트 등록

- 공식 Base Sepolia Identity Registry `0x8004A818BFB912233c491871b3d84c89A494BD9e`와 Reputation Registry `0x8004B663056A597Dffe9eCcC1965A193B7388713`가 모두 version `2.0.0`임을 확인했다.
- Gemini 판매 에이전트는 ID `9154`로 등록됐다. 등록 거래는 `0xf8838103943775b7890becbf8d4afb8ce44a33b6ddecec99b6fd53277b83186a`, seller wallet 결속 거래는 `0xbd51221ea1ff82ab8d690dcf63493f8bfa58dad7799601296b9aa03a55541362`다.
- Nemotron 판매 에이전트는 ID `9155`로 등록됐다. 등록 거래는 `0xc3d570a4cb87d9b854df8c3a875ec8bbcd3d59b101700dce1b290e879210e212`, seller wallet 결속 거래는 `0xdd5aeaa8e97f62295bb3be3f9d71dca40f88d36836f4d7799767eac41e26f1e8`다.
- deployer가 두 identity NFT owner와 온체인 가스를 맡고, seller private key는 EIP-712 `AgentWalletSet`에만 서명한다. 두 seller 지갑에는 native ETH가 필요 없다.
- 공개 RPC가 성공 receipt 직후 같은 block을 일시적으로 찾지 못한 사례가 있어, 등록 자동화는 receipt block을 최대 20초 재조회한다. agent ID는 등록 직후 `.env.local`에 먼저 보존하며 재실행 시 이미 완료된 등록과 지갑 결속을 온체인 상태로 건너뛴다.
- `npm run erc8004:status`에서 두 ID의 owner와 `getAgentWallet` 일치가 모두 `true`임을 확인했다. 실제 ERC-8004 feedback은 성공한 구매·감사 뒤에만 제출하므로 아직 남은 외부 게이트다.

## 2026-09-02 — Base Sepolia x402·ERC-8004·EvidenceAnchor 실거래 완료

- 구매자 `0xa45Cd1a41E1e548e2daB0123E7Cb4E3dB964cdaB`의 native ETH가 0인 상태에서 자체 EIP-2612 토큰 PBLC를 x402.org Facilitator로 두 번 정산했다. 각 결제는 `100000` raw units, 즉 `0.1 PBLC`였고 buyer의 수동 Permit2 allowance 거래는 없었다.
- 첫 결제 `0xdcc3c9781e7ca5a38eadfd8d2a641110b4e2f70f013052a95a072e6c81f1d9c1`는 온체인 정산은 성공했지만 mock adapter가 서명된 `gemini-2.5-flash` 대신 `mock-v1`을 반환해 전달 전에 중단됐다. 규칙상 `AUD-DELIVERY-MISSING` 위험 대상이지만 해당 smoke DB에는 최종 `AUDITED` 이벤트가 저장되지 않았으므로, 완결된 이상 감사 사례가 아니라 정산 후 전달 실패의 미완결 증거로 보존한다.
- 두 번째 구매 `14f37c54-e26c-4ab4-83e6-cbc7c2a7c473`의 결제 `0xe8529c17bb1a4998a4ee75cf7b782a0b465cd286bb9005b0c318f22ddb33b680`는 block `46292655`, Transfer log index `23`에서 buyer → Gemini seller의 정확한 `0.1 PBLC`를 독립 검증했다. 전달은 선택된 provider/model/version과 일치했고 감사 결과는 `NORMAL`, findings 없음이었다.
- 정상 감사 bundle `sha256:436eceace08c3f38d1615bc8cc9105993a558cbd342320d7f19589576c55af38`을 Gemini ERC-8004 agent ID `9154`의 value `100` feedback에 결속했다. tx `0x5702e3ca225e3d0089a14bbc0e7aad851cebe2f6aebc4d230f1dad83d717f491`의 `NewFeedback` event에서 agent ID, buyer client address, value, tags, feedback hash를 Blockscout API로 재확인했다.
- 평판과 EvidenceAnchor 쓰기는 구매자의 x402 무가스 결제 증거를 보존한 뒤 별도로 공급한 `0.0001 ETH`만 사용했다. funding tx는 `0x052ced34cc6affb46d67f0807cbdbb3f2a920c879d6f39ae69d2b7cc44ca3336`이다.
- EvidenceAnchor tx `0xe2a5991639316b16dd30385551d8de02b2838618b1fd88e1a4fc8f455ec21bb0`는 purchase ID hash에 event count `11`, head `0xff6b2dda9c88830b5c3d1b4e2b30ce77d6008625a421032377ca4686e4cbb6a2`를 기록했다. MongoDB는 이를 12번째 `EVIDENCE_ANCHORED` event로 이어서 저장했고 전체 hash chain을 재검증했다.
- 실환경 smoke에서 네 가지 경계 오류를 발견하고 회귀 테스트를 추가했다: BSON millisecond timestamp hash 정규화, nullable latency 필드 생략, `sha256:` → bytes32 변환, 공개 RPC의 10,000-block log 조회 제한. mock adapter도 설정된 model version을 반환하도록 수정했다.
- 최종 검증: Ruff/mypy/TypeScript lint 통과; Python `71 passed, 1 skipped`; Seller `29 passed`; Gateway `33 passed`; Solidity `6 passed`; native replica-set Mongo `1 passed`; dashboard production build 통과; production dependency audit `0 vulnerabilities`; `git diff --check` 통과.
- 아직 외부 게이트인 항목은 실제 Gemini/Nemotron API 응답, AWS 비용 발생 배포, 공개 배포 전 민감 원문 보존/삭제 정책 재승인, 인증된 대시보드 거래 상세 화면 캡처다.


## 2026-09-03 — 감사 대시보드 역할 수정 승인

- 사용자는 개요 화면이 새 구매를 실행하는 곳이 아니라 정상·비정상 거래 결과를 관찰하는 감사 화면이어야 한다고 수정했다.
- 사용자가 제공한 정적 HTML 시안의 정보 밀도와 어두운 감사 콘솔 구조를 채택하되, 가짜 USDC·거래·보안 점검·이상거래 시뮬레이터는 사용하지 않는다.
- 개요에서 구매 폼을 제거하고 실제 계정 범위의 PBLC 잔액, 거래, 온체인 해시, 감사 등급, 경고, 연결 상태만 표시한다.
- 거래 생성 실험은 추후 별도 demonstration runner로 분리한다. 사용자의 “추천 방향으로 진행”을 이 범위 변경과 구현의 승인으로 기록한다.

## 2026-09-04 — 정상 거래 실험 실행 승인

- 사용자는 읽기 전용 감사 개요와 분리된 `/experiments` 실행 화면 구현을 승인했다.
- 이번 실행 범위는 고정 예산 `100000` raw units (`0.1 PBLC`)의 정상 거래 한 건으로 한정한다. 비정상 시나리오를 가장한 가짜 체인 거래·가짜 경고는 만들지 않는다.
- 실행 화면은 실제 Base Sepolia PBLC 결제임을 명시하고, 확인 체크 전에는 실행할 수 없으며, 진행 중 재클릭과 요청 생성 후 재시도로 인한 중복 구매를 막는다.
- 정상 거래의 결제·감사 증거는 canonical `pbl_audit` 데이터베이스와 로그인 owner 범위에 기록해 기존 smoke 데이터베이스를 변경하지 않는다.
- 사용자는 구현 확인 뒤 실행기가 사용자 대시보드 메뉴 안에 보이는 것이 역할 분리에 맞지 않음을 지적했다. 이를 설계 수정 승인으로 기록하고, 메뉴 링크와 대시보드 공용 셸에서 실행기를 제거해 발표·개발용 화면으로 분리했다.
- 첫 실행 `14b7dd10-fba1-4ea0-afc9-fb44500d6b4b`는 RPC 제출 오류 뒤 transaction hash 없는 `PAYMENT_RECONCILIATION_REQUIRED`에서 중단됐다. 결제는 발생하지 않았고 예약 `0.1 PBLC`는 보수적으로 유지한다. 이 기록은 삭제하지 않는다.
- 새 탭에서 생성된 두 번째 구매 `378beb23-e352-49f0-b450-87da88791292`는 결제 tx `0x32562decbafa3c670280501bafbce01b72ce698d0391c63f4e3c5113f070a0a8`로 완료됐다. block `46341684`, Transfer log index `117`, buyer → Gemini seller, `100000` raw units를 공개 RPC에서 독립 확인했다.
- 성공 구매는 `REQUESTED`부터 `AUDITED`까지 10개 hash-linked event를 가지며 head는 `sha256:7a5e5ab4a71dbd483b9364417c780479e928cba41d915f523052b0ac7e4614bc`, 감사는 `NORMAL`, findings 없음이다. buyer 잔액은 `999999700000` raw units, permit nonce는 `3`, Permit2 allowance는 `0`이다.
- 탭별 `sessionStorage`가 새 탭의 중복 purchase 생성을 막지 못한 실증 결과에 따라 보류 `purchaseId` 저장소를 동일 출처 탭이 공유하는 `localStorage`로 변경했다.

## 2026-09-10 — PBLC V2 지갑 결제 사전점검 준비

- 사용자 요청에 따라 실제 전송 전 준비 경로를 추가했다. `npm run pblc:payment:preflight`은 공개 `AEGIS_LIVE_PAYER_ADDRESS`를 사용해 ETH/PBLC 잔액·PBLC V2 메타데이터·Facilitator `/supported`만 읽는다. 개인키는 이 단계에서 저장하거나 읽지 않는다.

## 2026-09-10 — 사용자 소유 PBLC 및 모델별 결제 조건 준비

- 기존 PBLC V2의 owner는 사용자 MetaMask 계정이 아닌 주소임을 공개 읽기로 재확인했다. 기존 계약과 과거 거래는 그대로 보존하고, 사용자 지갑을 deployer/owner/initial holder로 하는 별도 PBLC ERC-3009 계약의 **오프라인 승인 패킷**만 준비했다.
- 현재 pending nonce 기준 예상 CREATE 주소·가스·초기 공급량은 [PBLC 사용자 소유 토큰 준비](../docs/PBLC_USER_TOKEN_PREPARATION.md)에 기록했다. 이 패킷은 개인키·RPC·서명·브로드캐스트를 사용하지 않으며 실제 배포·민팅·전송은 하지 않았다.
- OpenAI GPT-4.1 mini, Claude Haiku 4.5, Gemini 2.5 Flash의 정확한 model ID, seller 수신 지갑, 공개 1M-token 가격을 [모델별 표준 작업 결제 조건](../docs/MODEL_TASK_PRICING.md)에 기록했다. 현재는 Mock Gateway/Mock Facilitator 검증용 가격이며 실제 Provider 호출은 미구현이다.
- 서버의 AA free endpoint를 읽기 전용으로 확인한 결과 세 고정 모델의 exact ID/slug mapping을 얻지 못했다. live AA 모드는 fail-closed로 남기고 fixture AA를 유지한다.

## 2026-09-10 — 사용자 소유 PBLC Base Sepolia 배포·초기 민팅 완료

- 사용자 명시 승인 후 `0x043D966B3f30Ff9FAC08FD6b5eFeDa6ac895a0a3`를 deployer/owner/initial holder로 사용해 별도 PBLC ERC-3009 계약을 배포했다.
- 계약 `0xe75013d333bebb90b321dd658440c10b5a0face8`, 배포 tx `0xafb8c6851d07c637c18cfafd99ea32e7c4052297a2fa559b68855c0019903339`, block `46613692`.
- constructor가 사용자 지갑에 `1,000,000 PBLC` (6 decimals raw `1000000000000`)를 mint했음을 공개 RPC로 name/symbol/version/owner/totalSupply/balanceOf와 함께 재확인했다.
- 실제 x402 Facilitator verify/settle, seller 지급 전송, Provider API 호출, AWS 배포는 수행하지 않았다.
- 사전점검은 EIP-712 서명을 만들지 않고 Facilitator `/verify`·`/settle` 및 블록체인 트랜잭션 전송을 호출하지 않는다. PBLC custom-token 실제 수락과 자산 이동은 별도 사용자 승인 전까지 실행하지 않는다.

## 2026-09-10 — `/request` 실제 PBLC 결제 실행 경로 준비

- 사용자 방향전환: 기본 Mock 데모는 유지하되, 선택된 모델의 고정 가격을 사용자의 PBLC로 실제 Base Sepolia에서 결제할 수 있게 한다. Provider 응답은 계속 Mock이며 유료 Provider API와 AWS 배포는 진행하지 않는다.
- `AEGIS_EXECUTION_MODE=live`와 `AEGIS_REAL_PAYMENT_APPROVED=yes`를 모두 요구하고, 결제 실행 모듈이 `PBLC_USER_PRIVATE_KEY`로부터 유도한 주소가 `PBLC_USER_ADDRESS`와 일치할 때만 시작하게 했다. 브라우저가 모드를 지정하거나 개인키를 받지 않는다.
- 실제 제출은 `/request`의 실행 동의 뒤에만 외부 Facilitator `/verify`·`/settle`로 요청한다. 기본 x402.org Facilitator의 Base Sepolia exact 지원은 사전점검하지만, 사용자 정의 PBLC 수락 여부는 첫 승인된 요청의 외부 결과로만 확인된다.
- 이 준비 변경에서는 live verify/settle, seller Transfer, 유료 Provider 호출, AWS 배포를 실행하지 않았다. 절차와 모델별 고정 가격은 [실제 PBLC 요청 흐름](../docs/LIVE_PBLC_REQUEST_FLOW.md)을 단일 운영 참고로 둔다.

## 2026-09-10 — 로컬 Mock 판매자 수신 지갑 매핑

- 사용자가 정한 새 수신 지갑을 local runtime catalog에 적용했다: OpenAI→seller1, Anthropic→seller2, Google→seller3. 기존 기록은 변경하지 않고, 이후 Mock 실행의 결제 조건만 이 주소를 가리킨다.
