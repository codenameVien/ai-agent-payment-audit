// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {AegisTokenOz} from "../src-oz/AegisTokenOz.sol";

/// @notice 결제 경로가 기대는 성질만 확인한다: 6자리 소수점, 서명 전송, 재사용 거부, 기한 만료.
contract AegisTokenOzTest is Test {
    bytes32 private constant TRANSFER_WITH_AUTHORIZATION_TYPEHASH = keccak256(
        "TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
    );

    AegisTokenOz private token;
    uint256 private payerKey = 0xA11CE;
    address private payer;
    address private payee = address(0xBEEF);

    function setUp() public {
        payer = vm.addr(payerKey);
        token = new AegisTokenOz(payer, 1_000_000 * 10 ** 6);
    }

    /// @dev OpenZeppelin 은 DOMAIN_SEPARATOR() 를 노출하지 않으므로 ERC-5267 값으로 직접 만든다.
    function _domainSeparator() private view returns (bytes32) {
        (, string memory name, string memory version, uint256 chainId, address verifyingContract,,) =
            token.eip712Domain();
        return keccak256(
            abi.encode(
                keccak256(
                    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
                ),
                keccak256(bytes(name)),
                keccak256(bytes(version)),
                chainId,
                verifyingContract
            )
        );
    }

    function _sign(uint256 value, uint256 validAfter, uint256 validBefore, bytes32 nonce)
        private
        view
        returns (uint8 v, bytes32 r, bytes32 s)
    {
        bytes32 structHash = keccak256(
            abi.encode(
                TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
                payer,
                payee,
                value,
                validAfter,
                validBefore,
                nonce
            )
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", _domainSeparator(), structHash));
        (v, r, s) = vm.sign(payerKey, digest);
    }

    function test_metadata() public view {
        assertEq(token.name(), "AEGIS");
        assertEq(token.symbol(), "AEGIS");
        assertEq(token.decimals(), 6);
        assertEq(token.totalSupply(), 1_000_000 * 10 ** 6);
        assertEq(token.balanceOf(payer), 1_000_000 * 10 ** 6);
    }

    function test_transferWithAuthorization() public {
        uint256 value = 12_960;
        bytes32 nonce = keccak256("nonce-1");
        (uint8 v, bytes32 r, bytes32 s) = _sign(value, 0, block.timestamp + 1 hours, nonce);

        // 서명자가 아닌 제3자가 제출해도 통과해야 한다. 가스를 대신 내는 쪽이 따로 있기 때문이다.
        vm.prank(address(0xCAFE));
        token.transferWithAuthorization(
            payer, payee, value, 0, block.timestamp + 1 hours, nonce, v, r, s
        );

        assertEq(token.balanceOf(payee), value);
    }

    function test_sameAuthorizationCannotBeReused() public {
        uint256 value = 500;
        bytes32 nonce = keccak256("nonce-2");
        uint256 validBefore = block.timestamp + 1 hours;
        (uint8 v, bytes32 r, bytes32 s) = _sign(value, 0, validBefore, nonce);

        token.transferWithAuthorization(payer, payee, value, 0, validBefore, nonce, v, r, s);

        vm.expectRevert();
        token.transferWithAuthorization(payer, payee, value, 0, validBefore, nonce, v, r, s);
    }

    function test_expiredAuthorizationIsRejected() public {
        vm.warp(1_000_000);
        uint256 value = 700;
        bytes32 nonce = keccak256("nonce-3");
        uint256 validBefore = block.timestamp - 1;
        (uint8 v, bytes32 r, bytes32 s) = _sign(value, 0, validBefore, nonce);

        vm.expectRevert();
        token.transferWithAuthorization(payer, payee, value, 0, validBefore, nonce, v, r, s);
    }
}
