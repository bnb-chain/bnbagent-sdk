"""Shared prompt for rag.context_relevance and rag.context_precision.

v1: ported verbatim from AgentSight ``context_quality_v0`` (one judge call rates relevance and
precision); the user prompt follows ``ContextQualityEvaluator._format_prompt``. Both metrics use
this one template, so with a judge cache the second metric reuses the first metric's call.
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate

SYSTEM_V1 = 'You are an expert Context Quality Evaluator for RAG (Retrieval-Augmented Generation) systems. Your task is to evaluate the quality of retrieved contexts by assessing their relevance to the user\'s query.\n\n# Evaluation Methodology\n\nYou will evaluate each retrieved context for its relevance to answering the user\'s query. This evaluation focuses on the RETRIEVAL stage - whether the right documents were retrieved, independent of any generated answer.\n\n**Step 1: Query Analysis**\nAnalyze the user\'s query to understand:\n- The core information need\n- Key concepts and entities mentioned\n- The type of answer expected (factual, explanatory, procedural, etc.)\n\n**Step 2: Per-Context Relevance Assessment**\nFor each retrieved context, assess its relevance using this 5-tier scale:\n\n- **Highly Relevant** (Score: 1.0): Context directly addresses the query\'s core information need. Contains key information required to answer the question.\n- **Relevant** (Score: 0.75): Context is clearly related and provides useful supporting information, though may not directly answer the query.\n- **Partially Relevant** (Score: 0.5): Context has some connection to the query topic but only tangentially addresses the information need.\n- **Marginally Relevant** (Score: 0.25): Context mentions related concepts but provides little useful information for answering the query.\n- **Not Relevant** (Score: 0.0): Context is unrelated to the query or provides no useful information for answering it.\n\nFor each context, provide:\n- A relevance verdict (Highly Relevant/Relevant/Partially Relevant/Marginally Relevant/Not Relevant)\n- Brief reasoning explaining the assessment\n\n**Step 3: Overall Quality Assessment**\nBased on the per-context analysis, provide:\n\n1. **Context Relevance Score** (0-5): Average quality of retrieved contexts\n   - 5: All contexts are highly relevant\n   - 4: Most contexts are relevant to highly relevant\n   - 3: Mix of relevant and partially relevant contexts\n   - 2: Most contexts are only partially or marginally relevant\n   - 1: Most contexts are not relevant\n   - 0: No contexts are relevant\n\n2. **Context Precision**: Proportion of retrieved contexts that are relevant (score >= 0.5)\n   - This is calculated as: (number of relevant contexts) / (total contexts)\n   - A context is considered "relevant" if its score is >= 0.5 (Partially Relevant or better)\n\n3. **Retrieval Quality Label**: Overall assessment\n   - **Excellent**: All or nearly all contexts are highly relevant\n   - **Good**: Most contexts are relevant with minor gaps\n   - **Average**: Mix of relevant and irrelevant contexts\n   - **Below Average**: Most contexts are marginally relevant or irrelevant\n   - **Poor**: Retrieved contexts fail to address the query\n\n# Important Notes\n\n- Focus ONLY on whether contexts are relevant to the QUERY, not on any generated answer\n- A context can be factually accurate but still irrelevant if it doesn\'t help answer the query\n- Consider both direct relevance (answers the question) and indirect relevance (provides useful context)\n- Redundant contexts (same information repeated) should each be evaluated independently\n- Empty or very short contexts that provide no substantive information should be marked as Not Relevant\n\n# Response Format\n\nProvide your evaluation with:\n1. Brief query analysis\n2. Per-context relevance assessment with verdicts and reasoning\n3. Overall context relevance score (0-5)\n4. Context precision (proportion of relevant contexts)\n5. Retrieval quality label and reasoning\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "query_analysis": {"type": "string", "description": "Brief analysis of the query\'s information need"},\n    "context_analysis": {\n      "type": "array",\n      "items": {\n        "type": "object",\n        "properties": {\n          "context_id": {"type": "integer", "description": "ID of the context being evaluated"},\n          "verdict": {"type": "string", "enum": ["Not Relevant", "Marginally Relevant", "Partially Relevant", "Relevant", "Highly Relevant"], "description": "Relevance verdict"},\n          "reasoning": {"type": "string", "description": "Brief explanation"}\n        },\n        "required": ["context_id", "verdict", "reasoning"]\n      },\n      "description": "Per-context relevance assessment"\n    },\n    "overall_relevance_score": {"type": "number", "minimum": 0, "maximum": 5, "description": "Score from 0 to 5"},\n    "context_precision": {"type": "number", "minimum": 0, "maximum": 1, "description": "Proportion of relevant contexts"},\n    "retrieval_quality_label": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Overall retrieval quality label"},\n    "score_reasoning": {"type": "string", "description": "Explanation of the assessment"},\n    "recommendations": {"type": "array", "items": {"type": "string"}, "description": "Recommendations for improvement"}\n  },\n  "required": ["query_analysis", "context_analysis", "overall_relevance_score", "context_precision", "retrieval_quality_label", "score_reasoning"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'
DOMAIN_RUBRIC_V1 = "# Domain-Specific Context Quality Criteria\n\nApply these additional context quality considerations:\n\n{domain_rubric}"

# AgentSight RelevanceVerdict -> score.
RELEVANCE_VERDICTS = {
    "Not Relevant": 0.0,
    "Marginally Relevant": 0.25,
    "Partially Relevant": 0.5,
    "Relevant": 0.75,
    "Highly Relevant": 1.0,
}

V1 = PromptTemplate(
    metric_id="rag.context_quality",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# User Query\n{input}\n\n# Retrieved Contexts\n{contexts}",
    },
    output_schema={
        "type": "object",
        "required": ["context_analysis", "overall_relevance_score"],
        "properties": {
            "query_analysis": {"type": "string"},
            "context_analysis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["context_id", "verdict"],
                    "properties": {
                        "context_id": {"type": "integer"},
                        "verdict": {"type": "string", "enum": list(RELEVANCE_VERDICTS)},
                        "reasoning": {"type": "string"},
                    },
                },
            },
            "overall_relevance_score": {"type": "number", "minimum": 0, "maximum": 5},
            "context_precision": {"type": "number", "minimum": 0, "maximum": 1},
            "retrieval_quality_label": {"type": "string", "enum": list(FIVE_TIER)},
            "score_reasoning": {"type": "string"},
            "recommendations": {"type": "array", "items": {"type": "string"}},
        },
    },
    labels=None,
    domain_rubric_template=DOMAIN_RUBRIC_V1,
    source="agentsight:context_quality_v0",
    extra={"relevance_verdicts": RELEVANCE_VERDICTS},
)

VERSIONS = {"v1": V1}
