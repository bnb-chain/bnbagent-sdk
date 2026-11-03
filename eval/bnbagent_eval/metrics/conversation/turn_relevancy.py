"""``conversation.turn_relevancy``: each assistant turn fits the preceding context (DeepEval)."""

from __future__ import annotations

from ...judges.prompts.conversation import turn_relevancy as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class TurnRelevancy(ConversationJudge):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="conversation.turn_relevancy",
        category="conversation",
        description="Each assistant turn is relevant to the preceding context (5-tier judge).",
        methods=(conversation_method(),),
        params_schema=conversation_params(),
        default_threshold=0.5,
        sources=("deepeval:turn_relevancy",),
    )
