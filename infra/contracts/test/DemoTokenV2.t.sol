// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {DemoTokenV2} from "../src/DemoTokenV2.sol";

interface VmV2 {
    function addr(uint256 privateKey) external returns (address);
    function sign(uint256 privateKey, bytes32 digest) external returns (uint8, bytes32, bytes32);
    function warp(uint256 timestamp) external;
}

contract DemoTokenV2Test {
    VmV2 private constant vm = VmV2(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 private constant HOLDER_KEY = 0xA11CE;

    function _signed(
        DemoTokenV2 token,
        uint256 key,
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce
    ) private returns (uint8 v, bytes32 r, bytes32 s) {
        bytes32 structHash = keccak256(
            abi.encode(
                token.TRANSFER_WITH_AUTHORIZATION_TYPEHASH(), from, to, value,
                validAfter, validBefore, nonce
            )
        );
        bytes32 digest = keccak256(
            abi.encodePacked("\x19\x01", token.DOMAIN_SEPARATOR(), structHash)
        );
        return vm.sign(key, digest);
    }

    function _call(
        DemoTokenV2 token,
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) private returns (bool ok) {
        (ok,) = address(token).call(abi.encodeCall(
            token.transferWithAuthorization,
            (from, to, value, validAfter, validBefore, nonce, v, r, s)
        ));
    }

    function testValidAuthorizationTransfersAndConsumesNonce() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        address seller = address(0xBEEF);
        DemoTokenV2 token = new DemoTokenV2(address(this), holder, 10_000000);
        bytes32 nonce = keccak256("random-one");
        (uint8 v, bytes32 r, bytes32 s) = _signed(
            token, HOLDER_KEY, holder, seller, 100000, 999, 1_100, nonce
        );
        token.transferWithAuthorization(holder, seller, 100000, 999, 1_100, nonce, v, r, s);
        require(token.balanceOf(seller) == 100000, "exact transfer missing");
        require(token.authorizationState(holder, nonce), "nonce not consumed");
    }

    function testWrongSignerFails() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        DemoTokenV2 token = new DemoTokenV2(address(this), holder, 10_000000);
        bytes32 nonce = keccak256("wrong-signer");
        (uint8 v, bytes32 r, bytes32 s) = _signed(
            token, 0xB0B, holder, address(0xBEEF), 100000, 999, 1_100, nonce
        );
        require(!_call(token, holder, address(0xBEEF), 100000, 999, 1_100, nonce, v, r, s), "wrong signer accepted");
        require(!token.authorizationState(holder, nonce), "failed nonce consumed");
    }

    function testExpiredAndNotYetValidFail() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        DemoTokenV2 token = new DemoTokenV2(address(this), holder, 10_000000);
        bytes32 expiredNonce = keccak256("expired");
        (uint8 ev, bytes32 er, bytes32 es) = _signed(
            token, HOLDER_KEY, holder, address(0xBEEF), 100000, 900, 1_000, expiredNonce
        );
        require(!_call(token, holder, address(0xBEEF), 100000, 900, 1_000, expiredNonce, ev, er, es), "expired accepted");
        bytes32 futureNonce = keccak256("future");
        (uint8 fv, bytes32 fr, bytes32 fs) = _signed(
            token, HOLDER_KEY, holder, address(0xBEEF), 100000, 1_000, 1_100, futureNonce
        );
        require(!_call(token, holder, address(0xBEEF), 100000, 1_000, 1_100, futureNonce, fv, fr, fs), "future accepted");
    }

    function testNonceReplayFails() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        DemoTokenV2 token = new DemoTokenV2(address(this), holder, 10_000000);
        bytes32 nonce = keccak256("replay");
        (uint8 v, bytes32 r, bytes32 s) = _signed(
            token, HOLDER_KEY, holder, address(0xBEEF), 100000, 999, 1_100, nonce
        );
        token.transferWithAuthorization(holder, address(0xBEEF), 100000, 999, 1_100, nonce, v, r, s);
        require(!_call(token, holder, address(0xBEEF), 100000, 999, 1_100, nonce, v, r, s), "replay accepted");
    }

    function testInsufficientBalanceFailsWithoutConsumingNonce() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        DemoTokenV2 token = new DemoTokenV2(address(this), holder, 100000);
        bytes32 nonce = keccak256("insufficient");
        (uint8 v, bytes32 r, bytes32 s) = _signed(
            token, HOLDER_KEY, holder, address(0xBEEF), 200000, 999, 1_100, nonce
        );
        require(!_call(token, holder, address(0xBEEF), 200000, 999, 1_100, nonce, v, r, s), "overspend accepted");
        require(!token.authorizationState(holder, nonce), "reverted nonce consumed");
    }
}
