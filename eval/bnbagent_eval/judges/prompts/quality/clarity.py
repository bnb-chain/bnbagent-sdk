"""quality.clarity prompt templates.

v1: ported verbatim from AgentSight ``clarity_v0`` (prompt text, modes, labels).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = 'You are an expert evaluator assessing response clarity.\n\n# Evaluation Criteria\n\nAssess how clear and easy to understand the response is:\n\n1. **Structure**: Is the response well-organized and logically structured?\n2. **Language**: Is the language clear, precise, and appropriate?\n3. **Readability**: Is it easy to follow and understand?\n4. **Ambiguity**: Are statements unambiguous and well-defined?\n\n# Scoring Guidelines (5-Tier Scale)\n\n- **Excellent** (1.0): Crystal clear, well-structured, easy to understand\n- **Good** (0.75): Clear with minor areas that could be clearer\n- **Average** (0.5): Understandable but has clarity issues\n- **Below Average** (0.25): Confusing structure or unclear language\n- **Poor** (0.0): Very difficult to understand, poorly organized\n\nBe objective. List specific clarity issues when scoring below Excellent.\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Reasoning explaining the clarity assessment"},\n    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical clarity rating"},\n    "clarity_issues": {"type": "array", "items": {"type": "string"}, "description": "Specific clarity problems identified"}\n  },\n  "required": ["reasoning", "score"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

V1 = PromptTemplate(
    metric_id="quality.clarity",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Response to Evaluate:\n{actual_output}",
        "with_input": "# Question/Request:\n{input}\n\n# Response to Evaluate:\n{actual_output}",
        "with_expected": "# Expected Response (Reference):\n{expected_output}\n\n# Actual Response to Evaluate:\n{actual_output}",
        "with_input_and_expected": "# Question/Request:\n{input}\n\n# Expected Response (Reference):\n{expected_output}\n\n# Actual Response to Evaluate:\n{actual_output}",
        "conversation": "# Conversation History:\n{conversation_history}",
        "task_simple": "\n\n# Task:\nEvaluate the clarity of this response.",
        "task_with_input": "\n\n# Task:\nEvaluate how clearly the response addresses the question.",
        "task_with_expected": "\n\n# Task:\nEvaluate the clarity of the response compared to the reference.",
        "task_with_input_and_expected": "\n\n# Task:\nEvaluate the clarity of the response compared to the reference.",
        "task_conversation": "\n\n# Task:\nEvaluate the clarity of the assistant's responses across this conversation.",
    },
    output_schema=five_tier_schema(
        {"clarity_issues": {"type": "array", "items": {"type": "string"}}}
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Clarity Criteria\n\nApply these additional clarity considerations:\n\n{domain_rubric}",
    source="agentsight:clarity_v0",
)

VERSIONS = {"v1": V1}
