// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title AttestationHook
 * @notice Writes an immutable EAS attestation for every completed or rejected
 *         ERC-8183 job, creating on-chain receipts that feed reputation systems.
 *
 * PROBLEM
 * -------
 * Off-chain trust scores and reputation graphs (e.g. ERC-8004) need a
 * verifiable, tamper-proof record of job outcomes. Without an attestation
 * layer, any reputation system must trust a centralised data source.
 *
 * SOLUTION
 * --------
 * This hook calls EAS after every job completion or rejection, storing:
 * jobId, client, provider, evaluator, budget, outcome reason, completed flag.
 * Permanently queryable by anyone on-chain.
 *
 * FLOW
 * ----
 *  1. Job completes/rejects on ERC-8183 contract
 *  2. afterAction callback triggers this hook
 *  3. Hook reads job data from ERC-8183 via getJob()
 *  4. Hook calls EAS.attest() with job outcome data
 *  5. Attestation UID stored for reference
 *
 * TRUST MODEL
 * -----------
 * - Attestations are non-revocable — job outcomes are facts
 * - Hook never blocks lifecycle transitions (afterAction only)
 * - Each jobId attested exactly once (idempotency guard + CEI sentinel)
 * - EAS failures never revert the parent transaction (try/catch)
 */

/// @notice Minimal EAS interface
interface IEAS {
    struct AttestationRequestData {
        address recipient;
        uint64 expirationTime;
        bool revocable;
        bytes32 refUID;
        bytes data;
        uint256 value;
    }

    struct AttestationRequest {
        bytes32 schema;
        AttestationRequestData data;
    }

    function attest(AttestationRequest calldata request) external payable returns (bytes32);
}

/// @notice Minimal ERC-8183 reader interface
interface IERC8183Reader {
    struct Job {
        address client;
        address provider;
        address evaluator;
        address hook;
        uint256 budget;
        uint256 expiredAt;
        uint8 status;
        bytes32 deliverable;
        string description;
    }

    function getJob(uint256 jobId) external view returns (Job memory);
}

