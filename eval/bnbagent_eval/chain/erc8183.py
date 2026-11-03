"""ERC-8183 job workflows: provider gate, client and voter advice.

- The suite is pinned at negotiation as one ``success_criteria`` entry
  ``eval_suite:<uri>#<suite_hash>`` (the SDK keeps and signs only deliverables,
  quality_standards and success_criteria). Advisors always evaluate against this negotiated suite.
- Evidence is bound to the job: ``JobSubject(chain, commerce, job, provider, output_hash)``.
- ``EvalGate`` is an ``on_funded`` callback for the SDK's ``funded_job_watcher``: work and
  evaluation run as a background task with persistent per-job state; it returns
  ``{"retry": True}`` while running and ``None`` (fire-once: never resubmit) when done.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..config import EvalConfig
from ..engine.resolve import resolve_suite
from ..engine.targets import register_target, unregister_target
from ..exceptions import AttestationError, ChainIntegrationError, EvalError, SuiteError
from ..hashing import hash_obj
from ..logging_utils import get_logger
from ..metrics import registry
from ..ports import CommercePort, JobView, PolicyPort, SignerPort, StoragePort, SubmitResult
from ..privacy import encrypt_report
from ..types.case import EvalCase
from ..types.options import RunOptions
from ..types.request import EvalRequest, EvalResponse
from ..types.subject import JobSubject
from ..types.suite import Suite
from ..types.target import TargetRef
from ..types.trajectory import Session
from . import attestation, evidence
from .manifest import content_hash

logger = get_logger(__name__)

SUITE_PREFIX = "eval_suite:"


class Evaluator(Protocol):
    async def evaluate(self, request: EvalRequest) -> EvalResponse: ...


# --------------------------------------------------------------------------- suite pinning


def suite_criterion(suite_uri: str, suite_hash: str) -> str:
    """The ``success_criteria`` entry that pins a suite at negotiation."""
    if "#" in suite_uri:
        raise ValueError("suite_uri must not contain '#'")
    return f"{SUITE_PREFIX}{suite_uri}#{suite_hash}"


def parse_suite_criterion(description: str | Mapping[str, Any] | None) -> tuple[str, str] | None:
    """(suite_uri, suite_hash) from a job description's ``terms.success_criteria``."""
    if description is None:
        return None
    if isinstance(description, str):
        try:
            description = json.loads(description)
        except ValueError:
            return None
    terms = description.get("terms") if isinstance(description, Mapping) else None
    criteria = (terms or {}).get("success_criteria") or []
    for item in criteria:
        if isinstance(item, str) and item.startswith(SUITE_PREFIX):
            uri, _, digest = item[len(SUITE_PREFIX) :].rpartition("#")
            if uri and digest.startswith("0x"):
                return uri, digest
    return None


def job_task(job: JobView) -> str | None:
    """The negotiated task text (``description.task``), else the raw description."""
    if not job.description:
        return None
    try:
        doc = json.loads(job.description)
    except ValueError:
        return job.description
    return doc.get("task") if isinstance(doc, Mapping) else job.description


# Metrics a negotiated suite may not use: they call arbitrary local Python.
NEGOTIATED_DENIED_IDS = frozenset({"custom.python"})


def check_negotiated_suite(
    suite: Suite, *, allowed_judge_base_urls: Iterable[str] | None = None
) -> None:
    """A negotiated suite is written by the other party; it runs on this machine with its keys.

    Denied: ``custom.python`` (an arbitrary callable) and a judge ``base_url`` other than the
    locally configured one (``EVAL_JUDGE_BASE_URL``) or ``allowed_judge_base_urls``, which would
    send the local judge API key to a host the other party chose. (``$ref`` is rejected when
    the suite is loaded.)
    """
    for cfg in suite.metrics:
        try:
            canonical = registry.resolve(cfg.id)[0].spec.id
        except KeyError:
            canonical = cfg.id
        if canonical in NEGOTIATED_DENIED_IDS:
            raise ChainIntegrationError(
                f"metric {cfg.id!r} is not allowed in a negotiated suite", code="suite_denied"
            )
    if allowed_judge_base_urls is None:
        local = EvalConfig.from_env().judge_base_url
        allowed_judge_base_urls = (local,) if local else ()
    allowed = {u.rstrip("/") for u in allowed_judge_base_urls}
    judge = suite.judge
    if judge is not None and judge.base_url and judge.base_url.rstrip("/") not in allowed:
        raise ChainIntegrationError(
            "the negotiated suite pins a judge base_url that is not configured locally",
            code="suite_denied",
        )


