"""quality.correctness prompt templates.

v1: written for bnbagent-eval (the reference-free correctness judgement AgentCore offers as
``Builtin.Correctness``; its prompt is not public, so this text is our own).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = """You are an expert evaluator assessing whether a response is correct.

# Evaluation Criteria

Judge the correctness of the response itself, using your own knowledge and any reference given:

1. **Facts**: Are the factual statements true? Penalize false or fabricated claims.
2. **Reasoning**: Do the conclusions follow from the stated facts and steps?
3. **Calculations**: Are numbers, units, amounts and computations right?
4. **Task**: Does the response correctly do what was asked (not merely talk about it)?

A reference answer, when given, is one correct answer: other wording, extra correct detail or a
different valid approach is fine. Do not reward or penalize style, length or tone.

# Scoring Guidelines (5-Tier Scale)

- **Excellent** (1.0): Fully correct; no false statements or errors
- **Good** (0.75): Correct in substance; a minor imprecision that does not change the answer
- **Average** (0.5): Partly correct; at least one error that matters
- **Below Average** (0.25): Mostly incorrect; the main answer is wrong or unsupported
- **Poor** (0.0): Wrong, fabricated or contradicts well-established facts

List each error you find when scoring below Excellent.

## Output Format

The output MUST be a well-formatted JSON object that conforms to the following schema:

```json
{
  "properties": {
    "reasoning": {"type": "string", "description": "Reasoning explaining the correctness assessment"},
    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical correctness rating"},
    "errors": {"type": "array", "items": {"type": "string"}, "description": "Specific errors found"}
  },
  "required": ["reasoning", "score"]
}
```

Do not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```)."""

V1 = PromptTemplate(
    metric_id="quality.correctness",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Response to Evaluate:\n{actual_output}",
        "with_input": "# Question/Request:\n{input}\n\n# Response to Evaluate:\n{actual_output}",
        "with_expected": "# Reference Answer:\n{expected_output}\n\n# Response to Evaluate:\n{actual_output}",
        "with_input_and_expected": "# Question/Request:\n{input}\n\n# Reference Answer:\n{expected_output}\n\n# Response to Evaluate:\n{actual_output}",
        "conversation": "# Conversation History:\n{conversation_history}",
        "task_simple": "\n\n# Task:\nEvaluate whether this response is correct.",
        "task_with_input": "\n\n# Task:\nEvaluate whether the response correctly answers the question.",
        "task_with_expected": "\n\n# Task:\nEvaluate whether the response is correct, using the reference answer.",
        "task_with_input_and_expected": "\n\n# Task:\nEvaluate whether the response correctly answers the question, using the reference answer.",
        "task_conversation": "\n\n# Task:\nEvaluate whether the assistant's responses in this conversation are correct.",
    },
    output_schema=five_tier_schema({"errors": {"type": "array", "items": {"type": "string"}}}),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Correctness Criteria\n\nApply these additional correctness considerations:\n\n{domain_rubric}",
    source="bnbagent-eval:new",
)

VERSIONS = {"v1": V1}
