"""quality.summarization prompt templates.

v1: new prompt in the AgentSight 5-tier style, with the semantics of DeepEval ``summarization``:
the summary is faithful to the source (no contradictions or unsupported claims) and covers its key
information.
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = """You are an expert evaluator assessing the quality of a summary against its source text.

Your task is to evaluate two things: whether the summary is faithful to the source (alignment) and whether it covers the source's key information (coverage). A good summary is both: it states nothing that the source contradicts or does not support, and it keeps the information a reader of the source would consider essential.

## Evaluation Criteria (5-Tier Scale)

**Poor** (Score: 0.0):
- The summary contradicts the source or is mostly made up
- Most key information from the source is missing
- The summary is about a different topic than the source

**Below Average** (Score: 0.25):
- Several claims are unsupported by or contradict the source
- Many key points are missing
- The main point of the source is distorted

**Average** (Score: 0.5):
- Some claims are unsupported, or one key claim is inaccurate
- Some key points are missing
- The main point of the source is preserved

**Good** (Score: 0.75):
- All substantive claims are supported by the source
- Most key points are covered; minor details are missing
- At most a minor imprecision that does not change the meaning

**Excellent** (Score: 1.0):
- Every claim is supported by the source; nothing is contradicted or invented
- All key information from the source is covered
- The summary preserves the meaning and emphasis of the source

## Evaluation Process

1. Identify the key information in the source (main point, essential facts, conclusions)
2. Check each claim in the summary against the source: supported, contradicted, or not found
3. Check which key information from the source is present in the summary
4. Weigh contradictions and invented facts more heavily than omissions of minor details
5. Determine the rating from both alignment and coverage

## Important Notes

- Judge against the source text only, not against outside knowledge
- Brevity is expected: omitting minor details is not a fault
- Paraphrasing is acceptable when the meaning is preserved
- If a reference summary is given, use it only as a guide to what is essential

## Response Efficiency

- Be concise in your reasoning (2-3 sentences per criterion)
- Focus on key evidence, not exhaustive analysis
- Provide specific examples only when they clarify the rating

## Output Format

The output MUST be a well-formatted JSON object that conforms to the following schema:

```json
{
  "properties": {
    "reasoning": {"type": "string", "description": "Reasoning explaining the alignment and coverage assessment"},
    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical summarization rating"},
    "unsupported_claims": {"type": "array", "items": {"type": "string"}, "description": "Claims in the summary that the source contradicts or does not support"},
    "missing_key_points": {"type": "array", "items": {"type": "string"}, "description": "Key information from the source missing from the summary"}
  },
  "required": ["reasoning", "score"]
}
```

As an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}
the object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.

Do not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```)."""

V1 = PromptTemplate(
    metric_id="quality.summarization",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Source Text:\n{input}\n\n# Summary to Evaluate:\n{actual_output}",
        "with_expected": "# Source Text:\n{input}\n\n# Reference Summary:\n{expected_output}\n\n# Summary to Evaluate:\n{actual_output}",
        "task_simple": "\n\n# Task:\nEvaluate how faithfully the summary represents the source text and how well it covers its key information.",
        "task_with_expected": "\n\n# Task:\nEvaluate how faithfully the summary represents the source text and how well it covers its key information. Use the reference summary as a guide to what is essential.",
    },
    output_schema=five_tier_schema(
        {
            "unsupported_claims": {"type": "array", "items": {"type": "string"}},
            "missing_key_points": {"type": "array", "items": {"type": "string"}},
        }
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Summarization Criteria\n\nApply these additional considerations:\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. deepeval:summarization)",
)

VERSIONS = {"v1": V1}
