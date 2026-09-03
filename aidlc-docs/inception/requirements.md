# Requirements — AI Agent M2M Payment Audit

Status: Revised and Approved
Approved: 2026-09-02
Revised: 2026-09-04 — request/dashboard boundary, Buyer ownership, Audit Evidence API, SDK Wrappers, and parallel ERC-3009 migration

## 1. Scope Contract

- **product-quality:** high
- **engineering-assurance baseline:** standard
- **scoped override:** high-assurance for SIWE authentication, autonomous wallet policy, payment authorization/submission, audit-evidence integrity, and encrypted sensitive payloads
- **security-sensitive:** yes
- **has-ui:** yes
- **operating model:** one demo user on Base Sepolia, with provider APIs and blockchain infrastructure treated as external and potentially unavailable
- **primary threat model:** prevent unauthorized, duplicated, replayed, substituted, or evidence-detached payments; prevent disclosure or silent modification of sensitive decision evidence

## 2. Confirmed Product Intent

### Problem and user

The primary user is a person delegating an AI-inference purchase to a buyer agent. A successful blockchain payment does not prove that the agent selected an appropriate model or followed the user's budget and policy.

### Why now

x402 enables autonomous agent payments, while ERC-8004 provides an emerging identity and reputation layer. The graduation project must demonstrate how those mechanisms can be combined with off-chain decision evidence rather than treating payment success as sufficient accountability.

### Observable success

In one reproducible demonstration, the user can submit an AI request, see the buyer compare eligible Gemini and Nemotron offers, observe exactly one custom-token payment on Base Sepolia, receive the purchased response, and inspect a linked audit report that explains the choice and detects a deliberately introduced rule violation or evidence mismatch.

### Binding constraints

- Use a project-issued ERC-20 token rather than repeatedly acquiring faucet USDC.
- Target Base Sepolia x402 v2 `exact` with PBLC V2 ERC-3009. Keep the proven Permit2/EIP-2612 path active until ERC-3009 passes local tests and an explicitly approved real settlement.
- Place the internal Audit Evidence API between every agent/payment service and MongoDB; Seller Service and Payment Executor never access MongoDB directly.
- Preserve provider/model portability through adapters.
- Prove the complete path locally before AWS deployment.

### Out of scope for MVP

- Production money, mainnet settlement, or a real token economy
- Multi-user tenancy, organization administration, or public marketplace onboarding
- Seller integrations beyond Gemini and Nemotron
- Automatic refunds, automatic repurchase after failed delivery, or dispute arbitration
- On-chain publication of semantic/LLM-only reputation judgments
- A model-specific seller agent for every model
- Production SLA, global scaling, or cross-chain operation

## 3. Actors and System Boundaries

- **User:** owns a MetaMask wallet, states the request/budget/policy, and reviews results.
- **Purchase request page:** `/request`; collects request, optional budget, and priority, then starts the buyer agent. It is not part of the audit dashboard.
- **Buyer agent:** owns request analysis, quote comparison, seller selection, purchase approval, payment-execution request, delivery, and final result return.
- **Seller agent:** represents one provider, advertises multiple models, returns signed/traceable quotes, and delivers one inference result after settlement.
- **Payment Executor:** the renamed Commerce Gateway implementation used by the buyer agent. It validates policy/quote bindings, isolates the programmatic buyer key, signs x402 payloads, and is the only blockchain writer. It may remain a separate process/container.
- **Audit Evidence API:** a MongoDB record/query module, not an agent. It joins request, quote, decision, payment, response, and audit evidence by `purchaseId` and enforces validation, event order, hash-chain integrity, and internal access control.
- **Audit engine:** applies deterministic rules and optional LLM semantic analysis.
- **Audit dashboard:** `/` and `/dashboard`; read-only balance, decisions, transactions, seller reputation, and alerts. It contains no purchase form or experiment runner.
- **Admin:** deploys/registers contracts and agents, mints demo credits, and funds the programmatic buyer wallet.

## 4. Functional Requirements

### US-01 — Authenticate and bind the user's wallets

As a user, I want to sign in with MetaMask so that the dashboard can show records belonging to my owner wallet and its bound buyer-agent wallet.

- **AC-01.1:** GIVEN a valid SIWE challenge WHEN the user signs it with MetaMask THEN the system creates an authenticated session bound to that wallet address.
- **AC-01.2:** GIVEN an invalid, expired, wrong-domain, wrong-chain, or replayed SIWE message WHEN authentication is attempted THEN access is denied and the nonce cannot be reused.
- **AC-01.3:** GIVEN an authenticated owner wallet WHEN the dashboard loads THEN it shows the bound programmatic wallet address and demo-token balance without exposing its private key.

