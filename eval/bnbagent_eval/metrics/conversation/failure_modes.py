"""``conversation.failure_modes``: failure modes the agent exhibited (AgentSight ``agent_sim``).

The judge (``ConversationAnalyzer``) labels the conversation with the ``StressAxis`` values the
user exhibited and the ``FailureMode`` values the agent exhibited; labels outside the enums are
dropped (as the source). Score = 1 − |distinct failure modes hit| / |FailureMode| (= 10).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...judges.prompts import PromptTemplate
from ...judges.prompts.conversation import failure_modes as prompts
from ...types.case import EvalCase, Message
from ...types.simulation import FailureMode, StressAxis
from ..base import MetricContext, MetricSpec, Outcome
from ..registry import register
from ._common import (
    ConversationJudge,
    conversation_messages,
    conversation_method,
    conversation_params,
)


def valid_labels(values: Sequence[Any], allowed: type[StressAxis] | type[FailureMode]) -> list:
    """Distinct enum values in order of first appearance; anything else is dropped."""
    known = {m.value for m in allowed}
    out: list[str] = []
    for v in values or ():
        if isinstance(v, str) and v in known and v not in out:
            out.append(v)
    return out


def failure_score(failure_modes: Sequence[str]) -> float:
    """1 − share of the 10 failure modes hit."""
    return 1.0 - len(valid_labels(failure_modes, FailureMode)) / len(FailureMode)


def _history(messages: Sequence[Message]) -> str:
    names = {"assistant": "AGENT"}
    return "\n".join(f"{names.get(m.role, m.role.upper())}: {m.content}" for m in messages)


@register
class FailureModes(ConversationJudge):
    templates = prompts.VERSIONS
    label_field = "exhibited_failure_modes"
    spec = MetricSpec(
        id="conversation.failure_modes",
        category="conversation",
        description="Share of the 10 agent failure modes not exhibited in the conversation.",
        methods=(conversation_method(),),
        params_schema=conversation_params(),
        default_threshold=0.5,
        version="2",  # v2: trajectory-derived turns are judged
        sources=("agentsight:agent_sim.ConversationAnalyzer",),
    )

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        # agent_sim history format: "USER: ..." / "AGENT: ..." one turn per line.
        return {"conversation_history": _history(conversation_messages(case))}

    def score_parsed(self, parsed: Mapping[str, Any], template: PromptTemplate) -> float:
        return failure_score(parsed.get("exhibited_failure_modes", ()))

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        scores = [self.score_parsed(s.parsed, template) for s in samples]
        out = self.outcome(samples, scores, provenance, template, mode=mode)
        chosen = samples[scores.index(out.score)].parsed
        failures = valid_labels(chosen.get("exhibited_failure_modes", ()), FailureMode)
        axes = valid_labels(chosen.get("exhibited_stress_axes", ()), StressAxis)
        details = {
            **out.details,
            "exhibited_failure_modes": failures,
            "exhibited_stress_axes": axes,
            "failure_modes_hit": len(failures),
            "failure_modes_total": len(FailureMode),
        }
        dropped = [
            v
            for key, keep in (
                ("exhibited_failure_modes", failures),
                ("exhibited_stress_axes", axes),
            )
            for v in chosen.get(key, ())
            if v not in keep
        ]
        if dropped:
            details["invalid_labels"] = dropped
        expected = case.metadata.get("scenario", {})
        if isinstance(expected, Mapping) and expected.get("expected_failures"):
            details["expected_failures_hit"] = [
                f for f in failures if f in set(expected["expected_failures"])
            ]
        return Outcome(
            score=out.score,
            reason=chosen.get("summary"),
            details=details,
            provenance=out.provenance,
        )
