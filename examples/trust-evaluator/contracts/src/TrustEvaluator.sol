// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title TrustEvaluator
 * @notice Fast-path ERC-8183 evaluator using on-chain trust scores.
 *         Evaluates jobs in ~1 block vs UMA's 30-minute liveness period.
 *         Reads provider reputation from an oracle, decides complete/reject.
 *
 * @dev Security features:
 *      - Caller restriction (opt-in) — prevents grief/front-run attacks
 *      - Job contract whitelist (opt-in) — prevents forged getJob() data
 *      - threatThreshold cannot be 0 — prevents silent threat system disable
 *      - CEI pattern — events before external calls
 *      - Double-evaluation prevention per (contract, jobId)
 */

import {Ownable2Step, Ownable} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/*//////////////////////////////////////////////////////////////
                        INTERFACES
//////////////////////////////////////////////////////////////*/

/// @notice ERC-8183 AgenticCommerce interface
/// @dev Compatible with both Virtuals ACP and BNB Chain APEX implementations
interface IERC8183 {
    enum Status {
        Open,
        Funded,
        Submitted,
        Completed,
        Rejected,
        Expired
    }

    struct Job {
        address client;
        address provider;
        address evaluator;
        address hook;
        uint256 budget;
        uint256 expiredAt;
        Status status;
        bytes32 deliverable;
        string description;
    }

    function getJob(uint256 jobId) external view returns (Job memory);
    function complete(uint256 jobId, bytes32 reason, bytes calldata optParams) external;
    function reject(uint256 jobId, bytes32 reason, bytes calldata optParams) external;
}

/// @notice Oracle interface for reading user reputation scores
interface ITrustOracle {
    struct UserReputation {
        uint256 reputationScore;
        uint256 totalReviews;
        uint256 scarabPoints;
        uint256 feeBps;
        bool initialized;
        uint256 lastUpdated;
    }

    function getUserData(address user) external view returns (UserReputation memory);
}

/*//////////////////////////////////////////////////////////////
                        ERRORS
//////////////////////////////////////////////////////////////*/

error JobNotSubmitted(uint256 jobId, uint8 currentStatus);
error NotJobEvaluator(uint256 jobId, address expected, address actual);
error ThresholdOutOfRange(uint256 value);
error ThreatThresholdCannotBeZero();
error ZeroAddress();
error AlreadyEvaluated(uint256 jobId);
error CallerNotAllowed(address caller);
error JobContractNotAllowed(address jobContract);

/*//////////////////////////////////////////////////////////////
                        CONTRACT
//////////////////////////////////////////////////////////////*/