### US-02 — Submit one purchasable AI request

As a user, I want to submit a prompt with optional budget and priority so that the buyer agent can purchase one appropriate inference.

- **AC-02.1:** GIVEN an authenticated user WHEN a non-empty prompt is submitted THEN the system creates one unique `purchaseId` and one request timeline.
- **AC-02.2:** GIVEN no explicit budget WHEN the request is submitted THEN the configured system limit applies.
- **AC-02.3:** GIVEN an explicit user budget lower than a system limit WHEN policy is evaluated THEN the lower limit is enforced.
- **AC-02.4:** GIVEN the priority preset balanced, quality, price, or speed WHEN candidates are scored THEN the documented preset weights are used and stored with the decision.
- **AC-02.5:** GIVEN `/request` WHEN the user submits a request THEN the page collects request, optional PBLC budget, and one priority preset, requires explicit testnet-payment acknowledgement, and retains the pending `purchaseId` across same-origin tabs so a retry does not silently create another purchase.
- **AC-02.6:** GIVEN `/experiments` WHEN it is opened THEN it redirects to `/request`; no existing purchase, payment intent, or evidence event is deleted or rewritten.

### US-03 — Discover eligible models and obtain live quotes

As a buyer agent, I want benchmark evidence and seller quotes so that candidate comparison is reproducible.

- **AC-03.1:** GIVEN a request WHEN discovery runs THEN only enabled Gemini and Nemotron seller integrations are purchasable in MVP.
- **AC-03.2:** GIVEN benchmark data no older than 24 hours WHEN discovery runs THEN the cached snapshot may be used and its source timestamp is recorded.
- **AC-03.3:** GIVEN missing required benchmark evidence WHEN no valid snapshot can be obtained THEN payment does not proceed and the user sees a recoverable failure.
- **AC-03.4:** GIVEN eligible candidates WHEN quoting runs THEN up to the top three purchasable candidates receive quote requests.
- **AC-03.5:** GIVEN a seller quote WHEN it is accepted as evidence THEN it includes provider/model/version, price, expected latency, input/output limits, availability, ERC-8004 identity, recipient, token, expiry, and optional counteroffer/reason.
- **AC-03.6:** GIVEN one provider-level seller agent WHEN its preferred model cannot satisfy the request THEN it may make at most one counteroffer using another model from the same provider.

### US-04 — Select and explain the winning offer

As a user, I want the buyer's choice and reasoning to be visible so that I can judge whether it followed my intent.

- **AC-04.1:** GIVEN candidate offers WHEN selection starts THEN hard filters reject candidates violating budget, availability, allowed-seller policy, or ERC-8004 identity requirements before scoring.
- **AC-04.2:** GIVEN remaining candidates WHEN scored THEN the system applies exactly one stored preset:
  - balanced: quality 40, price 25, speed 20, reputation 10, freshness 5
  - quality: quality 60, price 10, speed 15, reputation 10, freshness 5
  - price: price 50, quality 25, speed 10, reputation 10, freshness 5
  - speed: speed 50, quality 25, price 10, reputation 10, freshness 5
- **AC-04.3:** GIVEN the highest eligible score WHEN the buyer decides THEN it stores candidates, filters, weights, component scores, winner, rejected alternatives, benchmark snapshot ID, quote ID, and explanation.
- **AC-04.4:** GIVEN an LLM-generated explanation that conflicts with deterministic evidence WHEN audited THEN deterministic rules remain authoritative and the discrepancy is flagged.

### US-05 — Execute exactly one policy-compliant payment

As a user, I want the buyer agent to pay autonomously within strict limits so that the demonstration is useful without allowing uncontrolled spending.

- **AC-05.1:** GIVEN a selected unexpired quote WHEN payment is requested THEN the buyer agent asks the Payment Executor to verify `purchaseId`, amount, token, recipient, quote expiry, seller identity, user budget, transaction limit, and rolling daily limit before signing.
- **AC-05.2:** GIVEN PBLC V2 is configured for parallel verification WHEN payment is constructed THEN the Seller advertises x402 v2 `exact` with `extra.assetTransferMethod: eip3009`, and the Payment Executor signs an exact `TransferWithAuthorization` payload whose amount, recipient, token, validity window, and random `bytes32` nonce match the selected quote.
- **AC-05.2a:** GIVEN ERC-3009 has not yet passed the approved real-settlement gate WHEN normal purchases run THEN the existing Permit2/EIP-2612 path remains available and no historic Permit2 evidence is modified.
- **AC-05.3:** GIVEN any retry for a `purchaseId` that already has a successful settlement WHEN the Payment Executor receives it THEN no second payment is submitted and the existing result is returned or reported.
- **AC-05.4:** GIVEN an amount above 1 demo token per transaction or a rolling total above 20 demo tokens per day WHEN payment is requested THEN the Gateway rejects it before signing.
- **AC-05.5:** GIVEN a settled payment followed by service-delivery failure WHEN the workflow handles the failure THEN it creates an alert and does not automatically repurchase or refund.

