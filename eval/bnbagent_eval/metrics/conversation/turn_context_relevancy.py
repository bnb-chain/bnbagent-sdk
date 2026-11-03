"""``conversation.turn_context_relevancy``: contexts are relevant to the user's messages."""

from __future__ import annotations

from ...judges.prompts.conversation import turn_context_relevancy as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class TurnContextRelevancy(ConversationJudge):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="conversation.turn_context_relevancy",
        category="conversation",
        description="The retrieved contexts are relevant to the user's messages (5-tier).",
        methods=(conversation_method("contexts"),),
        params_schema=conversation_params(),
        default_threshold=0.5,
        sources=("deepeval:turn_contextual_relevancy",),
    )
