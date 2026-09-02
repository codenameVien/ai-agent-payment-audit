// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Six-decimal demo settlement token for the PBL Base Sepolia environment.
/// @dev The owner-controlled mint is deliberate for test distribution. Production assets must
///      replace this contract and its policy before any mainnet use.
contract DemoToken {
    string public constant name = "PBL Agent Credit";
    string public constant symbol = "PBLC";
    uint8 public constant decimals = 6;

    uint256 public totalSupply;
    address public owner;
    mapping(address account => uint256 amount) public balanceOf;
    mapping(address account => mapping(address spender => uint256 amount)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event OwnershipTransferred(address indexed previousOwner, address indexed nextOwner);

    error Unauthorized();
    error ZeroAddress();
    error InsufficientBalance();
    error InsufficientAllowance();

    constructor(address initialOwner, uint256 initialSupplyUnits) {
        if (initialOwner == address(0)) revert ZeroAddress();
        owner = initialOwner;
        emit OwnershipTransferred(address(0), initialOwner);
        _mint(initialOwner, initialSupplyUnits);
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
}
