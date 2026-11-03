"""quality.tone prompt templates.

v1: ported verbatim from AgentSight ``tone_v0`` (prompt text, modes, labels).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = 'You are an expert evaluator assessing the appropriateness of agent response tone.\n\nEvaluate tone based on the conversational context, NOT on response length, formality level, or cultural communication styles.\n\n## Evaluation Criteria (5-Tier Scale)\n\n**Poor** (Score: 0.0): Inappropriate, disrespectful, unhelpful, or significantly mismatched to context\n**Below Average** (Score: 0.25): Notable tone issues, somewhat inappropriate or unhelpful\n**Average** (Score: 0.5): Generally appropriate but some tone issues present\n**Good** (Score: 0.75): Respectful and helpful, minor tone mismatches with context\n**Excellent** (Score: 1.0): Respectful, helpful, clear, and perfectly matches context\n\n## Assessment Factors\n\n1. **Respect**: Is the response respectful regardless of user\'s tone?\n2. **Helpfulness**: Does the tone convey willingness to assist?\n3. **Clarity**: Is the tone clear and not confusing?\n4. **Context-match**: Does the tone match the seriousness/casualness of the topic?\n\n## Cultural Considerations\n\n- Directness vs. indirectness varies by culture - both can be appropriate\n- Formality expectations differ across contexts - focus on appropriateness, not style\n- Evaluate respect and helpfulness, not communication style preferences\n- Avoid penalizing culturally appropriate communication patterns\n- Consider the context: technical support may be direct, customer service may be warmer\n\n## Response Efficiency\n\n- Be concise in your reasoning (2-3 sentences)\n- Focus on respect, helpfulness, and context-appropriateness\n- Avoid subjective style preferences\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Reasoning covering respect, helpfulness, clarity, and context-match"},\n    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical tone rating"},\n    "tone_issues": {"type": "array", "items": {"type": "string"}, "description": "Specific tone problems identified"}\n  },\n  "required": ["reasoning", "score"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

V1 = PromptTemplate(
    metric_id="quality.tone",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# User Query:\n{input}\n\n# Agent Response:\n{actual_output}\n\n# Task:\nEvaluate the appropriateness of the response tone.",
        "with_expected": "# User Query:\n{input}\n\n# Expected Response:\n{expected_output}\n\n# Actual Response:\n{actual_output}\n\n# Task:\nEvaluate the appropriateness of the actual response tone. Consider the expected response as a reference for appropriate tone.",
        "conversation": "{conversation_history}\n\n# Task:\nEvaluate the appropriateness of the assistant's tone across the conversation.",
    },
    output_schema=five_tier_schema(
        {"tone_issues": {"type": "array", "items": {"type": "string"}}}
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Tone Guidelines\n\nApply these additional tone considerations:\n\n{domain_rubric}",
    source="agentsight:tone_v0",
)

VERSIONS = {"v1": V1}
