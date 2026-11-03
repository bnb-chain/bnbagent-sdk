"""Eval maker stage 2 (strategy): one evaluation dimension per stated requirement.

v1: AgentSight eval_maker ``STRATEGY_ANALYSIS_PROMPT`` (exactly one dimension per requirement,
no extra "general quality" dimensions, blackbox vs custom approach with a candidate), with the
blackbox registry replaced by the bnbagent-eval metric catalogue (approach ``catalogue`` or
``custom``) and a stage paragraph (``bnbagent-eval:new``).
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = """You analyze evaluation requirements and determine the optimal evaluation strategy.

# Task:
Identify each distinct requirement in the description (each bullet point, or each sentence that states a quality). Create EXACTLY one dimension per requirement.

For each requirement:
1. Create ONE dimension that directly maps to that requirement
2. Determine if a catalogue metric can cover it completely OR if a custom metric is needed
3. Use the "catalogue" approach ONLY if a catalogue metric directly matches the requirement, and name it as the candidate_metric (an id from the catalogue, verbatim)

CRITICAL RULES:
- Create EXACTLY as many dimensions as there are requirements
- Do NOT add extra dimensions for "general quality", "coherence", "helpfulness", etc.
- Do NOT split one requirement into multiple dimensions
- Do NOT combine multiple requirements into one dimension

Return dimensions with clear snake_case names, descriptions, and the recommended approach.

## Output Format

Return only a JSON object:
{"dimensions": [{"name": "...", "description": "...", "source_requirement": "...", "approach": "catalogue" | "custom", "candidate_metric": "<catalogue id or null>"}], "rationale": "..."}"""

V1 = PromptTemplate(
    metric_id="authoring.eval_maker_strategy",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": (
            "# Requirements:\n{description}\n\n# Assessment:\n{assessment}\n\n"
            "# Stage:\n{stage_guidance}\n\n# Available Catalogue Metrics:\n{catalogue}"
        )
    },
    output_schema={
        "type": "object",
        "required": ["dimensions"],
        "properties": {
            "dimensions": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["name", "description", "approach"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "description": {"type": "string"},
                        "source_requirement": {"type": "string"},
                        "approach": {"enum": ["catalogue", "custom"]},
                        "candidate_metric": {"type": ["string", "null"]},
                    },
                },
            },
            "rationale": {"type": "string"},
        },
    },
    source="agentsight:eval_maker.analyze_strategy",
)

VERSIONS = {"v1": V1}
