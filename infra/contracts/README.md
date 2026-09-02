# Base Sepolia contracts

- `DemoToken.sol`: 6-decimal ERC-20 used only for the project test economy. The deployment owner can mint team balances, so faucet replenishment is not required.
- `EvidenceAnchor.sol`: domain-neutral append-only checkpoint of MongoDB evidence heads.

Build and test without secrets:

```bash
forge test --root infra/contracts
```

Deployment is intentionally not automatic. Copy `.env.example` to `.env.local`, use `scripts/setup_keys.py` in a separate terminal, then deploy only after selecting the Base Sepolia owner/writer wallets. Contract addresses are injected through environment variables and must never be compiled into application code.
