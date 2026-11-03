"""Eval maker stage 3 (match): map dimensions to catalogue metrics.

v1: AgentSight eval_maker ``MATCH_PROMPT`` (best matching evaluator per dimension, confidence
0.0-1.0, match only if confidence >= 0.7), extended for the bnbagent-eval catalogue: verbatim
metric ids, an optional method, params satisfying the metric's required params, threshold and
required flag, the stage paragraph and reflection feedback (``bnbagent-eval:new``).
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = """You match evaluation dimensions to existing metrics of the bnbagent-eval catalogue.

For each dimension, find the best matching catalogue metric.
Return confidence 0.0-1.0 indicating how well the metric matches the dimension.
Only match if confidence >= 0.7; otherwise return metric_id null (a custom metric will be generated for it).

RULES:
- metric_id MUST be an id copied verbatim from the catalogue. Never invent ids.
- method is optional: omit it to use the metric's default method, or give one of the methods listed for that metric.
- params MUST include every required param listed for the metric, with concrete values derived from the requirements; omit params otherwise.
- threshold is the pass score in [0, 1]; omit it to use the metric's default.
- required is true for metrics that must pass.
- Prefer metrics whose inputs the sample cases (if listed) actually contain.
- You may add matches for feedback items that are not tied to a dimension (use the feedback text as the dimension).

## Output Format

Return only a JSON object:
{"matches": [{"dimension": "...", "metric_id": "<catalogue id or null>", "method": "<optional>", "params": {}, "threshold": 0.7, "required": true, "confidence": 0.0, "reasoning": "..."}]}"""

V1 = PromptTemplate(
    metric_id="authoring.eval_maker_match",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": (
            "# Dimensions to match:\n{dimensions}\n\n# Stage:\n{stage_guidance}\n\n"
            "# Inputs present in the sample cases:\n{inputs}\n\n"
            "# Feedback to incorporate (if any):\n{feedback}\n\n"
            "# Available Catalogue Metrics:\n{catalogue}"
        )
    },
    output_schema={
        "type": "object",
        "required": ["matches"],
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["dimension", "metric_id"],
                    "properties": {
                        "dimension": {"type": "string"},
                        "metric_id": {"type": ["string", "null"]},
                        "method": {"type": ["string", "null"]},
                        "params": {"type": ["object", "null"]},
                        "threshold": {"type": ["number", "null"]},
                        "required": {"type": ["boolean", "null"]},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "reasoning": {"type": "string"},
                    },
                },
            }
        },
    },
    source="agentsight:eval_maker.match_blackbox",
)

VERSIONS = {"v1": V1}
