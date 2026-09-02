// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {DemoToken} from "../src/DemoToken.sol";

interface Vm {
    function addr(uint256 privateKey) external returns (address);
    function sign(uint256 privateKey, bytes32 digest)
        external
        returns (uint8 v, bytes32 r, bytes32 s);
}

contract TokenActor {
    function mint(DemoToken token, address to, uint256 amount) external {
        token.mint(to, amount);
    }

    function transferFrom(DemoToken token, address from, address to, uint256 amount) external {
        token.transferFrom(from, to, amount);
    }
}

contract DemoTokenTest {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    function testSixDecimalsAndOwnerDistribution() external {
        address initialHolder = address(0xBEEF);
        DemoToken token = new DemoToken(address(this), initialHolder, 1_000_000_000000);
        require(token.decimals() == 6, "wrong decimals");
        require(token.owner() == address(this), "wrong owner");
        require(token.balanceOf(initialHolder) == 1_000_000_000000, "wrong supply holder");
        require(token.balanceOf(address(this)) == 0, "deployer received buyer supply");
        token.mint(initialHolder, 25_000000);
        require(token.balanceOf(initialHolder) == 1_000_025_000000, "mint failed");
    }

    function testAllowanceIsLimitedAndNonOwnerCannotMint() external {
        DemoToken token = new DemoToken(address(this), address(this), 10_000000);
        TokenActor actor = new TokenActor();
        token.approve(address(actor), 3_000000);
        actor.transferFrom(token, address(this), address(0xBEEF), 2_000000);
        require(token.allowance(address(this), address(actor)) == 1_000000, "allowance wrong");
        (bool ok,) =
            address(actor).call(abi.encodeCall(TokenActor.mint, (token, address(actor), 1_000000)));
        require(!ok, "non-owner minted");
    }

    function testPermitSetsExactAllowanceAndConsumesNonce() external {
        uint256 holderKey = 0xA11CE;
        address holder = vm.addr(holderKey);
        DemoToken token = new DemoToken(address(this), holder, 10_000000);
        TokenActor spender = new TokenActor();
        uint256 value = 1_000000;
        uint256 deadline = block.timestamp + 1 hours;
        bytes32 structHash = keccak256(
            abi.encode(
                token.PERMIT_TYPEHASH(),
                holder,
                address(spender),
                value,
                token.nonces(holder),
                deadline
            )
        );
        bytes32 digest = keccak256(
            abi.encodePacked("\x19\x01", token.DOMAIN_SEPARATOR(), structHash)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(holderKey, digest);

        token.permit(holder, address(spender), value, deadline, v, r, s);
        require(token.nonces(holder) == 1, "nonce not consumed");
        require(token.allowance(holder, address(spender)) == value, "permit allowance wrong");
        (bool replayed,) = address(token).call(
            abi.encodeCall(
                token.permit,
                (holder, address(spender), value, deadline, v, r, s)
            )
        );
        require(!replayed, "permit replay accepted");
        require(token.nonces(holder) == 1, "replay changed nonce");
        spender.transferFrom(token, holder, address(0xBEEF), value);
        require(token.allowance(holder, address(spender)) == 0, "allowance remained");
    }

    function testExpiredAndWrongSignerPermitsFailClosed() external {
        uint256 holderKey = 0xA11CE;
        address holder = vm.addr(holderKey);
        DemoToken token = new DemoToken(address(this), holder, 10_000000);
        TokenActor spender = new TokenActor();

        (bool expired,) = address(token).call(
            abi.encodeCall(
                token.permit,
                (holder, address(spender), 1_000000, 0, 27, bytes32(0), bytes32(0))
            )
        );
        require(!expired, "expired permit accepted");

        uint256 deadline = block.timestamp + 1 hours;
        bytes32 structHash = keccak256(
            abi.encode(
                token.PERMIT_TYPEHASH(),
                holder,
                address(spender),
                1_000000,
                token.nonces(holder),
                deadline
            )
        );
        bytes32 digest = keccak256(
            abi.encodePacked("\x19\x01", token.DOMAIN_SEPARATOR(), structHash)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(0xB0B, digest);
        (bool wrongSigner,) = address(token).call(
            abi.encodeCall(
                token.permit,
                (holder, address(spender), 1_000000, deadline, v, r, s)
            )
        );
        require(!wrongSigner, "wrong signer accepted");
        require(token.nonces(holder) == 0, "failed permit consumed nonce");
    }
}
