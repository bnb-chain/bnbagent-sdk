"""``conversation.turn_context_recall``: contexts hold what the expected outcome needs."""

from __future__ import annotations

from ...judges.prompts.conversation import turn_context_recall as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class TurnContextRecall(ConversationJudge):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="conversation.turn_context_recall",
        category="conversation",
        description="The contexts contain the information of the expected outcome (5-tier).",
        methods=(conversation_method("contexts", "expected"),),
        params_schema=conversation_params(),
        default_threshold=0.5,
        sources=("deepeval:turn_contextual_recall",),
    )
