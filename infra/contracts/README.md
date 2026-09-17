# Base Sepolia contracts

- `AEGISToken.sol`: the 6-decimal ERC-3009 ERC-20 used for exact x402 payments. The deployment owner can mint balances, so token faucet replenishment is not required.
- `EvidenceAnchor.sol`: domain-neutral append-only checkpoint of MongoDB evidence heads.

Build and test without secrets:

```bash
forge test --root infra/contracts
```

`AEGISToken` is already deployed at `0x3440294d5fdc4849461c6f383a7fcf89af0c4a4b` on Base Sepolia. Do not edit its source, including comments: any change breaks reproduction of the deployed bytecode.

The one-time deployment scripts were removed after the token was deployed. A redeployment would create a second token with a fresh supply and needs new, explicitly approved tooling.

Contract addresses are injected through environment variables and must never be compiled into application code.
