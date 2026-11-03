"""quality.instruction_following prompt templates.

v1: new prompt in the AgentSight 5-tier style, with the semantics of DeepEval ``prompt_alignment``
and Bedrock AgentCore ``Builtin.InstructionFollowing``: does the response follow every explicit
instruction it was given.
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = """You are an expert evaluator assessing whether an agent response follows the instructions it was given.

Your task is to identify every explicit instruction (format, length, language, style, content to include or exclude, steps to perform, constraints) and check whether the response complies with each one. Judge compliance only; do not judge the quality or correctness of the content beyond what the instructions require.

## Evaluation Criteria (5-Tier Scale)

**Poor** (Score: 0.0):
- The response ignores the instructions or does something else entirely
- Most or all instructions are violated
- Explicit constraints (format, length, language, exclusions) are broken

**Below Average** (Score: 0.25):
- Only a few instructions are followed
- Several important instructions are violated
- The response follows the general request but disregards most constraints

**Average** (Score: 0.5):
- About half of the instructions are followed
- At least one important instruction is violated
- Constraints are applied inconsistently

**Good** (Score: 0.75):
- All important instructions are followed
- A minor instruction is missed or only partly followed
- Constraints are respected with small deviations

**Excellent** (Score: 1.0):
- Every instruction is followed completely
- All explicit constraints are respected exactly
- Nothing the instructions excluded appears in the response

## Evaluation Process

1. List each explicit instruction. When no separate instruction list is given, extract the instructions from the user input
2. For each instruction, decide whether the response follows it, violates it, or follows it partly
3. Weigh important instructions (the main task, hard constraints) above minor ones (stylistic preferences)
4. Determine the rating from the share and importance of the instructions followed

## Important Notes

- Only explicit instructions count; do not invent requirements the user did not state
- An instruction that cannot apply to this response (e.g. a condition that does not occur) counts as followed
- If the instructions conflict, accept any reasonable resolution and note the conflict

## Response Efficiency

- Be concise in your reasoning (one sentence per instruction)
- Focus on key evidence, not exhaustive analysis

## Output Format

The output MUST be a well-formatted JSON object that conforms to the following schema:

```json
{
  "properties": {
    "reasoning": {"type": "string", "description": "Reasoning explaining which instructions were followed or violated"},
    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical instruction-following rating"},
    "instructions_followed": {"type": "array", "items": {"type": "string"}, "description": "Instructions the response follows"},
    "instructions_violated": {"type": "array", "items": {"type": "string"}, "description": "Instructions the response violates or only partly follows"}
  },
  "required": ["reasoning", "score"]
}
```

As an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}
the object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.

Do not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```)."""

V1 = PromptTemplate(
    metric_id="quality.instruction_following",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# User Input (contains the instructions):\n{input}\n\n# Response to Evaluate:\n{actual_output}",
        "with_instructions": "# Instructions:\n{instructions}\n\n# Response to Evaluate:\n{actual_output}",
        "with_input_and_instructions": "# Instructions:\n{instructions}\n\n# User Input:\n{input}\n\n# Response to Evaluate:\n{actual_output}",
        "task_simple": "\n\n# Task:\nIdentify the instructions in the user input and evaluate how well the response follows each of them.",
        "task_with_instructions": "\n\n# Task:\nEvaluate how well the response follows each of the instructions.",
        "task_with_input_and_instructions": "\n\n# Task:\nEvaluate how well the response to the user input follows each of the instructions.",
    },
    output_schema=five_tier_schema(
        {
            "instructions_followed": {"type": "array", "items": {"type": "string"}},
            "instructions_violated": {"type": "array", "items": {"type": "string"}},
        }
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Instruction-Following Criteria\n\nApply these additional considerations:\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. deepeval:prompt_alignment, agentcore:Builtin.InstructionFollowing)",
)

VERSIONS = {"v1": V1}
