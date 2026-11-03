"""quality.helpfulness prompt templates.

v1: ported verbatim from AgentSight ``helpfulness_v0`` (prompt text, modes, labels).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = 'You are an objective judge evaluating the helpfulness of an AI assistant\'s response from the user\'s perspective. Your task is to assess whether the assistant\'s turn moves the user closer to achieving or formulating their goals.\n\nIMPORTANT: Evaluate purely from the user\'s perspective, without considering the factual accuracy or backend operations. Focus only on how the response helps the user progress towards their goals.\n\n# Evaluation Framework\n\nFollow this systematic approach:\n\n1. **Identify User Goal**: What is the user trying to achieve or understand?\n2. **Assess Progress**: How much closer is the user to their goal after this response?\n3. **Evaluate Quality**: Is the information actionable and useful?\n4. **Consider Obstacles**: Does the response create any barriers or confusion?\n5. **Assign Score**: Use the 5-tier scale based on net progress toward goal\n\n# Evaluation Scale (5-Tier):\n\n**Poor** (Score: 0.0)\n- Fails to address the user\'s needs\n- Creates confusion, misunderstanding, or obstruction\n- Provides irrelevant, misleading, or nonsensical information\n- Actively hinders goal progress\n\n**Below Average** (Score: 0.25)\n- Partially addresses needs but with significant gaps\n- Provides some relevant information but incomplete or unclear\n- Makes minimal progress with unnecessary detours or delays\n- Mixed helpful and unhelpful elements\n\n**Average** (Score: 0.5)\n- Addresses some of the user\'s needs adequately\n- Provides relevant information but with notable gaps\n- Moves user somewhat toward their goal\n- Acceptable but room for improvement\n\n**Good** (Score: 0.75)\n- Addresses the user\'s needs with minor gaps\n- Provides relevant, actionable information\n- Moves user meaningfully toward their goal\n- Clear and useful response overall\n\n**Excellent** (Score: 1.0)\n- Fully addresses the user\'s needs\n- Provides comprehensive, actionable information\n- Anticipates follow-up needs or concerns\n- Gets user as close as possible to goal given any constraints\n\n# Response Efficiency\n\n- Be concise in your reasoning (2-3 sentences)\n- Focus on goal progress assessment\n- Clearly state the score and justification\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Step by step reasoning to derive the final score"},\n    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical helpfulness rating"}\n  },\n  "required": ["reasoning", "score"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

V1 = PromptTemplate(
    metric_id="quality.helpfulness",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# User Query:\n{input}\n\n# Assistant Response:\n{actual_output}\n\n# Task:\nEvaluate how helpful the assistant's response is in addressing the user's query.",
        "with_expected": "# User Query:\n{input}\n\n# Expected Response:\n{expected_output}\n\n# Actual Response:\n{actual_output}\n\n# Task:\nEvaluate how helpful the actual response is in addressing the user's query. Consider the expected response as a reference for what would be ideal.",
        "conversation": "{conversation_history}\n\n# Task:\nEvaluate how helpful the assistant's most recent response is in moving the user closer to their goal.",
    },
    output_schema=five_tier_schema(),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Helpfulness Criteria\n\nConsider these domain requirements when evaluating helpfulness:\n\n{domain_rubric}",
    source="agentsight:helpfulness_v0",
)

VERSIONS = {"v1": V1}