async def suite_from_job(
    job: JobView,
    storage: StoragePort,
    *,
    allowed_judge_base_urls: Iterable[str] | None = None,
) -> Suite:
    """Fetch the negotiated suite, verify it against the pinned ``suite_hash`` and check it
    with :func:`check_negotiated_suite` (it comes from the other party)."""
    pinned = parse_suite_criterion(job.description)
    if pinned is None:
        raise ChainIntegrationError(
            f"job {job.job_id} has no '{SUITE_PREFIX}' success criterion", code="no_suite"
        )
    uri, expected = pinned
    try:
        suite = Suite.load(await storage.download(uri), refs=False)
    except SuiteError as exc:
        raise ChainIntegrationError(f"suite at {uri}: {exc}", code="suite_denied") from exc
    check_negotiated_suite(suite, allowed_judge_base_urls=allowed_judge_base_urls)
    actual = resolve_suite(suite).suite_hash
    if actual.lower() != expected.lower():
        raise ChainIntegrationError(
            f"suite at {uri} hashes to {actual}, negotiated {expected}", code="suite_hash_mismatch"
        )
    return suite


def job_subject(commerce: CommercePort, job: JobView, content: str) -> JobSubject:
    return JobSubject(
        chain_id=commerce.chain_id,
        commerce=commerce.commerce_address,
        job_id=job.job_id,
        provider=job.provider,
        output_hash=content_hash(content),
    )


# --------------------------------------------------------------------------- gate state


@dataclass(frozen=True)
class GateRecord:
    status: str  # running | retry | resubmit | submitted | abandoned
    attempts: int = 0
    result_hash: str | None = None
    reason: str | None = None
    # status "resubmit": the evaluated deliverable {content, metadata}, resent as it is
    pending: dict[str, Any] | None = None


class MemoryGateState:
    def __init__(self) -> None:
        self._data: dict[int, GateRecord] = {}

    def get(self, job_id: int) -> GateRecord | None:
        return self._data.get(job_id)

    def set(self, job_id: int, record: GateRecord) -> None:
        self._data[job_id] = record


