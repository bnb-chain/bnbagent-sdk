"""rag.context_recall prompt templates.

v1: new prompt with RAGAS / DeepEval ``context_recall`` semantics: split the expected answer
into statements and decide, for each, whether the retrieved contexts contain it. Score = share
of attributable statements.
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = """You are an expert evaluator assessing the recall of a retrieval system.

# Your Task

Given a question, the retrieved contexts and an expected (reference) answer, decide whether the retrieved contexts contain the information needed to produce the expected answer.

# Evaluation Process

1. Break the expected answer into atomic statements (indivisible facts)
2. For each statement, decide whether it can be attributed to the retrieved contexts:
   - "yes": the contexts state or directly imply the statement
   - "no": the contexts do not contain the statement
3. Give a short reason for each verdict, citing the context number when the verdict is "yes"

# Important Notes

- Judge only against the retrieved contexts; do NOT use prior knowledge
- Evaluate the expected answer, not any generated answer
- Every statement of the expected answer must receive exactly one verdict

# Output Format

Return a JSON object with these fields:
- "reasoning": a concise summary of the assessment
- "statements": a list of objects {"statement": <string>, "attributed": "yes" or "no", "reason": <string>}

Do not return any preamble or explanations, return only the JSON object."""

V1 = PromptTemplate(
    metric_id="rag.context_recall",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "with_expected": "# User Question:\n{input}\n\n# Retrieved Contexts:\n{contexts}\n\n# Expected Answer (Reference):\n{expected_output}",
        "task_with_expected": "\n\n# Evaluation Task:\nFor each statement of the expected answer, decide whether the retrieved contexts contain it.",
    },
    output_schema={
        "type": "object",
        "required": ["reasoning", "statements"],
        "properties": {
            "reasoning": {"type": "string"},
            "statements": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["statement", "attributed"],
                    "properties": {
                        "statement": {"type": "string"},
                        "attributed": {"type": "string", "enum": ["yes", "no"]},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
    },
    labels=None,
    domain_rubric_template="# Domain-Specific Recall Criteria\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. ragas:context_recall, deepeval:contextual_recall)",
)

VERSIONS = {"v1": V1}
