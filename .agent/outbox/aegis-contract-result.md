# AEGIS 계약 준비 결과 (Coder Opus, contract subtask)

작성: 2026-09-09. 범위: AEGIS-02의 독립 계약/plan 항목만. 다른 Opus의 AA Python 소스와 공용 manifest는 건드리지 않았다.

## 생성 파일 (전부 신규, 기존 파일 수정 0건)

| 경로 | 내용 |
|---|---|
| `infra/contracts/src/AEGISToken.sol` | non-upgradeable AEGIS ERC-20 + ERC-3009 `transferWithAuthorization` |
| `infra/contracts/test/AEGISToken.t.sol` | 15개 offline Foundry 테스트 |
| `scripts/aegis_token_plan.mjs` | plan-only 배포 planner (네트워크·키·브로드캐스트 경로 없음) |
| `scripts/aegis_token_plan.test.mjs` | planner 11개 node 테스트 (경계 포함) |
| `docs/AEGIS_TOKEN_PREPARATION.md` | PBLC immutable 증거, 계약 명세, 테스트/gas 근거, 승인 패킷 체크리스트 |

`git status` 확인: 위 5개 파일만 신규이고, 기존 PBLC 계약/스크립트/테스트, `package.json`, env 파일, 애플리케이션 코드는 무변경이다. 커밋은 orchestrator에 남긴다.

## 실행한 검증 (모두 오프라인)

- `forge test --root infra/contracts` → 26 tests PASS (AEGIS 15 + 기존 DemoToken 4 / DemoTokenV2 5 / EvidenceAnchor 2, 기존 스위트 무변경 통과).
- `node --test scripts/aegis_token_plan.test.mjs` → 11 tests PASS.
- `sandbox-exec -p '(version 1)(allow default)(deny network*)' node scripts/aegis_token_plan.mjs …` → exit 0, plan 정상 출력. 네트워크 차단 상태에서 동작함을 실증.
- `node scripts/aegis_token_plan.mjs` (인자 없음) → 필수 입력 오류 + usage, exit 1.

## 계약 요점

- `name`/`symbol` 모두 `AEGIS`, `decimals = 6`, EIP-712 `version = "1"` 명시 상수.
- 검증 로직은 Base Sepolia에서 실증된 PBLC V2 패턴 유지: 시간창 경계 거부, nonce 1회 소비, `v ∈ {27,28}` + `s ≤ secp256k1n/2` malleability 차단, domain(name/version/chainId/verifyingContract) 결속, chainId 변경 시 separator 재구성, zero address 거부, `onlyOwner` mint/ownership.
- 테스트가 덮는 것: 정상 승인 시 이벤트가 정확히 `AuthorizationUsed` → `Transfer` 2건(topic/data 전수 비교), wrong signer/domain name/domain version/verifyingContract/chainId, `validAfter`·`validBefore` 경계와 첫·마지막 유효 초, nonce replay, 잔액 부족 rollback(nonce 미소비·잔액·총공급 불변), malleable 서명과 `v = 29`, mint 접근제어와 소유권 이전, ERC-20 allowance 경로, 메타데이터·domain separator·typehash.

## plan 스크립트 요점

- 필수 공개 입력: `--deployer --holder --nonce --initial-supply-units`. 선택: `--owner --chain-id --local-creation-gas(-source) --max-fee-per-gas-wei(-source) --test-payment-units`.
- 산출: 예상 CREATE 주소, creation calldata 길이·keccak, creation bytecode keccak, 생성자 입력, 로컬 gas 산술, 제안 테스트 금액(제안 상태), plan-only/승인대기 표기, 명목 환산·PBLC 보존 note.
- 거부: 개인키 모양 값, `--private-key/--rpc-url/--broadcast/--mnemonic/--env-file`, 미지의 플래그, 중복 플래그, 잘못된 주소·정수.
- 토큰 메타데이터를 스크립트에 하드코딩하지 않고 `AEGISToken.sol` 상수에서 파싱한다. 상수가 없으면 실패한다.
- 미제공/미측정 값은 `UNKNOWN`으로 남기고 추정치로 채우지 않는다.

## 로컬 측정값

- creation frame gas `745,062` (`testDeploymentCreationGasMeasuredLocally`, 로컬 EVM `gasleft()` 차분).
- creation code 3,968 bytes, creation calldata 4,064 bytes, intrinsic tx gas `84,188` (로컬 계산), 합계 `829,250`.
- 참고로 PBLC V2의 RPC 추정치는 `848,512`였다. 위 값은 로컬 산정이며 `eth_estimateGas`를 대체하지 않는다.

## 한계와 UNKNOWN

- `receiveWithAuthorization`, `cancelAuthorization` 미구현. 근거: x402 v2 exact는 `transferWithAuthorization`만 사용하며 실증된 PBLC V2 표면과 동일하게 유지. 전체 ERC-3009 표면이 필요하면 별도 승인 항목.
- 실제 배포 지갑/holder 지갑, 실제 pending nonce, 라이브 gas quote, 실제 예상 주소, deployer ETH 잔액은 모두 UNKNOWN. 조회하지 않았다.
- 문서의 샘플 지갑은 공개 Anvil 기본 테스트 계정이며 test-only로 명시했다. 실제 지갑으로 오인될 값을 넣지 않았다.
- `package.json`을 수정하지 않았으므로 두 테스트는 npm alias 없이 `forge test --root infra/contracts`, `node --test scripts/aegis_token_plan.test.mjs`로 실행한다. npm 스크립트 등록이 필요하면 orchestrator 결정 사항이다.
- `forge build` 경고: `block-timestamp`(ERC-3009 시간창 특성상 불가피, 기존 `DemoToken`/`DemoTokenV2`와 동일 유형)와 테스트 파일의 `unsafe-typecast`(revert 데이터에서 4바이트 selector 추출, 테스트 전용 의도된 캐스트). 기존 스위트도 동종 경고를 가진 상태다.
- 미실행: 배포, 자산 이동, 실제 테스트넷 결제, RPC/Provider/Mongo/AWS 접근, `.env.local`·시크릿 접근, git commit.

## 다음 경계

승인 패킷은 실제 지갑·nonce·gas quote가 확정된 뒤에만 채운다. 제안 첫 결제 금액은 `0.1 AEGIS`(`100000` units)이며 승인 전에는 제안 상태로 유지한다.
