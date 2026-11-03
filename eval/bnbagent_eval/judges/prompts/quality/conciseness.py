"""quality.conciseness prompt templates.

v1: ported verbatim from AgentSight ``conciseness_v0`` (prompt text, modes, sub-dimension labels).

One call returns four ratings (verbosity, redundancy, repetition, overall); the
score is the weighted mean of the first three (``ConcisenessEvaluator``).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate

SYSTEM_V1 = 'You are an expert evaluator assessing response conciseness across three dimensions: verbosity, redundancy, and repetition.\n\n## Verbosity Dimension (5-Tier)\n**Very Verbose** (0.0): Excessively long, significant filler content\n**Verbose** (0.25): Unnecessarily long, some filler content\n**Acceptable** (0.5): Slightly verbose but acceptable\n**Concise** (0.75): Appropriate length, minimal filler\n**Very Concise** (1.0): Optimal length, no filler, information-dense\n\n## Redundancy Dimension (5-Tier)\n**High Redundancy** (0.0): Significant semantic overlap\n**Moderate Redundancy** (0.25): Noticeable repeated ideas/concepts\n**Some Redundancy** (0.5): Some semantic repetition\n**Minimal Redundancy** (0.75): Minor semantic repetition\n**No Redundancy** (1.0): Each idea presented once, no semantic overlap\n\n## Repetition Dimension (5-Tier)\n**High Repetition** (0.0): Excessive repeated phrases\n**Moderate Repetition** (0.25): Noticeable lexical repetition\n**Some Repetition** (0.5): Some repeated phrases\n**Minimal Repetition** (0.75): Few repeated phrases\n**No Repetition** (1.0): No repeated phrases or patterns\n\n## Overall Conciseness (5-Tier)\n**Poor** (0.0): Verbose, redundant, and repetitive\n**Below Average** (0.25): Significant issues in multiple dimensions\n**Average** (0.5): Some verbosity, redundancy, or repetition\n**Good** (0.75): Mostly concise with minor issues\n**Excellent** (1.0): Concise, no redundancy, no repetition\n\n## Quantitative Guidelines\n\n**Verbosity Assessment**:\n- Count unnecessary filler words/phrases ("basically", "actually", "in order to")\n- Compare response length to minimum needed\n- Ideal: Could be 20-30% shorter without losing meaning\n\n**Redundancy Detection**:\n- Identify semantically identical statements\n- Look for paraphrased repetitions of the same concept\n- Flag concepts mentioned more than once without adding new information\n\n**Repetition Analysis**:\n- Track repeated phrases (3+ words appearing multiple times)\n- Count lexical repetitions (same words/phrases used repeatedly)\n- Note formulaic patterns that don\'t add value\n\n## Conversation-Specific Guidelines (when applicable):\n- Check if information is repeated unnecessarily across turns\n- Assess if responses build efficiently on previous context\n- Identify redundant restatements of earlier information\n- Evaluate each assistant response individually\n- Consider cumulative verbosity across the conversation\n\n## Response Efficiency\n\n- Be concise in your reasoning (2-3 sentences per dimension)\n- Focus on key evidence, not exhaustive analysis\n- Provide specific examples only when they clarify the rating\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Integrated reasoning covering all three dimensions"},\n    "verbosity_score": {"type": "string", "enum": ["Very Verbose", "Verbose", "Acceptable", "Concise", "Very Concise"], "description": "Verbosity assessment"},\n    "redundancy_score": {"type": "string", "enum": ["High Redundancy", "Moderate Redundancy", "Some Redundancy", "Minimal Redundancy", "No Redundancy"], "description": "Redundancy assessment"},\n    "repetition_score": {"type": "string", "enum": ["High Repetition", "Moderate Repetition", "Some Repetition", "Minimal Repetition", "No Repetition"], "description": "Repetition assessment"},\n    "overall_score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Overall conciseness rating"},\n    "verbosity_examples": {"type": "array", "items": {"type": "string"}, "description": "Examples of verbose content"},\n    "redundancy_examples": {"type": "array", "items": {"type": "string"}, "description": "Examples of redundant content"},\n    "repetition_examples": {"type": "array", "items": {"type": "string"}, "description": "Examples of repeated phrases"}\n  },\n  "required": ["reasoning", "verbosity_score", "redundancy_score", "repetition_score", "overall_score"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

VERBOSITY = {
    "Very Verbose": 0.0,
    "Verbose": 0.25,
    "Acceptable": 0.5,
    "Concise": 0.75,
    "Very Concise": 1.0,
}
REDUNDANCY = {
    "High Redundancy": 0.0,
    "Moderate Redundancy": 0.25,
    "Some Redundancy": 0.5,
    "Minimal Redundancy": 0.75,
    "No Redundancy": 1.0,
}
REPETITION = {
    "High Repetition": 0.0,
    "Moderate Repetition": 0.25,
    "Some Repetition": 0.5,
    "Minimal Repetition": 0.75,
    "No Repetition": 1.0,
}
SUB_LABELS = {
    "verbosity_score": VERBOSITY,
    "redundancy_score": REDUNDANCY,
    "repetition_score": REPETITION,
    "overall_score": FIVE_TIER,
}
DEFAULT_WEIGHTS = {"verbosity": 0.4, "redundancy": 0.3, "repetition": 0.3}

_EXAMPLES = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "required": ["reasoning", *SUB_LABELS],
    "properties": {
        "reasoning": {"type": "string"},
        **{k: {"type": "string", "enum": list(v)} for k, v in SUB_LABELS.items()},
        "verbosity_examples": _EXAMPLES,
        "redundancy_examples": _EXAMPLES,
        "repetition_examples": _EXAMPLES,
    },
}

V1 = PromptTemplate(
    metric_id="quality.conciseness",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Response to Evaluate:\n{actual_output}",
        "with_expected": "# User Query:\n{input}\n\n# Expected Output (Reference):\n{expected_output}\n\n# Actual Output to Evaluate:\n{actual_output}",
        "conversation": "# Conversation History:\n{conversation_history}",
        "task_simple": "\n\n# Task:\nEvaluate the conciseness of this response across verbosity, redundancy, and repetition dimensions.",
        "task_with_expected": "\n\n# Task:\nEvaluate the conciseness of the actual output compared to the expected output. Consider:\n- Is the actual output more verbose than necessary compared to the reference?\n- Does it contain redundant information not in the expected output?\n- Are there repetitive patterns that the expected output avoids?",
        "task_conversation": "\n\n# Task:\nEvaluate the conciseness of the assistant's responses across all turns in this conversation. Consider:\n- Are responses appropriately concise for each turn?\n- Is information repeated unnecessarily across turns?\n- Do responses build efficiently on previous context without redundancy?",
    },
    output_schema=SCHEMA,
    labels=None,
    domain_rubric_template="# Domain-Specific Conciseness Criteria\n\nApply these additional conciseness considerations:\n\n{domain_rubric}",
    source="agentsight:conciseness_v0",
    extra={"sub_labels": SUB_LABELS, "weights": DEFAULT_WEIGHTS},
)

VERSIONS = {"v1": V1}
