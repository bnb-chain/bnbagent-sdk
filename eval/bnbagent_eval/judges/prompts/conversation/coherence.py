"""conversation.coherence prompt templates.

v1: ported verbatim from AgentSight ``conversational_coherence_v0`` (system prompt, rubric template) and
``ConversationalCoherenceEvaluator`` (user prompt, labels).
The judge returns ``level`` (not ``score``); 1.0 = best.
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = 'You are an expert evaluator assessing conversational coherence across multiple turns.\n\nYour task is to evaluate context continuity and goal preservation throughout the conversation.\n\n## Evaluation Criteria\n\n**HIGHLY_COHERENT**: Perfect context continuity, goals maintained, logical flow\n**COHERENT**: Good context continuity with minor lapses\n**SOMEWHAT_COHERENT**: Noticeable context breaks or goal drift\n**MOSTLY_INCOHERENT**: Significant context breaks, goals frequently lost\n**INCOHERENT**: Poor context continuity, goals lost, disjointed flow\n\n## Assessment Factors\n\n1. **Context Continuity**: Does each turn build on previous context?\n2. **Goal Preservation**: Are user goals maintained throughout?\n3. **Topic Consistency**: Does the conversation stay on topic?\n4. **Logical Flow**: Do turns follow logically from each other?\n5. **Reference Resolution**: Are pronouns and references clear?\n\n## Scoring Rubric\n\n**HIGHLY_COHERENT** (1.0):\n- Zero context breaks across all turns\n- Perfect goal alignment throughout\n- Seamless topic flow and transitions\n- All references clearly resolved\n\n**COHERENT** (0.75):\n- 1-2 minor context lapses that don\'t disrupt flow\n- Goals mostly maintained with minor drift\n- Generally logical flow with smooth transitions\n- Most references clear\n\n**SOMEWHAT_COHERENT** (0.5):\n- 3-4 noticeable context breaks\n- Some goal drift or loss of focus\n- Occasional topic jumps or non-sequiturs\n- Some unclear references\n\n**MOSTLY_INCOHERENT** (0.25):\n- Multiple significant context breaks (4-5)\n- Goals frequently lost or abandoned\n- Frequent topic jumps with poor transitions\n- Many unclear or unresolved references\n\n**INCOHERENT** (0.0):\n- Frequent context loss (6+ breaks)\n- Goals completely abandoned or forgotten\n- Disjointed conversation with abrupt changes\n- Most references unresolved or confusing\n\n## Evaluation Process\n\n1. Review the full conversation history\n2. Identify the main topic and user goals\n3. Check if each turn maintains context from previous turns\n4. Assess if the conversation progresses logically\n5. Determine if goals are preserved or drift occurs\n6. Assign coherence rating using the rubric above\n\n## Response Efficiency\n\n- Be concise in your reasoning (2-3 sentences per factor)\n- Focus on key context breaks or goal drift\n- Provide specific turn numbers where issues occur\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Detailed reasoning covering all coherence factors"},\n    "level": {"type": "string", "enum": ["Incoherent", "Mostly Incoherent", "Somewhat Coherent", "Coherent", "Highly Coherent"], "description": "Coherence level rating"},\n    "coherence_issues": {"type": "array", "items": {"type": "string"}, "description": "Specific coherence problems"}\n  },\n  "required": ["reasoning", "level"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

LEVELS = {
    "Incoherent": 0.0,
    "Mostly Incoherent": 0.25,
    "Somewhat Coherent": 0.5,
    "Coherent": 0.75,
    "Highly Coherent": 1.0,
}

SCHEMA = {
    "type": "object",
    "required": ["reasoning", "level"],
    "properties": {
        "reasoning": {"type": "string"},
        "level": {"type": "string", "enum": list(LEVELS)},
        "coherence_issues": {"type": "array", "items": {"type": "string"}},
    },
}

V1 = PromptTemplate(
    metric_id="conversation.coherence",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "conversation": "# Conversation History:\n{conversation_history}",
        "task_conversation": "\n\n# Task:\nEvaluate the conversational coherence across these turns.",
    },
    output_schema=SCHEMA,
    labels=LEVELS,
    domain_rubric_template="# Domain-Specific Coherence Criteria\n\nApply these additional coherence considerations:\n\n{domain_rubric}",
    source="agentsight:conversational_coherence_v0",
)

VERSIONS = {"v1": V1}
