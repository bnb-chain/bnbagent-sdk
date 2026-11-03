"""``conversation.coherence``: context continuity and goal preservation across turns.

Port of AgentSight ConversationalCoherenceEvaluator; the judge rates a coherence ``level``.
"""

from __future__ import annotations

from ...judges.prompts.conversation import coherence as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class ConversationCoherence(ConversationJudge):
    min_turns = 2  # AgentSight CONVERSATIONAL: >= 2 turns
    templates = prompts.VERSIONS
    label_field = "level"
    spec = MetricSpec(
        id="conversation.coherence",
        category="conversation",
        description="Turns keep context, goals and logical flow across the conversation.",
        methods=(conversation_method(),),
        params_schema=conversation_params(),
        default_threshold=0.5,
        version="2",  # v2: a trajectory needs 2 agent turns (not 2 messages)
        sources=("agentsight:ConversationalCoherenceEvaluator",),
    )
