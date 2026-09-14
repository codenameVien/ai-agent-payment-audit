# Base Sepolia contracts

- `AEGISToken.sol`: the 6-decimal ERC-3009 ERC-20 used for exact x402 payments. The deployment owner can mint balances, so token faucet replenishment is not required.
- `EvidenceAnchor.sol`: domain-neutral append-only checkpoint of MongoDB evidence heads.

Build and test without secrets:

```bash
forge test --root infra/contracts
```

`AEGISToken` is already deployed at `0x3440294d5fdc4849461c6f383a7fcf89af0c4a4b` on Base Sepolia. Do not edit its source, including comments: any change breaks reproduction of the deployed bytecode.

Deployment is intentionally not automatic, and a new deployment creates a second token with a fresh 1,000,000 AEGIS supply. Only when a redeployment is explicitly approved:

1. Store the user wallet key locally with `bash scripts/set_aegis_user_wallet_key.sh`. The key is never printed.
2. Review the public-address-only plan with `npm run aegis:token:plan`.
3. Deploy with `AEGIS_TOKEN_DEPLOY_APPROVED=yes node scripts/aegis_token_deploy.mjs deploy`. The user wallet pays the gas, becomes the owner and receives the initial supply.

Contract addresses are injected through environment variables and must never be compiled into application code.
