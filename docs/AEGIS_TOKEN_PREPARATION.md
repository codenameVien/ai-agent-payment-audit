# AEGIS 토큰 준비 기록 (plan only)

작성일: 2026-09-09. 대상 요구: AEGIS-US-05, 작업 AEGIS-02의 토큰 준비 항목.

상태: **준비 완료, 실행 미승인.** 배포·자산 이동·실제 테스트넷 결제는 실행하지 않았다. 이 문서의 수치는 모두 로컬 Foundry 측정 또는 로컬 산술이며 RPC 조회 결과가 아니다.

## 1. PBLC 개명 불가 증거

| 근거 | 위치 | 내용 |
|---|---|---|
| 이름/심볼 상수 | `infra/contracts/src/DemoTokenV2.sol:7-10` | `name = "PBL Agent Credit"`, `symbol = "PBLC"`, `version = "2"`, `decimals = 6`이 모두 `constant`다. |
| setter 없음 | `DemoTokenV2.sol` 전체 | `setName`/`setSymbol`류 함수, `initialize`, `upgradeTo`, `_authorizeUpgrade`, `delegatecall`이 존재하지 않는다. |
| 배포본 고정 | `docs/ERC3009_DEPLOYMENT_GATE.md` | PBLC V2는 `0xDed7F4992D98eF31453dCebbB8c2A6b50d0284B3`에 non-upgradeable로 배포되어 있다. |

따라서 기존 배포본을 AEGIS로 개명하는 경로는 존재하지 않는다. 신규 이름/심볼은 **별도 계약 배포**만으로 가능하다. PBLC V1/V2 소스, 주소, 과거 거래·증거는 수정하지 않았고 AEGIS 라벨을 붙이지도 않았다.

## 2. 신규 계약 명세

`infra/contracts/src/AEGISToken.sol` (신규, non-upgradeable)

| 항목 | 값 |
|---|---|
| `name` / `symbol` | `AEGIS` / `AEGIS` (둘 다 상수) |
| `decimals` | `6` |
| EIP-712 domain | `name = "AEGIS"`, `version = "1"` (명시 상수), `chainId`, `verifyingContract` |
| 표준 | ERC-20 + ERC-3009 `transferWithAuthorization` (x402 v2 exact 경로) |
| solc | `0.8.24+commit.e11b9ed9`, optimizer on / runs 200 (`infra/contracts/foundry.toml`) |
| creation code | 3,968 bytes, keccak `0x9bfd9946b3652abcd3565f5b1cf5533d06bb499505cd4319217f590ddfb551ce` |
| constructor | `(address initialOwner, address initialHolder, uint256 initialSupplyUnits)` |

검증 로직은 Base Sepolia에서 verify/settle이 실증된 PBLC V2 구현 패턴을 그대로 따른다(`docs/ERC3009_DEPLOYMENT_GATE.md`). 기존 소스는 수정하지 않고 신규 파일에 동일 안전장치를 유지했다.

유지한 안전장치:

- 시간창: `block.timestamp <= validAfter` → `AuthorizationNotYetValid`, `block.timestamp >= validBefore` → `AuthorizationExpired` (경계 시각은 거부).
- nonce: `authorizationState(authorizer, nonce)` 1회 소비, 재사용 시 `AuthorizationAlreadyUsed`.
- 서명: EIP-712 domain(name/version/chainId/verifyingContract) 결속, `v ∈ {27,28}`, `s ≤ secp256k1n/2` malleability 차단, `ecrecover` 결과 ≠ `from`이면 `InvalidAuthorization`.
- chainId 재계산: 배포 시 chainId와 다르면 domain separator를 재구성하므로 fork/replay 서명이 통과하지 않는다.
- 잔액 부족은 nonce 소비 전 revert가 아니라 전체 트랜잭션 rollback으로 처리되어 nonce가 소비되지 않는다.
- mint 정책: `onlyOwner` mint, `transferOwnership`은 owner 전용이며 zero address 거부.

### 의도적 미구현 (명시 한계)

