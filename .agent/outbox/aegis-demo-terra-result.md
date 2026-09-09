# AEGIS presentation-demo verification — Terra

Date: 2026-09-10 (Asia/Seoul)
Branch/HEAD inspected: `feature/aegis-aa-v1` / `9f6e23f`

## Result

No core-code or package-script fix was necessary. The existing local mock composition passed the reduced presentation criteria.

## Commands actually run and results

1. `npm run build --workspace @pbl/commerce-gateway`
   - Passed. This compilation precedes the focused compiled TypeScript test below.
2. `node --test --test-name-pattern='tampered 402|gateway refuses a payment payload' services/commerce-gateway/dist/tests/aegis-runtime.test.js`
   - Passed: 2 tests, 0 failures.
   - Proves a tampered `402` is refused before signing/settlement, and a payment payload that disagrees with the recorded decision receives `402` with zero settlements.
3. `node --test --require ./scripts/aegis_outbound_guard.cjs --test-name-pattern='(openai is selected|anthropic is selected|google is selected|budget below every candidate|two concurrent runs)' scripts/aegis_phase6_scenarios.test.mjs`
   - Passed: 5 tests, 0 failures, using a harness-owned temporary MongoDB replica set and loopback-only local services.
   - OpenAI, Anthropic Claude, and Google Gemini each selected their AA-fixture model, settled one x402 Mock payment, returned a result, and recorded decision/payment/delivery/audit evidence.
   - A budget below every candidate returned `409` before payment (`PAYMENT_SETTLED = 0`).
   - Two concurrent runs for one `purchaseId` recorded exactly one claimed intent and exactly one settlement/delivery.
4. `npm run build --workspace @pbl/dashboard`
   - Passed: Next.js production build; all 10 routes generated/validated.
5. `npm run lint`
   - Passed: Ruff, mypy (59 source files), seller-service TypeScript, commerce-gateway TypeScript, and dashboard TypeScript.

An earlier exploratory invocation placed Node's name-pattern option after the test file and consequently ran the complete 14-test scenario harness successfully. It did not alter persistent user storage. The focused evidence above is the intended reduced verification record.

## Changed paths

- `.agent/outbox/aegis-demo-terra-result.md` (this evidence record only)

All other existing dirty changes were preserved without modification. No commit, push, reset, rebase, amend, squash, data rewrite, deployment, asset transfer, testnet payment, AWS action, real provider call, or real Artificial Analysis API call was performed.

## Mock-versus-real boundary and remaining limitations

- Provider Gateway and payment-execution modules were the actual local application code. The harness used an ephemeral local key to perform actual ERC-3009/EIP-712 authorization signing, and its own disposable temporary MongoDB replica set; no existing transaction/audit storage was opened or cleaned.
- AA catalog/pages were synthetic fixtures, Provider responses were Mock Provider responses, and Facilitator verification/settlement was a Mock Facilitator. `x402mock:` references are mock settlement evidence, not blockchain transactions.
- The outbound guard was enabled for the focused end-to-end run; observed interactions were loopback local services. This is not a claim that every Python outbound path is comprehensively instrumented.
- Deferred non-blockers remain deferred: broad historical zero-write coverage, Mongo failure-cleanup stress, repeated broad suites, Python complete outbound instrumentation, and additional security/performance work.
