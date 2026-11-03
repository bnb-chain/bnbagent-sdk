"""quality.fluency prompt templates.

v1: ported verbatim from AgentSight ``fluency_v0`` (prompt text, modes, labels).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = 'You are an expert evaluator assessing the fluency of agent responses.\n\nYour task is to evaluate the grammatical correctness, readability, and natural phrasing of the response.\n\n## Evaluation Criteria (5-Tier Scale)\n\n**Poor** (Score: 0.0): Many grammatical errors, poor readability, unnatural phrasing\n**Below Average** (Score: 0.25): Several grammatical errors, below acceptable readability, awkward phrasing\n**Average** (Score: 0.5): Some grammatical errors, acceptable readability, occasional awkward phrasing\n**Good** (Score: 0.75): Minor grammatical issues, good readability, mostly natural phrasing\n**Excellent** (Score: 1.0): Perfect or near-perfect grammar, highly readable, natural phrasing\n\n## Evaluation Process\n\n1. Check for grammatical errors (subject-verb agreement, tense, articles, etc.)\n2. Assess readability (sentence structure, flow, coherence)\n3. Evaluate naturalness of phrasing\n4. Provide specific examples of issues found\n5. Assign fluency rating\n\n## Conversation-Specific Guidelines (when applicable):\n- Evaluate fluency for each turn in the conversation\n- Check if grammatical quality is maintained throughout\n- Identify if readability degrades across turns\n- Assess consistency of natural phrasing\n\n## Response Efficiency\n\n- Be concise in your reasoning (2-3 sentences)\n- Focus on the most significant fluency issues\n- Provide 2-3 specific examples maximum\n- Avoid exhaustive lists of minor errors\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Detailed reasoning with specific examples of issues"},\n    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical fluency rating"},\n    "grammar_issues": {"type": "array", "items": {"type": "string"}, "description": "Specific grammatical errors found"}\n  },\n  "required": ["reasoning", "score"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

V1 = PromptTemplate(
    metric_id="quality.fluency",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Response to Evaluate:\n{actual_output}",
        "with_expected": "# Expected Response (Reference):\n{expected_output}\n\n# Actual Response to Evaluate:\n{actual_output}",
        "conversation": "# Conversation History:\n{conversation_history}",
        "task_simple": "\n\n# Task:\nEvaluate the fluency of this response.",
        "task_with_expected": "\n\n# Task:\nEvaluate the fluency of the actual response compared to the expected response. Consider:\n- Does the actual response have more grammatical errors than the expected response?\n- Is the actual response less readable or natural than the expected response?\n- Are there fluency issues in the actual response that the expected response avoids?",
        "task_conversation": "\n\n# Task:\nEvaluate the fluency of the assistant's responses across the conversation. Consider:\n- Are responses grammatically correct throughout?\n- Is readability maintained across all turns?\n- Do responses use natural phrasing consistently?",
    },
    output_schema=five_tier_schema(
        {"grammar_issues": {"type": "array", "items": {"type": "string"}}}
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Fluency Criteria\n\nApply these additional fluency considerations:\n\n{domain_rubric}",
    source="agentsight:fluency_v0",
)

VERSIONS = {"v1": V1}
