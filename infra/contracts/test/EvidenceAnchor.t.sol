// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {EvidenceAnchor} from "../src/EvidenceAnchor.sol";

contract AnchorActor {
    function anchor(
        EvidenceAnchor target,
        bytes32 purchase,
        uint64 count,
        bytes32 previous,
        bytes32 head
    ) external {
        target.anchor(purchase, count, previous, head);
    }
}

contract EvidenceAnchorTest {
    function testMonotonicContinuity() external {
        EvidenceAnchor target = new EvidenceAnchor(address(this));
        bytes32 purchase = keccak256("purchase-1");
        bytes32 first = keccak256("head-1");
        bytes32 second = keccak256("head-2");
        target.anchor(purchase, 3, bytes32(0), first);
        target.anchor(purchase, 7, first, second);
        (uint64 count, bytes32 head,) = target.latest(purchase);
        require(count == 7 && head == second, "anchor mismatch");

        (bool badContinuity,) = address(target)
            .call(abi.encodeCall(target.anchor, (purchase, 8, first, keccak256("bad"))));
        require(!badContinuity, "accepted wrong previous head");
        (bool badCount,) = address(target)
            .call(abi.encodeCall(target.anchor, (purchase, 7, second, keccak256("bad-count"))));
        require(!badCount, "accepted non-monotonic count");
    }

    function testOnlyWriterCanAnchor() external {
        EvidenceAnchor target = new EvidenceAnchor(address(this));
        AnchorActor actor = new AnchorActor();
        (bool rejected,) = address(actor)
            .call(
                abi.encodeCall(
                    AnchorActor.anchor,
                    (target, keccak256("purchase"), 1, bytes32(0), keccak256("head"))
                )
            );
        require(!rejected, "unauthorized writer anchored");
        target.setWriter(address(actor), true);
        actor.anchor(target, keccak256("purchase"), 1, bytes32(0), keccak256("head"));
    }
}
