# PBLC V2 ERC-3009 배포 승인 게이트

확인일: 2026-09-04

## 승인·실행 결과

- 사용자 승인 후 PBLC V2를 `0xDed7F4992D98eF31453dCebbB8c2A6b50d0284B3`에 배포했다.
- 배포 tx: `0x5e5e6b1acde5d51e0d11f4c3784d64fc738fb6daa814f3bfe14afae1c8d4e83f`, block `46349391`.
- 성공 purchase: `451f8657-cbc0-4469-acb6-a7037b4d4865`.
- x402 settle tx: `0x5b555e3c50629430cdee30c528db8f45f56441a3a058888e89ab0fb4797892ee`, block `46349821`.
- `AuthorizationUsed` log index `196`, buyer→Gemini seller `100000` units Transfer log index `197`을 독립 RPC로 확인했다.
- 동일 payload 재검증은 `invalid_exact_evm_nonce_already_used`로 거부됐다.
- 첫 두 시도는 돈이 이동하지 않았고 append-only `RECONCILIATION_REQUIRED` 증거로 보존했다. 첫 오류는 상세 응답 누락, 둘째는 공식 클라이언트와 달리 `validAfter=now`를 사용해 발생한 `invalid_exact_evm_transaction_simulation_failed`였다. 공식 구현대로 `validAfter=0`으로 수정한 뒤 성공했다.
- 같은 UTC 날짜의 V1/V2 정책이 충돌하지 않도록 wallet policy 키를 `buyer + date + token`으로 확장했다.

## 공식 지원 근거

- [x402 v2 specification](https://github.com/x402-foundation/x402/blob/main/specs/x402-specification-v2.md): v2 payload/requirements 구조와 `extra.assetTransferMethod` 확장 경계를 정의한다.
- [Exact EVM scheme](https://github.com/x402-foundation/x402/blob/main/specs/schemes/exact/scheme_exact_evm.md): EIP-3009를 호환 토큰의 권장 exact 방식으로 설명하고 `authorization` payload를 정의한다.
- [Coinbase CDP Facilitator](https://docs.cdp.coinbase.com/x402/seller/facilitator): Base Sepolia x402 v2 exact 및 EIP-3009/Permit2 ERC-20을 문서화한다. CDP `/supported` 실측에는 인증이 필요했다.
- [ERC-3009](https://eips.ethereum.org/EIPS/eip-3009): `transferWithAuthorization`, 유효 시간, 랜덤 `bytes32` nonce, `authorizationState`, `AuthorizationUsed`의 기준이다.
- x402.org 공개 `/supported` 실측은 x402 v2 `exact`, `eip155:84532`를 반환했지만 전송 방식이나 임의 토큰 주소는 열거하지 않았다. 따라서 PBLC V2 호환성은 실제 verify/settle smoke로만 확정한다.

## 보존 상태

- 기존 PBLC V1: `0x9DFFfdDcF5d7E526Bda60728e4c8F79dBA50CeD9`
- 성공 Permit2 거래: `0x32562decbafa3c670280501bafbce01b72ce698d0391c63f4e3c5113f070a0a8`
- 미완결 purchase: `14b7dd10-fba1-4ea0-afc9-fb44500d6b4b`
- 신규 구매 실행은 `eip3009`만 허용한다. 기존 Permit2 온체인 거래와 MongoDB 증거는 읽기 전용으로 보존하지만 실행·재조정 경로는 비활성화했다.

## 승인 대상

`npm run chain:v2:plan`은 RPC 조회와 gas estimation만 수행했으며 트랜잭션을 전송하지 않았다.

| 항목 | 값 |
|---|---|
| 네트워크 | Base Sepolia (`84532`) |
| 예상 PBLC V2 주소 | `0xDed7F4992D98eF31453dCebbB8c2A6b50d0284B3` |
| deployer/owner | `0x5B2BC76a3e4DeA700309FD9D746180162bcAbec8` |
| 초기 holder/buyer | `0xa45Cd1a41E1e548e2daB0123E7Cb4E3dB964cdaB` |
| 초기 공급 | `1,000,000 PBLC` (`1,000,000,000,000` units, 6 decimals) |
| 예상 gas | `848,512` |
| 예상 최대 수수료 | `0.000005939584 ETH` |
| smoke 금액 | `0.1 PBLC` (`100,000` units) |
| 판매자 | 선택 결과에 따라 Gemini 또는 Nemotron seller 지갑 |

예상 주소는 deployer의 현재 pending nonce에 의존하므로 승인 후 다른 거래가 먼저 발생하면 다시 계산해야 한다.

## 승인 후 증명할 것

1. PBLC V2 배포 receipt와 buyer 초기 잔액
2. seller 402 `exact + eip3009`
3. Facilitator `/verify` 성공
4. Facilitator `/settle` 성공
5. 정확한 buyer → selected seller `Transfer(0.1 PBLC)`
6. 같은 receipt의 `AuthorizationUsed(buyer, nonce)`
7. 동일 nonce 재결제 거부
8. Audit Evidence API, 대시보드 잔액·거래·감사 결과 반영

이 항목은 배포 전 승인 조건이었다. 성공 검증 이후 Permit2 fallback은 제거됐으며 후속 실패는 ERC-3009 reconciliation 증거로만 처리한다.
