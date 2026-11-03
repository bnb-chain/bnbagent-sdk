"""Failure diagnosis: root causes and recommendations for failing cases.

Ported from AgentSight ``evaluators/diagnoser``:

- ``FailureDiagnoser``: two judge stages per failing case. Stage 1 (root cause analysis) returns
  root causes in 7 categories with severity and confidence; causes below ``min_confidence``
  (default 0.5) are dropped and, if none remain, stage 2 is skipped. Stage 2 returns
  recommendations for 7 remediation targets, sorted by priority (critical first).
- ``DiagnosisAggregator``: counts per category / target, severity and priority overviews, and
  summarized actions per target sorted by their highest priority.

Without a judge (or when a judge call fails), :func:`diagnose_rules` gives deterministic hints
from metric error codes and metric categories (ours; the source has no fallback).
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..adapters.traces.render import render_trajectory
from ..exceptions import EvalError, JudgeParseError
from ..hashing import hash_obj
from ..judges.base import JudgeModel, JudgeRequest
from ..judges.fencing import fence, fence_clause, make_nonce
from ..judges.prompts import PromptTemplate
from ..judges.prompts.analysis.diagnosis import RECOMMENDATION_V1, ROOT_CAUSE_V1
from ..judges.structured import REPAIR_INSTRUCTION, parse
from ..types.case import EvalCase, content_text
from ..types.request import EvalResponse
from ..types.result import CaseResult, MetricResult
from ..types.trajectory import AgentStep

__all__ = [
    "CaseDiagnosis",
    "Diagnosis",
    "DiagnosisAggregator",
    "DiagnosisSummary",
    "Recommendation",
    "RemediationTarget",
    "RootCause",
    "RootCauseCategory",
    "Severity",
    "diagnose",
    "diagnose_rules",
    "failing_cases",
]

DEFAULT_MIN_CONFIDENCE = 0.5
TOP_CATEGORIES = 7  # AgentSight keeps the top 7 categories in the summary


class RootCauseCategory(str, enum.Enum):
    PROMPT_ENGINEERING = "prompt_engineering"
    TOOL_USAGE = "tool_usage"
    TOOL_OUTPUT_QUALITY = "tool_output_quality"
    REASONING_PLANNING = "reasoning_planning"
    MODEL_CAPABILITY = "model_capability"
    AGENT_COORDINATION = "agent_coordination"
    OTHER = "other"


class Severity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RemediationTarget(str, enum.Enum):
    SYSTEM_PROMPT = "system_prompt"
    TOOL_DEFINITIONS = "tool_definitions"
    TOOL_RESPONSE = "tool_response"
    MODEL_SELECTION = "model_selection"
    AGENT_ORCHESTRATION = "agent_orchestration"
    NEW_MODULES = "new_modules"
    OTHER = "other"


PRIORITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}


@dataclass(frozen=True)
class RootCause:
    category: RootCauseCategory
    description: str
    confidence: float
    severity: Severity
    evidence: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()  # metric keys this cause explains (rule hints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "description": self.description,
            "confidence": self.confidence,
            "severity": self.severity.value,
            "evidence": list(self.evidence),
            "metrics": list(self.metrics),
        }


@dataclass(frozen=True)
class Recommendation:
    target: RemediationTarget
    action: str
    priority: Severity

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.value,
            "action": self.action,
            "priority": self.priority.value,
        }


@dataclass(frozen=True)
class CaseDiagnosis:
    """AgentSight ``FailureDiagnosis`` for one case (all its failing metrics at once)."""

    case_id: str
    failed_metrics: tuple[str, ...]
    root_causes: tuple[RootCause, ...] = ()
    recommendations: tuple[Recommendation, ...] = ()
    root_cause_thinking: str | None = None
    recommendation_thinking: str | None = None
    source: str = "judge"  # "judge" | "rules"
    error: str | None = None  # why the judge was not used (fallback to rules)
    filtered: int = 0  # root causes dropped below min_confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "failed_metrics": list(self.failed_metrics),
            "root_causes": [r.to_dict() for r in self.root_causes],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "root_cause_thinking": self.root_cause_thinking,
            "recommendation_thinking": self.recommendation_thinking,
            "source": self.source,
            "error": self.error,
            "filtered": self.filtered,
        }


@dataclass(frozen=True)
class SummarizedAction:
    target: RemediationTarget
    summary: str
    occurrence_count: int
    priority: Severity
    sample_actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.value,
            "summary": self.summary,
            "occurrence_count": self.occurrence_count,
            "priority": self.priority.value,
            "sample_actions": list(self.sample_actions),
        }


@dataclass(frozen=True)
class DiagnosisSummary:
    """AgentSight ``DiagnosisAggregatorOutput``."""

    total_samples: int
    samples_with_root_causes: int
    samples_with_recommendations: int
    top_root_cause_categories: tuple[Mapping[str, Any], ...] = ()
    severity_overview: Mapping[str, int] = field(default_factory=dict)
    top_recommendation_targets: tuple[Mapping[str, Any], ...] = ()
    priority_overview: Mapping[str, int] = field(default_factory=dict)
    summarized_actions: tuple[SummarizedAction, ...] = ()

    @property
    def top_causes(self) -> list[str]:
        return [c["category"] for c in self.top_root_cause_categories]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_samples": self.total_samples,
            "samples_with_root_causes": self.samples_with_root_causes,
            "samples_with_recommendations": self.samples_with_recommendations,
            "top_root_cause_categories": [dict(c) for c in self.top_root_cause_categories],
            "severity_overview": dict(self.severity_overview),
            "top_recommendation_targets": [dict(t) for t in self.top_recommendation_targets],
            "priority_overview": dict(self.priority_overview),
            "summarized_actions": [a.to_dict() for a in self.summarized_actions],
        }


@dataclass(frozen=True)
class Diagnosis:
    cases: tuple[CaseDiagnosis, ...]
    summary: DiagnosisSummary
    min_confidence: float = DEFAULT_MIN_CONFIDENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_confidence": self.min_confidence,
            "cases": [c.to_dict() for c in self.cases],
            "summary": self.summary.to_dict(),
        }


# ------------------------------------------------------------------------------- aggregation
_TARGET_SUMMARIES = {
    RemediationTarget.SYSTEM_PROMPT: (
        "Review and improve system prompts ({n} issues identified). Focus on adding clearer "
        "constraints, better task definitions, and explicit output format specifications."
    ),
    RemediationTarget.TOOL_DEFINITIONS: (
        "Update tool definitions and schemas ({n} issues identified). Improve tool "
        "descriptions, parameter validation, and usage guidance."
    ),
    RemediationTarget.TOOL_RESPONSE: (
        "Fix tool response quality ({n} issues identified). Ensure tools return correct, "
        "complete, and properly formatted data."
    ),
    RemediationTarget.MODEL_SELECTION: (
        "Consider model upgrades ({n} issues identified). Evaluate switching to more capable "
        "models for complex reasoning tasks."
    ),
    RemediationTarget.AGENT_ORCHESTRATION: (
        "Improve agent orchestration ({n} issues identified). Add verification steps, better "
        "routing logic, and error handling."
    ),
    RemediationTarget.OTHER: (
        "Address miscellaneous issues ({n} issues identified). Review specific recommendations "
        "for details."
    ),
}


class DiagnosisAggregator:
    """Collects case diagnoses and summarizes them (AgentSight ``DiagnosisAggregator``)."""

    def __init__(self, max_sample_actions: int = 5, max_sample_descriptions: int = 3):
        self.max_sample_actions = max_sample_actions
        self.max_sample_descriptions = max_sample_descriptions
        self._diagnoses: list[CaseDiagnosis] = []

    def add(self, diagnosis: CaseDiagnosis) -> None:
        self._diagnoses.append(diagnosis)

    def add_batch(self, diagnoses: Iterable[CaseDiagnosis]) -> None:
        for d in diagnoses:
            self.add(d)

    def reset(self) -> None:
        self._diagnoses.clear()

    def by_category(self, category: RootCauseCategory) -> list[CaseDiagnosis]:
        return [d for d in self._diagnoses if any(r.category == category for r in d.root_causes)]

    def by_target(self, target: RemediationTarget) -> list[CaseDiagnosis]:
        return [d for d in self._diagnoses if any(r.target == target for r in d.recommendations)]

    def summarize(self) -> DiagnosisSummary:
        causes = [rc for d in self._diagnoses for rc in d.root_causes]
        recs = [r for d in self._diagnoses for r in d.recommendations]

        cat: dict[RootCauseCategory, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "sev": defaultdict(int), "conf": [], "desc": []}
        )
        severity: dict[str, int] = defaultdict(int)
        for rc in causes:
            data = cat[rc.category]
            data["count"] += 1
            data["sev"][rc.severity.value] += 1
            data["conf"].append(rc.confidence)
            if len(data["desc"]) < self.max_sample_descriptions:
                data["desc"].append(rc.description)
            severity[rc.severity.value] += 1
        total_rc = len(causes) or 1
        top_causes = sorted(
            (
                {
                    "category": c.value,
                    "count": d["count"],
                    "percentage": round(d["count"] / total_rc * 100, 1),
                    "avg_confidence": round(sum(d["conf"]) / len(d["conf"]), 2),
                    "severity_distribution": dict(d["sev"]),
                    "sample_descriptions": list(d["desc"]),
                }
                for c, d in cat.items()
            ),
            key=lambda x: (-x["count"], x["category"]),
        )[:TOP_CATEGORIES]

        tgt: dict[RemediationTarget, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "prio": defaultdict(int), "actions": []}
        )
        priority: dict[str, int] = defaultdict(int)
        for r in recs:
            data = tgt[r.target]
            data["count"] += 1
            data["prio"][r.priority.value] += 1
            if r.action not in data["actions"] and len(data["actions"]) < self.max_sample_actions:
                data["actions"].append(r.action)
            priority[r.priority.value] += 1
        total_rec = len(recs) or 1
        targets = sorted(tgt.items(), key=lambda kv: (-kv[1]["count"], kv[0].value))
        top_targets = tuple(
            {
                "target": t.value,
                "count": d["count"],
                "percentage": round(d["count"] / total_rec * 100, 1),
                "priority_distribution": dict(d["prio"]),
                "sample_actions": list(d["actions"]),
            }
            for t, d in targets
        )
        actions = []
        for t, d in targets:
            highest = next(s for s in PRIORITY_ORDER if d["prio"].get(s.value))
            template = _TARGET_SUMMARIES.get(
                t, f"Address {t.value} issues ({{n}} issues identified)."
            )
            actions.append(
                SummarizedAction(
                    target=t,
                    summary=template.format(n=d["count"]),
                    occurrence_count=d["count"],
                    priority=highest,
                    sample_actions=tuple(d["actions"]),
                )
            )
        actions.sort(key=lambda a: (PRIORITY_ORDER[a.priority], -a.occurrence_count))
        return DiagnosisSummary(
            total_samples=len(self._diagnoses),
            samples_with_root_causes=sum(1 for d in self._diagnoses if d.root_causes),
            samples_with_recommendations=sum(1 for d in self._diagnoses if d.recommendations),
            top_root_cause_categories=tuple(top_causes),
            severity_overview=dict(severity),
            top_recommendation_targets=top_targets,
            priority_overview=dict(priority),
            summarized_actions=tuple(actions),
        )


# ------------------------------------------------------------------------------ selection
def _failed_metrics(result: CaseResult, low_score: float | None) -> list[MetricResult]:
    out = []
    for m in result.metrics:
        if m.status == "error" or m.passed is False:
            out.append(m)
        elif low_score is not None and m.score is not None and m.score < low_score:
            out.append(m)
    return out


def failing_cases(
    response: EvalResponse, *, low_score: float | None = None
) -> list[tuple[CaseResult, list[MetricResult]]]:
    """Cases with a failed / errored metric (or a score below ``low_score``) and those metrics."""
    out = []
    for result in response.results:
        failed = _failed_metrics(result, low_score)
        if not failed and result.passed is False:
            failed = [m for m in result.metrics if m.status != "skipped"]
        if failed:
            out.append((result, failed))
    return out


# ---------------------------------------------------------------------------- rule hints
# Metric error codes that point at the evaluation setup rather than at the agent.
_EVAL_SETUP_CODES = {
    "missing_input": "the dataset lacks an input the metric needs",
    "judge_parse_error": "the judge reply could not be parsed",
    "judge_unavailable": "the judge model was unavailable",
    "budget_exhausted": "the run budget was exhausted",
    "deadline": "the run deadline was reached",
    "vendor_nan": "the vendor library returned no score",
    "missing_extra": "an optional dependency (extra) is not installed",
    "sandbox_unavailable": "no code sandbox is available",
    "policy_denied": "the service policy denied the metric",
    "truncated": "the input exceeded max_field_chars",
    "metric_error": "the metric raised an error",
}

# Metric id (prefix or exact) -> root-cause category; the longest match wins.
_METRIC_CATEGORIES: dict[str, RootCauseCategory] = {
    "agent.tool_": RootCauseCategory.TOOL_USAGE,
    "agent.mcp_use": RootCauseCategory.TOOL_USAGE,
    "agent.trajectory_efficiency": RootCauseCategory.TOOL_USAGE,
    "agent.trajectory_stats": RootCauseCategory.TOOL_USAGE,
    "agent.multi_agent": RootCauseCategory.AGENT_COORDINATION,
    "agent.execution_chain": RootCauseCategory.AGENT_COORDINATION,
    "agent.reasoning_hallucination": RootCauseCategory.MODEL_CAPABILITY,
    "agent.": RootCauseCategory.REASONING_PLANNING,
    "rag.context_": RootCauseCategory.TOOL_OUTPUT_QUALITY,
    "rag.": RootCauseCategory.MODEL_CAPABILITY,
    "conversation.turn_context": RootCauseCategory.TOOL_OUTPUT_QUALITY,
    "conversation.role_adherence": RootCauseCategory.PROMPT_ENGINEERING,
    "conversation.topic_adherence": RootCauseCategory.PROMPT_ENGINEERING,
    "conversation.": RootCauseCategory.REASONING_PLANNING,
    "quality.instruction_following": RootCauseCategory.MODEL_CAPABILITY,
    "quality.answer_correctness": RootCauseCategory.MODEL_CAPABILITY,
    "quality.correctness": RootCauseCategory.MODEL_CAPABILITY,
    "quality.contradiction": RootCauseCategory.MODEL_CAPABILITY,
    "quality.": RootCauseCategory.PROMPT_ENGINEERING,
    "format.": RootCauseCategory.PROMPT_ENGINEERING,
    "safety.": RootCauseCategory.PROMPT_ENGINEERING,
    "redteam.": RootCauseCategory.PROMPT_ENGINEERING,
    "text.": RootCauseCategory.MODEL_CAPABILITY,
    "code.": RootCauseCategory.MODEL_CAPABILITY,
    "onchain.": RootCauseCategory.TOOL_USAGE,
    "ops.": RootCauseCategory.OTHER,
    "custom.": RootCauseCategory.OTHER,
}

_DEFAULT_TARGET = {
    RootCauseCategory.PROMPT_ENGINEERING: RemediationTarget.SYSTEM_PROMPT,
    RootCauseCategory.TOOL_USAGE: RemediationTarget.TOOL_DEFINITIONS,
    RootCauseCategory.TOOL_OUTPUT_QUALITY: RemediationTarget.TOOL_RESPONSE,
    RootCauseCategory.REASONING_PLANNING: RemediationTarget.SYSTEM_PROMPT,
    RootCauseCategory.MODEL_CAPABILITY: RemediationTarget.MODEL_SELECTION,
    RootCauseCategory.AGENT_COORDINATION: RemediationTarget.AGENT_ORCHESTRATION,
    RootCauseCategory.OTHER: RemediationTarget.OTHER,
}

_HINTS = {
    RootCauseCategory.PROMPT_ENGINEERING: (
        "Instructions likely miss a constraint, format or policy checked by {metrics}.",
        "Add explicit instructions to the system prompt for what {metrics} checks "
        "(constraints, output format, tone, refusal policy), with a short example.",
    ),
    RootCauseCategory.TOOL_USAGE: (
        "Tool selection or parameters are likely wrong ({metrics}).",
        "Clarify tool descriptions and parameter schemas (when to call each tool, required "
        "arguments, valid values) and validate arguments before execution.",
    ),
    RootCauseCategory.TOOL_OUTPUT_QUALITY: (
        "Retrieved or tool-returned data is likely irrelevant or incomplete ({metrics}).",
        "Improve retrieval / tool responses: tune chunking and ranking, return only relevant, "
        "complete and current data in a consistent format.",
    ),
    RootCauseCategory.REASONING_PLANNING: (
        "The agent's plan or reasoning likely went wrong ({metrics}).",
        "Add step-by-step planning instructions and a self-check of the plan against the task "
        "before the final answer.",
    ),
    RootCauseCategory.MODEL_CAPABILITY: (
        "The answer is likely incorrect or unsupported despite the instructions ({metrics}).",
        "Try a more capable model for this task, or ground answers in the provided context and "
        "require citations.",
    ),
    RootCauseCategory.AGENT_COORDINATION: (
        "Delegation or hand-offs between agents likely lost context ({metrics}).",
        "Pass the full task context on each hand-off and add an orchestrator check that the "
        "right sub-agent handles each step.",
    ),
    RootCauseCategory.OTHER: (
        "Operational or custom checks failed ({metrics}).",
        "Review the failing checks ({metrics}) and the operational limits (latency, cost, "
        "reliability) of the agent's infrastructure.",
    ),
}


def _category_for(metric_id: str) -> RootCauseCategory:
    best = ""
    for prefix in _METRIC_CATEGORIES:
        if (metric_id == prefix or metric_id.startswith(prefix)) and len(prefix) > len(best):
            best = prefix
    return _METRIC_CATEGORIES[best] if best else RootCauseCategory.OTHER


def _severity_for(m: MetricResult) -> Severity:
    low = m.score is not None and m.score <= 0.25
    if m.required:
        return Severity.CRITICAL if (low or m.status == "error") else Severity.HIGH
    return Severity.HIGH if low else Severity.MEDIUM


def _evidence(m: MetricResult) -> str:
    score = "n/a" if m.score is None else f"{m.score:.2f}"
    text = f"{m.name}: score {score}"
    if m.threshold is not None:
        text += f" (threshold {m.threshold:.2f})"
    if m.error is not None:
        text += f", error {m.error.code}"
    if m.reason:
        text += f": {m.reason[:300]}"
    return text


def rule_case_diagnosis(
    result: CaseResult, failed: Sequence[MetricResult], *, error: str | None = None
) -> CaseDiagnosis:
    """Deterministic hints for one failing case from error codes and metric categories."""
    groups: dict[tuple[RootCauseCategory, str | None], list[MetricResult]] = defaultdict(list)
    for m in failed:
        code = m.error.code if m.error is not None and m.status == "error" else None
        if code is not None and code != "target_error":
            groups[(RootCauseCategory.OTHER, code)].append(m)
        elif code == "target_error":
            groups[(RootCauseCategory.TOOL_OUTPUT_QUALITY, code)].append(m)
        else:
            groups[(_category_for(m.metric_id), None)].append(m)
    causes, recs = [], []
    for (category, code), metrics in groups.items():
        names = ", ".join(sorted({m.name for m in metrics}))
        severity = min((_severity_for(m) for m in metrics), key=PRIORITY_ORDER.__getitem__)
        if code == "target_error":
            desc = f"The agent invocation failed, so {names} could not be scored."
            action = "Fix the agent endpoint errors (see the run errors) before re-evaluating."
            target, confidence = RemediationTarget.TOOL_RESPONSE, 0.9
        elif code is not None:
            why = _EVAL_SETUP_CODES.get(code, code)
            desc = f"Evaluation setup issue, not an agent failure: {why} ({names})."
            action = f"Fix the evaluation setup for {names}: {why}."
            target, confidence = RemediationTarget.OTHER, 0.9
        else:
            desc_t, action_t = _HINTS[category]
            desc, action = desc_t.format(metrics=names), action_t.format(metrics=names)
            target, confidence = _DEFAULT_TARGET[category], 0.6
        causes.append(
            RootCause(
                category=category,
                description=desc,
                confidence=confidence,
                severity=severity,
                evidence=tuple(_evidence(m) for m in metrics),
                metrics=tuple(m.name for m in metrics),
            )
        )
        recs.append(Recommendation(target=target, action=action, priority=severity))
    recs.sort(key=lambda r: PRIORITY_ORDER[r.priority])
    return CaseDiagnosis(
        case_id=result.case_id,
        failed_metrics=tuple(m.name for m in failed),
        root_causes=tuple(causes),
        recommendations=tuple(recs),
        source="rules",
        error=error,
    )


def diagnose_rules(response: EvalResponse, *, low_score: float | None = None) -> Diagnosis:
    """Deterministic diagnosis (no judge): rule hints for every failing case."""
    diagnoses = [
        rule_case_diagnosis(r, f) for r, f in failing_cases(response, low_score=low_score)
    ]
    agg = DiagnosisAggregator()
    agg.add_batch(diagnoses)
    return Diagnosis(cases=tuple(diagnoses), summary=agg.summarize())


# ---------------------------------------------------------------------------- judge stages
def _agent_definitions(case: EvalCase | None) -> str:
    if case is None or case.trajectory is None:
        return ""
    lines = []
    for step in case.trajectory.steps():
        if isinstance(step, AgentStep) and (step.system_prompt or step.available_tools):
            lines.append(f"Agent: {step.agent_id or 'agent'}")
            if step.system_prompt:
                lines.append(f"System prompt: {step.system_prompt}")
            for tool in step.available_tools:
                desc = tool.get("description") or "No description"
                lines.append(f"- tool {tool.get('name')}: {desc}")
    return "\n".join(dict.fromkeys(lines))


def _history(case: EvalCase | None) -> str:
    if case is None:
        return ""
    if case.trajectory is not None:
        return render_trajectory(case.trajectory)
    return "\n\n".join(f"{m.role.capitalize()}: {m.content}" for m in case.conversation)


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = content_text(value) if not isinstance(value, (list, int, float, bool)) else None
    return text if text is not None else str(value)


def _failed_evaluation(failed: Sequence[MetricResult]) -> str:
    blocks = []
    for m in failed:
        lines = [f"Evaluator: {m.name} ({m.metric_id}, method {m.method})"]
        lines.append(f"Score: {m.score}")
        lines.append(f"Pass: {m.passed}")
        if m.threshold is not None:
            lines.append(f"Threshold: {m.threshold}")
        if m.error is not None:
            lines.append(f"Error: {m.error.code} {m.error.message}".rstrip())
        lines.append(f"Explanation: {m.reason or ''}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _root_causes_text(causes: Sequence[RootCause]) -> str:
    parts = []
    for i, rc in enumerate(causes, 1):
        parts.append(
            f"<root_cause_{i}>\n<category>{rc.category.value}</category>\n"
            f"<severity>{rc.severity.value}</severity>\n<confidence>{rc.confidence}</confidence>"
            f"\n<description>{rc.description}</description>\n"
            f"<evidence>{', '.join(rc.evidence)}</evidence>\n</root_cause_{i}>"
        )
    return "\n".join(parts)


async def _ask(
    judge: JudgeModel, template: PromptTemplate, fields: Mapping[str, str], case_key: str
) -> dict[str, Any]:
    nonce = make_nonce(case_key, f"{template.metric_id}@{template.version}", 0)
    fenced = {k: fence(k, v, nonce) for k, v in fields.items()}
    request = JudgeRequest(
        system=template.system + fence_clause(nonce),
        user=template.render("simple", fenced),
        output_schema=template.output_schema,
        metadata={
            "metric_id": template.metric_id,
            "prompt_version": template.version,
            "mode": "simple",
            "sample_idx": 0,
        },
    )
    response = await judge.complete(request)
    try:
        return parse(response.text, response.parsed, template.output_schema)
    except JudgeParseError as first:
        repair = dataclasses.replace(
            request, user=request.user + "\n\n" + REPAIR_INSTRUCTION.format(problems=first)
        )
        response = await judge.complete(repair)
        return parse(response.text, response.parsed, template.output_schema)


async def _judge_case(
    judge: JudgeModel,
    result: CaseResult,
    failed: Sequence[MetricResult],
    case: EvalCase | None,
    min_confidence: float,
) -> CaseDiagnosis:
    trajectory = _history(case)
    definitions = _agent_definitions(case)
    fields = {
        "agent_definitions": definitions,
        "trajectory": trajectory,
        "input": _text(case.input) if case else "",
        "actual_output": _text(case.output) if case else "",
        "expected_output": _text(case.expected) if case else "",
        "failed_evaluation": _failed_evaluation(failed),
    }
    key = hash_obj({"case": case.to_dict() if case else result.case_id, "failed": fields})
    stage1 = await _ask(judge, ROOT_CAUSE_V1, fields, key)
    all_causes = [
        RootCause(
            category=RootCauseCategory(rc["category"]),
            description=rc["description"],
            confidence=float(rc["confidence"]),
            severity=Severity(rc["severity"]),
            evidence=tuple(rc.get("evidence") or ()),
        )
        for rc in stage1["root_causes"]
    ]
    causes = [rc for rc in all_causes if rc.confidence >= min_confidence]
    base = CaseDiagnosis(
        case_id=result.case_id,
        failed_metrics=tuple(m.name for m in failed),
        filtered=len(all_causes) - len(causes),
    )
    if not causes:  # AgentSight: no confident root cause -> no recommendations
        return base
    stage2 = await _ask(
        judge,
        RECOMMENDATION_V1,
        {
            "root_causes": _root_causes_text(causes),
            "trajectory": trajectory,
            "agent_definitions": definitions,
        },
        key,
    )
    recs = sorted(
        (
            Recommendation(
                target=RemediationTarget(r["target"]),
                action=r["action"],
                priority=Severity(r["priority"]),
            )
            for r in stage2["recommendations"]
        ),
        key=lambda r: PRIORITY_ORDER[r.priority],
    )
    return dataclasses.replace(
        base,
        root_causes=tuple(causes),
        recommendations=tuple(recs),
        root_cause_thinking=stage1.get("thinking"),
        recommendation_thinking=stage2.get("thinking"),
    )


async def diagnose(
    response: EvalResponse,
    *,
    judge: JudgeModel | None = None,
    cases: Sequence[EvalCase] | Mapping[str, EvalCase] | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    low_score: float | None = None,
    max_cases: int | None = None,
    concurrency: int = 4,
    fallback: bool = True,
) -> Diagnosis:
    """Diagnose every failing (or below ``low_score``) case of ``response``.

    ``cases`` (the evaluated ``EvalCase``s) give the judge the input, output and trajectory;
    without them only the metric results are shown. With ``judge=None`` the deterministic
    rule hints are used. A judge error falls back to rule hints for that case (``fallback``)
    or is re-raised.
    """
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be within [0, 1]")
    by_id: Mapping[str, EvalCase] = (
        cases if isinstance(cases, Mapping) else {c.id: c for c in cases or ()}
    )
    selected = failing_cases(response, low_score=low_score)
    if max_cases is not None:
        selected = selected[:max_cases]
    if judge is None:
        diagnoses = [rule_case_diagnosis(r, f) for r, f in selected]
    else:
        sem = asyncio.Semaphore(max(1, concurrency))

        async def one(result: CaseResult, failed: list[MetricResult]) -> CaseDiagnosis:
            async with sem:
                try:
                    return await _judge_case(
                        judge, result, failed, by_id.get(result.case_id), min_confidence
                    )
                except (EvalError, ValueError, KeyError) as exc:
                    if not fallback:
                        raise
                    return rule_case_diagnosis(
                        result, failed, error=f"{type(exc).__name__}: {exc}"
                    )

        diagnoses = list(await asyncio.gather(*(one(r, f) for r, f in selected)))
    agg = DiagnosisAggregator()
    agg.add_batch(diagnoses)
    return Diagnosis(
        cases=tuple(diagnoses), summary=agg.summarize(), min_confidence=min_confidence
    )
