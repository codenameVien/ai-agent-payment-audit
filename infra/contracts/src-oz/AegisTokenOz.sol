// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC3009} from "@openzeppelin/contracts/token/ERC20/extensions/draft-ERC3009.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";

/// @notice AEGIS 테스트 토큰. 소수점 6자리 ERC-20 에 ERC-3009(서명 전송)를 얹는다.
/// @dev 직접 구현하던 것을 OpenZeppelin 5.7 의 감사받은 구현으로 바꾼 두 번째 배포다.
///      이름·심볼·EIP-712 버전은 첫 배포와 같게 두어 x402 결제 조건(extra.name, extra.version)을
///      그대로 쓸 수 있게 한다. 주소만 달라진다.
///      업그레이드 경로와 setter 가 없어 배포 후에는 어떤 값도 바꿀 수 없다.
contract AegisTokenOz is ERC3009 {
    uint8 private constant TOKEN_DECIMALS = 6;

    /// @param initialHolder 발행분을 전부 받는 주소
    /// @param initialSupply 최소 단위 기준 발행량 (1 AEGIS = 1_000_000)
    constructor(address initialHolder, uint256 initialSupply)
        ERC20("AEGIS", "AEGIS")
        EIP712("AEGIS", "1")
    {
        _mint(initialHolder, initialSupply);
    }

    /// @dev 기존 배포와 같은 6자리를 유지한다. OpenZeppelin 기본값은 18 이다.
    function decimals() public pure override returns (uint8) {
        return TOKEN_DECIMALS;
    }
}