### US-06 — Deliver and prove the purchased response

As a user, I want the actual model response connected to the payment so that payment and delivery can be audited together.

- **AC-06.1:** GIVEN a confirmed settlement WHEN the chosen seller serves the inference THEN the result records provider, model version, latency, status, and response hash.
- **AC-06.2:** GIVEN a raw prompt or response WHEN persisted THEN it is encrypted in the separate `sensitivePayloads` store; the general event log stores references and hashes only.
- **AC-06.3:** GIVEN MVP retention policy B WHEN records are stored THEN the system does not automatically delete sensitive payloads.
- **AC-06.4:** GIVEN preparation for public AWS deployment WHEN the release gate is evaluated THEN deployment is blocked until TTL or manual-deletion policy and demonstration-evidence preservation are explicitly reconsidered.

### US-07 — Audit decision, payment, and delivery evidence

As a user, I want rule-based and semantic audit results so that objective violations and uncertain concerns are distinguishable.

- **AC-07.1:** GIVEN a completed or failed purchase WHEN audit runs THEN deterministic checks cover: request priority versus stored weights; hard-filter reasons versus request/budget/policy; whether a higher-scoring eligible candidate was unfairly rejected or bypassed; winner versus score ordering; generated explanation versus preset/winner/score; quote versus payment amount-token-recipient; duplicate settlement per `purchaseId`; seller ERC-8004 identity; and independent Base Sepolia `Transfer` evidence.
- **AC-07.2:** GIVEN prompt, decision explanation, and structured evidence WHEN semantic audit runs THEN the LLM may flag request-versus-rationale concerns but cannot clear a deterministic violation.
- **AC-07.3:** GIVEN findings WHEN severity is assigned THEN results use normal, caution, or risk; objective violations are risk and semantic uncertainty or missing context is caution unless supported by objective evidence.
- **AC-07.4:** GIVEN an audit report WHEN displayed THEN each finding names the applicable rule, evidence reference, human-readable reason, and recommended inspection action.

### US-08 — Preserve tamper-evident evidence

As an auditor, I want every stage connected by identifiers and hashes so that missing or altered evidence can be detected.

- **AC-08.1:** GIVEN any purchase transition WHEN it is stored THEN an append-only event records `purchaseId`, event type, timestamp, actor, previous-event hash, payload hash, and evidence references.
- **AC-08.2:** GIVEN a pre-payment decision WHEN the Gateway authorizes it THEN the Gateway signs the decision/evidence hash before settlement.
- **AC-08.3:** GIVEN objective ERC-8004 feedback WHEN published THEN success maps to 100, confirmed failure maps to 0, and the detailed off-chain evidence is referenced through `feedbackHash`.
- **AC-08.4:** GIVEN semantic-only warnings WHEN reputation is updated THEN they remain off-chain and do not reduce the objective on-chain score.
- **AC-08.5:** GIVEN Seller Service or Payment Executor needs to record/read evidence WHEN it accesses persistence THEN it uses authenticated Audit Evidence API endpoints and cannot connect directly to MongoDB.

### US-09 — Inspect the system through a dashboard

As a user, I want one place to inspect balances, transactions, choices, reputation, and warnings.

- **AC-09.1:** GIVEN an authenticated user WHEN the overview loads THEN it shows owner/agent wallet addresses, token balance, recent purchases, and unresolved risk/caution counts.
- **AC-09.2:** GIVEN a purchase WHEN its detail is opened THEN the user sees the request summary, candidate comparison, winning rationale, quote, status timeline, transaction link, response proof, and audit findings.
- **AC-09.3:** GIVEN a seller identity WHEN its reputation view opens THEN the dashboard shows ERC-8004 registration and objective feedback with links to supporting purchase evidence.
- **AC-09.4:** GIVEN a material state change WHEN the dashboard is open THEN the UI receives an SSE update or clearly indicates degraded/stale data and allows refresh.
- **AC-09.5:** GIVEN MVP alerting WHEN a caution or risk is created THEN it appears in the dashboard; email, SMS, and push notifications are not required.
- **AC-09.6:** GIVEN any audit-dashboard route WHEN it renders THEN it contains no request form, purchase-submit action, scenario runner, or experiment trigger.

