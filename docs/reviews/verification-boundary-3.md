# Verification Boundary 3 — Final Recovery and Product Boundary

Date: 2026-09-02
Decision requested: `Approve local completion` or ranked P0–P3 findings.

## Review contract

Perform a read-only fresh-context review of the final worktree. Do not use secrets, call real providers, write to Base Sepolia/ERC-8004, apply AWS resources, publish GitHub state, or modify files.

Verify these hostile boundaries directly:

1. Cached or concurrent paid results require the exact `PAYMENT-SIGNATURE` fingerprint and prompt binding.
2. Seller settlement survives response loss through the encrypted MongoDB journal without creating a second payment authorization.
3. Hashless reconciliation accepts only a one-time `None → recovered exact tx` CAS and rejects transaction substitution.
4. Provider result persistence loss cannot invoke a paid provider twice after restart; `PROVIDER_SUBMITTED` remains an explicit unresolved delivery risk.
5. Delivery seller/provider/model/version match the selected signed quote before a safe audit result.
6. `/purchases/{id}/run`, dashboard, Compose, reputation, and anchor recovery remain connected.
7. The MVP claims one active writer per seller and Gateway, not active-active exactly-once behavior.

## Required evidence

```bash
npm run lint
npm test
npm run test:mongo:local
npm run build --workspace @pbl/dashboard
env SESSION_SECRET_BASE64=test PAYLOAD_MASTER_KEY_BASE64=test INTERNAL_SERVICE_TOKEN=test GATEWAY_SERVICE_TOKEN=test docker compose -f infra/docker/compose.yml --profile app --profile agents config --quiet
git diff --check
```

The external credential/authority gates may remain pending without blocking local completion if they are explicitly documented.
