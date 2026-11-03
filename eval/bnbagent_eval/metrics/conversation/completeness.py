"""``conversation.completeness``: every user request in the conversation is addressed.

Port of AgentSight ConversationCompletenessEvaluator; the judge rates a completeness ``level``.
"""

from __future__ import annotations

from ...judges.prompts.conversation import completeness as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class ConversationCompleteness(ConversationJudge):
    min_turns = 2  # AgentSight CONVERSATIONAL: >= 2 turns
    templates = prompts.VERSIONS
    label_field = "level"
    spec = MetricSpec(
        id="conversation.completeness",
        category="conversation",
        description="All user requests and questions in the conversation are addressed.",
        methods=(conversation_method(),),
        params_schema=conversation_params(),
        default_threshold=0.5,
        version="2",  # v2: a trajectory needs 2 agent turns (not 2 messages)
        sources=(
            "agentsight:ConversationCompletenessEvaluator",
            "deepeval:conversation_completeness",
        ),
    )
