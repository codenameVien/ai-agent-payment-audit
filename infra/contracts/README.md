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

## OpenZeppelin 기반 토큰 (src-oz)

`src-oz/AegisTokenOz.sol` 은 OpenZeppelin 5.7 의 `ERC20` 과 `draft-ERC3009` 를 상속한 두 번째 AEGIS
배포다. 첫 배포(`src/AEGISToken.sol`)는 같은 기능을 직접 구현한 것이며, 배포된 바이트코드를 그대로
재현해야 해서 컴파일러 설정까지 따로 둔다.

```bash
forge install OpenZeppelin/openzeppelin-contracts --no-git   # lib/ 는 커밋하지 않는다
FOUNDRY_PROFILE=oz forge test
```

| | 첫 배포 | 두 번째 배포 |
|---|---|---|
| 소스 | `src/AEGISToken.sol` (직접 구현) | `src-oz/AegisTokenOz.sol` (OpenZeppelin) |
| 주소 | `0x3440294d5fdc4849461c6f383a7fcf89af0c4a4b` | `0xE208f63B826FcDD9De0836EFFEb0FA49d1c4142b` |
| solc | 0.8.24 (`profile.default`) | 0.8.28 (`profile.oz`) |

이름·심볼·소수점·EIP-712 버전이 같아서 x402 결제 조건은 주소만 바꾸면 된다.
첫 배포 토큰과 잔액은 서로 옮길 수 없다.
