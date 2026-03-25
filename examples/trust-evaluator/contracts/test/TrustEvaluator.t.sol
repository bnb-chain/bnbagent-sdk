// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {TrustEvaluator, ITrustOracle, IERC8183} from "../src/TrustEvaluator.sol";

/*//////////////////////////////////////////////////////////////
                    MOCK: TrustOracle
//////////////////////////////////////////////////////////////*/

contract MockOracle is ITrustOracle {
    mapping(address => UserReputation) public reps;

    function setUserData(address user, uint256 score, bool initialized) external {
        reps[user] = UserReputation({
            reputationScore: score,
            totalReviews: 1,
            scarabPoints: 0,
            feeBps: 50,
            initialized: initialized,
            lastUpdated: block.timestamp
        });
    }

    function getUserData(address user) external view returns (UserReputation memory) {
        return reps[user];
    }
}

/*//////////////////////////////////////////////////////////////
                    MOCK: ERC-8183
//////////////////////////////////////////////////////////////*/

contract MockERC8183 is IERC8183 {
    mapping(uint256 => Job) public jobs;
    uint256 public nextId = 1;

    function createMockJob(
        address client,
        address provider,
        address evaluator,
        uint256 budget
    ) external returns (uint256) {
        uint256 id = nextId++;
        jobs[id] = Job({
            client: client,
            provider: provider,
            evaluator: evaluator,
            hook: address(0),
            budget: budget,
            expiredAt: block.timestamp + 7 days,
            status: Status.Submitted,
            deliverable: keccak256("test-deliverable"),
            description: "Test job"
        });
        return id;
    }

    function setJobStatus(uint256 jobId, Status status) external {
        jobs[jobId].status = status;
    }

    function getJob(uint256 jobId) external view returns (Job memory) {
        return jobs[jobId];
    }

    function complete(uint256 jobId, bytes32, bytes calldata) external {
        jobs[jobId].status = Status.Completed;
    }

    function reject(uint256 jobId, bytes32, bytes calldata) external {
        jobs[jobId].status = Status.Rejected;
    }
}

/*//////////////////////////////////////////////////////////////
                    TEST SUITE
//////////////////////////////////////////////////////////////*/