### US-10 — Bootstrap the demonstration environment

As an admin, I want a repeatable setup so that the team can reproduce the graduation demonstration.

- **AC-10.1:** GIVEN a fresh Base Sepolia setup WHEN deployment scripts run THEN they deploy or configure the 6-decimal project ERC-20 whose unit represents one USD-equivalent demo credit.
- **AC-10.2:** GIVEN the admin wallet WHEN demo funding is performed THEN only the admin can mint credits and transfer them to the bound buyer-agent wallet; no public faucet is required.
- **AC-10.2a:** GIVEN a buyer-agent wallet with project credits but no native ETH WHEN its first x402 Permit2 payment is authorized THEN an EIP-2612 permit lets the Facilitator sponsor the approval and settlement gas without a buyer-funded approval transaction.
- **AC-10.2b:** GIVEN the ERC-3009 migration WHEN PBLC V2 is built THEN it is a separately deployed, non-upgrade proxy-free ERC-20 with `transferWithAuthorization`, `authorizationState`, random `bytes32` nonce replay protection, `validAfter`/`validBefore`, EIP-712 verification, and `AuthorizationUsed`.
- **AC-10.2c:** GIVEN PBLC V2 has not been deployed WHEN the deployment step is reached THEN the operator reports the deployment account, buyer/seller addresses, predicted contract address if available, estimated gas/cost, mint amount, and `0.1 PBLC` test amount and waits for explicit user approval.
- **AC-10.3:** GIVEN buyer and seller agents WHEN registration runs THEN each has an ERC-8004 identity and the application stores the resulting chain identifiers.
- **AC-10.4:** GIVEN missing credentials or private keys WHEN local or AWS services start THEN they fail safely with actionable configuration errors and never print secret values.

## 5. Interaction Surfaces

| Surface | User stories | Responsibility | User outcome |
|---|---|---|---|
| Sign-in | US-01 | SIWE challenge and wallet binding | User enters only records associated with the signed wallet |
| Purchase request (`/request`) | US-02–US-05 | Request, optional PBLC budget, priority, acknowledgement, buyer-agent start, and safe retry | User delegates one autonomous purchase outside the audit dashboard |
| Audit overview (`/`, `/dashboard`) | US-01, US-09 | Read-only balance, payment, audit, and alert summary | User immediately sees account and evidence health without purchase or experiment controls |
| Transaction list | US-09 | Filterable request/payment/audit summaries | User finds a prior decision quickly |
| Transaction detail | US-03–US-09 | Evidence comparison and end-to-end timeline | User understands what was chosen, why, paid, delivered, and flagged |
| Seller reputation | US-03, US-08, US-09 | ERC-8004 identities and objective feedback | User compares seller trust evidence |
| Audit alerts | US-07, US-09 | Risk/caution queue and evidence links | User can inspect unresolved concerns |

## 6. Key Flows

### Main flow

1. User signs in with MetaMask.
2. User submits prompt, optional budget, and priority.
3. Buyer parses constraints, loads a benchmark snapshot, and requests live seller quotes.
4. Buyer hard-filters, scores, selects, and records the decision.
5. Buyer agent asks its separately deployed Payment Executor to validate and submit one x402 exact payment.
6. Selected seller returns one model inference.
7. Audit engine cross-checks decision, quote, chain settlement, delivery, and evidence hashes.
8. Dashboard receives status updates and exposes the complete trace.

### Alternate and recovery flows

- Missing/expired benchmark: refresh once; if evidence remains unavailable, stop before payment.
- Expired or invalid quote: return to quoting without paying.
- Insufficient balance or policy limit: stop before signing and show the exact limit.
- Facilitator or chain unavailable: keep the purchase recoverable at payment-pending/failed without creating a duplicate settlement.
- Payment settled but inference failed: retain settlement proof, create a risk alert, and require manual follow-up.
- Seller model unavailable: accept at most one in-provider counteroffer, then re-score.
- SSE disconnected: mark data as potentially stale and support manual refresh.
- Tampered or missing evidence: show a risk finding and preserve the last verifiable hash-chain point.

## 7. Applicable UI States

- **Sign-in:** initial, wallet unavailable, signature pending, authenticated, rejected/expired challenge.
- **Purchase request:** ready, acknowledgement required, request creation, discovery/quote/decision/payment in progress, completed, limit exceeded, and recoverable external failure with the created `purchaseId` retained.
- **Audit overview:** loading, empty, populated, stale/degraded, and failed with refresh; it has no mutation control except authentication/session actions.
- **Transaction list:** loading, empty, populated, stale/degraded, failed with retry.
- **Transaction detail:** requested, discovered, quoted, decided, payment pending, settled, delivered, audited, and explicit failure states at each boundary.
- **Seller reputation:** loading, registered with feedback, registered without feedback, identity mismatch, chain unavailable.
- **Audit alerts:** empty/normal, caution, risk, acknowledged locally, stale/degraded.