class SqliteGateState:
    """Gate state that survives restarts (no duplicate work or submission)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS gate (job_id INTEGER PRIMARY KEY, v TEXT)")

    def get(self, job_id: int) -> GateRecord | None:
        with self._lock, sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT v FROM gate WHERE job_id = ?", (job_id,)).fetchone()
        return GateRecord(**json.loads(row[0])) if row else None

    def set(self, job_id: int, record: GateRecord) -> None:
        with self._lock, sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO gate (job_id, v) VALUES (?, ?)",
                (job_id, json.dumps(dataclasses.asdict(record))),
            )


WorkFn = Callable[[JobView], Any]
SuiteResolver = Callable[[JobView, StoragePort], Awaitable[Suite]]


def _job_id(job: Any) -> int:
    if isinstance(job, JobView):
        return job.job_id
    if isinstance(job, int) and not isinstance(job, bool):
        return job
    if isinstance(job, Mapping):
        for key in ("job_id", "jobId", "id"):
            if key in job:
                return int(job[key])
    raise ValueError(f"cannot find a job id in {job!r}")


def required_watcher_attempts(gate_attempts: int, work_seconds: float, interval: float) -> int:
    """``max_attempts`` the SDK watcher needs: every poll while a task runs counts as a retry."""
    polls_per_attempt = max(1, int(work_seconds // max(interval, 1e-9)) + 1)
    return gate_attempts * (polls_per_attempt + 1) + 1


class EvalGate:
    """Provider self-check before submit, as a ``funded_job_watcher`` ``on_funded`` callback."""

    def __init__(
        self,
        commerce: CommercePort,
        storage: StoragePort,
        client: Evaluator,
        work: WorkFn,
        *,
        suite_resolver: SuiteResolver = suite_from_job,
        state: MemoryGateState | SqliteGateState | None = None,
        max_attempts: int = 2,
        submit_margin_s: int = 300,
        signer: SignerPort | None = None,
        report_storage: StoragePort | None = None,
        evidence_budget: int = evidence.DEFAULT_BUDGET_BYTES,
        encrypt_to: tuple[str, ...] = (),
        runs: int = 1,
        clock=time.time,
    ):
        if runs < 1:
            raise ValueError("runs must be >= 1")
        self.runs = runs  # work() calls per attempt; the first deliverable is the one submitted
        self.encrypt_to = tuple(encrypt_to)  # X25519 public keys: reports are encrypted
        self.commerce = commerce
        self.storage = storage
        self.client = client
        self.work = work
        self.suite_resolver = suite_resolver
        self.state = state or MemoryGateState()
        self.max_attempts = max_attempts
        self.submit_margin_s = submit_margin_s
        self.signer = signer
        self.report_storage = report_storage
        self.evidence_budget = evidence_budget
        self.clock = clock
        self._tasks: dict[int, asyncio.Task] = {}
        self.last_response: dict[int, EvalResponse] = {}
        if hasattr(inspect, "markcoroutinefunction"):  # Python 3.12+
            # The SDK watcher awaits on_funded only if inspect.iscoroutinefunction(on_funded);
            # otherwise it runs it in a thread and drops the coroutine. Pass gate.__call__ (or
            # use integrations.bnbagent.watch) on older Pythons.
            inspect.markcoroutinefunction(self)

    async def __call__(self, job: Any) -> dict | None:
        job_id = _job_id(job)
        record = self.state.get(job_id)
        if record and record.status in ("submitted", "abandoned"):
            return None
        task = self._tasks.get(job_id)
        if task is not None:
            if not task.done():
                return {"retry": True}
            self._tasks.pop(job_id)
            record = self.state.get(job_id)
            if record and record.status in ("submitted", "abandoned"):
                return None
        view = await self.commerce.get_job(job_id)
        if view.status != "funded":
            return self._abandon(job_id, record, f"job is {view.status}")
        if view.expired_at and self.clock() > view.expired_at - self.submit_margin_s:
            return self._abandon(job_id, record, "deadline: too close to job expiry")
        # A "running" record without a task means a previous process died mid-attempt; that
        # attempt still counts.
        attempts = record.attempts if record else 0
        if record and record.status == "resubmit" and record.pending:
            # Evaluated already: resend the same deliverable, never redo the work.
            self.state.set(job_id, dataclasses.replace(record, status="running"))
            self._tasks[job_id] = asyncio.create_task(
                self._send(
                    view,
                    attempts,
                    record.pending["content"],
                    record.pending["metadata"],
                    record.result_hash,
                )
            )
            return {"retry": True}
        if attempts >= self.max_attempts:
            return self._abandon(job_id, record, f"gave up after {attempts} attempt(s)")
        self.state.set(job_id, GateRecord(status="running", attempts=attempts + 1))
        self._tasks[job_id] = asyncio.create_task(self._attempt(view, attempts + 1))
        return {"retry": True}

    def _abandon(self, job_id: int, record: GateRecord | None, reason: str) -> None:
        self.state.set(
            job_id,
            GateRecord(
                status="abandoned", attempts=record.attempts if record else 0, reason=reason
            ),
        )
        logger.info("[EvalGate] job %s abandoned: %s", job_id, reason)
        return None

    async def _run_work(self, view: JobView) -> tuple[str, Session | None, dict[str, Any]]:
        out = (
            self.work(view)
            if inspect.iscoroutinefunction(self.work)
            else await asyncio.to_thread(self.work, view)
        )
        if inspect.isawaitable(out):
            out = await out
        if isinstance(out, str):
            return out, None, {}
        if isinstance(out, Mapping) and isinstance(out.get("content"), str):
            trajectory = out.get("trajectory")
            if isinstance(trajectory, Mapping):
                trajectory = Session.from_dict(trajectory)
            return out["content"], trajectory, dict(out.get("metadata") or {})
        raise TypeError("work() must return a string or {'content': str, ...}")

    async def _attempt(self, view: JobView, attempt: int) -> None:
        job_id = view.job_id
        try:
            suite = await self.suite_resolver(view, self.storage)
        except Exception as exc:  # noqa: BLE001 - a bad suite abandons, a transient failure retries
            if isinstance(exc, EvalError) and not getattr(exc, "retryable", False):
                self._abandon(job_id, GateRecord("running", attempt), f"suite: {exc}")
            else:
                logger.warning("[EvalGate] job %s suite fetch failed: %s", job_id, exc)
                self.state.set(
                    job_id,
                    GateRecord("retry", attempt, reason=f"suite: {type(exc).__name__}: {exc}"),
                )
            return
        replay = None
        try:
            samples = [await self._run_work(view) for _ in range(self.runs)]
            content, trajectory, extra = samples[0]
            deadline = (view.expired_at - self.submit_margin_s) if view.expired_at else None
            case = EvalCase(
                id=f"job-{job_id}", input=job_task(view), output=content, trajectory=trajectory
            )
            target = None
            if self.runs > 1:
                # The runs are already collected: replay them as the request's target so
                # ops.consistency / ops.reliability score them (local evaluators only).
                replay = f"evalgate-replay-{id(self):x}-{job_id}-{attempt}"
                pending = iter(samples)

                def _next(_input: Any) -> dict[str, Any]:
                    text, traj, _ = next(pending)
                    return {"output": text, "trajectory": traj}

                register_target(replay, _next)
                target = TargetRef(kind="callable", uri=replay)
                case = dataclasses.replace(case, output=None, trajectory=None)
            request = EvalRequest(
                stage="pre_submit",
                suite=suite,
                cases=(case,),
                subject=job_subject(self.commerce, view, content),
                target=target,
                options=RunOptions(deadline=deadline, runs=self.runs),
            )
            response = await self.client.evaluate(request)
            if self.signer is not None:
                response = attestation.sign(response, self.signer)
            self.last_response[job_id] = response
        except Exception as exc:  # noqa: BLE001 - a failed attempt is retried (or abandoned)
            logger.warning("[EvalGate] job %s attempt %s failed: %s", job_id, attempt, exc)
            self.state.set(
                job_id, GateRecord("retry", attempt, reason=f"{type(exc).__name__}: {exc}")
            )
            return
        finally:
            if replay is not None:
                unregister_target(replay)
        action = response.verdict.action
        if action == "submit":
            try:
                await self._submit(view, attempt, content, extra, response)
            except Exception as exc:  # noqa: BLE001 - report upload / evidence build: retried
                logger.warning("[EvalGate] job %s evidence failed: %s", job_id, exc)
                self.state.set(
                    job_id,
                    GateRecord(
                        "retry",
                        attempt,
                        response.result_hash,
                        f"evidence: {type(exc).__name__}: {exc}",
                    ),
                )
        elif action == "decline":
            self.state.set(
                job_id,
                GateRecord(
                    "abandoned",
                    attempt,
                    response.result_hash,
                    "declined: " + "; ".join(response.verdict.reasons),
                ),
            )
        else:
            self.state.set(
                job_id,
                GateRecord(
                    "retry",
                    attempt,
                    response.result_hash,
                    "; ".join(response.verdict.reasons) or action,
                ),
            )

    async def _submit(
        self,
        view: JobView,
        attempt: int,
        content: str,
        extra: dict[str, Any],
        response: EvalResponse,
    ) -> None:
        report_uri = report_hash = None
        if self.report_storage is not None:
            report = response.to_dict()
            if self.encrypt_to:
                report = encrypt_report(report, self.encrypt_to)
            report_hash = hash_obj(report)
            report_uri = await self.report_storage.upload(report, f"job-{view.job_id}-eval.json")
            if report_uri.startswith("file://"):
                # Privacy: a local path is unreachable for clients and voters.
                logger.warning(
                    "[EvalGate] job %s: report stored at a file:// URI; clients and voters "
                    "cannot fetch it (use IPFS or HTTPS storage)",
                    view.job_id,
                )
        ev = evidence.build(
            response,
            report_uri=report_uri,
            report_hash=report_hash,
            budget_bytes=self.evidence_budget,
        )
        await self._send(view, attempt, content, {**extra, "eval": ev}, response.result_hash)

    async def _send(
        self, view: JobView, attempt: int, content: str, metadata: dict[str, Any], result_hash: str
    ) -> None:
        try:
            result = await self.commerce.submit_result(view.job_id, content, metadata)
        except Exception as exc:  # noqa: BLE001 - treated as a retryable submit failure
            logger.warning("[EvalGate] job %s submit failed: %s", view.job_id, exc)
            result = SubmitResult(success=False, error_code=type(exc).__name__, retryable=True)
        pending = None
        if result.success:
            status, reason = "submitted", None
        elif result.retryable:
            # The deliverable is already evaluated: resend it, do not redo the work.
            status, reason = "resubmit", f"submit failed: {result.error_code}"
            pending = {"content": content, "metadata": metadata}
        else:
            status, reason = "abandoned", f"submit failed: {result.error_code}"
        self.state.set(view.job_id, GateRecord(status, attempt, result_hash, reason, pending))

    async def wait(self) -> None:
        """Wait for running attempts (tests / graceful shutdown)."""
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)


# --------------------------------------------------------------------------- advisors


@dataclass(frozen=True)
class Advice:
    action: str  # dispute | no_action | vote_reject | abstain | too_late | not_submitted | ...
    reasons: tuple[str, ...] = ()
    deadline: int | None = None
    response: EvalResponse | None = None
    provider_evidence: Mapping[str, Any] = field(default_factory=dict)


def check_provider_evidence(
    ev: Mapping[str, Any] | None,
    *,
    negotiated_suite_hash: str,
    subject: JobSubject,
    now: float | None = None,
) -> dict[str, Any]:
    """Check the provider's evidence: present, negotiated suite, binding, attestation."""
    if not ev:
        return {"present": False}
    out: dict[str, Any] = {
        "present": True,
        "claimed_status": (ev.get("verdict") or {}).get("status"),
        "claimed_score": (ev.get("aggregate") or {}).get("score"),
    }
    out["suite_match"] = str(ev.get("suite_hash", "")).lower() == negotiated_suite_hash.lower()
    claimed = ev.get("subject") or {}
    expected = subject.to_dict()
    mismatched = sorted(
        k for k in expected if str(claimed.get(k, "")).lower() != str(expected[k]).lower()
    )
    out["binding_ok"] = not mismatched
    if mismatched:
        out["binding_mismatch"] = mismatched
    try:
        out["evaluator"] = attestation.verify_evidence(ev, now=now)
        out["attestation_ok"] = True
    except (AttestationError, KeyError, ValueError, TypeError, ImportError) as exc:
        out["attestation_ok"] = False
        out["attestation_error"] = str(exc)
    else:
        # The provider signing its own evidence is self-grading (allowed, but say so).
        out["self_attested"] = str(out["evaluator"]).lower() == str(subject.provider).lower()
    summary = ev.get("voter_summary") or {}
    # The summary text is not signed: it must agree with the signed status.
    out["summary_consistent"] = not summary or summary.get("status") == out["claimed_status"]
    return out