contract TrustEvaluator is Ownable2Step, ReentrancyGuard {
    /*//////////////////////////////////////////////////////////////
                            CONSTANTS
    //////////////////////////////////////////////////////////////*/

    uint256 public constant MAX_SCORE = 100;

    bytes32 public constant REASON_LOW_TRUST = keccak256("LOW_TRUST_SCORE");
    bytes32 public constant REASON_FLAGGED = keccak256("FLAGGED_AGENT");
    bytes32 public constant REASON_UNINITIALIZED = keccak256("UNINITIALIZED_PROVIDER");

    /*//////////////////////////////////////////////////////////////
                            STATE
    //////////////////////////////////////////////////////////////*/

    /// @notice Trust oracle for reading provider scores
    ITrustOracle public oracle;

    /// @notice Minimum reputation score to pass evaluation (0-100)
    uint256 public threshold;

    /// @notice Number of threat reports that triggers auto-reject
    uint256 public threatThreshold;

    /// @notice Threat report count per provider
    mapping(address => uint256) public threatReports;

    /// @notice Addresses allowed to call evaluate()
    mapping(address => bool) public allowedCallers;

    /// @notice Whitelisted ERC-8183 job contracts
    mapping(address => bool) public allowedJobContracts;

    /// @notice Whether caller restriction is enabled
    bool public callerRestrictionEnabled;

    /// @notice Whether job contract restriction is enabled
    bool public jobContractRestrictionEnabled;

    /// @notice Track evaluated jobs to prevent double-evaluation
    mapping(address => mapping(uint256 => bool)) public evaluated;

    /// @notice Stats
    uint256 public totalEvaluations;
    uint256 public totalCompleted;
    uint256 public totalRejected;

    /*//////////////////////////////////////////////////////////////
                            EVENTS
    //////////////////////////////////////////////////////////////*/

    event EvaluationResult(
        address indexed jobContract,
        uint256 indexed jobId,
        address indexed provider,
        uint256 score,
        bool completed,
        bytes32 reason
    );

    event ThresholdUpdated(uint256 oldThreshold, uint256 newThreshold);
    event ThreatThresholdUpdated(uint256 oldCount, uint256 newCount);
    event ThreatReported(address indexed provider, uint256 newCount, address reporter);
    event OracleUpdated(address indexed oldOracle, address indexed newOracle);
    event CallerUpdated(address indexed caller, bool allowed);
    event JobContractUpdated(address indexed jobContract, bool allowed);
    event CallerRestrictionToggled(bool enabled);
    event JobContractRestrictionToggled(bool enabled);
    event ThreatsCleared(address indexed provider);

    /*//////////////////////////////////////////////////////////////
                            CONSTRUCTOR
    //////////////////////////////////////////////////////////////*/

    /// @param _oracle Trust oracle address
    /// @param _threshold Minimum score to pass (0-100)
    /// @param _threatThreshold Threat reports for auto-reject (must be > 0)
    /// @param _owner Contract owner
    constructor(
        address _oracle,
        uint256 _threshold,
        uint256 _threatThreshold,
        address _owner
    ) Ownable(_owner) {
        if (_oracle == address(0)) revert ZeroAddress();
        if (_threshold > MAX_SCORE) revert ThresholdOutOfRange(_threshold);
        if (_threatThreshold == 0) revert ThreatThresholdCannotBeZero();

        oracle = ITrustOracle(_oracle);
        threshold = _threshold;
        threatThreshold = _threatThreshold;
    }

    /*//////////////////////////////////////////////////////////////
                        CORE: EVALUATE
    //////////////////////////////////////////////////////////////*/

    /// @notice Evaluate a submitted ERC-8183 job using trust scores
    /// @param jobContract The ERC-8183 AgenticCommerce contract
    /// @param jobId The job to evaluate
    function evaluate(address jobContract, uint256 jobId) external nonReentrant {
        if (jobContract == address(0)) revert ZeroAddress();

        // Caller restriction
        if (callerRestrictionEnabled && !allowedCallers[msg.sender]) {
            revert CallerNotAllowed(msg.sender);
        }

        // Job contract whitelist
        if (jobContractRestrictionEnabled && !allowedJobContracts[jobContract]) {
            revert JobContractNotAllowed(jobContract);
        }

        // Prevent double evaluation
        if (evaluated[jobContract][jobId]) {
            revert AlreadyEvaluated(jobId);
        }

        IERC8183 erc8183 = IERC8183(jobContract);
        IERC8183.Job memory job = erc8183.getJob(jobId);

        // Must be Submitted
        if (job.status != IERC8183.Status.Submitted) {
            revert JobNotSubmitted(jobId, uint8(job.status));
        }

        // This contract must be the evaluator
        if (job.evaluator != address(this)) {
            revert NotJobEvaluator(jobId, address(this), job.evaluator);
        }

        // Read provider score from oracle
        ITrustOracle.UserReputation memory rep = oracle.getUserData(job.provider);

        uint256 score = rep.initialized ? rep.reputationScore : 0;
        uint256 cappedScore = score > MAX_SCORE ? MAX_SCORE : score;

        // Decision logic
        bool shouldComplete;
        bytes32 reason;

        uint256 threats = threatReports[job.provider];

        if (threats >= threatThreshold && threatThreshold > 0) {
            shouldComplete = false;
            reason = REASON_FLAGGED;
        } else if (!rep.initialized) {
            shouldComplete = false;
            reason = REASON_UNINITIALIZED;
        } else if (cappedScore >= threshold) {
            shouldComplete = true;
            reason = bytes32(cappedScore);
        } else {
            shouldComplete = false;
            reason = REASON_LOW_TRUST;
        }

        // Effects before interactions (CEI pattern)
        evaluated[jobContract][jobId] = true;
        totalEvaluations++;

        if (shouldComplete) {
            totalCompleted++;
        } else {
            totalRejected++;
        }

        // Emit event BEFORE external call
        emit EvaluationResult(jobContract, jobId, job.provider, cappedScore, shouldComplete, reason);

        // Interaction last
        if (shouldComplete) {
            erc8183.complete(jobId, reason, "");
        } else {
            erc8183.reject(jobId, reason, "");
        }
    }

    /*//////////////////////////////////////////////////////////////
                        VIEW: PRE-CHECK
    //////////////////////////////////////////////////////////////*/

    /// @notice Check if a provider would pass evaluation (read-only)
    /// @param provider Address to check
    /// @return score The provider's current reputation score (capped at 100)
    /// @return wouldPass Whether score >= threshold and not flagged
    function preCheck(address provider) external view returns (uint256 score, bool wouldPass) {
        ITrustOracle.UserReputation memory rep = oracle.getUserData(provider);

        if (!rep.initialized) {
            return (0, false);
        }

        score = rep.reputationScore > MAX_SCORE ? MAX_SCORE : rep.reputationScore;

        uint256 threats = threatReports[provider];
        bool flagged = threatThreshold > 0 && threats >= threatThreshold;

        wouldPass = score >= threshold && !flagged;
    }

    /*//////////////////////////////////////////////////////////////
                        ADMIN: CONFIGURATION
    //////////////////////////////////////////////////////////////*/

    function setThreshold(uint256 _threshold) external onlyOwner {
        if (_threshold > MAX_SCORE) revert ThresholdOutOfRange(_threshold);
        uint256 old = threshold;
        threshold = _threshold;
        emit ThresholdUpdated(old, _threshold);
    }

    /// @dev Cannot be set to 0 — that would silently disable the entire threat system
    function setThreatThreshold(uint256 _count) external onlyOwner {
        if (_count == 0) revert ThreatThresholdCannotBeZero();
        uint256 old = threatThreshold;
        threatThreshold = _count;
        emit ThreatThresholdUpdated(old, _count);
    }

    function setOracle(address _oracle) external onlyOwner {
        if (_oracle == address(0)) revert ZeroAddress();
        address old = address(oracle);
        oracle = ITrustOracle(_oracle);
        emit OracleUpdated(old, _oracle);
    }

    function reportThreat(address provider) external onlyOwner {
        if (provider == address(0)) revert ZeroAddress();
        threatReports[provider]++;
        emit ThreatReported(provider, threatReports[provider], msg.sender);
    }

    function reportThreats(address[] calldata providers) external onlyOwner {
        for (uint256 i = 0; i < providers.length; i++) {
            if (providers[i] == address(0)) revert ZeroAddress();
            threatReports[providers[i]]++;
            emit ThreatReported(providers[i], threatReports[providers[i]], msg.sender);
        }
    }

    function clearThreats(address provider) external onlyOwner {
        threatReports[provider] = 0;
        emit ThreatsCleared(provider);
    }

    /*//////////////////////////////////////////////////////////////
                    ADMIN: ACCESS CONTROL
    //////////////////////////////////////////////////////////////*/

    function setCallerRestriction(bool _enabled) external onlyOwner {
        callerRestrictionEnabled = _enabled;
        emit CallerRestrictionToggled(_enabled);
    }

    function setJobContractRestriction(bool _enabled) external onlyOwner {
        jobContractRestrictionEnabled = _enabled;
        emit JobContractRestrictionToggled(_enabled);
    }

    function setAllowedCaller(address caller, bool allowed) external onlyOwner {
        if (caller == address(0)) revert ZeroAddress();
        allowedCallers[caller] = allowed;
        emit CallerUpdated(caller, allowed);
    }

    function setAllowedJobContract(address jobContract, bool allowed) external onlyOwner {
        if (jobContract == address(0)) revert ZeroAddress();
        allowedJobContracts[jobContract] = allowed;
        emit JobContractUpdated(jobContract, allowed);
    }
}
