"""conversation.entity_consistency prompt templates.

v1: ported verbatim from AgentSight ``entity_consistency_v0`` (system prompt, rubric template) and
``EntityConsistencyEvaluator`` (user prompt, labels).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = 'You are an expert evaluator assessing entity consistency across conversation turns.\n\nYour task is to track entity references and detect inconsistencies in how entities are described or referenced.\n\n## Evaluation Criteria (5-Tier Scale)\n\n**Poor** (Score: 0.0): Significant contradictions in entity information across turns\n**Below Average** (Score: 0.25): Multiple noticeable inconsistencies or contradictions\n**Average** (Score: 0.5): Some inconsistencies in entity attributes or references\n**Good** (Score: 0.75): Minor inconsistencies in entity attributes\n**Excellent** (Score: 1.0): All entity references are accurate and consistent across turns\n\n## Entity Types & Examples\n\n**People**: Names, roles, relationships, attributes\n- Example: "John (CEO)" → later "John (CTO)" = INCONSISTENT\n- Example: "Sarah\'s manager" → later "Sarah\'s colleague" = INCONSISTENT\n\n**Places**: Locations, addresses, geographical references\n- Example: "Seattle office" → later "Portland office" for same entity = INCONSISTENT\n- Example: "downtown location" → later "suburban location" = INCONSISTENT\n\n**Dates/Times**: Temporal references, timelines\n- Example: "launched in 2020" → later "launched in 2019" = INCONSISTENT\n- Example: "meeting at 3pm" → later "meeting at 2pm" = INCONSISTENT\n\n**Organizations**: Company names, departments, teams\n- Example: "Marketing team" → later "Sales team" for same group = INCONSISTENT\n- Example: "Acme Corp" → later "Acme Inc" = INCONSISTENT\n\n**Attributes**: Characteristics, properties, specifications\n- Example: "blue car" → later "red car" for same vehicle = INCONSISTENT\n- Example: "5-year warranty" → later "3-year warranty" = INCONSISTENT\n\n## Evaluation Process\n\n1. Identify all entities mentioned (people, places, organizations, dates, etc.)\n2. Track how each entity is described across conversation turns\n3. Check for contradictions in entity attributes or relationships\n4. Note any changes in entity references that seem incorrect\n5. Assign consistency rating\n\n## Response Efficiency\n\n- Be concise in your reasoning (2-3 sentences)\n- List specific entity inconsistencies found\n- Quote the contradictory references\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Detailed reasoning with specific examples"},\n    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Entity consistency rating"},\n    "inconsistencies": {"type": "array", "items": {"type": "string"}, "description": "Specific entity inconsistencies found"}\n  },\n  "required": ["reasoning", "score"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

V1 = PromptTemplate(
    metric_id="conversation.entity_consistency",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "conversation": "# Conversation History:\n{conversation_history}",
        "task_conversation": "\n\n# Task:\nEvaluate entity consistency across these conversation turns.",
    },
    output_schema=five_tier_schema(
        {"inconsistencies": {"type": "array", "items": {"type": "string"}}}
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Entity Consistency Criteria\n\nApply these additional entity consistency considerations:\n\n{domain_rubric}",
    source="agentsight:entity_consistency_v0",
)

VERSIONS = {"v1": V1}
