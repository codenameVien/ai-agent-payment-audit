// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Six-decimal AEGIS test token exposing the EIP-3009 exact-transfer primitive used by x402 v2.
/// @dev New, non-upgradeable deployment prepared for the AEGIS AA three-factor flow.
///      `name`, `symbol` and `version` are compile-time constants and there is no upgrade or setter path,
///      so the already deployed PBLC V2 token cannot be renamed; a separate deployment is required.
///      PBLC V1/V2 sources, addresses and historical transactions stay untouched.
///      The authorization logic mirrors the PBLC V2 implementation that was proven on Base Sepolia
///      (docs/ERC3009_DEPLOYMENT_GATE.md) so the settlement path keeps identical safeguards.
contract AEGISToken {
    string public constant name = "AEGIS";
    string public constant symbol = "AEGIS";
    /// @dev Explicit EIP-712 domain version for this first AEGIS deployment.
    string public constant version = "1";
    uint8 public constant decimals = 6;

    bytes32 public constant TRANSFER_WITH_AUTHORIZATION_TYPEHASH = keccak256(
        "TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
    );
    bytes32 private constant _DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    uint256 private constant _SECP256K1N_DIV_2 =
        0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;

    uint256 public totalSupply;
    address public owner;
    mapping(address account => uint256 amount) public balanceOf;
    mapping(address account => mapping(address spender => uint256 amount)) public allowance;
    mapping(address authorizer => mapping(bytes32 nonce => bool used)) private _authorizationStates;

    uint256 private immutable _initialChainId;
    bytes32 private immutable _initialDomainSeparator;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event AuthorizationUsed(address indexed authorizer, bytes32 indexed nonce);
    event OwnershipTransferred(address indexed previousOwner, address indexed nextOwner);

    error Unauthorized();
    error ZeroAddress();
    error InsufficientBalance();
    error InsufficientAllowance();
    error AuthorizationNotYetValid();
    error AuthorizationExpired();
    error AuthorizationAlreadyUsed();
    error InvalidAuthorization();

    constructor(address initialOwner, address initialHolder, uint256 initialSupplyUnits) {
        if (initialOwner == address(0) || initialHolder == address(0)) revert ZeroAddress();
        owner = initialOwner;
        _initialChainId = block.chainid;
        _initialDomainSeparator = _buildDomainSeparator();
        emit OwnershipTransferred(address(0), initialOwner);
        _mint(initialHolder, initialSupplyUnits);
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _transfer(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        if (spender == address(0)) revert ZeroAddress();
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 available = allowance[from][msg.sender];
        if (available < value) revert InsufficientAllowance();
        if (available != type(uint256).max) {
            unchecked { allowance[from][msg.sender] = available - value; }
            emit Approval(from, msg.sender, allowance[from][msg.sender]);
        }
        _transfer(from, to, value);
        return true;
    }

    function authorizationState(address authorizer, bytes32 nonce) external view returns (bool) {
        return _authorizationStates[authorizer][nonce];
    }

    function DOMAIN_SEPARATOR() public view returns (bytes32) {
        return block.chainid == _initialChainId ? _initialDomainSeparator : _buildDomainSeparator();
    }

    /// @notice Executes an EIP-3009 authorization. The submitter may be any account; only the
    ///         authorizer's signature over this token's domain moves funds.
    function transferWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external {
        if (block.timestamp <= validAfter) revert AuthorizationNotYetValid();
        if (block.timestamp >= validBefore) revert AuthorizationExpired();
        if (_authorizationStates[from][nonce]) revert AuthorizationAlreadyUsed();
        if (from == address(0) || to == address(0)) revert ZeroAddress();
        if ((v != 27 && v != 28) || uint256(s) > _SECP256K1N_DIV_2) {
            revert InvalidAuthorization();
        }
        bytes32 structHash = keccak256(
            abi.encode(
                TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
                from,
                to,
                value,
                validAfter,
                validBefore,
                nonce
            )
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR(), structHash));
        if (ecrecover(digest, v, r, s) != from) revert InvalidAuthorization();

        _authorizationStates[from][nonce] = true;
        emit AuthorizationUsed(from, nonce);
        _transfer(from, to, value);
    }

    function mint(address to, uint256 value) external onlyOwner { _mint(to, value); }

    function transferOwnership(address nextOwner) external onlyOwner {
        if (nextOwner == address(0)) revert ZeroAddress();
        address previous = owner;
        owner = nextOwner;
        emit OwnershipTransferred(previous, nextOwner);
    }

    function _mint(address to, uint256 value) private {
        if (to == address(0)) revert ZeroAddress();
        totalSupply += value;
        balanceOf[to] += value;
        emit Transfer(address(0), to, value);
    }

    function _transfer(address from, address to, uint256 value) private {
        if (to == address(0)) revert ZeroAddress();
        uint256 available = balanceOf[from];
        if (available < value) revert InsufficientBalance();
        unchecked { balanceOf[from] = available - value; balanceOf[to] += value; }
        emit Transfer(from, to, value);
    }

    function _buildDomainSeparator() private view returns (bytes32) {
        return keccak256(
            abi.encode(
                _DOMAIN_TYPEHASH,
                keccak256(bytes(name)),
                keccak256(bytes(version)),
                block.chainid,
                address(this)
            )
        );
    }
}