class _Advisor:
    def __init__(
        self,
        commerce: CommercePort,
        policy: PolicyPort,
        storage: StoragePort,
        client: Evaluator,
        *,
        clock=time.time,
    ):
        self.commerce = commerce
        self.policy = policy
        self.storage = storage
        self.client = client
        self.clock = clock

    async def _evaluate(
        self, stage: str, job: JobView, content: str, suite: Suite
    ) -> EvalResponse:
        request = EvalRequest(
            stage=stage,
            suite=suite,
            cases=(EvalCase(id=f"job-{job.job_id}", input=job_task(job), output=content),),
            subject=job_subject(self.commerce, job, content),
        )
        return await self.client.evaluate(request)


class DisputeAdvisor(_Advisor):
    """Client side: should I dispute this submitted job?"""

    async def advise(self, job_id: int) -> Advice:
        job = await self.commerce.get_job(job_id)
        if job.status != "submitted":
            return Advice("not_submitted", (f"job is {job.status}",))
        deadline = (job.submitted_at or 0) + await self.policy.dispute_window()
        if job.submitted_at is not None and self.clock() > deadline:
            return Advice("too_late", ("the dispute window has closed",), deadline)
        deliverable = await self.commerce.get_deliverable(job_id)
        if deliverable is None:
            return Advice("dispute", ("no retrievable deliverable",), deadline)
        if not deliverable.verified:
            return Advice(
                "dispute",
                ("tampered_deliverable: manifest hash does not match the on-chain deliverable",),
                deadline,
            )
        suite = await suite_from_job(job, self.storage)
        response = await self._evaluate("review", job, deliverable.content, suite)
        check = check_provider_evidence(
            deliverable.metadata.get("eval"),
            negotiated_suite_hash=response.suite_hash,
            subject=job_subject(self.commerce, job, deliverable.content),
            now=self.clock(),
        )
        return Advice(
            response.verdict.action or "no_action",
            response.verdict.reasons,
            deadline,
            response,
            check,
        )


