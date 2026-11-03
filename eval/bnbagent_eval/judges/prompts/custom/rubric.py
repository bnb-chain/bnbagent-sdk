"""custom.rubric prompt templates.

v1: a general rubric prompt modelled on the AgentSight RubricBasedEvaluator prompts (e.g.
``agent_reasoning`` V0: Property / Rationale / Verdict per rubric, a JSON ``evaluations`` list),
with verdicts yes / no / not_applicable and rubric ids. The suite's rubrics are trusted
configuration and are substituted for ``RUBRICS_MARKER`` by :func:`with_rubrics`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from .. import PromptTemplate

SYSTEM_V1 = """You are evaluating the quality of an AI agent's response against a list of rubrics (properties).

Analyze the provided content and evaluate each rubric independently.

For each rubric, provide:
- Property: [exact rubric text]
- Rationale: [detailed explanation of your assessment]
- Verdict: [yes/no/not_applicable]

Use "yes" if the response demonstrates the property, "no" if it fails to demonstrate it, and "not_applicable" if the property is not relevant to assess for this content.

# Output format:
Respond with valid JSON in this exact format:
{
  "evaluations": [
    {
      "rubric_id": "The id of the rubric, as given in square brackets",
      "property": "The exact property text being evaluated",
      "rationale": "Your detailed reasoning for the verdict",
      "verdict": "yes/no/not_applicable"
    }
  ]
}

Evaluate every rubric exactly once."""

RUBRICS_MARKER = "<<rubrics>>"
_TASK = (
    "\n\n<properties>\n"
    + RUBRICS_MARKER
    + '\n</properties>\n\nREMEMBER: Your answer will help improve the AI agent. It is important to determine the fulfillment of the properties correctly. Even answering "no" will improve the agent!'
)

V1 = PromptTemplate(
    metric_id="custom.rubric",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "<user_prompt>\n{input}\n</user_prompt>\n\n<response>\n{actual_output}\n</response>",
        "with_expected": "<user_prompt>\n{input}\n</user_prompt>\n\n<reference_response>\n{expected_output}\n</reference_response>\n\n<response>\n{actual_output}\n</response>",
        "conversation": "<conversation>\n{conversation_history}\n</conversation>",
        "task_simple": _TASK,
        "task_with_expected": _TASK,
        "task_conversation": _TASK,
    },
    output_schema={
        "type": "object",
        "required": ["evaluations"],
        "properties": {
            "evaluations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["verdict"],
                    "properties": {
                        "rubric_id": {"type": "string"},
                        "property": {"type": "string"},
                        "rationale": {"type": "string"},
                        "verdict": {"type": "string", "enum": ["yes", "no", "not_applicable"]},
                    },
                },
            }
        },
    },
    labels=None,
    domain_rubric_template="# Domain-Specific Criteria\n\n{domain_rubric}",
    source="bnbagent-eval:new (cf. agentsight:RubricBasedEvaluator, agent_reasoning V0)",
)

VERSIONS = {"v1": V1}


def with_rubrics(template: PromptTemplate, rubrics: Sequence[Mapping[str, Any]]) -> PromptTemplate:
    """Insert the (trusted) rubric list into the task parts (task parts are not formatted)."""
    listing = "\n".join(f"*  [{r['id']}] {r['text']}" for r in rubrics)
    parts = {k: v.replace(RUBRICS_MARKER, listing) for k, v in template.parts.items()}
    return dataclasses.replace(template, parts=parts)
