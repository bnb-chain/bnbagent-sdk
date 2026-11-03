"""Shared base for conversation.* judges: conversation-only mode plus rendered params."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from ...adapters.traces.render import session_messages
from ...types.case import EvalCase, Message
from ...types.trajectory import AgentStep
from ..base import MethodSpec, MetricContext
from ..judge_base import JudgeMetric, judge_params, native_method


def conversation_method(*required: str) -> MethodSpec:
    """``native`` method that always needs ``conversation`` (plus ``required``)."""
    return native_method("conversation", *required)


def conversation_params(
    extra: Mapping[str, Any] | None = None, required: Sequence[str] = ()
) -> dict[str, Any]:
    """Judge params with ``input_mode`` limited to the only mode these templates define."""
    schema = judge_params({"input_mode": {"enum": ["auto", "conversation"]}, **(extra or {})})
    if required:
        schema["required"] = list(required)
    return schema


class ConversationJudge(JudgeMetric):
    """Conversation-mode judge; ``prompt_params`` are rendered into the prompt fields.

    The prompt text comes from ``fields()["conversation_history"]`` (``Role: content`` turns).
    """

    prompt_params: ClassVar[tuple[str, ...]] = ()
    # AgentSight CONVERSATIONAL category (``categories.validate_inputs``): at least 2 turns.
    min_turns: ClassVar[int] = 1

    def has_input(self, name: str, case: EvalCase, ctx: MetricContext) -> bool:
        if name == "conversation" and self.min_turns > 1:
            return turn_count(case) >= self.min_turns
        return super().has_input(name, case, ctx)

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        return render_prompt_params(super().fields(case, ctx, mode), ctx, self.prompt_params)


def turn_count(case: EvalCase) -> int:
    """Conversation turns as AgentSight counts them (``_get_conversation_turn_count``): explicit
    messages count one each (an input message list); a trajectory counts one turn per top-level
    agent step (a user prompt with its response), not two messages."""
    if case.conversation:
        return len(case.conversation)
    if case.trajectory is None:
        return 0
    return sum(
        1
        for step in case.trajectory.steps()
        if isinstance(step, AgentStep) and step.parent_id is None
        if step.user_prompt or step.response
    )


def conversation_messages(case: EvalCase) -> tuple[Message, ...]:
    """Explicit messages, else the turns derived from the trajectory's agent steps."""
    if case.conversation or case.trajectory is None:
        return tuple(case.conversation)
    return session_messages(case.trajectory)


def render_prompt_params(
    fields: dict[str, str], ctx: MetricContext, names: Sequence[str]
) -> dict[str, str]:
    """Add metric params rendered as prompt fields (lists joined with ``, ``)."""
    for name in names:
        value = ctx.params.get(name, "")
        fields[name] = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return fields
