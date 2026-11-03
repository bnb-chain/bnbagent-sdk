"""``conversation.entity_consistency``: entities are referenced consistently across turns.

Port of AgentSight EntityConsistencyEvaluator (5-tier ``score`` labels).
"""

from __future__ import annotations

from ...judges.prompts.conversation import entity_consistency as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class EntityConsistency(ConversationJudge):
    min_turns = 2  # AgentSight CONVERSATIONAL: >= 2 turns
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="conversation.entity_consistency",
        category="conversation",
        description="Entities (names, values, attributes) stay consistent across turns.",
        methods=(conversation_method(),),
        params_schema=conversation_params(),
        default_threshold=0.5,
        version="2",  # v2: a trajectory needs 2 agent turns (not 2 messages)
        sources=("agentsight:EntityConsistencyEvaluator",),
    )
