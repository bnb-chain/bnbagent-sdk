"""conversation.knowledge_retention prompt templates.

v1: ported verbatim from AgentSight ``knowledge_retention_v0`` (system prompt, rubric template) and
``KnowledgeRetentionEvaluator`` (user prompt, labels).
The judge returns ``level`` (not ``score``); 1.0 = best.
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = 'You are an expert evaluator assessing knowledge retention in multi-turn conversations.\n\nYour task is to evaluate how well the assistant remembers and correctly uses information provided by the user throughout the conversation.\n\n## Evaluation Criteria\n\n**PERFECT_RETENTION**: All user-provided information is remembered and used correctly\n**GOOD_RETENTION**: Most information retained with minor omissions\n**PARTIAL_RETENTION**: Some information retained but notable gaps exist\n**POOR_RETENTION**: Significant information forgotten or misremembered\n**NO_RETENTION**: User-provided information is completely forgotten or ignored\n\n## Assessment Factors\n\n1. **Factual Recall**: Does the assistant remember facts the user provided?\n2. **Preference Memory**: Does the assistant remember user preferences and constraints?\n3. **Context Continuity**: Does the assistant maintain context from earlier turns?\n4. **Correct Application**: Is remembered information applied correctly?\n5. **Consistency**: Are responses consistent with previously provided information?\n\n## Scoring Rubric\n\n**PERFECT_RETENTION** (1.0):\n- All user-provided facts, preferences, and constraints remembered\n- Information applied correctly in all subsequent responses\n- Zero contradictions with earlier user statements\n- Perfect context continuity across all turns\n\n**GOOD_RETENTION** (0.75):\n- Most user-provided information remembered (90%+)\n- 1-2 minor omissions that don\'t significantly impact responses\n- Information generally applied correctly\n- Good context continuity with minor lapses\n\n**PARTIAL_RETENTION** (0.5):\n- Some user-provided information remembered (50-89%)\n- Notable gaps in recall affecting response quality\n- Some information misapplied or partially forgotten\n- Moderate context continuity issues\n\n**POOR_RETENTION** (0.25):\n- Limited information retained (25-49%)\n- Significant facts or preferences forgotten\n- Frequent misapplication of remembered information\n- Poor context continuity, requires user to repeat information\n\n**NO_RETENTION** (0.0):\n- Almost no user-provided information retained (<25%)\n- User must constantly repeat information\n- Responses ignore or contradict user-provided context\n- No evidence of learning from conversation history\n\n## Evaluation Process\n\n1. Identify all user-provided information (facts, preferences, constraints, context)\n2. Track how this information is used in subsequent assistant responses\n3. Note any forgotten, misremembered, or misapplied information\n4. Assess overall retention quality using the rubric above\n5. List specific items that were forgotten or misremembered\n\n## Response Efficiency\n\n- Be concise in your reasoning (2-3 sentences per factor)\n- Focus on specific examples of retention or forgetting\n- Provide turn numbers where retention issues occur\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Detailed reasoning covering retention assessment"},\n    "level": {"type": "string", "enum": ["No Retention", "Poor Retention", "Partial Retention", "Good Retention", "Perfect Retention"], "description": "Retention level rating"},\n    "forgotten_items": {"type": "array", "items": {"type": "string"}, "description": "Specific information that was forgotten or misremembered"}\n  },\n  "required": ["reasoning", "level"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

LEVELS = {
    "No Retention": 0.0,
    "Poor Retention": 0.25,
    "Partial Retention": 0.5,
    "Good Retention": 0.75,
    "Perfect Retention": 1.0,
}

SCHEMA = {
    "type": "object",
    "required": ["reasoning", "level"],
    "properties": {
        "reasoning": {"type": "string"},
        "level": {"type": "string", "enum": list(LEVELS)},
        "forgotten_items": {"type": "array", "items": {"type": "string"}},
    },
}

V1 = PromptTemplate(
    metric_id="conversation.knowledge_retention",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "conversation": "# Conversation History:\n{conversation_history}",
        "task_conversation": "\n\n# Task:\nEvaluate the knowledge retention across these turns.",
    },
    output_schema=SCHEMA,
    labels=LEVELS,
    domain_rubric_template="# Domain-Specific Retention Criteria\n\nApply these additional retention considerations:\n\n{domain_rubric}",
    source="agentsight:knowledge_retention_v0",
)

VERSIONS = {"v1": V1}
