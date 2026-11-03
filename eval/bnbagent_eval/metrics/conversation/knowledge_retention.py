"""``conversation.knowledge_retention``: the assistant remembers what the user told it.

Port of AgentSight KnowledgeRetentionEvaluator; the judge rates a retention ``level``.
"""

from __future__ import annotations

from ...judges.prompts.conversation import knowledge_retention as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class KnowledgeRetention(ConversationJudge):
    min_turns = 2  # AgentSight CONVERSATIONAL: >= 2 turns
    templates = prompts.VERSIONS
    label_field = "level"
    spec = MetricSpec(
        id="conversation.knowledge_retention",
        category="conversation",
        description="The assistant retains and correctly reuses user-provided information.",
        methods=(conversation_method(),),
        params_schema=conversation_params(),
        default_threshold=0.5,
        version="2",  # v2: a trajectory needs 2 agent turns (not 2 messages)
        sources=("agentsight:KnowledgeRetentionEvaluator", "deepeval:knowledge_retention"),
    )
