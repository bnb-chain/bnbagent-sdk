"""``agent.execution_chain``: quality of six execution-chain links (AgentSight
ExecutionChainEvaluator).

Per invocation the judge scores intent_understanding, plan_quality, tool_selection,
tool_execution, result_interpretation and output_quality from 1 to 5 (-1 = N/A). With several
samples (default 3) each link takes the rounded median of its valid scores. The invocation score
is the weighted mean of ``(s - 1) / 4`` over the applicable links (weights .2, .2, .15, .15, .15,
.15; 0 when none applies); invocations are averaged. Every invocation is judged (no
NOT_EVALUATED skip, as in the source).
"""

from __future__ import annotations

import dataclasses
import json
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from ...judges.prompts import PromptTemplate
from ...judges.prompts.agent import execution_chain as prompts
from ...types.case import EvalCase
from ...types.trajectory import AgentStep
from ..base import MetricContext, MetricSpec
from ..judge_base import Sample, judge_params, native_method
from ..registry import register
from ._whitebox import Invocation, WhiteboxJudge, as_text, previous_turns, tools_by_agent

CHAIN_WEIGHTS = {
    "intent_understanding": 0.20,
    "plan_quality": 0.20,
    "tool_selection": 0.15,
    "tool_execution": 0.15,
    "result_interpretation": 0.15,
    "output_quality": 0.15,
}
MIN_SCORE, MAX_SCORE, NA_SCORE = 1, 5, -1
DEFAULT_SAMPLES = 3
MAX_RESULT_CHARS = 500


def validate_score(score: Any) -> int:
    """``_validate_score``: -1 stays N/A, anything else is clamped to 1..5."""
    value = int(score)
    if value == NA_SCORE:
        return NA_SCORE
    return max(MIN_SCORE, min(MAX_SCORE, value))