## 8. Data and Audit Constraints

- The canonical workflow identity is `purchaseId`; transaction hash, quote ID, benchmark snapshot ID, event IDs, audit report ID, ERC-8004 agent ID, and feedback hash attach to it.
- Existing successful Permit2 settlements, failed/incomplete purchases, payment intents, seller journals, encrypted payloads, and evidence heads are immutable migration inputs and must not be deleted, overwritten, or backfilled with fabricated ERC-3009 fields.
- General structured evidence and sensitive raw payloads must be separable by access policy and storage encryption.
- General purchase events are append-only and hash-linked.
- Corrections are new events; existing audit evidence is never silently overwritten.
- The initial MongoDB model must cover users, agents, providers, model catalog, benchmark snapshots, purchase events, audit reports, wallet policies, and separate sensitive payloads.
- Timestamps are stored in UTC and displayed in the user's locale.

## 9. Non-Functional Requirements

### Security

- **NFR-SEC-01:** Private keys and API credentials never enter source control, application logs, dashboard payloads, or chat-based setup.
- **NFR-SEC-02:** Deployed secrets are retrieved from AWS Secrets Manager with least-privilege task roles.
- **NFR-SEC-03:** Sensitive payload encryption uses an authenticated encryption scheme and stores key material separately from MongoDB.
- **NFR-SEC-04:** Payment authorization is idempotent and rejects replay, mismatched quote bindings, expired quotes, and chain-ID mismatch.
- **NFR-SEC-05:** Authentication cookies/tokens are secure, short-lived, and bound to verified SIWE sessions.
- **NFR-SEC-06:** FastAPI never receives or stores the buyer private key; key use remains isolated in the separately deployed Payment Executor.

### Reliability and integrity

- **NFR-REL-01:** A process restart or retry cannot create a second successful payment for the same `purchaseId`.
- **NFR-REL-02:** External timeout/failure states remain inspectable and retryable only at safe pre-payment boundaries.
- **NFR-REL-03:** Audit hash-chain verification identifies the first missing or modified event.

### Performance

- **NFR-PERF-01:** Excluding external model, facilitator, and chain latency, dashboard/API operations should respond within 2 seconds at demo load.
- **NFR-PERF-02:** Progress updates should appear within 2 seconds of the backend recording a new state under normal demo conditions.

### Usability and accessibility

- **NFR-UX-01:** Every risk/caution message explains what happened, which evidence supports it, and what the user can inspect next.
- **NFR-UX-02:** Primary dashboard paths are keyboard accessible and meet WCAG 2.1 AA color-contrast expectations.
- **NFR-UX-03:** The dashboard remains usable at desktop and tablet widths; mobile optimization beyond readable inspection is not required.

### Observability

- **NFR-OBS-01:** Services emit structured logs with `purchaseId` and correlation IDs but exclude prompt/response bodies and secrets.
- **NFR-OBS-02:** AWS deployment exposes service health, payment failures, audit failures, and unhandled exceptions through CloudWatch.

## 10. External Dependencies and Proof Obligations

- Official x402 v2 and Coinbase documentation plus live `/supported` evidence must be captured for Base Sepolia `exact`; custom PBLC V2 EIP-3009 compatibility remains unproven until `/verify`, `/settle`, `AuthorizationUsed`, and exact `Transfer` are observed in an explicitly approved real transaction.
- The current PBLC/Permit2 real transaction remains the rollback path until the ERC-3009 proof obligation succeeds. A failed ERC-3009 smoke records the Facilitator response and cause without deleting Permit2.
- Gemini and Nemotron integrations must be proven with actual provider responses before final demonstration; mocks are acceptable for local development tests only.
- ERC-8004 registration and feedback must be confirmed with transaction hashes and readable on-chain state.
- MongoDB persistence claims require a stored record query, not only a connection message.
- AWS deployment happens only after the local end-to-end path passes.

## 11. Requirements Approval Gate

- **Decision:** Approve and Continue
- **Approved on:** 2026-09-02; revision explicitly directed by the user on 2026-09-04.
- This revision supersedes conflicting Permit2-only, Commerce-Gateway-as-owner, `/experiments`, and Evidence-Repository-as-component wording while preserving historic evidence.
- Next external checkpoint: PBLC V2 deployment approval after local implementation and verification.
