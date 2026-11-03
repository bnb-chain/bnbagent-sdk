"""Eval maker stage 5 (reflect): self-critique of the draft suite, fed by lint findings.

v1: AgentSight eval_maker ``REFLECTION_PROMPT`` (prompt quality and rubric coverage 0.0-1.0,
placeholder usage, dimension coverage, issues, improvements, ``should_regenerate``; "Be
critical"), applied to a whole draft suite and extended with the suite-lint findings and the
stage paragraph (``bnbagent-eval:new``). The mode-upgrade branch is not ported.
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = """You critically review a generated evaluation suite.

# Critique on these dimensions (score 0.0-1.0):

1. CRITERIA QUALITY: Are the judge prompts, rubrics and requirements clear, complete and unambiguous?
   - Are instructions explicit?
   - Are edge cases handled?
   - Is every criterion specific and checkable (no vague words)?

2. COVERAGE: Does the suite cover ALL requirements?
   - Is every stated requirement measured by at least one metric?
   - Are there metrics that measure something nobody asked for?

3. PLACEHOLDER USAGE (custom.judge_prompt):
   - Prompts MUST use the standard placeholders {input}, {output}, {expected}, {contexts}, {conversation}, {trajectory}.

4. ARBITRABILITY: Do the thresholds, required flags and deterministic metrics fit the stage?

5. LINT FINDINGS: every listed lint finding is a defect to fix.

Be critical. Set should_regenerate=true if:
- Any lint finding is listed
- A requirement is not covered
- Scoring guidance is unclear or a criterion is vague
- Required placeholders are missing

List concrete, actionable improvements_suggested (e.g. which metric to add, which criterion to rewrite and how).

## Output Format

Return only a JSON object:
{"criteria_quality_score": 0.0, "coverage_score": 0.0, "issues_found": ["..."], "improvements_suggested": ["..."], "should_regenerate": true}"""

_STRINGS = {"type": "array", "items": {"type": "string"}}
_SCORE = {"type": "number", "minimum": 0.0, "maximum": 1.0}

V1 = PromptTemplate(
    metric_id="authoring.eval_maker_reflect",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": (
            "# Original Requirements:\n{description}\n\n# Stage:\n{stage_guidance}\n\n"
            "# Draft Suite:\n{draft}\n\n# Lint Findings:\n{lint}\n\n# Notes:\n{notes}"
        )
    },
    output_schema={
        "type": "object",
        "required": ["should_regenerate"],
        "properties": {
            "criteria_quality_score": _SCORE,
            "coverage_score": _SCORE,
            "issues_found": _STRINGS,
            "improvements_suggested": _STRINGS,
            "should_regenerate": {"type": "boolean"},
        },
    },
    source="agentsight:eval_maker.reflect_on_generation (+ bnbagent-eval:new lint findings)",
)

VERSIONS = {"v1": V1}
