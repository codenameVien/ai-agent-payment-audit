# Base Sepolia contracts

- `DemoTokenV2.sol`: active 6-decimal ERC-3009 ERC-20 for exact x402 payments. The deployment owner can mint team balances, so token faucet replenishment is not required.
- `DemoToken.sol`: source of the previously deployed V1 token, retained only to explain historical on-chain evidence; application payment code cannot use it.
- `EvidenceAnchor.sol`: domain-neutral append-only checkpoint of MongoDB evidence heads.

Build and test without secrets:

```bash
forge test --root infra/contracts
```

Deployment is intentionally not automatic. Copy `.env.example` to `.env.local`, use `scripts/setup_keys.py` in a separate terminal, then fund only the dedicated deployer wallet with enough Base Sepolia ETH for one-time contract deployment. The buyer receives PBLC in the constructor and does not need native ETH for x402 approval or settlement. Contract addresses are injected through environment variables and must never be compiled into application code.
