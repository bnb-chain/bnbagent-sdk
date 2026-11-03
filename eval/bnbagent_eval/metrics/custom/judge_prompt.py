"""``custom.judge_prompt``: bring your own judge prompt.

AgentSight CustomPromptEvaluator (placeholders, categories, auto-appended content, 5-tier scale),
its whitebox variant's pass / fail / not_applicable scale, and ETK llm_as_judge custom
``description`` (numeric 0-1 score). The prompt is part of the template, so ``prompt_hash``
covers it; unknown placeholders are a :class:`SuiteError` at suite resolution.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...adapters.traces.render import render_trajectory
from ...exceptions import SuiteError
from ...judges.prompts import FIVE_TIER, PromptTemplate
from ...judges.prompts.custom import judge_prompt as prompts
from ...judges.sampling import median_low
from ...types.case import EvalCase, content_text
from ...types.result import ErrorInfo
from ..base import Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..conversation._common import turn_count
from ..judge_base import JudgeMetric, conversation_text, judge_params
from ..registry import register
from ..text._common import as_text

CATEGORIES = tuple(prompts.CATEGORY_PLACEHOLDERS)
SCALES = tuple(prompts.SCHEMAS)


def _turn_count(case: EvalCase) -> int:
    """Conversation turns: explicit messages, else turns derived from the trajectory (AgentSight
    ``_get_conversation_turn_count`` reads the trajectory first)."""
    return turn_count(case)


def category_problem(case: EvalCase, category: str) -> str | None:
    """AgentSight ``validate_inputs`` per category; returns the missing-input message."""
    has_output = case.has("output")
    turns = _turn_count(case)
    has_conv = turns >= 1
    if category == "rag":
        if not case.has("contexts"):
            return "rag category requires contexts"
        if not case.has("input"):
            return "rag category requires input (the query)"
    elif category == "conversational":
        if turns < 2:
            return "conversational category requires a conversation with at least 2 turns"
    elif category == "comparison":
        if case.expected is None:
            return "comparison category requires expected"
        if not has_output and not has_conv:
            return "comparison category requires output or conversation"
    elif not has_output and not has_conv:
        return "general category requires output or conversation"
    return None


_PARAMS = judge_params(
    {
        "prompt": {"type": "string", "minLength": 1},
        "category": {"enum": list(CATEGORIES)},
        "scale": {"enum": list(SCALES)},
    }
)
_PARAMS["properties"].pop("prompt_version")


@register
class JudgePrompt(JudgeMetric):
    templates = {}
    spec = MetricSpec(
        id="custom.judge_prompt",
        category="custom",
        description="A user-supplied judge prompt with placeholders (BYOP).",
        methods=(
            MethodSpec(
                "native",
                Kind.JUDGE,
                optional_inputs=frozenset(
                    {"input", "output", "expected", "contexts", "conversation", "trajectory"}
                ),
            ),
        ),
        params_schema={
            **_PARAMS,  # no prompt_version: the prompt is the suite's own text
            "required": ["prompt"],
        },
        default_threshold=0.5,
        # v2: a trajectory counts one turn per agent step (categories); no prompt_version param
        version="2",
        sources=(
            "agentsight:CustomPromptEvaluator",
            "agentsight:WhiteboxCustomPromptEvaluator",
            "etk:llm_as_judge(custom description)",
        ),
    )

    def template_for(self, params: Mapping[str, Any]) -> PromptTemplate:
        try:
            return prompts.build(
                params.get("prompt", ""),
                params.get("category", "general"),
                params.get("scale", "five_tier"),
            )
        except ValueError as exc:
            raise SuiteError(str(exc)) from exc

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        input_text = content_text(case.input) or as_text(case.input)
        if mode == "conversation":
            output = " ".join(m.content for m in case.conversation if m.role == "assistant")
            conversation = conversation_text(case)
        else:
            output = content_text(case.output) or as_text(case.output)
            # {conversation} is always filled (AgentSight WhiteboxCustomPrompt renders the agents'
            # history, tool calls included): explicit messages, else the rendered trajectory.
            if case.conversation:
                conversation = conversation_text(case)
            elif case.trajectory is not None:
                conversation = render_trajectory(case.trajectory)
            else:
                conversation = ""
        expected = as_text(case.expected) if case.expected is not None else ""
        contexts = "\n".join(c.text for c in case.contexts)
        trajectory = (
            json.dumps(case.trajectory.to_dict(), sort_keys=True, ensure_ascii=False)
            if case.trajectory is not None
            else ""
        )
        # AgentSight auto-append, used when the prompt has no placeholder.
        if mode == "conversation" and conversation:
            content = f"## Conversation:\n{conversation}\n"
        else:
            content = ""
            if input_text:
                content += f"## Input:\n{input_text}\n\n"
            if output:
                content += f"## Response:\n{output}\n\n"
            if expected:
                content += f"## Expected:\n{expected}\n"
            if contexts:
                content += f"## Contexts:\n{contexts}\n"
        return {
            "input": input_text,
            "actual_output": output,
            "expected_output": expected,
            "contexts": contexts,
            "conversation": conversation,
            "trajectory": trajectory,
            "content": content,
        }

    @staticmethod
    def read(parsed: Mapping[str, Any], scale: str) -> tuple[float | None, str | None, Any]:
        """(score, reason, label) of one reply."""
        if scale == "five_tier":
            return FIVE_TIER[parsed["score"]], parsed.get("reasoning"), parsed["score"]
        if scale == "pass_fail_na":
            label = parsed["label"].lower()
            return prompts.PASS_FAIL_LABELS.get(label, 0.0), parsed.get("explanation"), label
        return float(parsed["score"]), parsed.get("context"), None

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        category = template.extra["category"]
        problem = category_problem(case, category)
        if problem:
            return Outcome(None, status="skipped", error=ErrorInfo("missing_input", problem))
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        scale = template.extra["scale"]
        reads = [self.read(s.parsed, scale) for s in samples]
        details: dict[str, Any] = {"mode": mode, "category": category, "scale": scale}
        if len(samples) > 1:
            details["sample_scores"] = [r[0] for r in reads]
        applicable = [r for r in reads if r[0] is not None]
        if not applicable:  # every sample said not_applicable
            details["label"] = "not_applicable"
            return Outcome(None, reason=reads[0][1], details=details, provenance=provenance)
        score = median_low([r[0] for r in applicable])
        _, reason, label = next(r for r in applicable if r[0] == score)
        if label is not None:
            details["label"] = label
        return Outcome(score=score, reason=reason, details=details, provenance=provenance)
