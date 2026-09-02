# Verification Boundary 1 — Second Fresh-Context Rereview Result

Reviewed: 2026-09-02
Reviewer: fresh-context `verification_boundary_1_rereview_2`
Manifest: `a8274106b4bf1e9c8073a0bc2aa75053bccc7406c703bd207ab61629a7af0bb3`
Decision: `Request changes`

## Findings

### P1

1. Lifecycle projection checks only `QUOTED`/`DECIDED`; a chain containing `REQUESTED → PAYMENT_SETTLED` can still append `QUOTED → DECIDED`. Enforce an explicit business-event transition projection.
2. Decision precheck and `QUOTED` append are not atomic. Two synchronized decisions can leave `REQUESTED → QUOTED → DECIDED → QUOTED`. Add a repository-level expected-head/count conditional transition and singleton protection.

### P2

1. Mongo append checks only the latest event/head pair. Deleting an interior head still allows a later append. Verify full event count, chain, and every sequence/hash anchor inside the transaction.

No P0/P3 findings.

## Confirmed

All four findings from the previous rereview were independently confirmed fixed. Prior integrity, confidentiality, signature, payment binding, wire, strict-input, and x402 placeholder attacks remain blocked. Required lint/test/native Mongo/script commands passed with Python 46/1 skip and TypeScript 18.

## Remediation status

- [x] Explicit business lifecycle projection
- [x] Atomic expected-head transition and QUOTED/DECIDED singleton defense
- [x] Full Mongo event-to-anchor verification
- [x] Broad suite and native Mongo rerun
- [x] Fresh-context approval
