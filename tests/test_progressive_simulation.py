"""Simulation: multi-cycle progressive scan with realistic APEX job lifecycle.

Tests the full polling lifecycle:
  - Startup scan
  - Progressive scan with no new jobs
  - New jobs appearing
  - Jobs transitioning from OPEN → FUNDED between polls
  - Jobs expiring / completing (terminal states)
  - Multiple concurrent scenarios

This is NOT a unit test — it's a scenario-based simulation that exercises
the real APEXJobOps code paths with a mock chain backend.
"""

import time
from unittest.mock import MagicMock

import pytest

from bnbagent.apex.client import APEXStatus
from bnbagent.apex.server.job_ops import APEXJobOps

FAKE_ADDRESS = "0x" + "aa" * 20
FAKE_PRIVATE_KEY = "0x" + "bb" * 32
OTHER_PROVIDER = "0x" + "ff" * 20

FUTURE_TS = int(time.time()) + 7200
PAST_TS = int(time.time()) - 100


class ChainSimulator:
    """Simulates on-chain state for APEXJobOps testing.

    Maintains a dict of jobs and job_counter, wired into a mock APEXClient.
    """

    def __init__(self):
        self.jobs: dict[int, dict] = {}
        self._job_counter = 0
        self.client = MagicMock()
        self.client._account = FAKE_ADDRESS
        self.client.w3.eth.block_number = 1000

        # Wire mock methods to simulator state
        self.client.job_counter.side_effect = lambda: self._job_counter
        self.client.get_jobs_batch.side_effect = self._get_jobs_batch
        self.client.get_job.side_effect = self._get_job
        self.client.get_job_funded_events.return_value = []

    def create_job(
        self,
        provider: str = FAKE_ADDRESS,
        status: APEXStatus = APEXStatus.OPEN,
        expired_at: int = FUTURE_TS,
        description: str = "",
    ) -> int:
        """Simulate createJob() on-chain."""
        self._job_counter += 1
        job_id = self._job_counter
        self.jobs[job_id] = {
            "jobId": job_id,
            "provider": provider,
            "status": status,
            "expiredAt": expired_at,
            "description": description or f"job-{job_id}",
            "client": "0x" + "cc" * 20,
            "evaluator": "0x" + "ee" * 20,
            "hook": "0x" + "00" * 20,
            "budget": 1000,
        }
        return job_id

    def fund_job(self, job_id: int):
        """Simulate fund() on-chain — changes status, does NOT change job_counter."""
        assert job_id in self.jobs, f"Job {job_id} not found"
        self.jobs[job_id]["status"] = APEXStatus.FUNDED

    def complete_job(self, job_id: int):
        """Simulate submit+complete on-chain."""
        self.jobs[job_id]["status"] = APEXStatus.COMPLETED

    def expire_job(self, job_id: int):
        """Simulate expiration."""
        self.jobs[job_id]["expiredAt"] = PAST_TS

    def _get_jobs_batch(self, job_ids: list[int]) -> list[dict | None]:
        return [self.jobs.get(jid) for jid in job_ids]

    def _get_job(self, job_id: int) -> dict:
        if job_id not in self.jobs:
            raise Exception(f"Job {job_id} not found")
        return self.jobs[job_id]


def _make_ops_with_simulator() -> tuple[APEXJobOps, ChainSimulator]:
    """Create APEXJobOps wired to a ChainSimulator."""
    ops = APEXJobOps(
        rpc_url="https://fake-rpc",
        erc8183_address="0x" + "ab" * 20,
        private_key=FAKE_PRIVATE_KEY,
        chain_id=97,
    )
    sim = ChainSimulator()
    ops._client = sim.client
    return ops, sim


# ─── Scenario Tests ───


