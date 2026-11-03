"""agent.plan_adherence prompt templates.

v1: new prompt in the AgentSight 5-tier style, with the semantics of DeepEval ``plan_adherence``
(did the agent's execution follow the plan it stated).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema
from ._section_modes import section_parts

SYSTEM_V1 = """You are an expert evaluator assessing whether an AI agent followed its own plan.

Your task is to identify the plan the agent stated (usually in its reasoning before or while acting) and to evaluate whether the actions in its execution trajectory followed that plan. Judge adherence to the plan, not the quality of the plan or the correctness of the final answer.

## Evaluation Criteria (5-Tier Scale)

**Poor** (Score: 0.0):
- The execution ignores the stated plan
- Most planned steps were never carried out
- The agent did substantially different things from what it planned, without explanation
- The agent claims to have completed planned steps that the trajectory does not show

**Below Average** (Score: 0.25):
- Only a few planned steps were carried out
- Major unexplained deviations from the plan
- Planned steps were performed in an order that breaks their dependencies
- Significant unplanned actions replace planned ones

**Average** (Score: 0.5):
- About half of the planned steps were carried out as planned
- Some steps were skipped, reordered or changed without explanation
- Some unplanned actions
- The overall direction of the plan was partly kept

**Good** (Score: 0.75):
- Most planned steps were carried out in the planned order
- Deviations are minor, or are justified by new information (for example a tool error) and acknowledged
- Few unplanned actions
- The execution clearly follows the plan's intent

**Excellent** (Score: 1.0):
- Every planned step was carried out, in the planned order
- Any change to the plan is explicitly justified by new information and the revised plan is followed
- No unexplained unplanned actions
- The final answer is consistent with the executed plan

## Evaluation Process

1. Extract the plan the agent stated, as a list of steps (if the agent revised its plan, track the revisions)
2. Map each action in the trajectory to a planned step, or mark it as unplanned
3. Identify planned steps that were skipped, reordered or changed
4. Decide for each deviation whether it was justified by new information and acknowledged
5. Determine the plan adherence level based on the share of the plan that was followed

## Important Notes

- A justified, acknowledged revision of the plan is not a deviation
- Do not judge whether the plan was good or whether the task was completed
- If the agent stated no explicit plan, use the plan implied by its first reasoning or announced intentions, set plan_found to false, and rate how consistently the execution follows it

## Response Efficiency

- Be concise in your reasoning (2-3 sentences per criterion)
- Focus on key evidence, not exhaustive analysis
- Provide specific examples only when they clarify the rating

## Output Format

The output MUST be a well-formatted JSON object that conforms to the following schema:

```json
{
  "properties": {
    "reasoning": {"type": "string", "description": "Reasoning explaining the plan adherence assessment"},
    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical plan adherence rating"},
    "plan": {"type": "string", "description": "The plan the execution was compared with, summarized as steps"},
    "plan_found": {"type": "boolean", "description": "Whether the agent stated its plan explicitly"},
    "deviations": {"type": "array", "items": {"type": "string"}, "description": "Skipped, reordered, changed or unplanned steps"}
  },
  "required": ["reasoning", "score"]
}
```

As an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}
the object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.

Do not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```)."""

SECTIONS = [
    ("input", "# User Input (Task):\n{input}"),
    ("plan", "# Agent Reasoning (LLM steps, may contain the plan):\n{plan_text}"),
    (None, "# Agent Trajectory:\n{trajectory}"),
]
TASK = "\n\n# Evaluation Task:\nAssess whether the agent's execution followed the plan it stated."

V1 = PromptTemplate(
    metric_id="agent.plan_adherence",
    version="v1",
    system=SYSTEM_V1,
    parts=section_parts("simple", SECTIONS, TASK),
    output_schema=five_tier_schema(
        {
            "plan": {"type": "string"},
            "plan_found": {"type": "boolean"},
            "deviations": {"type": "array", "items": {"type": "string"}},
        }
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Plan Adherence Criteria\n\nApply these additional criteria when assessing plan adherence:\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. deepeval:plan_adherence)",
)

VERSIONS = {"v1": V1}
