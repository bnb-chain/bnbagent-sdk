"""``conversation.role_adherence``: assistant turns stay in the assigned ``role`` (DeepEval)."""

from __future__ import annotations

from ...judges.prompts.conversation import role_adherence as prompts
from ..base import MetricSpec
from ..registry import register
from ._common import ConversationJudge, conversation_method, conversation_params


@register
class RoleAdherence(ConversationJudge):
    templates = prompts.VERSIONS
    prompt_params = ("role",)
    spec = MetricSpec(
        id="conversation.role_adherence",
        category="conversation",
        description="Every assistant turn adheres to the assigned role (5-tier judge).",
        methods=(conversation_method(),),
        params_schema=conversation_params(
            {"role": {"type": "string", "minLength": 1, "maxLength": 2000}}, required=["role"]
        ),
        default_threshold=0.5,
        sources=("deepeval:role_adherence",),
    )
