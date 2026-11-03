"""Eval recommendation: which registered metrics a dataset can support.

Ported from AgentSight ``auto_evals``:

- data prerequisites (``AutoEval.check_dataset_prerequisites`` / ``check_trace_prerequisites`` /
  ``check_prerequisites``): dataset validity, trace availability (whitebox evals need a trace
  on every case) and trace quality (the source asks an LLM; here deterministic checks: steps
  present, first user message and final agent response captured, tool calls named, tool
  results present). Captured user message + final response keep blackbox evals possible.
- metric prerequisites (``EvalRecommender._filter_evals_by_available_fields``): a method is
  kept only if its ``required_inputs`` are available, plus our checks for installed extras,
  required params and the data shape (JSON / XML / code outputs).

The source's LLM shortlist and automatic mapper creation are not ported, so no judge is needed;
metrics are ranked deterministically instead.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .._optional import available
from ..adapters.traces.render import session_messages
from ..metrics.base import INPUT_NAMES, Kind, MethodSpec, MetricSpec
from ..metrics.registry import list_specs
from ..types.case import EvalCase, content_text
from ..types.suite import MetricConfig, Suite
from ..types.trajectory import AgentStep, LlmStep, ToolStep

Priority = Literal["high", "medium", "low"]
NEGOTIATED_STAGES = ("registration", "pre_submit", "review", "vote")
WHITEBOX_INPUTS = frozenset({"trajectory", "tx"})
_KIND_ORDER = {Kind.RULE: 0, Kind.JUDGE: 1, Kind.MODEL: 2, Kind.VENDOR: 3}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
# Categories that need a dedicated run rather than a plain dataset.
_RUN_CATEGORIES = {"redteam": "needs a red-team run (generation.redteam)"}


@dataclass(frozen=True)
class Prerequisites:
    """AgentSight ``PrerequisitesCheck`` (deterministic)."""

    blackbox_valid: bool
    whitebox_valid: bool
    has_traces: bool
    case_count: int
    available_inputs: dict[str, int] = field(default_factory=dict)  # input -> cases having it
    trace_quality: dict[str, int] = field(default_factory=dict)  # check -> cases passing it
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "blackbox_valid": self.blackbox_valid,
            "whitebox_valid": self.whitebox_valid,
            "has_traces": self.has_traces,
            "case_count": self.case_count,
            "available_inputs": dict(self.available_inputs),
            "trace_quality": dict(self.trace_quality),
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class RecommendedMetric:
    config: MetricConfig
    kind: str
    priority: Priority
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "kind": self.kind,
            "priority": self.priority,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class SkippedMetric:
    id: str
    reason: str
    missing: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "reason": self.reason, "missing": list(self.missing)}


@dataclass(frozen=True)
class Recommendation:
    prerequisites: Prerequisites
    metrics: tuple[RecommendedMetric, ...]
    skipped: tuple[SkippedMetric, ...]

    @property
    def configs(self) -> list[MetricConfig]:
        return [m.config for m in self.metrics]

    def suite(self, name: str = "recommended", version: str = "1", **kwargs: Any) -> Suite:
        """A suite of the recommended metrics (raises ``ValueError`` if none were kept)."""
        return Suite(name=name, version=version, metrics=tuple(self.configs), **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prerequisites": self.prerequisites.to_dict(),
            "metrics": [m.to_dict() for m in self.metrics],
            "skipped": [s.to_dict() for s in self.skipped],
        }


# --------------------------------------------------------------------------- prerequisites
def _has(case: EvalCase, name: str) -> bool:
    if name == "conversation" and not case.conversation and case.trajectory is not None:
        return len(session_messages(case.trajectory)) > 0
    return case.has(name)


def _trace_checks(case: EvalCase) -> dict[str, bool]:
    session = case.trajectory
    if session is None:
        return {}
    steps = list(session.steps())
    agent = [s for s in steps if isinstance(s, AgentStep)]
    llm = [s for s in steps if isinstance(s, LlmStep)]
    tools = [s for s in steps if isinstance(s, ToolStep)]
    user = any(s.user_prompt for s in agent) or any(s.messages for s in llm)
    user = user or case.input is not None
    return {
        "has_steps": bool(steps),
        "first_user_message": bool(user),
        "final_agent_response": session.final_response() is not None or case.output is not None,
        "tool_calls_named": all(bool(t.call.name) for t in tools),
        "tool_results_present": all(t.result is not None or t.error for t in tools),
    }


def check_prerequisites(
    cases: Sequence[EvalCase], *, traces_available: bool | None = None, runs: int = 1
) -> Prerequisites:
    """Data prerequisites: dataset validity, trace availability and trace quality."""
    issues: list[str] = []
    n = len(cases)
    if n == 0:
        return Prerequisites(False, False, False, 0, issues=("the dataset is empty",))
    ids = [c.id for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        issues.append(f"duplicate case ids: {dupes[:5]}")
    evaluable = [c for c in cases if any(_has(c, k) for k in ("output", "conversation"))]
    evaluable_traces = [c for c in cases if c.trajectory is not None]
    counts = {name: sum(_has(c, name) for c in cases) for name in sorted(INPUT_NAMES - {"runs"})}
    counts["runs"] = n if runs >= 2 else 0
    with_traces = counts["trajectory"]
    has_traces = with_traces > 0 if traces_available is None else traces_available
    quality: dict[str, int] = {}
    whitebox = has_traces and with_traces == n
    if has_traces and with_traces < n:
        issues.append(
            f"only {with_traces}/{n} cases have a trajectory, so whitebox (trace) metrics "
            "cannot be used"
        )
    if traces_available and with_traces == 0:
        issues.append("traces_available=True but no case has a trajectory")
        has_traces = False
    blackbox_from_traces = False
    if with_traces:
        checks = [_trace_checks(c) for c in cases if c.trajectory is not None]
        quality = {k: sum(ch[k] for ch in checks) for k in checks[0]}
        failing = [k for k, v in quality.items() if v < len(checks)]
        if failing:
            issues.append(f"trace quality checks failed on some cases: {failing}")
            whitebox = whitebox and not ({"has_steps", "tool_calls_named"} & set(failing))
        blackbox_from_traces = quality["first_user_message"] == len(checks) == n and quality[
            "final_agent_response"
        ] == len(checks)
    blackbox = len(evaluable) == n or blackbox_from_traces
    if not blackbox:
        missing = n - max(len(evaluable), len(evaluable_traces))
        issues.append(f"{missing}/{n} cases have no output, conversation or usable trace")
    if dupes:
        blackbox = whitebox = False
    return Prerequisites(
        blackbox_valid=blackbox,
        whitebox_valid=whitebox,
        has_traces=has_traces,
        case_count=n,
        available_inputs=counts,
        trace_quality=quality,
        issues=tuple(issues),
    )


# -------------------------------------------------------------------------- metric checks
def _is_json(case: EvalCase) -> bool:
    if "json" in case.output_content_type or isinstance(case.output, dict):
        return True
    text = content_text(case.output) or ""
    try:
        json.loads(text)
    except ValueError:
        return False
    return text.strip()[:1] in ("{", "[")


def _shape_issue(spec: MetricSpec, cases: Sequence[EvalCase]) -> str | None:
    with_output = [c for c in cases if c.output is not None]
    if not with_output:
        return None
    if spec.id in ("format.json_valid", "format.json_schema") and not all(
        _is_json(c) for c in with_output
    ):
        return "outputs are not JSON"
    if spec.id == "format.xml" and not all(
        "xml" in c.output_content_type or (content_text(c.output) or "").lstrip().startswith("<")
        for c in with_output
    ):
        return "outputs are not XML"
    if spec.category == "code" and not all(
        "x-" in c.output_content_type or "code" in c.output_content_type or c.artifacts
        for c in with_output
    ):
        return "outputs are not code (set output_content_type, e.g. text/x-python)"
    return None


def _priority(spec: MetricSpec, method: MethodSpec) -> Priority:
    specific = method.required_inputs - {"input", "output"}
    if specific:
        return "high"
    if spec.category in ("quality", "safety", "format", "text"):
        return "medium"
    return "low"


def _method_issue(
    method: MethodSpec,
    counts: dict[str, int],
    n: int,
    prereq: Prerequisites,
    kinds: set[Kind],
    min_coverage: float,
) -> tuple[str, tuple[str, ...]] | None:
    missing = tuple(
        sorted(name for name in method.required_inputs if counts.get(name, 0) < min_coverage * n)
    )
    if missing:
        detail = ", ".join(f"{m} ({counts.get(m, 0)}/{n} cases)" for m in missing)
        if "runs" in missing:
            detail += "; repeated runs need options.runs >= 2"
        return f"missing inputs: {detail}", missing
    optional = sorted(method.optional_inputs)
    if not method.required_inputs and optional and not any(counts.get(o) for o in optional):
        return f"no data for any of its inputs: {optional}", tuple(optional)
    if method.required_inputs & WHITEBOX_INPUTS and not prereq.whitebox_valid:
        return "whitebox prerequisites not met (traces missing or of low quality)", ("trajectory",)
    if not method.required_inputs & WHITEBOX_INPUTS and not prereq.blackbox_valid:
        return "blackbox prerequisites not met", ("output",)
    if method.kind not in kinds:
        return f"method kind {method.kind.value} not enabled", ()
    if method.extra_module and not available(method.extra_module):
        return f"extra not installed: pip install 'bnbagent-eval[{method.extra or 'all'}]'", (
            method.extra_module,
        )
    return None


def recommend(
    cases: Iterable[EvalCase],
    *,
    traces_available: bool | None = None,
    stage: str | None = None,
    judge: bool = True,
    vendor: bool = False,
    model: bool = False,
    runs: int = 1,
    min_coverage: float = 1.0,
    categories: Iterable[str] | None = None,
    specs: Sequence[MetricSpec] | None = None,
) -> Recommendation:
    """Recommend metrics for ``cases``: a ranked, suite-ready list and why others were skipped.

    ``judge`` / ``vendor`` / ``model`` enable those method kinds (rule methods always are);
    ``min_coverage`` is the share of cases that must have each required input. In negotiated
    stages rule methods are preferred over judge methods (deterministic evidence).
    """
    cases = list(cases)
    if not 0.0 < min_coverage <= 1.0:
        raise ValueError("min_coverage must be within (0, 1]")
    prereq = check_prerequisites(cases, traces_available=traces_available, runs=runs)
    counts, n = prereq.available_inputs, prereq.case_count
    kinds = {Kind.RULE}
    kinds |= {Kind.JUDGE} if judge else set()
    kinds |= {Kind.VENDOR} if vendor else set()
    kinds |= {Kind.MODEL} if model else set()
    wanted = set(categories) if categories is not None else None
    negotiated = stage in NEGOTIATED_STAGES
    picked: list[tuple[tuple, RecommendedMetric]] = []
    skipped: list[SkippedMetric] = []
    for spec in specs if specs is not None else list_specs():
        if wanted is not None and spec.category not in wanted:
            continue
        if spec.category in _RUN_CATEGORIES:
            skipped.append(SkippedMetric(spec.id, _RUN_CATEGORIES[spec.category]))
            continue
        required_params = tuple(spec.params_schema.get("required", ())) or tuple(
            k for alt in spec.params_schema.get("anyOf", ()) for k in alt.get("required", ())
        )
        if required_params or spec.validate_params({}):  # no usable default configuration
            reason = (
                f"needs params: {list(required_params)}"
                if spec.params_schema.get("required")
                else f"needs one of the params: {list(required_params)}"
                if required_params
                else "needs params"
            )
            skipped.append(SkippedMetric(spec.id, reason, required_params))
            continue
        if n and (issue := _shape_issue(spec, cases)):
            skipped.append(SkippedMetric(spec.id, issue))
            continue
        methods = list(spec.methods)
        if negotiated:
            methods.sort(key=lambda m: _KIND_ORDER[m.kind])
        chosen, first_issue = None, None
        for method in methods:
            issue = _method_issue(method, counts, n, prereq, kinds, min_coverage)
            if issue is None:
                chosen = method
                break
            first_issue = first_issue or issue
        if chosen is None:
            reason, missing = first_issue or ("no usable method", ())
            skipped.append(SkippedMetric(spec.id, reason, missing))
            continue
        priority = _priority(spec, chosen)
        reasons = [f"method {chosen.name} ({chosen.kind.value})"]
        if chosen.required_inputs:
            reasons.append(f"inputs available: {sorted(chosen.required_inputs)}")
        # Thresholds are left to the metric defaults; the method only when not the default.
        cfg = MetricConfig(id=spec.id, method=None if chosen is spec.methods[0] else chosen.name)
        rank = (
            _PRIORITY_ORDER[priority],
            _KIND_ORDER[chosen.kind] if negotiated else 0,
            -len(chosen.required_inputs),
            spec.id,
        )
        picked.append((rank, RecommendedMetric(cfg, chosen.kind.value, priority, tuple(reasons))))
    picked.sort(key=lambda p: p[0])
    return Recommendation(
        prerequisites=prereq,
        metrics=tuple(m for _, m in picked),
        skipped=tuple(sorted(skipped, key=lambda s: s.id)),
    )
