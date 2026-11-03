"""``conversation.topic_adherence``: answer on-topic questions, decline off-topic ones."""

from __future__ import annotations

from ...judges.prompts.conversation import topic_adherence as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class TopicAdherence(ConversationJudge):
    templates = prompts.VERSIONS
    prompt_params = ("topics",)
    spec = MetricSpec(
        id="conversation.topic_adherence",
        category="conversation",
        description="The assistant stays within the allowed topics (5-tier judge).",
        methods=(conversation_method(),),
        params_schema=conversation_params(
            {
                "topics": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1, "maxLength": 500},
                }
            },
            required=["topics"],
        ),
        default_threshold=0.5,
        sources=("deepeval:topic_adherence", "ragas:topic_adherence"),
    )
