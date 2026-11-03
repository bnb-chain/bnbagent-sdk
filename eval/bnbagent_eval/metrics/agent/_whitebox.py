"""Shared flow for the AgentSight whitebox judge metrics (``WhiteboxEvaluator`` family).

AgentSight runs the judge once per *invocation* (one user turn) with ``num_samples`` samples,
aggregates the samples per invocation, then aggregates across invocations. Here one
:class:`~bnbagent_eval.types.trajectory.Trace` is one invocation. An invocation without
intermediate data (no tool calls, delegations or handoffs) is ``NOT_EVALUATED`` with score 1,
as in ``WhiteboxEvaluator._evaluate_invocations``.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from ...adapters.traces.render import render_trajectory
from ...exceptions import SuiteError
from ...hashing import canonical_json
from ...judges.prompts import PromptTemplate
from ...judges.prompts.agent._whitebox import RUBRICS_FIELD, with_rubrics
from ...types.case import EvalCase, content_text
from ...types.trajectory import (
    AgentStep,
    DelegationStep,
    HandoffStep,
    LlmStep,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)
from ..base import MetricContext, Outcome
from ..custom.rubric import NA, YES, majority, sample_verdicts
from ..judge_base import JudgeMetric, Sample, judge_params
from ..text._common import as_text as plain_text

NOT_EVALUATED = "not_evaluated"
NO_INTERMEDIATE = (
    "No intermediate data found for invocation {id}. Skipping whitebox eval. Please verify that "
    "the trace is correct and the mapper is configured correctly."
)
NO_STEPS = "No intermediate steps were taken."
_RECORD = "# Conversation record\n"


# --------------------------------------------------------------------------- invocations
@dataclass(frozen=True)
class ToolUse:
    call: ToolCall
    result: ToolResult | None
    thought: str | None  # text of the LLM step that issued the call (AgentSight ``thinking``)
    agent_id: str | None


@dataclass(frozen=True)
class Invocation:
    index: int
    trace: Trace
    session: Session  # single-trace session, for render_trajectory
    user_input: str
    final_response: str
    tool_uses: tuple[ToolUse, ...]
    has_intermediate: bool


def _same_call(a: ToolCall, b: ToolCall) -> bool:
    return a.name == b.name and canonical_json(dict(a.arguments)) == canonical_json(
        dict(b.arguments)
    )


def tool_uses(trace: Trace) -> list[ToolUse]:
    """One entry per tool call (AgentSight ``eval_case`` counts each call once).

    A call issued by an LLM step and executed by a tool step is merged: by id, or (when either
    side has no id) by name and arguments, first-in-first-out. Calls never executed keep
    ``result=None``.
    """
    tool_ids = {s.call.id for s in trace.steps if isinstance(s, ToolStep) and s.call.id}
    llm_ids = {c.id for s in trace.steps if isinstance(s, LlmStep) for c in s.tool_calls if c.id}
    thoughts: dict[str, str] = {}
    last_thought: str | None = None
    out: list[ToolUse] = []
    pending: list[int] = []  # indices in ``out`` of LLM calls not executed (yet) by id
    for step in trace.steps:
        if isinstance(step, LlmStep) and step.tool_calls:
            last_thought = step.response or None
            for call in step.tool_calls:
                if call.id and last_thought:
                    thoughts[call.id] = last_thought
                if call.id is None or call.id not in tool_ids:
                    pending.append(len(out))
                    out.append(ToolUse(call, None, last_thought, step.agent_id))
        elif isinstance(step, ToolStep):
            if step.call.id is None or step.call.id not in llm_ids:
                k = next(
                    (
                        i
                        for i in pending
                        if (out[i].call.id is None or step.call.id is None)
                        and _same_call(out[i].call, step.call)
                    ),
                    None,
                )
                if k is not None:
                    pending.remove(k)
                    out[k] = ToolUse(step.call, step.result, out[k].thought, step.agent_id)
                    continue
            thought = thoughts.get(step.call.id or "", last_thought)
            out.append(ToolUse(step.call, step.result, thought, step.agent_id))
    return out


def invocations(session: Session, case_input: str = "") -> list[Invocation]:
    """One invocation per trace. A single-trace session without a user prompt in its agent steps
    takes ``case_input`` (the case's request) as the invocation's user input."""
    out = []
    fallback = case_input if len(session.traces) == 1 else ""
    for i, trace in enumerate(session.traces):
        single = Session(id=f"{session.id}/{trace.id}", traces=(trace,))
        agents = [s for s in trace.steps if isinstance(s, AgentStep)]
        roots = [s for s in agents if s.parent_id is None] or agents
        user = next((s.user_prompt for s in roots if s.user_prompt), "")
        final = next((s.response for s in reversed(roots) if s.response), None)
        uses = tuple(tool_uses(trace))
        delegated = any(isinstance(s, (DelegationStep, HandoffStep)) for s in trace.steps)
        out.append(
            Invocation(
                index=i,
                trace=trace,
                session=single,
                user_input=user or fallback,
                final_response=final or single.final_response() or "",
                tool_uses=uses,
                has_intermediate=bool(uses) or delegated,
            )
        )
    return out


# ------------------------------------------------------------------------- rendering
def as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def params_str(call: ToolCall) -> str:
    return ", ".join(f"{k}={v}" for k, v in call.arguments.items())


def result_text(result: ToolResult | None) -> str:
    if result is None:
        return "None"
    if result.error:
        return f"Error: {result.error}"
    return as_text(result.content)


def tool_usage(inv: Invocation) -> str:
    """The invocation's conversation record (``render_trajectory`` without the tools header)."""
    text = render_trajectory(inv.session)
    at = text.find(_RECORD)
    return text[at:] if at >= 0 and inv.has_intermediate else NO_STEPS


def tools_by_agent(inv: Invocation, case: EvalCase) -> dict[str, list[Mapping[str, Any]]]:
    """Declared tools per agent (this trace's agent steps, else the whole session's)."""
    for session in (inv.session, case.trajectory):
        out: dict[str, list[Mapping[str, Any]]] = {}
        seen: set[tuple[str, str]] = set()
        for step in session.steps() if session is not None else ():
            if isinstance(step, AgentStep):
                agent = step.agent_id or "agent"
                for tool in step.available_tools:
                    key = (agent, str(tool.get("name", "")))
                    if key not in seen:
                        seen.add(key)
                        out.setdefault(agent, []).append(tool)
        if out:
            return out
    return {}


def tool_declarations(inv: Invocation, case: EvalCase, fallback: str) -> str:
    """AgentSight ``get_tool_declarations_as_json_str`` (agent -> {tools: [...]})."""
    tools = tools_by_agent(inv, case)
    if not tools:
        return fallback
    payload = {"tool_declarations": {a: {"tools": t} for a, t in tools.items()}}
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def previous_turns(
    previous: Sequence[Invocation], prefix: str = "\n\nConversation History:\n", empty: str = ""
) -> str:
    """AgentSight ``Turn i: / User: / Tool use: / Response:`` history of earlier invocations."""
    if not previous:
        return empty
    text = prefix
    for i, inv in enumerate(previous):
        text += f"Turn {i + 1}:\n"
        text += f"User: {inv.user_input}\n"
        text += f"Tool use: {tool_usage(inv)}\n"
        text += f"Response: {inv.final_response}\n\n"
    return text


def merge_provenance(provs: Sequence[dict], template: PromptTemplate) -> dict:
    if not provs:
        return {"prompt_hash": template.hash()}
    first = dict(provs[0])
    sources = {p["judge_source"] for p in provs}
    first["judge_source"] = sources.pop() if len(sources) == 1 else "mixed"
    first["truncated"] = any(p["truncated"] for p in provs)
    return first


def majority_score(scores: Sequence[float]) -> float:
    """AgentSight sample vote: most common score rounded to 0.1.

    Ties go to the lower score, not to the first sample seen as in AgentSight:
    the result must not depend on which of two equally frequent samples came back first.
    """
    if len(scores) == 1:
        return scores[0]
    counts = Counter(round(s, 1) for s in scores)
    return min(counts, key=lambda s: (-counts[s], s))


def clamp(score: float) -> float:
    return min(1.0, max(0.0, score))


# ---------------------------------------------------------------------------- metrics
class WhiteboxJudge(JudgeMetric):
    """Per-invocation judge flow; subclasses build fields and score one invocation."""

    skip_without_intermediate: ClassVar[bool] = True

    def invocation_fields(
        self, inv: Invocation, previous: Sequence[Invocation], case: EvalCase
    ) -> dict[str, str]:
        raise NotImplementedError

    def score_invocation(
        self, inv: Invocation, samples: list[Sample], template: PromptTemplate, ctx: MetricContext
    ) -> dict[str, Any]:
        """Row with at least ``score`` (float | None) and ``reason``."""
        raise NotImplementedError

    def combine(self, rows: list[dict[str, Any]], template: PromptTemplate) -> float | None:
        """Mean of the per-invocation scores (``aggregate_invocation_results``)."""
        scores = [r["score"] for r in rows if r["score"] is not None]
        return sum(scores) / len(scores) if scores else None

    def judge_ctx(self, ctx: MetricContext) -> MetricContext:
        return ctx

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        invs = invocations(case.trajectory, content_text(case.input) or plain_text(case.input))
        if not invs:
            return Outcome(
                score=0.0,
                reason="No invocation found in input. Ensure that the trace is correct.",
                details={"invocations": [], "not_evaluated": 0},
                provenance=merge_provenance([], template),
            )
        jctx = self.judge_ctx(ctx)
        rows: list[dict[str, Any]] = []
        provs: list[dict] = []
        for inv in invs:
            if self.skip_without_intermediate and not inv.has_intermediate:
                rows.append(
                    {
                        "trace_id": inv.trace.id,
                        "status": NOT_EVALUATED,
                        "score": 1.0,
                        "reason": NO_INTERMEDIATE.format(id=inv.trace.id),
                    }
                )
                continue
            fields = self.invocation_fields(inv, invs[: inv.index], case)
            samples, prov = await self.ask(
                case, jctx, template, mode, fields, tag=f"#inv{inv.index}"
            )
            provs.append(prov)
            row = self.score_invocation(inv, samples, template, jctx)
            rows.append({"trace_id": inv.trace.id, "status": "scored", **row})
        score = self.combine(rows, template)
        threshold = ctx.config.threshold
        if threshold is None:
            threshold = self.spec.default_threshold or 0.0
        parts = []
        for i, r in enumerate(rows):
            if not r.get("reason"):
                continue
            if r["status"] == NOT_EVALUATED:
                status = "NOT_EVALUATED"
            else:
                status = "PASS" if r["score"] is not None and r["score"] >= threshold else "FAIL"
            pct = f"{r['score']:.0%}" if r["score"] is not None else "N/A"
            parts.append(f"[Invocation {i + 1}] ({status}, {pct}): {r['reason']}")
        details = {
            "mode": mode,
            "prompt_version": template.version,
            "invocations": rows,
            "not_evaluated": sum(1 for r in rows if r["status"] == NOT_EVALUATED),
        }
        return Outcome(
            score=score,
            reason="\n\n".join(parts) or None,
            details=details,
            provenance=merge_provenance(provs, template),
        )


class StepRatioJudge(WhiteboxJudge):
    """Adaptivity / hallucination / efficiency: a step-count ratio per sample, vote, mean."""

    def score_sample(self, parsed: Mapping[str, Any], steps: int) -> float:
        raise NotImplementedError

    def score_invocation(
        self, inv: Invocation, samples: list[Sample], template: PromptTemplate, ctx: MetricContext
    ) -> dict[str, Any]:
        steps = len(inv.tool_uses)
        raw = [self.score_sample(s.parsed, steps) for s in samples]
        first = samples[0].parsed
        row = {
            "score": clamp(majority_score(raw)),
            "reason": first.get("analysis"),
            "steps": steps,
            **{k: v for k, v in first.items() if k.endswith("_steps")},
        }
        if len(samples) > 1:
            row["sample_scores"] = raw
        return row


# --------------------------------------------------------------------- rubric metrics
RUBRICS_PARAM: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": ["string", "object"],
        "required": ["id", "text"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "text": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    },
}


