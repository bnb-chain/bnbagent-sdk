"""The Runner: resolve → targets → score → aggregate → verdict → seal."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Callable, Sequence

from .. import privacy
from .._version import __version__
from ..config import EvalConfig
from ..exceptions import RequestError, SuiteError
from ..hashing import hash_obj
from ..judges import cache as judge_cache
from ..judges.base import JudgeModel
from ..judges.cache import JudgeCache
from ..judges.providers import create_judge
from ..logging_utils import get_logger
from ..metrics.base import MetricContext
from ..types.case import EvalCase
from ..types.judge import JudgeConfig
from ..types.request import EvalRequest, EvalResponse, Provenance
from ..types.result import Aggregate, CaseResult, MetricResult, RunResult, Verdict
from ..types.suite import Suite
from . import aggregate as agg
from .budget import BudgetTracker
from .dataset import load_dataset_ref
from .resolve import PolicyCheck, ResolvedMetric, ResolvedSuite, resolve_suite
from .seal import seal_hashes
from .targets import from_ref, resolve_agent_target

logger = get_logger(__name__)


class Runner:
    def __init__(
        self,
        config: EvalConfig | None = None,
        *,
        judge: JudgeModel | None = None,
        policy: PolicyCheck | None = None,
        reader: object | None = None,
        storage: object | None = None,
        registry: object | None = None,
        cache: JudgeCache | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.config = config or EvalConfig()
        self.judge = judge
        self.policy = policy
        self.reader = reader
        self.storage = storage
        self.registry = registry  # RegistryPort, for erc8004 targets
        self.clock = clock
        self.cache = cache if cache is not None else judge_cache.from_url(self.config.judge_cache)

    # ------------------------------------------------------------------ public
    async def run(self, request: EvalRequest) -> EvalResponse:
        started = self.clock()
        resolved = await self._resolve(request)
        judge_cfg, judge = self._select_judge(request, resolved)
        cases = self._cases(request)
        budget = BudgetTracker(request.options.budget)
        limit = request.options.max_concurrency or self.config.max_concurrency
        semaphore = asyncio.Semaphore(limit)

        # 1. Targets: every case's runs (the first run is the one metrics score).
        runs_by_case = await self._invoke_targets(request, cases, semaphore)
        runs = [runs_by_case.get(c.id, (c,)) for c in cases]

        # 2. Score every metric × case under the concurrency limit.
        async def score(case_runs: tuple[EvalCase, ...], rm: ResolvedMetric) -> MetricResult:
            case = case_runs[0]
            async with semaphore:
                ctx = MetricContext(
                    config=rm.config,
                    method=rm.method,
                    params=rm.config.params,
                    options=request.options,
                    judge=judge,
                    budget=budget,
                    deadline=request.options.deadline,
                    language=case.language or request.options.language,
                    reader=self.reader,
                    storage=self.storage,
                    cache=self.cache,
                    stage=request.stage,
                    runs=case_runs,
                    eval_config=self.config,
                    clock=self.clock,
                    logger=logger,
                )
                return await rm.metric.evaluate(case, ctx)

        flat = await asyncio.gather(*(score(r, rm) for r in runs for rm in resolved.metrics))
        width = len(resolved.metrics)

        # 3. Aggregate, decide, seal.
        rule = resolved.suite.pass_rule
        case_results = tuple(
            agg.build_case(
                case.id,
                flat[i * width : (i + 1) * width],
                rule,
                runs=_run_results(runs_by_case.get(case.id, ())),
            )
            for i, case in enumerate(cases)
        )
        case_results = privacy.apply(case_results, request.options.privacy)
        aggregate = agg.aggregate(case_results)
        verdict = agg.decide(aggregate, case_results, rule, request.stage)
        return self._seal(
            request, resolved, judge_cfg, case_results, aggregate, verdict, budget, started
        )

    # ----------------------------------------------------------------- helpers
    async def _resolve(self, request: EvalRequest) -> ResolvedSuite:
        if request.suite is not None:
            if request.suite_ref:
                raise RequestError("give either suite or suite_ref, not both")
            return resolve_suite(request.suite, policy=self.policy)
        if not request.suite_ref:
            raise RequestError("a suite is required")
        return await self._resolve_ref(request.suite_ref)

    async def _resolve_ref(self, ref: str) -> ResolvedSuite:
        """``suite_ref`` = ``<uri>[#<suite_hash>]``: downloaded through ``ports.storage``
        (``$ref`` is rejected) and, with a fragment, checked against the resolved suite_hash."""
        if self.storage is None:
            raise RequestError("suite_ref needs a storage port (EvalClient(ports=...storage))")
        uri, _, expected = ref.partition("#")
        try:
            document = await self.storage.download(uri)
        except Exception as exc:  # noqa: BLE001 - any storage failure is a bad reference
            raise RequestError(f"cannot download suite_ref {uri}: {exc}") from exc
        suite = Suite.load(document, refs=False)
        resolved = resolve_suite(suite, policy=self.policy)
        if expected and resolved.suite_hash.lower() != expected.lower():
            raise SuiteError(
                f"suite at {uri} hashes to {resolved.suite_hash}, suite_ref pins {expected}"
            )
        return resolved

    def _cases(self, request: EvalRequest) -> Sequence[EvalCase]:
        if request.benchmark_ref:
            raise RequestError(
                "benchmark_ref is not supported by the local engine: "
                "pass cases or a local dataset_ref"
            )
        if request.cases and request.dataset_ref:
            raise RequestError("give either cases or dataset_ref, not both")
        if request.options.runs > 1 and request.target is None:
            raise RequestError("options.runs > 1 requires a target")
        cases = load_dataset_ref(request.dataset_ref) if request.dataset_ref else request.cases
        if not cases:
            raise RequestError("no cases to evaluate")
        ids = [c.id for c in cases]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise RequestError(f"duplicate case ids: {dupes}")
        return cases

    async def _invoke_targets(
        self, request: EvalRequest, cases: Sequence[EvalCase], semaphore: asyncio.Semaphore
    ) -> dict[str, tuple[EvalCase, ...]]:
        """Invoke the target ``options.runs`` times per case (runs of one case are sequential,
        so measured latencies are not inflated by the case's own concurrency)."""
        if request.target is None:
            return {}
        guarded = None
        if request.target.kind == "erc8004":
            target = await resolve_agent_target(request.target, self.registry)
            # The endpoint comes from the agent's registration file, not from the caller:
            # it goes through the egress guard like every URL the package did not choose.
            if getattr(target, "_client", False) is None:
                guarded = _guarded_client(float(getattr(target, "timeout_s", 60.0)))
                target._client = guarded  # type: ignore[attr-defined]
        else:
            target = from_ref(request.target, user_judge=self._user_judge(request))

        async def runs_for(case: EvalCase) -> tuple[str, tuple[EvalCase, ...]]:
            out = []
            for _ in range(request.options.runs):
                async with semaphore:
                    out.append(await target.invoke(case))
            return case.id, tuple(out)

        try:
            return dict(await asyncio.gather(*(runs_for(c) for c in cases)))
        finally:
            if guarded is not None:
                await guarded.aclose()

    def _user_judge(self, request: EvalRequest) -> JudgeModel | None:
        """The judge playing a simulated user: options.judge_override > client judge > env."""
        if request.target is None or request.target.simulated_user is None:
            return None
        cfg = request.options.judge_override
        if cfg is None and self.judge is not None:
            return self.judge
        cfg = cfg or self.config.judge_config()
        return create_judge(cfg) if cfg is not None else None

    def _select_judge(
        self, request: EvalRequest, resolved: ResolvedSuite
    ) -> tuple[JudgeConfig | None, JudgeModel | None]:
        """Precedence: suite pin > options.judge_override > client judge > env."""
        pinned, override = resolved.suite.judge, request.options.judge_override
        if pinned is not None and override is not None and pinned != override:
            raise SuiteError("the suite pins a judge; options.judge_override is not allowed")
        cfg = pinned or override
        if not resolved.needs_judge:
            return cfg, None
        if cfg is None:
            if self.judge is not None:
                return self.judge.config, self.judge
            cfg = self.config.judge_config()
            if cfg is None:
                raise SuiteError("the suite has judge metrics but no judge is configured")
        if self.judge is not None and self.judge.config == cfg:
            return cfg, self.judge
        return cfg, create_judge(cfg)

    def _seal(
        self,
        request: EvalRequest,
        resolved: ResolvedSuite,
        judge_cfg: JudgeConfig | None,
        case_results: tuple[CaseResult, ...],
        aggregate: Aggregate,
        verdict: Verdict,
        budget: BudgetTracker,
        started: float,
    ) -> EvalResponse:
        provenance = Provenance(
            package_version=__version__,
            suite_hash=resolved.suite_hash,
            request_hash=hash_obj(request.to_dict()),
            started_at=started,
            finished_at=self.clock(),
            metric_versions={k: v.metric_version for k, v in sorted(resolved.lock.items())},
            judge=judge_cfg.to_dict() if judge_cfg else None,
        )
        draft = EvalResponse(
            stage=request.stage,
            suite_hash=resolved.suite_hash,
            results=case_results,
            aggregate=aggregate,
            verdict=verdict,
            provenance=provenance,
            result_hash="",
            response_hash="",
            subject=request.subject,
            cost=budget.cost(),
        )
        result_hash, response_hash = seal_hashes(draft)
        return dataclasses.replace(draft, result_hash=result_hash, response_hash=response_hash)


def _guarded_client(timeout_s: float):
    """An httpx client whose every request (and redirect hop) passes the public-IP guard."""
    import httpx

    from ..net import public_transport

    return httpx.AsyncClient(
        transport=public_transport(), timeout=timeout_s, trust_env=False, follow_redirects=False
    )


def _run_results(runs: Sequence[EvalCase]) -> tuple[RunResult, ...]:
    return tuple(
        RunResult(
            run_index=i,
            output_hash=None if r.output is None else hash_obj(r.to_dict()["output"]),
            app_metrics=r.app_metrics,
            error=r.app_metrics.error if r.app_metrics else None,
        )
        for i, r in enumerate(runs)
    )


async def run(request: EvalRequest, **kwargs) -> EvalResponse:
    return await Runner(**kwargs).run(request)


__all__ = ["Runner", "run"]
