"""``conversation.turn_faithfulness``: assistant claims are supported by the contexts."""

from __future__ import annotations

from ...judges.prompts.conversation import turn_faithfulness as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class TurnFaithfulness(ConversationJudge):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="conversation.turn_faithfulness",
        category="conversation",
        description="Claims in the assistant turns are supported by the contexts (5-tier).",
        methods=(conversation_method("contexts"),),
        params_schema=conversation_params(),
        default_threshold=0.5,
        sources=("deepeval:turn_faithfulness",),
    )
