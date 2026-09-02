// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice External, append-only checkpoint for MongoDB evidence heads.
contract EvidenceAnchor {
    struct Anchor {
        uint64 eventCount;
        bytes32 headHash;
        uint64 anchoredAt;
    }

    address public owner;
    mapping(address writer => bool enabled) public isWriter;
    mapping(bytes32 purchaseIdHash => Anchor anchor) public latest;

    event WriterUpdated(address indexed writer, bool enabled);
    event EvidenceAnchored(
        bytes32 indexed purchaseIdHash,
        uint64 eventCount,
        bytes32 indexed previousHeadHash,
        bytes32 indexed headHash
    );

    error Unauthorized();
    error ZeroAddress();
    error InvalidContinuity();
    error NonMonotonicCount();

    constructor(address initialOwner) {
        if (initialOwner == address(0)) revert ZeroAddress();
        owner = initialOwner;
        isWriter[initialOwner] = true;
        emit WriterUpdated(initialOwner, true);
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    function setWriter(address writer, bool enabled) external onlyOwner {
        if (writer == address(0)) revert ZeroAddress();
        isWriter[writer] = enabled;
        emit WriterUpdated(writer, enabled);
    }

    function anchor(
        bytes32 purchaseIdHash,
        uint64 eventCount,
        bytes32 previousHeadHash,
        bytes32 headHash
    ) external {
        if (!isWriter[msg.sender]) revert Unauthorized();
        Anchor memory previous = latest[purchaseIdHash];
        if (previous.headHash != previousHeadHash) revert InvalidContinuity();
        if (eventCount <= previous.eventCount) revert NonMonotonicCount();
        latest[purchaseIdHash] = Anchor(eventCount, headHash, uint64(block.timestamp));
        emit EvidenceAnchored(purchaseIdHash, eventCount, previousHeadHash, headHash);
    }
}
