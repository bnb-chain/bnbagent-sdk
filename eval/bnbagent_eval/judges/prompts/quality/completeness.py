"""quality.completeness prompt templates.

v1: ported verbatim from AgentSight ``completeness_v0`` (prompt text, modes, labels).

``CONTEXT_V1`` is the second (context completeness) call: the source system prompt (with the
domain rubric, if any) followed by ``CONTEXT_AWARE_ADDENDUM``, and the user prompt from
``CompletenessEvaluator._build_context_prompt``.
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = 'You are an expert evaluator assessing response completeness.\n\n# Evaluation Criteria\n\nAssess whether the response fully addresses all aspects of the question or request:\n\n1. **Coverage**: Are all parts of the question addressed?\n2. **Depth**: Is sufficient detail provided for each point?\n3. **Key Elements**: Are essential information and requirements included?\n4. **Follow-through**: Are implied needs and logical follow-ups addressed?\n\n# Scoring Guidelines (5-Tier Scale)\n\n- **Excellent** (1.0): Fully addresses all aspects with appropriate depth\n- **Good** (0.75): Addresses most aspects, minor gaps in coverage\n- **Average** (0.5): Addresses core question but misses secondary aspects\n- **Below Average** (0.25): Significant gaps, key elements missing\n- **Poor** (0.0): Fails to address the main question or misses critical elements\n\nBe objective. List specific missing elements when scoring below Excellent.\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Reasoning explaining the completeness assessment"},\n    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical completeness rating"},\n    "missing_elements": {"type": "array", "items": {"type": "string"}, "description": "Key elements not addressed"}\n  },\n  "required": ["reasoning", "score"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

CONTEXT_AWARE_ADDENDUM = "\n\n# Context-Aware Evaluation (RAG Scenarios)\n\nWhen retrieval context is provided, also evaluate:\n- Does the response incorporate relevant information from the context?\n- Are key facts from the context included in the response?\n- Is important context information missing from the response?"

DOMAIN_RUBRIC_TEMPLATE = "# Domain-Specific Completeness Criteria\n\nApply these additional completeness considerations:\n\n{domain_rubric}"

SCHEMA = five_tier_schema({"missing_elements": {"type": "array", "items": {"type": "string"}}})

CONTEXT_V1 = PromptTemplate(
    metric_id="quality.completeness",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "context": "# User Question:\n{input}\n\n# Retrieval Context (Available Information):\n{contexts}\n\n# Response to Evaluate:\n{actual_output}",
        "task_context": "\n\n# Task:\nEvaluate how completely the response utilizes the relevant information from the retrieval\ncontext to answer the question. Consider:\n- Does the response include key facts from the context?\n- Are important details from the context incorporated?\n- Is relevant context information missing from the response?",
    },
    output_schema=SCHEMA,
    labels=FIVE_TIER,
    domain_rubric_template=DOMAIN_RUBRIC_TEMPLATE,
    source="agentsight:completeness_v0 (CompletenessEvaluator._build_context_prompt)",
    # source: ``self.system_prompt + CONTEXT_AWARE_ADDENDUM`` (after the domain rubric)
    extra={"system_suffix": CONTEXT_AWARE_ADDENDUM},
)

V1 = PromptTemplate(
    metric_id="quality.completeness",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Response to Evaluate:\n{actual_output}",
        "with_input": "# Question/Request:\n{input}\n\n# Response to Evaluate:\n{actual_output}",
        "with_expected": "# Expected Response (Reference):\n{expected_output}\n\n# Actual Response to Evaluate:\n{actual_output}",
        "with_input_and_expected": "# Question/Request:\n{input}\n\n# Expected Response (Reference):\n{expected_output}\n\n# Actual Response to Evaluate:\n{actual_output}",
        "conversation": "# Conversation History:\n{conversation_history}",
        "task_simple": "\n\n# Task:\nEvaluate the completeness of this response.",
        "task_with_input": "\n\n# Task:\nEvaluate how completely the response addresses the question.",
        "task_with_expected": "\n\n# Task:\nEvaluate how completely the response addresses the topic compared to the reference.",
        "task_with_input_and_expected": "\n\n# Task:\nEvaluate how completely the response addresses the question compared to the reference.",
        "task_conversation": "\n\n# Task:\nEvaluate the completeness of the assistant's responses across this conversation.",
    },
    output_schema=SCHEMA,
    labels=FIVE_TIER,
    domain_rubric_template=DOMAIN_RUBRIC_TEMPLATE,
    source="agentsight:completeness_v0",
    extra={"context_template": CONTEXT_V1.hash()},
)

VERSIONS = {"v1": V1}
CONTEXT_VERSIONS = {"v1": CONTEXT_V1}
