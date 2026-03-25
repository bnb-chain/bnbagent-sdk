// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @notice Oracle interface for token safety checks
interface ITokenSafetyOracle {
    struct TokenSafety {
        bool isHoneypot;
        bool isVerified;
        uint256 buyTaxBps;   // basis points
        uint256 sellTaxBps;  // basis points
        uint256 liquidityUsd;
        uint256 lastChecked;
    }

    /// @notice Check if a token is safe for use as payment
    /// @return safety Token safety data
    /// @return isSafe Whether the token passes all safety checks
    function checkToken(address token) external view returns (TokenSafety memory safety, bool isSafe);
}

/**
 * @title TokenSafetyHook
 * @notice Pre-funding safety check for ERC-8183 job payment tokens.
 *         Prevents providers from accepting payment in honeypot or
 *         high-tax tokens that can't be sold after receiving them.
 *
 * PROBLEM
 * -------
 * ERC-8183 jobs can be funded with arbitrary ERC-20 tokens. A malicious
 * client could fund a job with a token that:
 *   - Is a honeypot (sells blocked)
 *   - Has extremely high sell tax (>50%)
 *   - Has zero liquidity (can't be swapped)
 *
 * This hook checks token safety before allowing funding to proceed.
 *
 * FLOW
 * ----
 * 1. Client calls fund(jobId, optParams) on ERC-8183
 * 2. beforeAction hook triggers → queries ITokenSafetyOracle
 * 3. Oracle returns unsafe → revert (funding blocked)
 * 4. Oracle returns safe → allow funding to proceed
 *
 * CONFIGURATION
 * -------------
 * - Owner can whitelist known-safe tokens (skip oracle check)
 * - Owner can set/update the safety oracle address
 * - Only the ERC-8183 contract can call hook functions
 */
contract TokenSafetyHook is Ownable {
    /// @notice Token safety oracle
    ITokenSafetyOracle public oracle;

    /// @notice ERC-8183 contract this hook is attached to
    address public jobContract;

    /// @notice Whitelisted tokens that skip oracle check
    mapping(address => bool) public tokenWhitelist;

    /// @dev Well-known selector for fund(uint256,bytes)
    bytes4 private constant FUND_SELECTOR = bytes4(keccak256("fund(uint256,bytes)"));

    event OracleUpdated(address indexed oldOracle, address indexed newOracle);
    event JobContractUpdated(address indexed oldContract, address indexed newContract);
    event TokenWhitelistUpdated(address indexed token, bool allowed);
    event TokenBlocked(address indexed token, uint256 indexed jobId, string reason);

    error OnlyJobContract();
    error ZeroAddress();
    error UnsafeToken(address token);

    modifier onlyJobContract() {
        if (msg.sender != jobContract) revert OnlyJobContract();
        _;
    }

    constructor(
        address jobContract_,
        address oracle_,
        address owner_
    ) Ownable(owner_) {
        if (jobContract_ == address(0) || oracle_ == address(0)) revert ZeroAddress();
        jobContract = jobContract_;
        oracle = ITokenSafetyOracle(oracle_);
    }

    /*//////////////////////////////////////////////////////////////
                    HOOK CALLBACKS
    //////////////////////////////////////////////////////////////*/

    /// @notice Called before fund() — checks token safety
    /// @dev Fail-closed: reverts if data is malformed or missing
    function beforeAction(uint256 jobId, bytes4 selector, bytes calldata data) external onlyJobContract {
        if (selector != FUND_SELECTOR) return; // only check on funding

        // Extract payment token from optParams
        // Convention: optParams = abi.encode(tokenAddress, amount) = 64 bytes minimum
        if (data.length < 64) {
            revert UnsafeToken(address(0)); // fail-closed: malformed data = block funding
        }

        (address token,) = abi.decode(data, (address, uint256));

        // Skip whitelisted tokens
        if (tokenWhitelist[token]) return;

        // Query oracle
        (, bool isSafe) = oracle.checkToken(token);
        if (!isSafe) {
            emit TokenBlocked(token, jobId, "Token failed safety check");
            revert UnsafeToken(token);
        }
    }

    /// @notice No-op for afterAction
    function afterAction(uint256, bytes4, bytes calldata) external view onlyJobContract {
        // pass
    }

    /*//////////////////////////////////////////////////////////////
                    ADMIN
    //////////////////////////////////////////////////////////////*/

    function setOracle(address oracle_) external onlyOwner {
        if (oracle_ == address(0)) revert ZeroAddress();
        address old = address(oracle);
        oracle = ITokenSafetyOracle(oracle_);
        emit OracleUpdated(old, oracle_);
    }

    function setJobContract(address jobContract_) external onlyOwner {
        if (jobContract_ == address(0)) revert ZeroAddress();
        address old = jobContract;
        jobContract = jobContract_;
        emit JobContractUpdated(old, jobContract_);
    }

    function setTokenWhitelist(address token, bool allowed) external onlyOwner {
        if (token == address(0)) revert ZeroAddress();
        tokenWhitelist[token] = allowed;
        emit TokenWhitelistUpdated(token, allowed);
    }
}
