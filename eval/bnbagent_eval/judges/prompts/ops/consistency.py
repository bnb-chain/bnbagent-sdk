"""ops.consistency (judge method) prompt templates.

v1: new prompt: are two outputs of repeated runs of the same request semantically equivalent?
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = """You are an expert evaluator judging the consistency of an AI agent across repeated runs of the same request.

You are given the request and two responses (A and B) that the agent produced for it in separate runs. Decide whether the two responses are semantically equivalent.

## Criteria

Answer "yes" when both responses:
- convey the same meaning, facts, conclusions and recommendations
- agree on every number, name, date, amount, address and decision
- would lead a user to the same understanding and the same actions

Answer "no" when they differ in any of these, or when one response contains material information or a decision the other lacks or contradicts.

Ignore differences in wording, tone, formatting, length and ordering that do not change the meaning.

## Output Format

Return a JSON object with these fields:
- "reasoning": a concise explanation of the key similarities or differences
- "score": "yes" (semantically equivalent) or "no" (not equivalent)

Do not return any preamble or explanations, return only the JSON object."""

LABELS = {"yes": 1.0, "no": 0.0}

V1 = PromptTemplate(
    metric_id="ops.consistency",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Request:\n{input}\n\n# Response A:\n{output_a}\n\n# Response B:\n{output_b}",
        "task_simple": "\n\n# Evaluation Task:\nAre responses A and B semantically equivalent?",
    },
    output_schema={
        "type": "object",
        "required": ["reasoning", "score"],
        "properties": {
            "reasoning": {"type": "string"},
            "score": {"type": "string", "enum": list(LABELS)},
        },
    },
    labels=LABELS,
    domain_rubric_template="# Domain-Specific Equivalence Criteria\n\n{domain_rubric}",
    source="bnbagent-eval:new (judge method)",
)

VERSIONS = {"v1": V1}
