"""rag.hallucination prompt templates.

v1: new prompt with DeepEval ``hallucination`` semantics: one verdict per context on whether the
response agrees with it ("yes") or contradicts it ("no"). The raw hallucination score is the share
of contradicted contexts (higher is worse; the metric inverts it).
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = """You are an expert evaluator detecting hallucinations in a response, measured against a list of reference contexts.

# Your Task

For EACH context, decide whether the response agrees with that context.

- "yes": the response agrees with the context, or does not contradict it
- "no": the response contradicts the context (a hallucination with respect to that context)

# Important Notes

- Take each context at face value; do NOT use prior knowledge
- Only answer "no" when there is an actual CONTRADICTION; forgive a response that merely lacks detail or omits information from a context
- Produce exactly one verdict per context, in the order the contexts are given, with the context's number as "context_id"
- When the verdict is "no", explain the contradiction (and the correction) in "reason"

# Output Format

Return a JSON object with these fields:
- "reasoning": a concise summary of the assessment
- "verdicts": a list with one object per context: {"context_id": <int>, "verdict": "yes" or "no", "reason": <string>}

Do not return any preamble or explanations, return only the JSON object."""

V1 = PromptTemplate(
    metric_id="rag.hallucination",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Contexts:\n{contexts}\n\n# Response to Evaluate:\n{actual_output}",
        "task_simple": "\n\n# Evaluation Task:\nFor each context, state whether the response agrees with it (yes) or contradicts it (no).",
    },
    output_schema={
        "type": "object",
        "required": ["reasoning", "verdicts"],
        "properties": {
            "reasoning": {"type": "string"},
            "verdicts": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["verdict"],
                    "properties": {
                        "context_id": {"type": "integer"},
                        "verdict": {"type": "string", "enum": ["yes", "no"]},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
    },
    labels=None,
    domain_rubric_template="# Domain-Specific Hallucination Criteria\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. deepeval:hallucination)",
)

VERSIONS = {"v1": V1}
