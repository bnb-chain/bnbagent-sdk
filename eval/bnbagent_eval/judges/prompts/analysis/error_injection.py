"""generation.error_injection prompt templates: judge-assisted two-step error injection.

v1: ported from AgentSight ``agent_cost_reduction/error_injector.py`` (``BASE_CONTEXT``,
``SELECT_AND_PLAN_PROMPT``, ``EXECUTE_EDIT_PROMPT``, ``WHITEBOX_VISIBILITY`` /
``BLACKBOX_VISIBILITY``). The trusted parts (evaluation criteria, visibility) are in the system
prompt; the conversation is fenced; replies are schema-validated JSON objects.
"""

from __future__ import annotations

from .. import PromptTemplate

BASE_CONTEXT = """You are creating test data for benchmarking LLM-as-judge models.

CONTEXT: We have conversations between users and an AI agent. We need to create a balanced dataset where half the conversations have realistic errors and half are clean. Different LLM judges (cheap vs expensive) will then score these conversations. The goal is to see which cheap judge can reliably distinguish good conversations from bad ones, just like the expensive judge does.

For this to work, the errors must be realistic: the kind of mistakes we would actually see in traces of this agent operating in production. Not obvious garbage, but subtle failures that would slip past a casual reader and require a careful evaluator to catch.

CRITICAL - Conversation coherence: Only the single edited message changes. All other turns stay exactly as they are. This means your edit must remain coherent with what comes before AND after it. For example: if the user's next message says "thanks for the offer, I'll take it", you cannot remove the offer entirely."""

WHITEBOX_VISIBILITY = (
    "The judge has access to the FULL execution trace: not just the conversation text, but "
    "also every action the agent took internally (tool calls, sub-agent invocations, "
    "parameters, results)."
)
BLACKBOX_VISIBILITY = (
    "The judge only sees the conversation text (user and agent messages). It does not see any "
    "internal execution traces or actions. Any error in the text is sufficient."
)

SELECT_SYSTEM_V1 = (
    BASE_CONTEXT
    + """

You select which message to edit to create evaluation test data. Pick ONE message (user or agent) to edit so the conversation would score poorly on the evaluation criteria given below. Only that single message will change.

## Output Format

Return only a JSON object: {"turn_index": <int>, "role": "<user or agent>", "edit_suggestion": "<describe specifically what realistic error to introduce and why it would cause a low score>"}."""
)

EDIT_SYSTEM_V1 = (
    BASE_CONTEXT
    + """

You rewrite messages to contain specific errors for evaluation testing. Rewrite the given message to introduce the realistic error described in the edit instructions.

Rules:
- Only this single message will change; all other messages stay exactly as they are
- Keep the message roughly the same length and tone

## Output Format

Return only a JSON object: {"rewritten_message": "<the rewritten message>", "error_description": "<1-2 sentence description of the realistic error introduced>"}."""
)

SELECT_V1 = PromptTemplate(
    metric_id="generation.error_injection.select",
    version="v1",
    system=SELECT_SYSTEM_V1,
    parts={
        "simple": "Conversation:\n{conversation}",
        "task_simple": "\n\nPick ONE message to edit and describe the edit.",
    },
    output_schema={
        "type": "object",
        "required": ["turn_index", "edit_suggestion"],
        "properties": {
            "turn_index": {"type": "integer", "minimum": 0},
            "role": {"type": "string"},
            "edit_suggestion": {"type": "string"},
        },
    },
    domain_rubric_template="<evaluation_criteria>\n{domain_rubric}\n</evaluation_criteria>",
    source="agentsight:error_injector.SELECT_AND_PLAN_PROMPT",
)

EDIT_V1 = PromptTemplate(
    metric_id="generation.error_injection.edit",
    version="v1",
    system=EDIT_SYSTEM_V1,
    parts={
        "simple": (
            "Original message ({role}, turn {turn_index}):\n{original_text}\n\n"
            "Edit instructions:\n{edit_suggestion}\n\n"
            "Full conversation for context:\n{conversation}"
        ),
        "task_simple": "\n\nRewrite the original message following the edit instructions.",
    },
    output_schema={
        "type": "object",
        "required": ["rewritten_message"],
        "properties": {
            "rewritten_message": {"type": "string"},
            "error_description": {"type": "string"},
        },
    },
    domain_rubric_template="<evaluation_criteria>\n{domain_rubric}\n</evaluation_criteria>",
    source="agentsight:error_injector.EXECUTE_EDIT_PROMPT",
)

VERSIONS = {"v1": {"select": SELECT_V1, "edit": EDIT_V1}}
