// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {DemoToken} from "../src/DemoToken.sol";

contract TokenActor {
    function mint(DemoToken token, address to, uint256 amount) external {
        token.mint(to, amount);
    }

    function transferFrom(DemoToken token, address from, address to, uint256 amount) external {
        token.transferFrom(from, to, amount);
    }
}

contract DemoTokenTest {
    function testSixDecimalsAndOwnerDistribution() external {
        DemoToken token = new DemoToken(address(this), 1_000_000_000000);
        require(token.decimals() == 6, "wrong decimals");
        require(token.balanceOf(address(this)) == 1_000_000_000000, "wrong supply");
        token.mint(address(0xBEEF), 25_000000);
        require(token.balanceOf(address(0xBEEF)) == 25_000000, "mint failed");
    }

    function testAllowanceIsLimitedAndNonOwnerCannotMint() external {
        DemoToken token = new DemoToken(address(this), 10_000000);
        TokenActor actor = new TokenActor();
        token.approve(address(actor), 3_000000);
        actor.transferFrom(token, address(this), address(0xBEEF), 2_000000);
        require(token.allowance(address(this), address(actor)) == 1_000000, "allowance wrong");
        (bool ok,) =
            address(actor).call(abi.encodeCall(TokenActor.mint, (token, address(actor), 1_000000)));
        require(!ok, "non-owner minted");
    }
}
