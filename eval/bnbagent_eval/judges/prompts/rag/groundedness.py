"""rag.groundedness prompt templates.

v1: new prompt in the AgentSight 5-tier style with RAGAS ``response_groundedness`` semantics: is
every claim of the response supported by the retrieved contexts? No query is needed.
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = """You are an expert evaluator assessing whether a response is grounded in a set of retrieved contexts.

Your task is to determine how much of the response is supported by the provided contexts. Judge only against the contexts: information that is true in the real world but absent from the contexts is NOT grounded.

## Evaluation Criteria (5-Tier Scale)

**Poor** (Score: 0.0):
- The response is not supported by the contexts at all
- Key statements contradict the contexts or are entirely absent from them

**Below Average** (Score: 0.25):
- Only a small part of the response is supported by the contexts
- Most statements are unsupported

**Average** (Score: 0.5):
- Roughly half of the response is supported by the contexts
- Several notable statements are unsupported

**Good** (Score: 0.75):
- Most of the response is supported by the contexts
- Only minor details are unsupported

**Excellent** (Score: 1.0):
- Every statement in the response is fully supported by the contexts
- Nothing is added beyond what the contexts state or directly imply

## Evaluation Process

1. Break the response into its individual statements
2. For each statement, check whether the contexts state or directly imply it
3. List the supported and the unsupported statements
4. Determine the groundedness level from the share and importance of supported statements

## Important Notes

- Do not use prior knowledge; take each context at face value
- Paraphrases and direct implications of the contexts count as supported
- Ignore writing style and formatting; judge only factual support

## Output Format

Return a JSON object with these fields:
- "reasoning": step-by-step reasoning explaining the assessment
- "score": one of "Poor", "Below Average", "Average", "Good", "Excellent"
- "supported_statements": list of statements supported by the contexts
- "unsupported_statements": list of statements not supported by the contexts

Do not return any preamble or explanations, return only the JSON object."""

V1 = PromptTemplate(
    metric_id="rag.groundedness",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Retrieved Contexts:\n{contexts}\n\n# Response to Evaluate:\n{actual_output}",
        "task_simple": "\n\n# Evaluation Task:\nAssess how well the response is grounded in the retrieved contexts.",
    },
    output_schema=five_tier_schema(
        {
            "supported_statements": {"type": "array", "items": {"type": "string"}},
            "unsupported_statements": {"type": "array", "items": {"type": "string"}},
        }
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Groundedness Criteria\n\nApply these additional criteria when assessing groundedness:\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. ragas:response_groundedness)",
)

VERSIONS = {"v1": V1}