ERC-3009의 `receiveWithAuthorization`, `cancelAuthorization`은 구현하지 않았다. 근거: x402 v2 exact EVM 스킴은 `transferWithAuthorization`만 사용하며, 실증된 PBLC V2 결제 경로와 동일 표면을 유지하는 것이 이번 준비 범위다. 완전한 ERC-3009 표면이 필요해지면 별도 승인·재검증 대상이다.

## 3. 테스트 결과 (offline)

```
forge test --root infra/contracts
```

- `test/AEGISToken.t.sol:AEGISTokenTest` 15 tests PASS, 전체 스위트 26 tests PASS (기존 `DemoToken`, `DemoTokenV2`, `EvidenceAnchor` 포함, 수정 없음).

| 테스트 | 검증 대상 |
|---|---|
| `testMetadataAndInitialSupply` | AEGIS/AEGIS/6/version "1", owner, 초기 공급, domain separator, typehash |
| `testConstructorRejectsZeroAddresses` | owner/holder zero address 배포 거부 |
| `testValidAuthorizationEmitsExactEventsAndConsumesNonce` | 이벤트가 정확히 2개: `AuthorizationUsed(authorizer, nonce)` → `Transfer(from, to, value)`, topic/data 전수 비교, 잔액·nonce·총공급 |
| `testWrongSignerRejected` | 다른 키 서명 거부, nonce 미소비, 자금 이동 없음 |
| `testWrongDomainNameOrVersionRejected` | domain name `PBL Agent Credit` 및 version `2` 서명 거부 |
| `testWrongTokenAddressRejected` | 다른 AEGIS 인스턴스(`verifyingContract`) 서명 거부 |
| `testWrongChainIdRejected` | chainId 변경 시 separator 재구성 및 기존 서명 거부 |
| `testTimeWindowRejectedAtBoundaries` | `validAfter`, `validBefore` 경계 시각 거부 + 오류 selector |
| `testTimeWindowAcceptedAtFirstAndLastValidSecond` | `validAfter+1`, `validBefore-1` 통과 |
| `testNonceReplayRejected` | 동일 nonce 재사용 거부, 추가 이체 없음 |
| `testInsufficientBalanceRollsBackWithoutConsumingNonce` | 잔액 부족 rollback, nonce 미소비, 잔액·총공급 불변 |
| `testMalleableSignatureRejected` | `s` 반전 + `v` 반전 서명, `v = 29` 거부 |
| `testMintPolicyAndOwnershipTransfer` | owner mint 이벤트, 비owner mint/ownership 거부, zero owner 거부, 이전 후 구 owner 차단 |
| `testErc20TransferAndAllowancePaths` | transfer/approve/transferFrom, allowance 차감, 초과 지출·zero 수신자 거부 |
| `testDeploymentCreationGasMeasuredLocally` | 로컬 creation gas 측정 및 회귀 상한 |

이벤트 검사 note: Foundry의 log recorder는 revert된 프레임의 로그도 보관하므로, rollback 증거는 로그 개수가 아니라 저장 상태(nonce/잔액/총공급)로 검사한다.

## 4. plan-only 스크립트

`scripts/aegis_token_plan.mjs` — 배포/브로드캐스트/서명/네트워크 접근 경로가 없다. 개인키 모양(0x + 64 hex) 값, `--private-key`, `--rpc-url`, `--broadcast`, `--mnemonic`, `--env-file` 플래그는 거부한다. 환경변수·dotenv 파일을 읽지 않는다.

실행:

```
forge build --root infra/contracts   # 아티팩트 생성 (오프라인)
node scripts/aegis_token_plan.mjs \
  --deployer <public 0x address> --holder <public 0x address> \
  --nonce <실제 nonce> --initial-supply-units 1000000000000 \
  [--owner <public 0x address>] [--chain-id 84532] \
  [--local-creation-gas 745062 --local-creation-gas-source "forge test ..."] \
  [--max-fee-per-gas-wei <실제 quote>]
```

테스트: `node --test scripts/aegis_token_plan.test.mjs` — 11 tests PASS. 소스 경계(네트워크/키/브로드캐스트 참조 부재), 필수 입력 강제, 개인키·네트워크 플래그 거부, 소스에서 읽은 토큰 메타데이터, CREATE 주소를 독립 RLP 유도와 대조, 미제공 값의 `UNKNOWN` 유지, 아티팩트 결손 시 실패를 검사한다.

