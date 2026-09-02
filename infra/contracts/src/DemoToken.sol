// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Six-decimal demo settlement token for the PBL Base Sepolia environment.
/// @dev The owner-controlled mint is deliberate for test distribution. Production assets must
///      replace this contract and its policy before any mainnet use.
contract DemoToken {
    string public constant name = "PBL Agent Credit";
    string public constant symbol = "PBLC";
    string public constant version = "1";
    uint8 public constant decimals = 6;

    bytes32 public constant PERMIT_TYPEHASH =
        keccak256("Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)");
    bytes32 private constant _DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");
    uint256 private constant _SECP256K1N_DIV_2 =
        0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;

    uint256 public totalSupply;
    address public owner;
    mapping(address account => uint256 amount) public balanceOf;
    mapping(address account => mapping(address spender => uint256 amount)) public allowance;
    mapping(address account => uint256 nonce) public nonces;

    uint256 private immutable _initialChainId;
    bytes32 private immutable _initialDomainSeparator;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event OwnershipTransferred(address indexed previousOwner, address indexed nextOwner);

    error Unauthorized();
    error ZeroAddress();
    error InsufficientBalance();
    error InsufficientAllowance();
    error PermitExpired();
    error InvalidPermit();

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

    function DOMAIN_SEPARATOR() public view returns (bytes32) {
        return block.chainid == _initialChainId
            ? _initialDomainSeparator
            : _buildDomainSeparator();
    }

    function permit(
        address tokenOwner,
        address spender,
        uint256 value,
        uint256 deadline,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external {
        if (block.timestamp > deadline) revert PermitExpired();
        if (tokenOwner == address(0) || spender == address(0)) revert ZeroAddress();
        if (v != 27 && v != 28) revert InvalidPermit();
        if (uint256(s) > _SECP256K1N_DIV_2) revert InvalidPermit();

        uint256 nonce = nonces[tokenOwner]++;
        bytes32 structHash = keccak256(
            abi.encode(PERMIT_TYPEHASH, tokenOwner, spender, value, nonce, deadline)
        );
        bytes32 digest = keccak256(
            abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR(), structHash)
        );
        if (ecrecover(digest, v, r, s) != tokenOwner) revert InvalidPermit();

        allowance[tokenOwner][spender] = value;
        emit Approval(tokenOwner, spender, value);
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 available = allowance[from][msg.sender];
        if (available < value) revert InsufficientAllowance();
        if (available != type(uint256).max) {
            unchecked {
                allowance[from][msg.sender] = available - value;
            }
            emit Approval(from, msg.sender, allowance[from][msg.sender]);
        }
        _transfer(from, to, value);
        return true;
    }

    function mint(address to, uint256 value) external onlyOwner {
        _mint(to, value);
    }

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
        unchecked {
            balanceOf[from] = available - value;
            balanceOf[to] += value;
        }
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