class TestScenario_StartupThenProgressivePolling:
    """Full lifecycle: startup → multiple progressive polls."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        ops, sim = _make_ops_with_simulator()

        # ── Pre-existing state: 3 jobs on chain ──
        sim.create_job(status=APEXStatus.FUNDED)        # job 1: FUNDED for us
        sim.create_job(provider=OTHER_PROVIDER,
                       status=APEXStatus.FUNDED)         # job 2: FUNDED for other
        sim.create_job(status=APEXStatus.OPEN)           # job 3: OPEN for us

        # ── Poll 1: Startup scan ──
        r1 = await ops.get_pending_jobs()
        assert r1["success"] is True
        assert len(r1["jobs"]) == 1  # only job 1
        assert r1["jobs"][0]["jobId"] == 1
        assert ops._startup_scan_done is True
        assert ops._last_known_counter == 3
        # Job 3 should be tracked as pending-open
        assert 3 in ops._pending_open_ids

        # ── Poll 2: No changes on chain ──
        r2 = await ops.get_pending_jobs()
        assert r2["success"] is True
        # Job 3 is still OPEN — re-checked via _pending_open_ids but no results
        assert len(r2["jobs"]) == 0
        assert 3 in ops._pending_open_ids  # still tracked

        # ── Chain event: Job 3 gets funded ──
        sim.fund_job(3)

        # ── Poll 3: Job 3 now FUNDED, picked up via _pending_open_ids ──
        r3 = await ops.get_pending_jobs()
        assert r3["success"] is True
        assert len(r3["jobs"]) == 1
        assert r3["jobs"][0]["jobId"] == 3
        assert 3 not in ops._pending_open_ids  # no longer tracked

        # ── Poll 4: No changes — should be empty ──
        r4 = await ops.get_pending_jobs()
        assert r4["success"] is True
        assert len(r4["jobs"]) == 0

        # ── Chain event: New job created + funded ──
        jid = sim.create_job(status=APEXStatus.FUNDED)  # job 4

        # ── Poll 5: New job discovered via job_counter change ──
        r5 = await ops.get_pending_jobs()
        assert r5["success"] is True
        assert len(r5["jobs"]) == 1
        assert r5["jobs"][0]["jobId"] == jid


class TestScenario_OpenToFundedAcrossPolls:
    """The critical gap: job created OPEN, funded later."""

    @pytest.mark.asyncio
    async def test_open_job_discovered_when_funded(self):
        ops, sim = _make_ops_with_simulator()

        # Job created OPEN
        sim.create_job(status=APEXStatus.OPEN)  # job 1

        # Startup scan: sees OPEN, tracks it
        r1 = await ops.get_pending_jobs()
        assert len(r1["jobs"]) == 0
        assert 1 in ops._pending_open_ids

        # Client funds the job — job_counter does NOT change
        sim.fund_job(1)
        assert sim._job_counter == 1  # unchanged

        # Progressive scan re-checks pending-open IDs
        r2 = await ops.get_pending_jobs()
        assert len(r2["jobs"]) == 1
        assert r2["jobs"][0]["jobId"] == 1
        assert 1 not in ops._pending_open_ids


class TestScenario_OpenJobExpires:
    """OPEN job expires before being funded — should stop tracking."""

    @pytest.mark.asyncio
    async def test_expired_open_job_removed(self):
        ops, sim = _make_ops_with_simulator()

        sim.create_job(status=APEXStatus.OPEN, expired_at=FUTURE_TS)  # job 1

        r1 = await ops.get_pending_jobs()
        assert 1 in ops._pending_open_ids

        # Job expires
        sim.expire_job(1)
        # Also simulate it being marked expired on-chain
        sim.jobs[1]["status"] = APEXStatus.EXPIRED

        r2 = await ops.get_pending_jobs()
        assert len(r2["jobs"]) == 0
        assert 1 not in ops._pending_open_ids  # cleaned up


class TestScenario_MixedNewAndOpenJobs:
    """New jobs + OPEN→FUNDED transitions in the same poll."""

    @pytest.mark.asyncio
    async def test_combined_scan(self):
        ops, sim = _make_ops_with_simulator()

        # Pre-existing: 2 OPEN jobs
        sim.create_job(status=APEXStatus.OPEN)  # job 1
        sim.create_job(status=APEXStatus.OPEN)  # job 2

        # Startup
        r1 = await ops.get_pending_jobs()
        assert len(r1["jobs"]) == 0
        assert ops._pending_open_ids == {1, 2}

        # Between polls: fund job 1, create+fund job 3
        sim.fund_job(1)
        sim.create_job(status=APEXStatus.FUNDED)  # job 3

        # Progressive scan: should find both job 1 (via open tracking)
        # and job 3 (via new job ID range)
        r2 = await ops.get_pending_jobs()
        assert r2["success"] is True
        found_ids = {j["jobId"] for j in r2["jobs"]}
        assert found_ids == {1, 3}
        assert 1 not in ops._pending_open_ids  # now funded
        assert 2 in ops._pending_open_ids       # still open


class TestScenario_NoNewJobsNoPendingOpen:
    """Completely quiet chain — zero RPC calls beyond job_counter."""

    @pytest.mark.asyncio
    async def test_zero_multicall_calls(self):
        ops, sim = _make_ops_with_simulator()

        # Empty chain
        r1 = await ops.get_pending_jobs()
        assert r1["jobs"] == []
        assert ops._startup_scan_done is True

        # Progressive: no new jobs, no pending-open
        r2 = await ops.get_pending_jobs()
        assert r2["jobs"] == []
        sim.client.get_jobs_batch.assert_not_called()  # startup used it, but progressive didn't

        # Verify: job_counter was called (1 cheap eth_call)
        assert sim.client.job_counter.call_count >= 1


class TestScenario_OtherProviderOpenJobIgnored:
    """OPEN jobs for other providers should NOT be tracked."""

    @pytest.mark.asyncio
    async def test_other_provider_not_tracked(self):
        ops, sim = _make_ops_with_simulator()

        sim.create_job(provider=OTHER_PROVIDER, status=APEXStatus.OPEN)

        r1 = await ops.get_pending_jobs()
        assert len(r1["jobs"]) == 0
        assert ops._pending_open_ids == set()  # not tracked


class TestScenario_ProgressiveScanError:
    """Error during progressive scan should not corrupt state."""

    @pytest.mark.asyncio
    async def test_error_preserves_state(self):
        ops, sim = _make_ops_with_simulator()

        sim.create_job(status=APEXStatus.OPEN)  # job 1

        # Startup succeeds
        await ops.get_pending_jobs()
        assert ops._last_known_counter == 1
        assert 1 in ops._pending_open_ids

        # job_counter fails on next poll
        sim.client.job_counter.side_effect = Exception("RPC timeout")

        r2 = await ops.get_pending_jobs()
        assert r2["success"] is False
        # State should be unchanged — next successful poll retries
        assert ops._last_known_counter == 1
        assert 1 in ops._pending_open_ids

        # RPC recovers
        sim.client.job_counter.side_effect = lambda: sim._job_counter
        sim.fund_job(1)

        r3 = await ops.get_pending_jobs()
        assert len(r3["jobs"]) == 1
        assert r3["jobs"][0]["jobId"] == 1


class TestScenario_DuplicateIdsInScan:
    """If a pending-open ID is also in the new range, don't scan it twice."""

    @pytest.mark.asyncio
    async def test_no_duplicate_ids(self):
        ops, sim = _make_ops_with_simulator()

        # Manually set up state as if startup already ran
        sim.create_job(status=APEXStatus.OPEN)  # job 1
        ops._startup_scan_done = True
        ops._last_known_counter = 0  # pretend we haven't seen job 1 yet
        ops._pending_open_ids = {1}

        # job_counter = 1 → new range = [1], pending_open = {1}
        # The scan should handle this gracefully (job 1 appears once in batch)
        sim.fund_job(1)

        r = await ops.get_pending_jobs()
        assert r["success"] is True
        assert len(r["jobs"]) == 1
        assert r["jobs"][0]["jobId"] == 1


class TestScenario_LargeNumberOfOpenJobs:
    """Many OPEN jobs — all re-checked, only funded ones returned."""

    @pytest.mark.asyncio
    async def test_many_open_jobs(self):
        ops, sim = _make_ops_with_simulator()

        # Create 20 OPEN jobs (IDs 1..20)
        for _ in range(20):
            sim.create_job(status=APEXStatus.OPEN)

        # Startup
        r1 = await ops.get_pending_jobs()
        assert len(r1["jobs"]) == 0
        assert len(ops._pending_open_ids) == 20

        # Fund 5 of them
        for jid in [4, 8, 12, 16, 20]:
            sim.fund_job(jid)

        # Progressive
        r2 = await ops.get_pending_jobs()
        assert len(r2["jobs"]) == 5
        found_ids = {j["jobId"] for j in r2["jobs"]}
        assert found_ids == {4, 8, 12, 16, 20}
        assert len(ops._pending_open_ids) == 15  # 20 - 5
