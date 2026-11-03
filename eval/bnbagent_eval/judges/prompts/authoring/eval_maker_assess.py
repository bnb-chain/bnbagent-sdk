"""Eval maker stage 1 (assess): domain, risks, needed qualities and input quality.

v1: AgentSight eval_maker ``QUALITY_ASSESSMENT_PROMPT`` (clarity / completeness / correlation,
0-1 each, issues, warnings, recommendations; "Be critical"), extended with the domain, the
risks and the qualities the suite must measure (``bnbagent-eval:new``).
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = """You assess the quality of evaluation requirements before an evaluation suite is generated from them.

From the description (and the sample cases, if any) identify:
- DOMAIN: the agent's domain and task, in a few words.
- RISKS: the ways the agent's output could fail or cause harm in this domain.
- QUALITIES: the qualities the evaluation must measure, one short phrase each, taken from the stated requirements (do not invent extra ones).

Then evaluate the requirements on three dimensions (score 0.0-1.0):

1. CLARITY: How clear and unambiguous are the requirements?
   - Are evaluation criteria explicitly stated?
   - Is the scoring scale defined?
   - Are edge cases addressed?

2. COMPLETENESS: Are all necessary elements present?
   - Input/output format specified?
   - Scoring levels defined?
   - Examples provided?

3. CORRELATION: How well do requirements and samples align?
   - Do samples demonstrate the criteria?
   - Is terminology consistent?

Be critical. Low-quality inputs produce unreliable evaluators.

## Output Format

Return only a JSON object:
{"domain": "...", "risks": ["..."], "qualities": ["..."],
 "clarity_score": 0.0, "clarity_issues": ["..."],
 "completeness_score": 0.0, "completeness_issues": ["..."],
 "correlation_score": 0.0, "correlation_issues": ["..."],
 "warnings": ["..."], "recommendations": ["..."]}"""

_STRINGS = {"type": "array", "items": {"type": "string"}}
_SCORE = {"type": "number", "minimum": 0.0, "maximum": 1.0}

V1 = PromptTemplate(
    metric_id="authoring.eval_maker_assess",
    version="v1",
    system=SYSTEM_V1,
    parts={"simple": "# Requirements:\n{description}\n\n# Sample Data (if provided):\n{samples}"},
    output_schema={
        "type": "object",
        "required": [
            "domain",
            "qualities",
            "clarity_score",
            "completeness_score",
            "correlation_score",
        ],
        "properties": {
            "domain": {"type": "string"},
            "risks": _STRINGS,
            "qualities": {**_STRINGS, "minItems": 1},
            "clarity_score": _SCORE,
            "clarity_issues": _STRINGS,
            "completeness_score": _SCORE,
            "completeness_issues": _STRINGS,
            "correlation_score": _SCORE,
            "correlation_issues": _STRINGS,
            "warnings": _STRINGS,
            "recommendations": _STRINGS,
        },
    },
    source="agentsight:eval_maker.assess_quality (+ bnbagent-eval:new domain/risks/qualities)",
)

VERSIONS = {"v1": V1}
