"""quality.coherence prompt templates.

v1: new prompt in the AgentSight 5-tier style, with the semantics of Bedrock AgentCore
``Builtin.Coherence`` (logical flow, internal consistency and organization of one response).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = """You are an expert evaluator assessing the coherence of a single agent response.

Your task is to evaluate whether the response forms a logically connected, internally consistent and well-organized whole. Judge how the response hangs together, not whether its facts are correct or whether it is complete.

## Evaluation Criteria (5-Tier Scale)

**Poor** (Score: 0.0):
- Ideas are disconnected or presented in an arbitrary order
- The reasoning does not follow; conclusions do not follow from what precedes them
- The response contradicts itself on its central point
- The reader cannot reconstruct the line of thought

**Below Average** (Score: 0.25):
- Frequent abrupt jumps between topics without transitions
- Several steps of reasoning are missing or out of order
- Notable internal inconsistencies
- The overall structure is hard to follow

**Average** (Score: 0.5):
- The main line of thought can be followed with some effort
- Some transitions are missing or some points are out of place
- Minor inconsistencies that do not undermine the main point
- Organization is partly logical

**Good** (Score: 0.75):
- The response follows a clear logical progression
- Points are connected and mostly well ordered
- No internal inconsistencies on substantive points
- Minor organizational issues only

**Excellent** (Score: 1.0):
- Every part builds on the previous one in a clear logical sequence
- Transitions make the relationship between ideas explicit
- The response is fully internally consistent
- The organization (paragraphs, lists, sections) fits the content

## Evaluation Process

1. Identify the main point or goal of the response
2. Trace the sequence of ideas and check that each step follows from the previous one
3. Check for statements that are inconsistent with other parts of the response
4. Assess whether the organization supports the line of thought
5. Determine the coherence level based on logical flow, consistency and organization

## Important Notes

- Do not penalize the response for being short if it is coherent
- Do not judge factual accuracy, completeness or tone; focus only on coherence
- When a question/request is given, use it only to understand what the response is trying to do

## Response Efficiency

- Be concise in your reasoning (2-3 sentences per criterion)
- Focus on key evidence, not exhaustive analysis
- Provide specific examples only when they clarify the rating

## Output Format

The output MUST be a well-formatted JSON object that conforms to the following schema:

```json
{
  "properties": {
    "reasoning": {"type": "string", "description": "Reasoning explaining the coherence assessment"},
    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical coherence rating"},
    "coherence_issues": {"type": "array", "items": {"type": "string"}, "description": "Specific breaks in logical flow, inconsistencies or organization problems"}
  },
  "required": ["reasoning", "score"]
}
```

As an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}
the object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.

Do not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```)."""

V1 = PromptTemplate(
    metric_id="quality.coherence",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Response to Evaluate:\n{actual_output}",
        "with_input": "# Question/Request:\n{input}\n\n# Response to Evaluate:\n{actual_output}",
        "task_simple": "\n\n# Task:\nEvaluate the coherence of this response.",
        "task_with_input": "\n\n# Task:\nEvaluate the coherence of the response to this question/request.",
    },
    output_schema=five_tier_schema(
        {"coherence_issues": {"type": "array", "items": {"type": "string"}}}
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Coherence Criteria\n\nApply these additional coherence considerations:\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. agentcore:Builtin.Coherence)",
)

VERSIONS = {"v1": V1}