def parse_links(parsed: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Validated link scores of one reply; a missing link scores 3 (as in the source)."""
    raw = parsed.get("chain_scores") or {}
    out = {}
    for name in CHAIN_WEIGHTS:
        if name in raw:
            item = raw[name]
            out[name] = {
                "score": validate_score(item.get("score", 3)),
                "evidence": item.get("evidence", "No evidence provided"),
                "counterfactual": item.get("counterfactual"),
                "cascading_impact": bool(item.get("cascading_impact", False)),
            }
        else:
            out[name] = {
                "score": 3,
                "evidence": "Score not provided by evaluator",
                "counterfactual": None,
                "cascading_impact": False,
            }
    return out


def aggregate_links(samples: Sequence[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """``_aggregate_samples``: rounded median of valid scores, majority cascading flag."""
    if len(samples) == 1:
        return samples[0]
    out = {}
    for name in CHAIN_WEIGHTS:
        links = [s[name] for s in samples]
        scores = [link["score"] for link in links]
        valid = [s for s in scores if s >= MIN_SCORE]
        median = int(round(statistics.median(valid))) if valid else NA_SCORE
        best = 0
        if valid:
            best = min(
                (i for i, s in enumerate(scores) if s >= MIN_SCORE),
                key=lambda i: abs(scores[i] - median),
            )
        cascading = [link["cascading_impact"] for link in links]
        counterfactuals = [link["counterfactual"] for link in links if link["counterfactual"]]
        out[name] = {
            "score": median,
            "evidence": links[best]["evidence"],
            "counterfactual": counterfactuals[0] if counterfactuals else None,
            "cascading_impact": sum(cascading) > len(cascading) / 2,
        }
    return out


def reliability_score(links: Mapping[str, Mapping[str, Any]]) -> float:
    """``_calculate_reliability_score``: weighted mean of (s - 1) / 4 over applicable links."""
    total = weighted = 0.0
    for name, weight in CHAIN_WEIGHTS.items():
        score = links[name]["score"]
        if score >= MIN_SCORE:
            weighted += (score - 1) / 4.0 * weight
            total += weight
    return weighted / total if total else 0.0


def agent_context(inv: Invocation, case: EvalCase) -> tuple[str, str]:
    """(agent instructions, tool declarations) labelled by agent."""
    agents: dict[str, str | None] = {}
    for step in inv.trace.steps:
        if isinstance(step, AgentStep):
            name = step.agent_id or "agent"
            if agents.get(name) is None:
                agents[name] = step.system_prompt
    instructions = "No agent instructions/system prompt available."
    if agents:
        instructions = "\n\n".join(
            f"#### Agent: {name}\n```\n{text}\n```"
            if text
            else f"#### Agent: {name}\n(No instructions provided)"
            for name, text in agents.items()
        )
    tools = tools_by_agent(inv, case)
    declarations = "Agent has no tools available."
    if tools:
        listing = "\n".join(
            f"**{agent}**: {', '.join(str(t.get('name')) for t in items) or 'No tools'}"
            for agent, items in tools.items()
        )
        schemas = {"tool_declarations": {a: {"tools": t} for a, t in tools.items()}}
        declarations = (
            listing
            + "\n\n**Full Tool Schemas:**\n"
            + json.dumps(schemas, indent=2, ensure_ascii=False, default=str)
        )
    return instructions, declarations


def execution_trace(inv: Invocation) -> str:
    """``_build_execution_trace``: thought, call, parameters and (truncated) result per step."""
    parts: list[str] = []
    for i, u in enumerate(inv.tool_uses):
        attr = f" (by {u.agent_id})" if u.agent_id else ""
        if u.thought:
            parts.append(f'[{i}] Thought{attr}: "{u.thought}"')
        params = json.dumps(dict(u.call.arguments), indent=2) if u.call.arguments else "{}"
        parts.append(f"[{i}] Tool Call{attr}: {u.call.name}")
        parts.append(f"    Parameters: {params}")
        if u.result is not None:
            content = f"Error: {u.result.error}" if u.result.error else as_text(u.result.content)
            if len(content) > MAX_RESULT_CHARS:
                content = content[:MAX_RESULT_CHARS] + "... [truncated]"
            parts.append(f"    Result: {content}")
        parts.append("")
    return "\n".join(parts) if parts else "No intermediate execution steps recorded."


def explanation(links: Mapping[str, Mapping[str, Any]], parsed: Mapping[str, Any]) -> str:
    lines = []
    for name, weight in CHAIN_WEIGHTS.items():
        link = links[name]
        score = f"{link['score']}/5" if link["score"] >= MIN_SCORE else "N/A"
        cascade = " [CASCADE]" if link["cascading_impact"] else ""
        lines.append(f"{name} ({weight:.0%}): {score}{cascade}")
        lines.append(f"   Evidence: {link['evidence']}")
        if link["counterfactual"] and link["score"] <= 3:
            lines.append(f"   Should have: {link['counterfactual']}")
    lines.append(f"\nSummary: {parsed.get('overall_reasoning', 'No reasoning provided')}")
    return "\n".join(lines)


@register
class ExecutionChain(WhiteboxJudge):
    templates = prompts.VERSIONS
    default_version = prompts.DEFAULT_VERSION
    skip_without_intermediate = False
    spec = MetricSpec(
        id="agent.execution_chain",
        category="agent",
        description="Weighted quality of six execution links, from intent to final output.",
        methods=(native_method("trajectory"),),
        params_schema=judge_params(),
        default_threshold=0.7,
        # v2: a call is counted once when only one side has an id; case input as user input
        version="2",
        sources=("agentsight:ExecutionChainEvaluator",),
    )

    def judge_ctx(self, ctx: MetricContext) -> MetricContext:
        if "samples" in ctx.params:
            return ctx
        return dataclasses.replace(ctx, params={**ctx.params, "samples": DEFAULT_SAMPLES})

    def invocation_fields(
        self, inv: Invocation, previous: Sequence[Invocation], case: EvalCase
    ) -> dict[str, str]:
        instructions, declarations = agent_context(inv, case)
        return {
            "agent_instructions": instructions,
            "tool_declarations": declarations,
            "user_input": inv.user_input,
            "conversation_history": previous_turns(
                previous, prefix="\n", empty="No previous conversation history."
            ),
            "execution_trace": execution_trace(inv),
            "agent_response": inv.final_response,
        }

    def score_invocation(
        self, inv: Invocation, samples: list[Sample], template: PromptTemplate, ctx: MetricContext
    ) -> dict[str, Any]:
        per_sample = [parse_links(s.parsed) for s in samples]
        links = aggregate_links(per_sample)
        failures: list[str] = []
        for s in samples:
            for f in s.parsed.get("critical_failures") or ():
                if f not in failures:
                    failures.append(f)
        row: dict[str, Any] = {
            "score": reliability_score(links),
            "reason": explanation(links, samples[0].parsed),
            "chain_scores": links,
            "critical_failures": failures,
        }
        if len(samples) > 1:
            row["sample_scores"] = [reliability_score(p) for p in per_sample]
        return row
