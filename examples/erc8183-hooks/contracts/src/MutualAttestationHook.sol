// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Minimal interface to read job participants from ERC-8183
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

/**
 * @title MutualAttestationHook
 * @notice Airbnb-style bilateral review system for ERC-8183 Agentic Commerce.
 *         Both client and provider attest each other on EAS after job completion,
 *         building two-sided on-chain reputation.
 *
 * PROBLEM
 * -------
 * One-sided reviews create incentive problems: clients can post vague specs
 * without accountability, and providers can deliver poor work while blaming
 * the spec. This hook requires both parties to leave reviews.
 *
 * FLOW
 * ----
 *  1. Job completes or is rejected on ERC-8183 contract
 *  2. afterAction callback records participants + timestamp
 *  3. Within reviewWindow (default 7 days):
 *       a. Client calls submitClientReview(jobId, score, comment)
 *          → EAS attestation with provider as recipient
 *       b. Provider calls submitProviderReview(jobId, score, comment)
 *          → EAS attestation with client as recipient
 *  4. Both reviews submitted → MutualReviewComplete event
 *
 * TRUST MODEL
 * -----------
 * Only recorded job participants can review. Each party reviews exactly once.
 * EAS attestations are non-revocable — reviews are permanent on-chain facts.
 */
contract MutualAttestationHook is ReentrancyGuard {
    /// @notice EAS contract for attestations
    IEAS public immutable eas;

    /// @notice ERC-8183 contract this hook is attached to
    address public immutable jobContract;

    /// @notice Schema UID for mutual attestations
    bytes32 public immutable schemaUID;

    /// @notice Review window after job completion (default 7 days)
    uint256 public immutable reviewWindow;

    /// @notice Job participants recorded at completion
    mapping(uint256 => address) public jobClient;
    mapping(uint256 => address) public jobProvider;

    /// @notice Job completion timestamps
    mapping(uint256 => uint256) public jobCompletedAt;

    /// @notice Tracks whether each party has submitted their review
    mapping(uint256 => bool) public clientReviewed;
    mapping(uint256 => bool) public providerReviewed;

    /// @notice Attestation UIDs for each job
    mapping(uint256 => bytes32) public clientAttestationUID;
    mapping(uint256 => bytes32) public providerAttestationUID;

    event ReviewSubmitted(
        uint256 indexed jobId,
        address indexed reviewer,
        address indexed reviewee,
        uint8 score,
        bytes32 attestationUID,
        bool isClientReview
    );

    event MutualReviewComplete(uint256 indexed jobId);
    event JobRecordedForReview(uint256 indexed jobId, address client, address provider);

    error ReviewWindowExpired();
    error AlreadyReviewed();
    error InvalidScore();
    error JobNotCompleted();
    error NotJobParticipant();
    error OnlyJobContract();

    modifier onlyJobContract() {
        if (msg.sender != jobContract) revert OnlyJobContract();
        _;
    }

    constructor(
        address jobContract_,
        address eas_,
        bytes32 schemaUID_,
        uint256 reviewWindow_
    ) {
        jobContract = jobContract_;
        eas = IEAS(eas_);
        schemaUID = schemaUID_;
        reviewWindow = reviewWindow_ == 0 ? 7 days : reviewWindow_;
    }

    /*//////////////////////////////////////////////////////////////
                    HOOK CALLBACKS (from ERC-8183)
    //////////////////////////////////////////////////////////////*/

    /// @notice Called by ERC-8183 after complete/reject — records participants for review
    /// @dev Matches the afterAction(uint256,bytes4,bytes) hook signature
    function afterAction(uint256 jobId, bytes4 selector, bytes calldata) external onlyJobContract {
        // complete() or reject() selector
        bytes4 COMPLETE = bytes4(keccak256("complete(uint256,bytes32,bytes)"));
        bytes4 REJECT = bytes4(keccak256("reject(uint256,bytes32,bytes)"));

        if (selector == COMPLETE || selector == REJECT) {
            _recordJobForReview(jobId);
        }
    }

    /// @notice No-op for beforeAction — this hook only acts after completion
    function beforeAction(uint256, bytes4, bytes calldata) external view onlyJobContract {
        // pass
    }

    /*//////////////////////////////////////////////////////////////
                    REVIEW SUBMISSION
    //////////////////////////////////////////////////////////////*/

    /// @notice Client reviews provider ("Was the work good?")
    /// @param jobId The job identifier
    /// @param score 1-5 star rating
    /// @param comment Brief review text
    function submitClientReview(
        uint256 jobId,
        uint8 score,
        string calldata comment
    ) external nonReentrant {
        _validateReview(jobId, score);
        if (msg.sender != jobClient[jobId]) revert NotJobParticipant();
        if (clientReviewed[jobId]) revert AlreadyReviewed();

        clientReviewed[jobId] = true;

        address provider_ = jobProvider[jobId];
        bytes32 uid = _createAttestation(jobId, msg.sender, provider_, score, comment, true);
        clientAttestationUID[jobId] = uid;

        emit ReviewSubmitted(jobId, msg.sender, provider_, score, uid, true);

        if (providerReviewed[jobId]) {
            emit MutualReviewComplete(jobId);
        }
    }

    /// @notice Provider reviews client ("Was the client fair?")
    /// @param jobId The job identifier
    /// @param score 1-5 star rating
    /// @param comment Brief review text
    function submitProviderReview(
        uint256 jobId,
        uint8 score,
        string calldata comment
    ) external nonReentrant {
        _validateReview(jobId, score);
        if (msg.sender != jobProvider[jobId]) revert NotJobParticipant();
        if (providerReviewed[jobId]) revert AlreadyReviewed();

        providerReviewed[jobId] = true;

        address client_ = jobClient[jobId];
        bytes32 uid = _createAttestation(jobId, msg.sender, client_, score, comment, false);
        providerAttestationUID[jobId] = uid;

        emit ReviewSubmitted(jobId, msg.sender, client_, score, uid, false);

        if (clientReviewed[jobId]) {
            emit MutualReviewComplete(jobId);
        }
    }

    /*//////////////////////////////////////////////////////////////
                    VIEW FUNCTIONS
    //////////////////////////////////////////////////////////////*/

    /// @notice Check if both reviews are submitted for a job
    function isFullyReviewed(uint256 jobId) external view returns (bool) {
        return clientReviewed[jobId] && providerReviewed[jobId];
    }

    /// @notice Get review status for a job
    function getReviewStatus(uint256 jobId) external view returns (
        bool clientDone,
        bool providerDone,
        uint256 deadline
    ) {
        return (
            clientReviewed[jobId],
            providerReviewed[jobId],
            jobCompletedAt[jobId] + reviewWindow
        );
    }

    /*//////////////////////////////////////////////////////////////
                    INTERNAL
    //////////////////////////////////////////////////////////////*/

    function _recordJobForReview(uint256 jobId) internal {
        IERC8183Reader.Job memory job = IERC8183Reader(jobContract).getJob(jobId);
        jobClient[jobId] = job.client;
        jobProvider[jobId] = job.provider;
        jobCompletedAt[jobId] = block.timestamp;

        emit JobRecordedForReview(jobId, job.client, job.provider);
    }

    function _validateReview(uint256 jobId, uint8 score) internal view {
        if (jobCompletedAt[jobId] == 0) revert JobNotCompleted();
        if (block.timestamp > jobCompletedAt[jobId] + reviewWindow) revert ReviewWindowExpired();
        if (score < 1 || score > 5) revert InvalidScore();
    }

    function _createAttestation(
        uint256 jobId,
        address reviewer,
        address reviewee,
        uint8 score,
        string calldata comment,
        bool isClientReview
    ) internal returns (bytes32) {
        return eas.attest(
            IEAS.AttestationRequest({
                schema: schemaUID,
                data: IEAS.AttestationRequestData({
                    recipient: reviewee,
                    expirationTime: 0,
                    revocable: false,
                    refUID: bytes32(0),
                    data: abi.encode(jobId, reviewer, reviewee, score, comment, isClientReview),
                    value: 0
                })
            })
        );
    }
}