contract AttestationHook {
    /*//////////////////////////////////////////////////////////////
                            CONSTANTS
    //////////////////////////////////////////////////////////////*/

    /// @dev Sentinel value to mark in-progress attestation (CEI pattern)
    bytes32 private constant _PENDING_SENTINEL = bytes32(type(uint256).max);

    /// @dev Well-known selectors for complete/reject
    bytes4 private constant COMPLETE_SELECTOR = bytes4(keccak256("complete(uint256,bytes32,bytes)"));
    bytes4 private constant REJECT_SELECTOR = bytes4(keccak256("reject(uint256,bytes32,bytes)"));

    /*//////////////////////////////////////////////////////////////
                            STORAGE
    //////////////////////////////////////////////////////////////*/

    /// @notice EAS contract
    IEAS public eas;

    /// @notice Schema UID for the job receipt schema
    bytes32 public schemaUID;

    /// @notice ERC-8183 contract to read job details
    IERC8183Reader public immutable jobContract;

    /// @notice Owner for admin functions
    address public owner;

    /// @notice Pending owner for two-step transfer
    address public pendingOwner;

    /// @notice Track attestation UIDs per job
    mapping(uint256 => bytes32) public jobAttestations;

    /// @notice Counter for total attestations written
    uint256 public totalAttestations;

    /*//////////////////////////////////////////////////////////////
                            EVENTS
    //////////////////////////////////////////////////////////////*/

    event AttestationCreated(
        uint256 indexed jobId,
        bytes32 indexed attestationUID,
        address indexed provider,
        bool completed
    );

    event AttestationFailed(uint256 indexed jobId, bytes reason);
    event SchemaUpdated(bytes32 indexed newSchemaUID);
    event EASUpdated(address indexed newEAS);
    event OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    /*//////////////////////////////////////////////////////////////
                            ERRORS
    //////////////////////////////////////////////////////////////*/

    error OnlyOwner();
    error OnlyPendingOwner();
    error OnlyJobContract();
    error ZeroAddress();
    error ZeroSchemaUID();

    /*//////////////////////////////////////////////////////////////
                            MODIFIERS
    //////////////////////////////////////////////////////////////*/

    modifier onlyOwner_() {
        if (msg.sender != owner) revert OnlyOwner();
        _;
    }

    modifier onlyJobContract_() {
        if (msg.sender != address(jobContract)) revert OnlyJobContract();
        _;
    }

    /*//////////////////////////////////////////////////////////////
                            CONSTRUCTOR
    //////////////////////////////////////////////////////////////*/

    /// @param jobContract_ ERC-8183 contract address
    /// @param eas_ EAS contract address
    /// @param schemaUID_ Pre-registered EAS schema UID for job receipts
    constructor(
        address jobContract_,
        address eas_,
        bytes32 schemaUID_
    ) {
        if (jobContract_ == address(0) || eas_ == address(0)) revert ZeroAddress();
        if (schemaUID_ == bytes32(0)) revert ZeroSchemaUID();

        jobContract = IERC8183Reader(jobContract_);
        eas = IEAS(eas_);
        schemaUID = schemaUID_;
        owner = msg.sender;

        emit OwnershipTransferred(address(0), msg.sender);
    }

    /*//////////////////////////////////////////////////////////////
                    HOOK CALLBACKS
    //////////////////////////////////////////////////////////////*/

    /// @notice No-op — this hook never blocks lifecycle transitions
    function beforeAction(uint256, bytes4, bytes calldata) external view onlyJobContract_ {
        // pass
    }

    /// @notice Called after complete/reject — writes EAS attestation
    function afterAction(uint256 jobId, bytes4 selector, bytes calldata data) external onlyJobContract_ {
        if (selector == COMPLETE_SELECTOR) {
            // Extract reason from data (first 32 bytes after jobId)
            bytes32 reason = data.length >= 64 ? abi.decode(data[32:64], (bytes32)) : bytes32(0);
            _writeAttestation(jobId, reason, true);
        } else if (selector == REJECT_SELECTOR) {
            bytes32 reason = data.length >= 64 ? abi.decode(data[32:64], (bytes32)) : bytes32(0);
            _writeAttestation(jobId, reason, false);
        }
    }

    /*//////////////////////////////////////////////////////////////
                    CORE: WRITE ATTESTATION
    //////////////////////////////////////////////////////////////*/

    /**
     * @dev Reads job data and writes an EAS attestation.
     *      Uses try/catch — EAS failures NEVER revert the parent tx.
     *      Idempotent — each jobId attested once only.
     *      Follows CEI pattern with a pending sentinel.
     *
     * Schema: (uint256 jobId, address client, address provider,
     *          address evaluator, uint256 budget, bytes32 reason, bool completed)
     *
     * Recipient = provider (they accumulate reputation)
     */
    function _writeAttestation(
        uint256 jobId,
        bytes32 reason,
        bool completed
    ) internal {
        // Idempotency guard
        if (jobAttestations[jobId] != bytes32(0)) return;

        // Read job data
        IERC8183Reader.Job memory job;
        try jobContract.getJob(jobId) returns (IERC8183Reader.Job memory j) {
            job = j;
        } catch (bytes memory err) {
            emit AttestationFailed(jobId, err);
            return;
        }

        // Encode attestation data
        bytes memory attestationData = abi.encode(
            jobId,
            job.client,
            job.provider,
            job.evaluator,
            job.budget,
            reason,
            completed
        );

        // CEI: Set sentinel BEFORE external call
        jobAttestations[jobId] = _PENDING_SENTINEL;

        // Write to EAS (never reverts parent tx)
        try eas.attest(
            IEAS.AttestationRequest({
                schema: schemaUID,
                data: IEAS.AttestationRequestData({
                    recipient: job.provider,
                    expirationTime: 0,       // permanent
                    revocable: false,        // facts, not opinions
                    refUID: bytes32(0),
                    data: attestationData,
                    value: 0
                })
            })
        ) returns (bytes32 uid) {
            jobAttestations[jobId] = uid;
            totalAttestations++;
            emit AttestationCreated(jobId, uid, job.provider, completed);
        } catch (bytes memory err) {
            // Reset sentinel on failure
            jobAttestations[jobId] = bytes32(0);
            emit AttestationFailed(jobId, err);
        }
    }

    /*//////////////////////////////////////////////////////////////
                    ADMIN
    //////////////////////////////////////////////////////////////*/

    function setSchemaUID(bytes32 schemaUID_) external onlyOwner_ {
        if (schemaUID_ == bytes32(0)) revert ZeroSchemaUID();
        schemaUID = schemaUID_;
        emit SchemaUpdated(schemaUID_);
    }

    function setEAS(address eas_) external onlyOwner_ {
        if (eas_ == address(0)) revert ZeroAddress();
        eas = IEAS(eas_);
        emit EASUpdated(eas_);
    }

    function transferOwnership(address newOwner) external onlyOwner_ {
        if (newOwner == address(0)) revert ZeroAddress();
        pendingOwner = newOwner;
        emit OwnershipTransferStarted(owner, newOwner);
    }

    function acceptOwnership() external {
        if (msg.sender != pendingOwner) revert OnlyPendingOwner();
        emit OwnershipTransferred(owner, msg.sender);
        owner = msg.sender;
        pendingOwner = address(0);
    }

    /*//////////////////////////////////////////////////////////////
                    VIEW
    //////////////////////////////////////////////////////////////*/

    /// @notice Get the EAS attestation UID for a given job
    function getAttestation(uint256 jobId) external view returns (bytes32) {
        bytes32 uid = jobAttestations[jobId];
        return uid == _PENDING_SENTINEL ? bytes32(0) : uid;
    }
}