contract TrustEvaluatorTest is Test {
    TrustEvaluator public evaluator;
    MockOracle public oracle;
    MockERC8183 public erc8183;

    address public owner = address(this);
    address public client = address(0xC1);
    address public provider = address(0xBEEF);

    uint256 public constant DEFAULT_THRESHOLD = 30;
    uint256 public constant DEFAULT_THREAT_THRESHOLD = 3;

    function setUp() public {
        oracle = new MockOracle();
        erc8183 = new MockERC8183();
        evaluator = new TrustEvaluator(
            address(oracle),
            DEFAULT_THRESHOLD,
            DEFAULT_THREAT_THRESHOLD,
            owner
        );
    }

    // ── Constructor ──────────────────────────────────────────────────────

    function test_constructor_setsValues() public view {
        assertEq(address(evaluator.oracle()), address(oracle));
        assertEq(evaluator.threshold(), DEFAULT_THRESHOLD);
        assertEq(evaluator.threatThreshold(), DEFAULT_THREAT_THRESHOLD);
    }

    function test_constructor_revertsZeroOracle() public {
        vm.expectRevert(abi.encodeWithSignature("ZeroAddress()"));
        new TrustEvaluator(address(0), 30, 3, owner);
    }

    function test_constructor_revertsThresholdTooHigh() public {
        vm.expectRevert(abi.encodeWithSignature("ThresholdOutOfRange(uint256)", 101));
        new TrustEvaluator(address(oracle), 101, 3, owner);
    }

    function test_constructor_revertsZeroThreatThreshold() public {
        vm.expectRevert(abi.encodeWithSignature("ThreatThresholdCannotBeZero()"));
        new TrustEvaluator(address(oracle), 30, 0, owner);
    }

    // ── Evaluate: Complete ───────────────────────────────────────────────

    function test_evaluate_completesAboveThreshold() public {
        oracle.setUserData(provider, 50, true);
        uint256 jobId = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);

        evaluator.evaluate(address(erc8183), jobId);

        assertEq(uint8(erc8183.getJob(jobId).status), uint8(IERC8183.Status.Completed));
    }

    function test_evaluate_completesAtExactThreshold() public {
        oracle.setUserData(provider, DEFAULT_THRESHOLD, true);
        uint256 jobId = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);

        evaluator.evaluate(address(erc8183), jobId);

        assertEq(uint8(erc8183.getJob(jobId).status), uint8(IERC8183.Status.Completed));
    }

    // ── Evaluate: Reject ────────────────────────────────────────────────

    function test_evaluate_rejectsBelowThreshold() public {
        oracle.setUserData(provider, DEFAULT_THRESHOLD - 1, true);
        uint256 jobId = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);

        evaluator.evaluate(address(erc8183), jobId);

        assertEq(uint8(erc8183.getJob(jobId).status), uint8(IERC8183.Status.Rejected));
    }

    function test_evaluate_rejectsUninitialized() public {
        uint256 jobId = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);

        evaluator.evaluate(address(erc8183), jobId);

        assertEq(uint8(erc8183.getJob(jobId).status), uint8(IERC8183.Status.Rejected));
    }

    function test_evaluate_rejectsFlaggedProvider() public {
        oracle.setUserData(provider, 100, true);
        uint256 jobId = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);

        for (uint256 i = 0; i < DEFAULT_THREAT_THRESHOLD; i++) {
            evaluator.reportThreat(provider);
        }

        evaluator.evaluate(address(erc8183), jobId);

        assertEq(uint8(erc8183.getJob(jobId).status), uint8(IERC8183.Status.Rejected));
    }

    // ── Evaluate: Reverts ───────────────────────────────────────────────

    function test_evaluate_revertsDoubleEvaluation() public {
        oracle.setUserData(provider, 50, true);
        uint256 jobId = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);
        evaluator.evaluate(address(erc8183), jobId);

        vm.expectRevert(abi.encodeWithSignature("AlreadyEvaluated(uint256)", jobId));
        evaluator.evaluate(address(erc8183), jobId);
    }

    // ── Access Control ──────────────────────────────────────────────────

    function test_callerRestriction_blocksUnauthorized() public {
        oracle.setUserData(provider, 50, true);
        uint256 jobId = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);

        evaluator.setCallerRestriction(true);

        vm.prank(address(0xCAFE));
        vm.expectRevert(abi.encodeWithSignature("CallerNotAllowed(address)", address(0xCAFE)));
        evaluator.evaluate(address(erc8183), jobId);
    }

    function test_callerRestriction_allowsWhitelisted() public {
        oracle.setUserData(provider, 50, true);
        uint256 jobId = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);

        evaluator.setCallerRestriction(true);
        evaluator.setAllowedCaller(address(0xCAFE), true);

        vm.prank(address(0xCAFE));
        evaluator.evaluate(address(erc8183), jobId);
        assertEq(uint8(erc8183.getJob(jobId).status), uint8(IERC8183.Status.Completed));
    }

    function test_jobContractRestriction_blocksFake() public {
        oracle.setUserData(provider, 50, true);
        uint256 jobId = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);

        evaluator.setJobContractRestriction(true);

        vm.expectRevert(abi.encodeWithSignature("JobContractNotAllowed(address)", address(erc8183)));
        evaluator.evaluate(address(erc8183), jobId);
    }

    function test_jobContractRestriction_allowsWhitelisted() public {
        oracle.setUserData(provider, 50, true);
        uint256 jobId = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);

        evaluator.setJobContractRestriction(true);
        evaluator.setAllowedJobContract(address(erc8183), true);

        evaluator.evaluate(address(erc8183), jobId);
        assertEq(uint8(erc8183.getJob(jobId).status), uint8(IERC8183.Status.Completed));
    }

    // ── Stats ───────────────────────────────────────────────────────────

    function test_stats_trackCorrectly() public {
        oracle.setUserData(provider, 50, true);
        uint256 job1 = erc8183.createMockJob(client, provider, address(evaluator), 0.02 ether);
        evaluator.evaluate(address(erc8183), job1);

        address badProvider = address(0xBAD);
        oracle.setUserData(badProvider, 5, true);
        uint256 job2 = erc8183.createMockJob(client, badProvider, address(evaluator), 0.02 ether);
        evaluator.evaluate(address(erc8183), job2);

        assertEq(evaluator.totalEvaluations(), 2);
        assertEq(evaluator.totalCompleted(), 1);
        assertEq(evaluator.totalRejected(), 1);
    }
}
