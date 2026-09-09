# Opus parallel bounded contract preparation

You are Coder Opus for AEGIS-02's independent contract subtask. Read AGENTS.md and aidlc-docs/inception requirements/design/tasks. Another Opus is implementing AA Python source: do NOT touch its files or global manifests.

Exclusive write scope: NEW infra/contracts/src/AEGISToken.sol, NEW infra/contracts/test/AEGISToken.t.sol, NEW scripts/aegis_token_plan.mjs and its dedicated NEW test if useful, NEW docs/AEGIS_TOKEN_PREPARATION.md, .agent/outbox/aegis-contract-result.md. Use apply_patch for source edits. Do not edit existing PBLC contracts/scripts/tests, package.json, env files, or application code. No subagents, no git commit (orchestrator will commit exact files).

Prepare new nonupgradeable AEGIS ERC20 + ERC3009: both name/symbol AEGIS, decimals6, explicit EIP712 version; preserve signature/time/nonce safeguards, mint owner policy. Prefer existing proven PBLC implementation pattern without modifying historical code. Test valid, wrong signer/domain/token/chain, expired/not-yet-valid boundaries, nonce replay, insufficient balance rollback, exact Transfer/AuthorizationUsed, mint access, metadata.

Plan-only script accepts explicit PUBLIC deployer/holder/nonce and gas-price inputs, computes CREATE predicted address and creation calldata, reports estimates only when measured locally and labels assumptions. It must have no deploy/broadcast, no network, no private-key/env-local read. Any missing real wallet/nonce/gas quote remains UNKNOWN, not invented. Local Foundry gas estimates allowed; test wallets conspicuously test-only. Document PBLC constant name/symbol and no upgrade setter: no relabeling PBLC history. 1 AEGIS=1 USD nominal conversion is not backing/redemption. Actual deployment/asset transfer/testnet payment approval pending; approval packet needs real wallets/nonces/gas+0.1 suggested amount only after independent approval.

Run focused forge tests and script tests offline. No RPC, provider, Mongo, AWS, live token operation, .env.local or secret access. Report paths/tests/limitations and STOP after bounded task.
