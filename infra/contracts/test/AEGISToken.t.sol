// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AEGISToken} from "../src/AEGISToken.sol";

interface VmAegis {
    struct Log {
        bytes32[] topics;
        bytes data;
        address emitter;
    }

    function addr(uint256 privateKey) external returns (address);
    function sign(uint256 privateKey, bytes32 digest) external returns (uint8, bytes32, bytes32);
    function warp(uint256 timestamp) external;
    function chainId(uint256 newChainId) external;
    function prank(address sender) external;
    function recordLogs() external;
    function getRecordedLogs() external returns (Log[] memory);
}

/// @notice Offline Foundry tests for the prepared AEGIS token. No RPC, provider or secret access.
contract AEGISTokenTest {
    struct Auth {
        address from;
        address to;
        uint256 value;
        uint256 validAfter;
        uint256 validBefore;
        bytes32 nonce;
    }

    VmAegis private constant vm = VmAegis(address(uint160(uint256(keccak256("hevm cheat code")))));
    address private constant CONSOLE = 0x000000000000000000636F6e736F6c652e6c6f67;

    bytes32 private constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    bytes32 private constant TRANSFER_EVENT = keccak256("Transfer(address,address,uint256)");
    bytes32 private constant AUTHORIZATION_USED_EVENT =
        keccak256("AuthorizationUsed(address,bytes32)");

    uint256 private constant HOLDER_KEY = 0xA11CE;
    uint256 private constant OTHER_KEY = 0xB0B;
    address private constant SELLER = address(0xBEEF);
    uint256 private constant SUPPLY_UNITS = 10_000000;
    uint256 private constant AMOUNT_UNITS = 100000;
    uint256 private constant SECP256K1N =
        0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141;

    function _deploy(address holder, uint256 supplyUnits) private returns (AEGISToken) {
        return new AEGISToken(address(this), holder, supplyUnits);
    }

    function _domainSeparator(
        string memory tokenName,
        string memory tokenVersion,
        uint256 chain,
        address verifyingContract
    ) private pure returns (bytes32) {
        return keccak256(
            abi.encode(
                DOMAIN_TYPEHASH,
                keccak256(bytes(tokenName)),
                keccak256(bytes(tokenVersion)),
                chain,
                verifyingContract
            )
        );
    }

    function _digest(bytes32 domainSeparator, Auth memory auth) private pure returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                keccak256(
                    "TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
                ),
                auth.from,
                auth.to,
                auth.value,
                auth.validAfter,
                auth.validBefore,
                auth.nonce
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", domainSeparator, structHash));
    }

    function _sign(uint256 key, bytes32 domainSeparator, Auth memory auth)
        private
        returns (uint8 v, bytes32 r, bytes32 s)
    {
        return vm.sign(key, _digest(domainSeparator, auth));
    }

    function _submit(AEGISToken token, Auth memory auth, uint8 v, bytes32 r, bytes32 s)
        private
        returns (bool ok, bytes memory ret)
    {
        (ok, ret) = address(token).call(
            abi.encodeCall(
                token.transferWithAuthorization,
                (auth.from, auth.to, auth.value, auth.validAfter, auth.validBefore, auth.nonce, v, r, s)
            )
        );
    }

    function _rejected(bytes memory ret, bytes4 expected) private pure returns (bool) {
        return ret.length >= 4 && bytes4(ret) == expected;
    }

    function _logUint(string memory label, uint256 value) private view {
        (bool ok,) = CONSOLE.staticcall(abi.encodeWithSignature("log(string,uint256)", label, value));
        ok;
    }

    function testMetadataAndInitialSupply() external {
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);
        require(
            keccak256(bytes(token.name())) == keccak256("AEGIS")
                && keccak256(bytes(token.symbol())) == keccak256("AEGIS"),
            "name/symbol must be AEGIS"
        );
        require(token.decimals() == 6, "decimals must be 6");
        require(keccak256(bytes(token.version())) == keccak256("1"), "eip712 version must be 1");
        require(token.owner() == address(this), "owner mismatch");
        require(token.totalSupply() == SUPPLY_UNITS, "total supply mismatch");
        require(token.balanceOf(holder) == SUPPLY_UNITS, "initial holder balance mismatch");
        require(
            token.DOMAIN_SEPARATOR()
                == _domainSeparator("AEGIS", "1", block.chainid, address(token)),
            "domain separator mismatch"
        );
        require(
            token.TRANSFER_WITH_AUTHORIZATION_TYPEHASH()
                == keccak256(
                    "TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
                ),
            "typehash mismatch"
        );
    }

    function testConstructorRejectsZeroAddresses() external {
        bytes memory creation = abi.encodePacked(
            type(AEGISToken).creationCode, abi.encode(address(0), address(0xCAFE), SUPPLY_UNITS)
        );
        (bool zeroOwner,) = address(this).call(abi.encodeCall(this.deployRaw, (creation)));
        require(!zeroOwner, "zero owner accepted");
        creation = abi.encodePacked(
            type(AEGISToken).creationCode, abi.encode(address(0xCAFE), address(0), SUPPLY_UNITS)
        );
        (bool zeroHolder,) = address(this).call(abi.encodeCall(this.deployRaw, (creation)));
        require(!zeroHolder, "zero holder accepted");
    }

    /// @dev External helper so a failing constructor can be observed through a low-level call.
    function deployRaw(bytes memory creation) external returns (address deployed) {
        assembly {
            deployed := create(0, add(creation, 0x20), mload(creation))
        }
        require(deployed != address(0), "create failed");
    }

    function testValidAuthorizationEmitsExactEventsAndConsumesNonce() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);
        Auth memory auth = Auth(holder, SELLER, AMOUNT_UNITS, 0, 1_100, keccak256("aegis-valid"));
        (uint8 v, bytes32 r, bytes32 s) = _sign(HOLDER_KEY, token.DOMAIN_SEPARATOR(), auth);

        vm.recordLogs();
        token.transferWithAuthorization(
            auth.from, auth.to, auth.value, auth.validAfter, auth.validBefore, auth.nonce, v, r, s
        );
        VmAegis.Log[] memory logs = vm.getRecordedLogs();

        require(logs.length == 2, "expected exactly two events");
        require(logs[0].emitter == address(token) && logs[1].emitter == address(token), "emitter");
        require(logs[0].topics.length == 3, "authorization topic count");
        require(logs[0].topics[0] == AUTHORIZATION_USED_EVENT, "authorization topic0");
        require(logs[0].topics[1] == bytes32(uint256(uint160(holder))), "authorizer topic");
        require(logs[0].topics[2] == auth.nonce, "nonce topic");
        require(logs[0].data.length == 0, "authorization data");
        require(logs[1].topics.length == 3, "transfer topic count");
        require(logs[1].topics[0] == TRANSFER_EVENT, "transfer topic0");
        require(logs[1].topics[1] == bytes32(uint256(uint160(holder))), "transfer from topic");
        require(logs[1].topics[2] == bytes32(uint256(uint160(SELLER))), "transfer to topic");
        require(
            keccak256(logs[1].data) == keccak256(abi.encode(AMOUNT_UNITS)), "transfer value data"
        );
        require(token.balanceOf(SELLER) == AMOUNT_UNITS, "exact transfer missing");
        require(token.balanceOf(holder) == SUPPLY_UNITS - AMOUNT_UNITS, "holder debit mismatch");
        require(token.authorizationState(holder, auth.nonce), "nonce not consumed");
        require(token.totalSupply() == SUPPLY_UNITS, "supply changed");
    }

    function testWrongSignerRejected() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);
        Auth memory auth = Auth(holder, SELLER, AMOUNT_UNITS, 0, 1_100, keccak256("wrong-signer"));
        (uint8 v, bytes32 r, bytes32 s) = _sign(OTHER_KEY, token.DOMAIN_SEPARATOR(), auth);
        (bool ok, bytes memory ret) = _submit(token, auth, v, r, s);
        require(!ok, "wrong signer accepted");
        require(_rejected(ret, AEGISToken.InvalidAuthorization.selector), "wrong signer error");
        require(!token.authorizationState(holder, auth.nonce), "failed nonce consumed");
        require(token.balanceOf(SELLER) == 0, "funds moved");
    }

    function testWrongDomainNameOrVersionRejected() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);
        Auth memory auth = Auth(holder, SELLER, AMOUNT_UNITS, 0, 1_100, keccak256("wrong-name"));
        (uint8 v, bytes32 r, bytes32 s) = _sign(
            HOLDER_KEY,
            _domainSeparator("PBL Agent Credit", "1", block.chainid, address(token)),
            auth
        );
        (bool nameOk, bytes memory nameRet) = _submit(token, auth, v, r, s);
        require(!nameOk, "foreign domain name accepted");
        require(_rejected(nameRet, AEGISToken.InvalidAuthorization.selector), "domain name error");

        auth.nonce = keccak256("wrong-version");
        (v, r, s) =
            _sign(HOLDER_KEY, _domainSeparator("AEGIS", "2", block.chainid, address(token)), auth);
        (bool versionOk, bytes memory versionRet) = _submit(token, auth, v, r, s);
        require(!versionOk, "foreign domain version accepted");
        require(_rejected(versionRet, AEGISToken.InvalidAuthorization.selector), "version error");
        require(token.balanceOf(SELLER) == 0, "funds moved");
    }

    function testWrongTokenAddressRejected() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);
        AEGISToken other = _deploy(holder, SUPPLY_UNITS);
        require(address(token) != address(other), "expected two deployments");
        Auth memory auth = Auth(holder, SELLER, AMOUNT_UNITS, 0, 1_100, keccak256("wrong-token"));
        (uint8 v, bytes32 r, bytes32 s) = _sign(HOLDER_KEY, other.DOMAIN_SEPARATOR(), auth);
        (bool ok, bytes memory ret) = _submit(token, auth, v, r, s);
        require(!ok, "foreign verifying contract accepted");
        require(_rejected(ret, AEGISToken.InvalidAuthorization.selector), "wrong token error");
        require(!token.authorizationState(holder, auth.nonce), "failed nonce consumed");
    }

    function testWrongChainIdRejected() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);
        bytes32 cachedSeparator = token.DOMAIN_SEPARATOR();
        Auth memory auth = Auth(holder, SELLER, AMOUNT_UNITS, 0, 1_100, keccak256("wrong-chain"));
        (uint8 v, bytes32 r, bytes32 s) = _sign(HOLDER_KEY, cachedSeparator, auth);

        vm.chainId(84532);
        require(token.DOMAIN_SEPARATOR() != cachedSeparator, "separator must follow chain id");
        require(
            token.DOMAIN_SEPARATOR() == _domainSeparator("AEGIS", "1", 84532, address(token)),
            "rebuilt separator mismatch"
        );
        (bool ok, bytes memory ret) = _submit(token, auth, v, r, s);
        require(!ok, "foreign chain id accepted");
        require(_rejected(ret, AEGISToken.InvalidAuthorization.selector), "wrong chain error");
        require(token.balanceOf(SELLER) == 0, "funds moved");
    }

    function testTimeWindowRejectedAtBoundaries() external {
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);
        Auth memory auth = Auth(holder, SELLER, AMOUNT_UNITS, 1_000, 2_000, keccak256("boundary"));
        (uint8 v, bytes32 r, bytes32 s) = _sign(HOLDER_KEY, token.DOMAIN_SEPARATOR(), auth);

        vm.warp(auth.validAfter);
        (bool atStart, bytes memory startRet) = _submit(token, auth, v, r, s);
        require(!atStart, "validAfter boundary accepted");
        require(
            _rejected(startRet, AEGISToken.AuthorizationNotYetValid.selector), "not-yet-valid error"
        );

        vm.warp(auth.validBefore);
        (bool atEnd, bytes memory endRet) = _submit(token, auth, v, r, s);
        require(!atEnd, "validBefore boundary accepted");
        require(_rejected(endRet, AEGISToken.AuthorizationExpired.selector), "expired error");
        require(token.balanceOf(SELLER) == 0, "funds moved outside the window");
        require(!token.authorizationState(holder, auth.nonce), "boundary nonce consumed");
    }

    function testTimeWindowAcceptedAtFirstAndLastValidSecond() external {
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);
        Auth memory auth = Auth(holder, SELLER, AMOUNT_UNITS, 1_000, 2_000, keccak256("first-tick"));
        (uint8 v, bytes32 r, bytes32 s) = _sign(HOLDER_KEY, token.DOMAIN_SEPARATOR(), auth);
        vm.warp(auth.validAfter + 1);
        (bool firstSecond,) = _submit(token, auth, v, r, s);
        require(firstSecond, "first valid second rejected");

        auth.nonce = keccak256("last-tick");
        (v, r, s) = _sign(HOLDER_KEY, token.DOMAIN_SEPARATOR(), auth);
        vm.warp(auth.validBefore - 1);
        (bool lastSecond,) = _submit(token, auth, v, r, s);
        require(lastSecond, "last valid second rejected");
        require(token.balanceOf(SELLER) == 2 * AMOUNT_UNITS, "window transfers missing");
    }

    function testNonceReplayRejected() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);
        Auth memory auth = Auth(holder, SELLER, AMOUNT_UNITS, 0, 1_100, keccak256("replay"));
        (uint8 v, bytes32 r, bytes32 s) = _sign(HOLDER_KEY, token.DOMAIN_SEPARATOR(), auth);
        (bool first,) = _submit(token, auth, v, r, s);
        require(first, "first authorization rejected");
        (bool second, bytes memory ret) = _submit(token, auth, v, r, s);
        require(!second, "replay accepted");
        require(
            _rejected(ret, AEGISToken.AuthorizationAlreadyUsed.selector), "replay error selector"
        );
        require(token.balanceOf(SELLER) == AMOUNT_UNITS, "replay moved extra funds");
    }

    function testInsufficientBalanceRollsBackWithoutConsumingNonce() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, AMOUNT_UNITS);
        Auth memory auth =
            Auth(holder, SELLER, AMOUNT_UNITS + 1, 0, 1_100, keccak256("insufficient"));
        (uint8 v, bytes32 r, bytes32 s) = _sign(HOLDER_KEY, token.DOMAIN_SEPARATOR(), auth);

        // Rollback evidence is storage state: the Foundry log recorder keeps entries emitted inside
        // reverted frames, while a real chain discards them, so log counts prove nothing here.
        (bool ok, bytes memory ret) = _submit(token, auth, v, r, s);
        require(!ok, "overspend accepted");
        require(_rejected(ret, AEGISToken.InsufficientBalance.selector), "insufficient error");
        require(!token.authorizationState(holder, auth.nonce), "reverted nonce consumed");
        require(token.balanceOf(holder) == AMOUNT_UNITS, "holder balance changed");
        require(token.balanceOf(SELLER) == 0, "seller balance changed");
        require(token.totalSupply() == AMOUNT_UNITS, "supply changed");
    }

    function testMalleableSignatureRejected() external {
        vm.warp(1_000);
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);
        Auth memory auth = Auth(holder, SELLER, AMOUNT_UNITS, 0, 1_100, keccak256("malleable"));
        (uint8 v, bytes32 r, bytes32 s) = _sign(HOLDER_KEY, token.DOMAIN_SEPARATOR(), auth);

        uint8 flippedV = v == 27 ? 28 : 27;
        bytes32 flippedS = bytes32(SECP256K1N - uint256(s));
        (bool ok, bytes memory ret) = _submit(token, auth, flippedV, r, flippedS);
        require(!ok, "malleable signature accepted");
        require(_rejected(ret, AEGISToken.InvalidAuthorization.selector), "malleability error");

        (bool badV, bytes memory badVRet) = _submit(token, auth, 29, r, s);
        require(!badV, "invalid v accepted");
        require(_rejected(badVRet, AEGISToken.InvalidAuthorization.selector), "invalid v error");
        require(!token.authorizationState(holder, auth.nonce), "failed nonce consumed");
    }

    function testMintPolicyAndOwnershipTransfer() external {
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(holder, SUPPLY_UNITS);

        vm.recordLogs();
        token.mint(holder, AMOUNT_UNITS);
        VmAegis.Log[] memory logs = vm.getRecordedLogs();
        require(logs.length == 1 && logs[0].topics[0] == TRANSFER_EVENT, "mint event");
        require(logs[0].topics[1] == bytes32(0), "mint from must be zero address");
        require(logs[0].topics[2] == bytes32(uint256(uint160(holder))), "mint to topic");
        require(token.totalSupply() == SUPPLY_UNITS + AMOUNT_UNITS, "mint supply mismatch");

        vm.prank(holder);
        (bool stranger, bytes memory strangerRet) =
            address(token).call(abi.encodeCall(token.mint, (holder, AMOUNT_UNITS)));
        require(!stranger, "non-owner mint accepted");
        require(_rejected(strangerRet, AEGISToken.Unauthorized.selector), "mint auth error");

        vm.prank(holder);
        (bool strangerOwner,) =
            address(token).call(abi.encodeCall(token.transferOwnership, (holder)));
        require(!strangerOwner, "non-owner ownership transfer accepted");

        (bool zeroOwner, bytes memory zeroRet) =
            address(token).call(abi.encodeCall(token.transferOwnership, (address(0))));
        require(!zeroOwner, "zero owner accepted");
        require(_rejected(zeroRet, AEGISToken.ZeroAddress.selector), "zero owner error");

        token.transferOwnership(holder);
        require(token.owner() == holder, "ownership not transferred");
        (bool formerOwner,) = address(token).call(abi.encodeCall(token.mint, (holder, 1)));
        require(!formerOwner, "former owner still mints");
    }

    function testErc20TransferAndAllowancePaths() external {
        address holder = vm.addr(HOLDER_KEY);
        AEGISToken token = _deploy(address(this), SUPPLY_UNITS);
        require(token.transfer(holder, AMOUNT_UNITS), "transfer failed");
        require(token.balanceOf(holder) == AMOUNT_UNITS, "transfer amount mismatch");

        vm.prank(holder);
        (bool approved,) =
            address(token).call(abi.encodeCall(token.approve, (address(this), AMOUNT_UNITS)));
        require(approved, "approve failed");
        require(token.allowance(holder, address(this)) == AMOUNT_UNITS, "allowance mismatch");
        require(token.transferFrom(holder, SELLER, AMOUNT_UNITS - 1), "transferFrom failed");
        require(token.allowance(holder, address(this)) == 1, "allowance not decremented");

        (bool overspend, bytes memory overspendRet) =
            address(token).call(abi.encodeCall(token.transferFrom, (holder, SELLER, AMOUNT_UNITS)));
        require(!overspend, "allowance overspend accepted");
        require(
            _rejected(overspendRet, AEGISToken.InsufficientAllowance.selector), "allowance error"
        );

        (bool zeroTo, bytes memory zeroToRet) =
            address(token).call(abi.encodeCall(token.transfer, (address(0), 1)));
        require(!zeroTo, "transfer to zero accepted");
        require(_rejected(zeroToRet, AEGISToken.ZeroAddress.selector), "zero recipient error");
    }

    /// @dev Local creation-gas measurement for the plan-only deployment packet. Logged with -vv.
    ///      Foundry-local number; it is not a network gas quote.
    function testDeploymentCreationGasMeasuredLocally() external {
        address holder = vm.addr(HOLDER_KEY);
        uint256 before = gasleft();
        AEGISToken token = new AEGISToken(address(this), holder, 1_000_000 * 1_000000);
        uint256 used = before - gasleft();
        _logUint("aegis_creation_gas_local", used);
        _logUint("aegis_creation_code_bytes", type(AEGISToken).creationCode.length);
        require(address(token) != address(0), "deployment failed");
        require(used < 1_500_000, "creation gas regressed beyond plan budget");
    }
}
