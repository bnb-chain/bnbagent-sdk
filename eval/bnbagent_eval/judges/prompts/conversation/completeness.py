"""conversation.completeness prompt templates.

v1: ported verbatim from AgentSight ``conversation_completeness_v0`` (system prompt, rubric template) and
``ConversationCompletenessEvaluator`` (user prompt, labels).
The judge returns ``level`` (not ``score``); 1.0 = best.
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = 'You are an expert evaluator assessing conversation completeness in multi-turn conversations.\n\nYour task is to evaluate whether all user requests, questions, and needs were fully addressed by the assistant throughout the conversation.\n\n## Evaluation Criteria\n\n**FULLY_COMPLETE**: All user requests addressed comprehensively\n**MOSTLY_COMPLETE**: Most requests addressed with minor gaps\n**PARTIALLY_COMPLETE**: Some requests addressed but notable omissions\n**MOSTLY_INCOMPLETE**: Many requests left unaddressed\n**INCOMPLETE**: User requests largely ignored or unaddressed\n\n## Assessment Factors\n\n1. **Request Coverage**: Were all explicit user requests addressed?\n2. **Question Answering**: Were all user questions answered?\n3. **Follow-up Handling**: Were follow-up questions and clarifications addressed?\n4. **Implicit Needs**: Were implicit user needs recognized and addressed?\n5. **Resolution Quality**: Were requests resolved satisfactorily, not just acknowledged?\n\n## Scoring Rubric - USE STRICT COUNTING\n\nFirst, count the total number of distinct user requests/questions. Then count how many were fully addressed.\n\n**FULLY_COMPLETE** (1.0):\n- 100% of user requests fully addressed\n- All questions answered comprehensively\n- All follow-ups handled appropriately\n- No outstanding unresolved items\n\n**MOSTLY_COMPLETE** (0.75):\n- 80-99% of user requests addressed\n- Most questions answered with 1-2 minor gaps\n- Follow-ups generally handled well\n- Overall user needs met\n\n**PARTIALLY_COMPLETE** (0.5):\n- 50-79% of user requests addressed\n- Some questions left unanswered or partially answered\n- Some follow-ups missed or inadequately handled\n- Notable gaps in addressing user needs\n\n**MOSTLY_INCOMPLETE** (0.25):\n- 25-49% of user requests addressed\n- Many questions left unanswered\n- Follow-ups frequently ignored\n- Significant user needs unmet\n\n**INCOMPLETE** (0.0):\n- <25% of user requests addressed\n- Most questions ignored or deflected\n- Follow-ups not handled\n- User needs largely unmet\n\n## Evaluation Process\n\n1. List ALL user requests, questions, and needs explicitly\n2. Count the total number of distinct items (N)\n3. For each item, determine if it was: FULLY addressed, PARTIALLY addressed, or NOT addressed\n4. Calculate: addressed_count / total_count\n5. Use the percentage to determine the rating\n6. List specific unaddressed items\n\nIMPORTANT: If a user explicitly asks for N items (e.g., "recommend 5 things"), and only M are addressed, the completion rate is M/N.\n\n## Response Efficiency\n\n- Be concise in your reasoning (2-3 sentences per factor)\n- Focus on specific unaddressed items\n- Provide turn numbers where gaps occur\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Detailed reasoning covering completeness assessment"},\n    "level": {"type": "string", "enum": ["Incomplete", "Mostly Incomplete", "Partially Complete", "Mostly Complete", "Fully Complete"], "description": "Completeness level rating"},\n    "unaddressed_items": {"type": "array", "items": {"type": "string"}, "description": "Specific requests or questions that were not addressed"}\n  },\n  "required": ["reasoning", "level"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

LEVELS = {
    "Incomplete": 0.0,
    "Mostly Incomplete": 0.25,
    "Partially Complete": 0.5,
    "Mostly Complete": 0.75,
    "Fully Complete": 1.0,
}

SCHEMA = {
    "type": "object",
    "required": ["reasoning", "level"],
    "properties": {
        "reasoning": {"type": "string"},
        "level": {"type": "string", "enum": list(LEVELS)},
        "unaddressed_items": {"type": "array", "items": {"type": "string"}},
    },
}

V1 = PromptTemplate(
    metric_id="conversation.completeness",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "conversation": "# Conversation History:\n{conversation_history}",
        "task_conversation": "\n\n# Task:\nEvaluate the conversation completeness across these turns.",
    },
    output_schema=SCHEMA,
    labels=LEVELS,
    domain_rubric_template="# Domain-Specific Completeness Criteria\n\nApply these additional completeness considerations:\n\n{domain_rubric}",
    source="agentsight:conversation_completeness_v0",
)

VERSIONS = {"v1": V1}