class VoterAssistant(_Advisor):
    """Voter side: re-evaluate a disputed job with the negotiated suite and a live judge."""

    def __init__(
        self,
        commerce: CommercePort,
        policy: PolicyPort,
        storage: StoragePort,
        client: Evaluator,
        signer: SignerPort,
        *,
        clock=time.time,
    ):
        super().__init__(commerce, policy, storage, client, clock=clock)
        self.signer = signer

    async def review(self, job_id: int) -> Advice:
        me = self.signer.address
        reasons = []
        if not await self.policy.is_disputed(job_id):
            reasons.append("job is not disputed")
        if not await self.policy.is_voter(me):
            reasons.append("signer is not a whitelisted voter")
        if await self.policy.has_voted(job_id, me):
            reasons.append("already voted")
        if reasons:
            return Advice("not_eligible", tuple(reasons))
        job = await self.commerce.get_job(job_id)
        deadline = await self._vote_deadline(job)
        if deadline is not None and self.clock() > deadline:
            return Advice("too_late", ("the vote window has closed",), deadline)
        deliverable = await self.commerce.get_deliverable(job_id)
        if deliverable is None or not deliverable.verified:
            return Advice("vote_reject", ("tampered or missing deliverable",), deadline)
        suite = await suite_from_job(job, self.storage)
        response = await self._evaluate("vote", job, deliverable.content, suite)
        check = check_provider_evidence(
            deliverable.metadata.get("eval"),
            negotiated_suite_hash=response.suite_hash,
            subject=job_subject(self.commerce, job, deliverable.content),
            now=self.clock(),
        )
        flags = []
        if check.get("present"):
            if not check.get("suite_match"):
                flags.append("provider evidence uses a different suite than negotiated")
            if not check.get("binding_ok"):
                flags.append("provider evidence is bound to a different job or output")
            if not check.get("attestation_ok"):
                flags.append("provider attestation is invalid")
            if not check.get("summary_consistent", True):
                flags.append("provider evidence summary contradicts its signed verdict")
        return Advice(
            response.verdict.action or "abstain",
            (*response.verdict.reasons, *flags),
            deadline,
            response,
            check,
        )

    async def _vote_deadline(self, job: JobView) -> int | None:
        """The latest time a vote can count: ``submitted_at + dispute_window + vote_window``.

        ``PolicyPort`` exposes no dispute timestamp, and a dispute is raised within the
        dispute window, so this is an upper bound (``None`` when the policy has no vote
        window or the job has no submission time).
        """
        window = await self.policy.vote_window()
        if window is None or job.submitted_at is None:
            return None
        return job.submitted_at + await self.policy.dispute_window() + window
