# 사용자 소유 PBLC 결제 준비

상태: **준비만 완료. 배포·민팅·자산 이동·실제 Facilitator 호출은 미실행.**

## 왜 새 PBLC 주소가 필요한가

기존 PBLC V2(`0xDed7F4992D98eF31453dCebbB8c2A6b50d0284B3`)의 `owner`는
`0x5B2BC76a3e4DeA700309FD9D746180162bcAbec8`이다. 이 주소는 사용자 MetaMask
계정이 아니므로, 그 계약에는 mint 권한이 없다. 이 권한을 우회하거나 타인 지갑을 사용하지
않는다. 과거 PBLC V2 거래·감사 증거는 수정하지 않는다.

실제 전환 시에는 같은 ERC-20 + ERC-3009 구현을 **새 주소에 독립 배포**하고, 사용자 지갑
`0x043D966B3f30Ff9FAC08FD6b5eFeDa6ac895a0a3`를 `deployer = owner = initial holder`
로 둘 수 있다. 이 지갑은 구매자/결제 승인자이며, OpenAI·Anthropic·Google 수신 지갑과는
다르다. 새 계약도 name `PBL Agent Credit`, symbol `PBLC`, 6 decimals, EIP-712 version `2`를
쓴다. 따라서 **새 계약 주소가 확정되기 전에는** 기존 PBLC V2를 새 자산인 것처럼 표시하지 않는다.

## plan-only 명령

```bash
npm run pblc:user-token:plan -- \
  --deployer 0x043D966B3f30Ff9FAC08FD6b5eFeDa6ac895a0a3 \
  --holder 0x043D966B3f30Ff9FAC08FD6b5eFeDa6ac895a0a3 \
  --nonce <Base-Sepolia-pending-nonce> \
  --initial-supply-units 1000000000000 \
  --chain-id 84532
```

명령은 공개 주소·nonce·공급량만으로 예상 CREATE 주소와 calldata를 계산한다. 개인키, `.env`,
RPC, 서명, 브로드캐스트를 읽거나 사용하지 않는다. `1,000,000 PBLC`는 6-decimal raw units
`1000000000000`이다.

## 배포 승인 전에 다시 제시할 값

1. deployer/owner/initial holder 공개 주소와 새 contract 예상 주소
2. 실행 직전 pending nonce와 live `eth_estimateGas`·max fee 기반 최대 가스비
3. 초기 공급량과 첫 결제 상한
4. 실제 x402 Facilitator의 custom PBLC `exact + eip3009` verify/settle 수락 계획

위 네 값을 제시한 뒤에만 사용자가 별도로 승인할 수 있다. 이 문서는 승인 자체가 아니다.
