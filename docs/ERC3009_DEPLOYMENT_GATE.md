# PBLC V2 ERC-3009 배포 승인 게이트

확인일: 2026-09-04

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
- 기본 `PAYMENT_TRANSFER_METHOD=permit2`; 배포/실거래 성공 전 변경하지 않는다.

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

실패 시 Facilitator 응답과 원인을 기록하고 기존 Permit2 경로를 그대로 유지한다.