def rubric_params(extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return judge_params({"rubrics": RUBRICS_PARAM, **(extra or {})})


class RubricWhitebox(WhiteboxJudge):
    """AgentSight ``RubricBasedEvaluator``: yes / no / nr per rubric (matched by property text),
    majority vote over samples per rubric, per-invocation mean of applicable rubrics, and an
    overall mean over every applicable rubric verdict of every invocation
    (``MeanInvocationResultsSummarizer``)."""

    default_rubrics: ClassVar[tuple[tuple[str, str], ...]] = ()

    def rubric_list(self, params: Mapping[str, Any]) -> list[dict[str, str]]:
        raw = params.get("rubrics")
        if not raw:
            if not self.default_rubrics:
                raise SuiteError(f"{self.spec.id} requires a non-empty rubrics param")
            return [{"id": i, "text": t} for i, t in self.default_rubrics]
        rubrics = []
        for n, r in enumerate(raw, 1):
            if isinstance(r, str):
                rubrics.append({"id": f"rubric_{n}", "text": r})
            else:
                rubrics.append({"id": str(r["id"]).strip(), "text": str(r["text"])})
        ids = [r["id"] for r in rubrics]
        if len(set(ids)) != len(ids):
            raise SuiteError(f"{self.spec.id}: rubric ids must be unique")
        return rubrics

    def uses_rubrics(self, template: PromptTemplate) -> bool:
        base = self.templates[template.version]  # before the rubrics are filled in
        return any(RUBRICS_FIELD in p for p in base.parts.values())

    def template_for(self, params: Mapping[str, Any]) -> PromptTemplate:
        template = super().template_for(params)
        if not self.uses_rubrics(template):
            return template
        return with_rubrics(template, [r["text"] for r in self.rubric_list(params)])

    def score_invocation(
        self, inv: Invocation, samples: list[Sample], template: PromptTemplate, ctx: MetricContext
    ) -> dict[str, Any]:
        rubrics = self.rubric_list(ctx.params)
        # Source ids are not shown to the judge: match on the property text.
        per_sample = [sample_verdicts(s.parsed, rubrics) for s in samples]
        rows = []
        for r in rubrics:
            votes = [v.get(r["id"], (NA, None)) for v in per_sample]
            verdict, rationale = majority(votes)
            rows.append({"id": r["id"], "verdict": verdict, "rationale": rationale})
        applicable = [r for r in rows if r["verdict"] != NA]
        score = (
            sum(1 for r in applicable if r["verdict"] == YES) / len(applicable)
            if applicable
            else None
        )
        reason = "\n".join(
            f"[{r['id']}] {'PASS' if r['verdict'] == YES else 'FAIL'}: {r['rationale']}"
            for r in applicable
            if r["rationale"]
        )
        return {"score": score, "reason": reason or None, "rubrics": rows}

    def combine(self, rows: list[dict[str, Any]], template: PromptTemplate) -> float | None:
        verdicts = [
            1.0 if v["verdict"] == YES else 0.0
            for r in rows
            for v in r.get("rubrics", ())
            if v["verdict"] != NA
        ]
        # AgentSight MeanInvocationResultsSummarizer averages rubric scores only: when every
        # invocation was skipped (no intermediate data) there is no score.
        return sum(verdicts) / len(verdicts) if verdicts else None

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        outcome = await super()._evaluate_native(case, ctx)
        per_rubric: dict[str, list[float]] = {}
        for row in outcome.details.get("invocations", ()):
            for v in row.get("rubrics", ()):
                if v["verdict"] != NA:
                    per_rubric.setdefault(v["id"], []).append(float(v["verdict"] == YES))
        if not per_rubric:
            return outcome
        details = dict(outcome.details)
        details["rubric_scores"] = {k: sum(v) / len(v) for k, v in per_rubric.items()}
        return Outcome(
            score=outcome.score,
            reason=outcome.reason,
            details=details,
            provenance=outcome.provenance,
        )
