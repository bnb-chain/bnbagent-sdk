"""``conversation.turn_context_precision``: useful contexts are ranked first (DeepEval)."""

from __future__ import annotations

from ...judges.prompts.conversation import turn_context_precision as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class TurnContextPrecision(ConversationJudge):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="conversation.turn_context_precision",
        category="conversation",
        description="Contexts useful for the expected outcome are ranked first (5-tier).",
        methods=(conversation_method("contexts", "expected"),),
        params_schema=conversation_params(),
        default_threshold=0.5,
        sources=("deepeval:turn_contextual_precision",),
    )
