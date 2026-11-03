"""quality.contradiction prompt templates.

v1: ported verbatim from AgentSight ``contradiction_v0`` (prompt text, modes, contradiction levels).

The judge returns ``level`` (not ``score``); 1.0 = no contradictions.
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = 'You are an expert evaluator detecting contradictions in agent responses.\n\nYour task is to identify logical contradictions within a response or across conversation turns.\n\n## Evaluation Criteria\n\n**NO_CONTRADICTIONS**: Response is logically consistent throughout with no contradictions\n**MINIMAL_CONTRADICTIONS**: Very minor inconsistencies in peripheral details that don\'t affect understanding\n**MINOR_CONTRADICTIONS**: Small inconsistencies that don\'t significantly impact meaning\n**MODERATE_CONTRADICTIONS**: Clear contradictions that create confusion\n**MAJOR_CONTRADICTIONS**: Severe contradictions that undermine the response\n\n## Types of Contradictions\n\n1. **Direct Contradictions**: Explicitly stating opposite claims\n2. **Implicit Contradictions**: Claims that logically conflict\n3. **Temporal Contradictions**: Inconsistent timeline or sequence\n4. **Factual Contradictions**: Conflicting factual statements\n\n## Severity Assessment\n\n**MINIMAL Contradictions**:\n- Very slight inconsistencies in non-essential peripheral details\n- Phrasing variations that could technically be seen as inconsistent\n- Impact: Negligible, reader unlikely to notice\n\n**MINOR Contradictions**:\n- Contradictions in non-essential details\n- Ambiguous phrasing that could be interpreted as contradictory\n- Impact: Minimal confusion, doesn\'t affect main message\n\n**MODERATE Contradictions**:\n- Contradictions in supporting facts or examples\n- Inconsistent explanations of the same concept\n- Impact: Creates confusion, requires clarification\n\n**MAJOR Contradictions**:\n- Contradictions in main claims or conclusions\n- Logically impossible statements\n- Impact: Undermines entire response credibility\n\n## Response Efficiency\n\n- Be concise in your reasoning (2-3 sentences)\n- Quote the specific contradictory statements\n- Clearly indicate the severity level and impact\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Detailed reasoning with specific examples"},\n    "level": {"type": "string", "enum": ["No Contradictions", "Minimal Contradictions", "Minor Contradictions", "Moderate Contradictions", "Major Contradictions"], "description": "Contradiction severity level"},\n    "contradictions": {"type": "array", "items": {"type": "string"}, "description": "Specific contradictions found"}\n  },\n  "required": ["reasoning", "level"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

LEVELS = {
    "No Contradictions": 1.0,
    "Minimal Contradictions": 0.75,
    "Minor Contradictions": 0.5,
    "Moderate Contradictions": 0.25,
    "Major Contradictions": 0.0,
}

SCHEMA = {
    "type": "object",
    "required": ["reasoning", "level"],
    "properties": {
        "reasoning": {"type": "string"},
        "level": {"type": "string", "enum": list(LEVELS)},
        "contradictions": {"type": "array", "items": {"type": "string"}},
    },
}

V1 = PromptTemplate(
    metric_id="quality.contradiction",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Response to Evaluate:\n{actual_output}\n\n# Task:\nDetect any contradictions in this response.",
        "with_expected": "# Expected Response:\n{expected_output}\n\n# Actual Response:\n{actual_output}\n\n# Task:\nDetect any contradictions in the actual response. Consider whether contradictions exist within the actual response itself, and optionally note if the actual response contradicts the expected response.",
        "conversation": "# Conversation History:\n{conversation_history}\n\n# Task:\nDetect any contradictions across the conversation turns. Look for statements that contradict earlier statements in the conversation.",
    },
    output_schema=SCHEMA,
    labels=LEVELS,
    domain_rubric_template="# Domain-Specific Contradiction Criteria\n\nApply these additional contradiction considerations:\n\n{domain_rubric}",
    source="agentsight:contradiction_v0",
)

VERSIONS = {"v1": V1}
