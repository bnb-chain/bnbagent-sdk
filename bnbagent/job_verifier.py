"""
JobVerifier — validates job state for agent middleware.

Encapsulates the common job verification logic:
- Parse agent routes from string format
- Verify job exists on-chain and is in a processable phase
- Check agentId matches the expected agent for the route
- Check deadline hasn't expired

On-chain job phase is the source of truth for replay protection:
- PaymentLocked or InProgress → processable
- Asserting, Completed, Cancelled, etc. → already processed

Example:
    verifier = JobVerifier(
        apex_client=client,
        agent_routes="blockchain-news:42,translation:67|68"
    )

    result = verifier.verify(job_id=123, route_key="blockchain-news")
    if not result.valid:
        return {"error": result.error}, result.error_code
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, Set, Any

from .apex_client import ApexClient, JobPhase

logger = logging.getLogger(__name__)


@dataclass
class JobVerificationResult:
    """Result of job verification."""

    valid: bool
    job: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_code: int = 200

    @property
    def phase(self) -> Optional[JobPhase]:
        """Get the job phase if job exists."""
        if self.job:
            return self.job.get("phase")
        return None

    @property
    def agent_id(self) -> Optional[int]:
        """Get the agentId if job exists."""
        if self.job:
            return self.job.get("agentId")
        return None

    @property
    def needs_accept(self) -> bool:
        """Whether the job needs to be accepted (PaymentLocked phase)."""
        return self.valid and self.phase == JobPhase.PAYMENT_LOCKED


def parse_agent_routes(routes_str: str) -> Dict[str, Set[int]]:
    """
    Parse agent routes from string format.

    Format: path_key:id1|id2,path_key2:id3
    Example: "google_a2a:42|67,blockchain-news:103"

    Returns:
        Dict mapping path keys to sets of allowed agent IDs.
        Empty dict if input is empty/invalid.
    """
    routes: Dict[str, Set[int]] = {}
    if not routes_str:
        return routes

    for pair in routes_str.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        path_key, ids_str = pair.split(":", 1)
        ids: Set[int] = set()
        for x in ids_str.split("|"):
            x = x.strip()
            if x:
                try:
                    ids.add(int(x))
                except ValueError:
                    pass
        if ids:
            routes[path_key.strip()] = ids

    return routes


class JobVerifier:
    """
    Verifies job state for agent request handling.

    Encapsulates the common verification logic used by job verification middleware:
    - Check job exists on-chain
    - Verify phase (PaymentLocked or InProgress)
    - Match agentId against expected agents for the route
    - Check submitDeadline hasn't expired
    - Track used jobs for replay protection
    """

    def __init__(
        self,
        apex_client: ApexClient,
        agent_routes: str | Dict[str, Set[int]],
        replay_protection_file: Optional[str] = None,
    ):
        """
        Initialize the job verifier.

        Args:
            apex_client: ApexClient instance for chain queries
            agent_routes: Either a string in format "path_key:id1|id2,path_key2:id3"
                          or a pre-parsed dict mapping path keys to agent ID sets
            replay_protection_file: Deprecated, ignored. On-chain phase is used instead.
        """
        self._client = apex_client

        if isinstance(agent_routes, str):
            self._routes = parse_agent_routes(agent_routes)
        else:
            self._routes = agent_routes

        # Deprecated: file-based replay protection removed
        # On-chain job phase is the source of truth for job state
        self._replay_file = None
        self._used_jobs: Set[str] = set()

        logger.info(
            f"[JobVerifier] Initialized with routes: "
            f"{{{', '.join(f'{k}: {sorted(v)}' for k, v in self._routes.items())}}}"
        )

    # Deprecated methods removed - on-chain phase is the source of truth

    def get_expected_agent_ids(self, route_key: str) -> Optional[Set[int]]:
        """
        Get the expected agent IDs for a route key.

        Args:
            route_key: The route key to look up (e.g., "blockchain-news")

        Returns:
            Set of expected agent IDs, or None if route not found
        """
        route_key_lower = route_key.lower()
        for key, agent_ids in self._routes.items():
            if key.lower() in route_key_lower or route_key_lower in key.lower():
                return agent_ids
        return None

    def match_route(self, request_path: str) -> Optional[Set[int]]:
        """
        Find matching agent IDs for a request path.

        Args:
            request_path: The full request path (e.g., "/agents/blockchain-news/task")

        Returns:
            Set of expected agent IDs, or None if no route matches
        """
        path_lower = request_path.lower()
        for route_key, agent_ids in self._routes.items():
            if route_key.lower() in path_lower:
                return agent_ids
        return None

    def verify(
        self,
        job_id: int,
        expected_agent_ids: Optional[Set[int]] = None,
        route_key: Optional[str] = None,
        request_path: Optional[str] = None,
        check_deadline: bool = True,
        check_replay: bool = True,
    ) -> JobVerificationResult:
        """
        Verify a job is valid for processing.

        Uses on-chain job phase as the source of truth for job state.
        A job is processable if phase is PaymentLocked or InProgress.

        Args:
            job_id: The job ID to verify
            expected_agent_ids: Explicit set of allowed agent IDs
            route_key: Route key to look up allowed agent IDs
            request_path: Request path to match against routes
            check_deadline: Whether to check submitDeadline
            check_replay: Deprecated, ignored. On-chain phase is used instead.

        Returns:
            JobVerificationResult with valid flag and job data or error
        """
        if expected_agent_ids is None:
            if route_key:
                expected_agent_ids = self.get_expected_agent_ids(route_key)
            elif request_path:
                expected_agent_ids = self.match_route(request_path)

        if expected_agent_ids is None:
            return JobVerificationResult(
                valid=False,
                error="No matching route found for this request",
                error_code=402,
            )

        try:
            job = self._client.get_job(job_id)
        except Exception as e:
            error_msg = str(e)
            if "JobNotFound" in error_msg or "revert" in error_msg.lower():
                return JobVerificationResult(
                    valid=False,
                    error=f"Job {job_id} not found on-chain",
                    error_code=404,
                )
            return JobVerificationResult(
                valid=False,
                error=f"Failed to verify job on-chain: {error_msg}",
                error_code=502,
            )

        agent_id = job["agentId"]
        phase = job["phase"]
        submit_deadline = job.get("submitDeadline", 0)

        if agent_id not in expected_agent_ids:
            return JobVerificationResult(
                valid=False,
                job=job,
                error=f"Job {job_id} is for agent {agent_id}, but this endpoint accepts agents {sorted(expected_agent_ids)}",
                error_code=403,
            )

        # On-chain phase check: only PaymentLocked or InProgress are processable
        if phase == JobPhase.ASSERTING:
            return JobVerificationResult(
                valid=False,
                job=job,
                error=f"Job {job_id} already submitted (phase: Asserting). Waiting for settlement.",
                error_code=409,
            )
        elif phase == JobPhase.COMPLETED:
            return JobVerificationResult(
                valid=False,
                job=job,
                error=f"Job {job_id} already completed and settled.",
                error_code=409,
            )
        elif phase == JobPhase.CANCELLED:
            return JobVerificationResult(
                valid=False,
                job=job,
                error=f"Job {job_id} was cancelled.",
                error_code=410,
            )
        elif phase == JobPhase.REFUNDED:
            return JobVerificationResult(
                valid=False,
                job=job,
                error=f"Job {job_id} was refunded.",
                error_code=410,
            )
        elif phase == JobPhase.DISPUTED:
            return JobVerificationResult(
                valid=False,
                job=job,
                error=f"Job {job_id} is under dispute.",
                error_code=409,
            )
        elif phase not in (JobPhase.PAYMENT_LOCKED, JobPhase.IN_PROGRESS):
            return JobVerificationResult(
                valid=False,
                job=job,
                error=f"Job {job_id} phase is {phase.name}, cannot process.",
                error_code=409,
            )

        if check_deadline and submit_deadline > 0:
            now = int(time.time())
            if phase == JobPhase.IN_PROGRESS and now > submit_deadline:
                return JobVerificationResult(
                    valid=False,
                    job=job,
                    error=f"Job {job_id} submit deadline has passed ({submit_deadline} < {now})",
                    error_code=408,
                )

        return JobVerificationResult(valid=True, job=job)

    def mark_used(self, job_id: int) -> None:
        """
        Deprecated: No-op. On-chain job phase is used for replay protection.
        
        The job's on-chain phase (Asserting, Completed, etc.) indicates
        whether it has been processed. No need for local tracking.
        """
        pass

    def is_used(self, job_id: int) -> bool:
        """
        Deprecated: Always returns False. Use on-chain job phase instead.
        """
        return False

    def accept_job(self, job_id: int) -> Dict[str, Any]:
        """
        Accept a job on-chain (PaymentLocked -> InProgress).

        Convenience method wrapping apex_client.accept_job.
        """
        return self._client.accept_job(job_id)

    @property
    def routes(self) -> Dict[str, Set[int]]:
        """Get the parsed agent routes."""
        return self._routes