무네트워크 실증: `sandbox-exec -p '(version 1)(allow default)(deny network*)' node scripts/aegis_token_plan.mjs ...` 가 정상 종료(exit 0)로 plan을 출력했다.

### 샘플 출력 (test-only 지갑)

아래 지갑은 **공개된 Anvil/Hardhat 기본 테스트 계정**이며 이 프로젝트의 실제 지갑이 아니다. 실제 배포 지갑은 아직 결정·승인되지 않았다.

| 항목 | 값 |
|---|---|
| deployer/owner (TEST-ONLY) | `0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266` |
| holder (TEST-ONLY) | `0x70997970C51812dc3A010C7d01b50e0d17dc79C8` |
| nonce | `0` |
| 예상 주소 (TEST-ONLY 입력 기준) | `0x5FbDB2315678afecb367f032d93F642f64180aa3` |
| 초기 공급 | `1000000 AEGIS` (`1000000000000` units) |
| creation calldata | 4,064 bytes |

### 로컬 gas 산정

| 항목 | 값 | 출처 |
|---|---|---|
| creation frame gas | `745,062` | `forge test --match-test testDeploymentCreationGasMeasuredLocally -vv` (로컬 EVM `gasleft()` 차분, 32000 create charge와 EIP-3860 initcode word cost 포함) |
| intrinsic tx gas | `84,188` | 로컬 계산: 21000 + 비영 바이트 3,911 × 16 + 영 바이트 153 × 4 |
| 합계 | `829,250` | 위 두 값의 합. `eth_estimateGas` 결과가 아니다 |
| 실제 gas price | UNKNOWN | 라이브 quote 없음. `--max-fee-per-gas-wei` 미지정 시 비용은 `UNKNOWN` |

참고: PBLC V2의 RPC 기반 추정치는 `848,512`였다(`docs/ERC3009_DEPLOYMENT_GATE.md`). 위 로컬 합계는 같은 자리수의 독립 산정이며 실제 네트워크 추정치를 대체하지 않는다.

## 5. UNKNOWN 목록 (승인 전 확정 불가)

- 실제 배포 지갑(deployer/owner), 실제 초기 holder 지갑
- 해당 지갑의 실제 pending nonce → 실제 예상 주소
- 라이브 gas quote(`maxFeePerGas`) 및 실제 최대 수수료
- 배포 대상 chainId 확정 및 deployer ETH 잔액

이 값들은 조회하지 않았고 추정치로 채우지 않았다. plan 출력은 미제공 항목을 `UNKNOWN`으로 표시한다.

## 6. 명목 환산 경계

`1 AEGIS = 1 USD`는 가격 표시용 명목 환산이다. 담보·예치·상환 보장이 아니며, 계약에는 어떤 환매·페그·담보 로직도 없다. 결제액은 최대 출력 토큰 기준 사전 고정 금액이며 사용량 사후 정산이 아니다.

## 7. 승인 패킷 체크리스트 (승인 요청 전에 채워 제시한다)

1. 사용 지갑: deployer/owner, 초기 holder (실제 공개 주소)
2. 배포 방식: EOA CREATE 단일 트랜잭션, factory/CREATE2 미사용
3. 예상 주소: 승인 요청 전 확인한 실제 nonce로 계산한 값. 실행 직전 nonce가 달라졌다면 변경된 주소를 다시 제시한다.
4. gas 추정: 로컬 `829,250`은 참고치다. 실제 배포 입력의 gas 추정과 당시 gas quote·Base 추가 수수료를 확인하여 예상 비용을 승인 요청 전에 제시한다.
5. 테스트 금액: `0.1 AEGIS` (`100000` units) 제안 — 승인 전에는 제안 상태로만 유지

패킷을 제시한 뒤 사용자 승인을 받고 실행한다. 값이 확정되지 않았을 때 포괄 배포 승인을 먼저 요청하지 않는다. 승인 전까지 배포, 자산 이동, 실제 테스트넷 결제는 실행하지 않는다. 위 승인 자료 준비를 위한 읽기 전용 조회는 배포·결제 실행과 구분하며, 이번 작업에서는 실제 RPC 조회도 수행하지 않았다.
