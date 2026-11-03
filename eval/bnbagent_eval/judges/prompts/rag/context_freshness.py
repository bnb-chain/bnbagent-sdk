"""rag.context_freshness prompt templates.

v1: ported verbatim from AgentSight ``context_time_freshness_v0`` (system prompt, rubric
template). The configured-thresholds and sensitivity-override sections are appended by
``rag.context_freshness`` exactly as in ``ContextTimeFreshnessEvaluator._build_system_prompt``;
the output schema mirrors its ``ContextTimeFreshnessRating`` model.
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = 'You are an expert evaluator assessing the temporal appropriateness of retrieved contexts in a Retrieval-Augmented Generation (RAG) system.\n\n# Your Task\n\nEvaluate whether the retrieved contexts are **temporally fresh enough** to appropriately answer the given query. Consider:\n1. Does the query have temporal requirements (explicit or implicit)?\n2. Are the retrieved contexts recent enough to satisfy these requirements?\n3. Would outdated information lead to incorrect or misleading answers?\n\n# Evaluation Criteria\n\n## Step 1: Identify Temporal Sensitivity\n\nClassify the query\'s temporal sensitivity:\n- **CRITICAL**: Query explicitly asks for recent/current/latest information (e.g., "latest", "current", "recent", "today", "this week")\n- **HIGH**: Query implicitly requires recent information (e.g., stock prices, weather, breaking news, ongoing events)\n- **MODERATE**: Query benefits from recent information but historical context is acceptable (e.g., company information, product reviews)\n- **LOW**: Query is timeless or historical in nature (e.g., historical events, mathematical concepts, established facts)\n\n## Step 2: Analyze Context Timestamps\n\nFor each context:\n- Check if publish/update dates are available\n- Calculate the time gap between context date and query timestamp\n- Determine if the age is appropriate for the temporal sensitivity level\n\n## Step 3: Evaluate Freshness Appropriateness\n\nBased on temporal sensitivity, assess if contexts are fresh enough:\n\n**For CRITICAL temporal sensitivity:**\n- Contexts should be from within the specified timeframe (e.g., "past 24 hours" requires contexts < 24 hours old)\n- Missing timestamps are a major issue\n- Score: 0 (Poor) if contexts > required timeframe, 3 (Good) if barely meeting, 5 (Excellent) if well within timeframe\n\n**For HIGH temporal sensitivity:**\n- Contexts should typically be from the past week to month depending on topic\n- Contexts older than 3 months are generally problematic\n- Score: 0-1 if > 3 months old, 2-3 if 1-3 months old, 4-5 if < 1 month old\n\n**For MODERATE temporal sensitivity:**\n- Contexts from the past year are generally acceptable\n- Some older contexts are OK if supplemented by recent ones\n- Score: 0-2 if all contexts > 2 years old, 3-4 if mixed, 5 if mostly recent\n\n**For LOW temporal sensitivity:**\n- Age of contexts is not critical\n- Score: 4-5 if contexts are accurate regardless of age\n\n## Step 4: Check for Missing Timestamps\n\n- Missing timestamps prevent proper freshness assessment\n- For time-sensitive queries, missing timestamps should lower the score\n- Note how many contexts lack timestamp information\n\n## Step 5: Consider Context Diversity\n\n- For time-sensitive topics, having multiple recent contexts is better than one\n- Check if contexts represent different time points or all from same period\n\n# Important Notes\n\n- Focus on temporal appropriateness, not content accuracy\n- Consider the specific domain and topic when assessing freshness requirements\n- Missing timestamps are particularly problematic for time-sensitive queries\n\n# Response Format\n\nProvide your evaluation with:\n1. Temporal sensitivity classification with reasoning\n2. Analysis of each context\'s timestamp and age\n3. Assessment of missing timestamps and their impact\n4. Overall freshness score (0-5)\n5. Detailed reasoning for the score\n6. Potential issues and recommendations'
DOMAIN_RUBRIC_V1 = "# Domain-Specific Freshness Criteria\n\nApply these additional temporal freshness considerations:\n\n{domain_rubric}"

SENSITIVITIES = ("CRITICAL", "HIGH", "MODERATE", "LOW")

V1 = PromptTemplate(
    metric_id="rag.context_freshness",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# Query\n{input}\n\n# Query Timestamp (Reference Time)\n{query_timestamp}\n\n# Retrieved Contexts\n{contexts}\n\n# Answer\n{answer}",
    },
    output_schema={
        "type": "object",
        "required": [
            "temporal_sensitivity",
            "temporal_sensitivity_reasoning",
            "context_analysis",
            "missing_timestamps_count",
            "missing_timestamps_impact",
            "overall_freshness_score",
            "score_reasoning",
            "potential_issues",
            "recommendations",
        ],
        "properties": {
            "temporal_sensitivity": {"type": "string", "enum": list(SENSITIVITIES)},
            "temporal_sensitivity_reasoning": {"type": "string"},
            "context_analysis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "context_id",
                        "has_timestamp",
                        "age_in_days",
                        "age_assessment",
                        "reasoning",
                    ],
                    "properties": {
                        "context_id": {"type": "integer"},
                        "has_timestamp": {"type": "boolean"},
                        "age_in_days": {"type": ["integer", "null"]},
                        "age_assessment": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                },
            },
            "missing_timestamps_count": {"type": "integer"},
            "missing_timestamps_impact": {"type": "string"},
            "overall_freshness_score": {"type": "number", "minimum": 0, "maximum": 5},
            "score_reasoning": {"type": "string"},
            "potential_issues": {"type": "array", "items": {"type": "string"}},
            "recommendations": {"type": "array", "items": {"type": "string"}},
        },
    },
    labels=None,
    domain_rubric_template=DOMAIN_RUBRIC_V1,
    source="agentsight:context_time_freshness_v0",
)

VERSIONS = {"v1": V1}
