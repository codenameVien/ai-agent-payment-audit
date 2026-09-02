# Base Sepolia contracts

- `DemoToken.sol`: 6-decimal EIP-2612 ERC-20 used only for the project test economy. The deployment owner can mint team balances, so token faucet replenishment is not required. Exact-amount permits let the x402 Facilitator sponsor the buyer's Permit2 approval gas.
- `EvidenceAnchor.sol`: domain-neutral append-only checkpoint of MongoDB evidence heads.

Build and test without secrets:

```bash
forge test --root infra/contracts
```

Deployment is intentionally not automatic. Copy `.env.example` to `.env.local`, use `scripts/setup_keys.py` in a separate terminal, then fund only the dedicated deployer wallet with enough Base Sepolia ETH for one-time contract deployment. The buyer receives PBLC in the constructor and does not need native ETH for x402 approval or settlement. Contract addresses are injected through environment variables and must never be compiled into application code.
