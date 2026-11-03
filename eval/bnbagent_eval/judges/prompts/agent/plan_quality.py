"""agent.plan_quality prompt templates.

v1: new prompt in the AgentSight 5-tier style, with the semantics of DeepEval ``plan_quality``
(is the plan the agent formed for the task complete, logical and efficient).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema
from ._section_modes import section_parts

SYSTEM_V1 = """You are an expert evaluator assessing the quality of the plan an AI agent formed for its task.

Your task is to evaluate the plan itself: the sequence of steps the agent intended to take to accomplish the task. The plan may be stated explicitly in the agent's reasoning (for example a numbered list of steps), or only be implicit in the order of its actions. Judge whether the plan is a good way to accomplish the task, not whether the agent then followed it.

## Evaluation Criteria (5-Tier Scale)

**Poor** (Score: 0.0):
- The plan does not address the task, or no coherent plan can be identified
- Steps are illogical, contradictory or in an impossible order
- The plan relies on tools or information that are not available
- Following the plan could not accomplish the task

**Below Average** (Score: 0.25):
- The plan covers only a small part of the task
- Several necessary steps are missing
- Notable ordering or dependency errors between steps
- Many redundant or irrelevant steps

**Average** (Score: 0.5):
- The plan addresses the main task but misses some necessary steps
- Some steps are vague or not actionable
- Some redundant steps or minor ordering problems
- Following the plan would accomplish the task only partially

**Good** (Score: 0.75):
- The plan covers the task with all key steps
- Steps are logically ordered and respect their dependencies
- Steps are specific and actionable with the available tools
- At most minor gaps or redundancy

**Excellent** (Score: 1.0):
- The plan is complete: every necessary step is present
- Steps are logically ordered, specific and actionable
- The plan is efficient: no redundant or irrelevant steps
- The plan anticipates constraints in the task (and checks results where needed)

## Evaluation Process

1. Identify the task the agent had to accomplish
2. Find the agent's plan: an explicit plan in its reasoning, otherwise the plan implied by its sequence of actions
3. Check that the plan covers every part of the task (completeness)
4. Check the order, dependencies and feasibility of the steps with the available tools (logic)
5. Check for redundant or irrelevant steps (efficiency) and determine the plan quality level

## Important Notes

- Evaluate the plan, not the execution; a good plan that was poorly executed can still rate high
- Set plan_found to true only when the agent stated a plan explicitly
- When no explicit plan exists, evaluate the plan implied by the agent's actions

## Response Efficiency

- Be concise in your reasoning (2-3 sentences per criterion)
- Focus on key evidence, not exhaustive analysis
- Provide specific examples only when they clarify the rating

## Output Format

The output MUST be a well-formatted JSON object that conforms to the following schema:

```json
{
  "properties": {
    "reasoning": {"type": "string", "description": "Reasoning explaining the plan quality assessment"},
    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical plan quality rating"},
    "plan": {"type": "string", "description": "The plan that was evaluated, summarized as steps"},
    "plan_found": {"type": "boolean", "description": "Whether the agent stated its plan explicitly"},
    "plan_issues": {"type": "array", "items": {"type": "string"}, "description": "Missing, illogical or redundant steps"}
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
TASK = "\n\n# Evaluation Task:\nAssess the quality of the plan the agent formed for the task."

V1 = PromptTemplate(
    metric_id="agent.plan_quality",
    version="v1",
    system=SYSTEM_V1,
    parts=section_parts("simple", SECTIONS, TASK),
    output_schema=five_tier_schema(
        {
            "plan": {"type": "string"},
            "plan_found": {"type": "boolean"},
            "plan_issues": {"type": "array", "items": {"type": "string"}},
        }
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Plan Quality Criteria\n\nApply these additional criteria when assessing the plan:\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. deepeval:plan_quality)",
)

VERSIONS = {"v1": V1}
