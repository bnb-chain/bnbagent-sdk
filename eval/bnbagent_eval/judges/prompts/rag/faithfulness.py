"""rag.faithfulness prompt templates.

v1: ported verbatim from AgentSight ``factual_correctness_v0`` (system prompt, rubric template);
the user prompt follows ``FactualCorrectnessEvaluator._format_prompt``.
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate

SYSTEM_V1 = 'You are an expert Factual Correctness Evaluator for a RAG (Retrieval-Augmented Generation) system. Your task is to evaluate a "Generated Answer" against a provided "Retrieval Context" (the source documents retrieved by the RAG system) and "User Question".\n\n# Evaluation Methodology\n\nYou will perform a "Chain of Thought" evaluation using the following steps. Do not skip steps.\n\n**Step 1: Claim Decomposition (Atomicity)**\nBreak the [Generated Answer] down into a list of atomic claims (indivisible facts).\n- *Guideline:* Ensure high atomicity. "Paris is in France and has a tower" becomes two claims: 1. Paris is in France. 2. Paris has a tower.\n\n**Step 2: Verification (5-Tier Verdict)**\nVerify each atomic claim against the [Retrieval Context] only. The Retrieval Context contains the source documents that were retrieved to answer the question.\n\nLabel each claim with one of these five verdicts:\n- **Supported** (Score: 1.0): The claim is explicitly stated or directly implied in the Retrieval Context\n- **Mostly Supported** (Score: 0.75): The claim is largely supported with minor gaps or imprecisions\n- **Partial** (Score: 0.5): The claim is partially supported - some aspects are in the Retrieval Context but details differ or are incomplete\n- **Hallucinated** (Score: 0.25): The claim is not present in the Retrieval Context but doesn\'t directly contradict it\n- **Contradicted** (Score: 0.0): The claim directly conflicts with information in the Retrieval Context\n\n**Step 3: Omission Check**\nCheck if the [Generated Answer] misses key details required to fully answer the [User Question] based *only* on the provided [Retrieval Context].\n\n**Step 4: Overall Scoring (5-Tier)**\nAssign an overall score based on this rubric:\n- **Excellent** (1.0): All claims are fully supported by the Retrieval Context, and the Question is completely answered\n- **Good** (0.75): The answer is mostly supported with minor issues or omissions\n- **Average** (0.5): The answer is partially supported but has notable gaps or minor hallucinations\n- **Below Average** (0.25): The answer contains significant hallucinations or is mostly unsupported\n- **Poor** (0.0): The answer contains contradictions or major factual errors\n\n# Important Notes\n\n- Only use information from the provided Retrieval Context for verification\n- Claims that are factually true in the real world but not in the Retrieval Context are still Hallucinated\n- Use "Partial" verdict when a claim has some truth but is incomplete or slightly inaccurate\n- Use "Mostly Supported" when a claim is largely correct but has minor imprecisions\n- Focus on factual accuracy, not writing style or formatting\n- Consider whether the answer fully addresses the question based on available context\n\n# Response Format\n\nProvide your evaluation with:\n1. List of atomic claims with verdicts (Supported/Mostly Supported/Partial/Hallucinated/Contradicted)\n2. Analysis of any missing key information\n3. Clear reasoning for the final score\n4. Overall score (Excellent/Good/Average/Below Average/Poor)\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "atomic_claims_analysis": {\n      "type": "array",\n      "items": {\n        "type": "object",\n        "properties": {\n          "claim": {"type": "string", "description": "The atomic claim extracted from the generated answer"},\n          "verdict": {"type": "string", "enum": ["Contradicted", "Hallucinated", "Partial", "Mostly Supported", "Supported"], "description": "Verdict for the claim"}\n        },\n        "required": ["claim", "verdict"]\n      },\n      "description": "List of atomic claims with their verdicts"\n    },\n    "omission_analysis": {"type": "string", "description": "Brief analysis of missing details from the context"},\n    "reasoning_summary": {"type": "string", "description": "Concise justification for the final score"},\n    "overall_score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Overall factual correctness rating"}\n  },\n  "required": ["atomic_claims_analysis", "omission_analysis", "reasoning_summary", "overall_score"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'
DOMAIN_RUBRIC_V1 = "# Domain-Specific Factual Correctness Criteria\n\nApply these additional criteria when evaluating factual correctness:\n\n{domain_rubric}"

# AgentSight ClaimVerdictType -> score.
CLAIM_VERDICTS = {
    "Contradicted": 0.0,
    "Hallucinated": 0.25,
    "Partial": 0.5,
    "Mostly Supported": 0.75,
    "Supported": 1.0,
}

V1 = PromptTemplate(
    metric_id="rag.faithfulness",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# User Question:\n{input}\n\n# Retrieval Context (Source Documents):\n{contexts}\n\n# Generated Answer:\n{actual_output}",
    },
    output_schema={
        "type": "object",
        "required": [
            "atomic_claims_analysis",
            "omission_analysis",
            "reasoning_summary",
            "overall_score",
        ],
        "properties": {
            "atomic_claims_analysis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["claim", "verdict"],
                    "properties": {
                        "claim": {"type": "string"},
                        "verdict": {"type": "string", "enum": list(CLAIM_VERDICTS)},
                    },
                },
            },
            "omission_analysis": {"type": "string"},
            "reasoning_summary": {"type": "string"},
            "overall_score": {"type": "string", "enum": list(FIVE_TIER)},
        },
    },
    labels=FIVE_TIER,
    domain_rubric_template=DOMAIN_RUBRIC_V1,
    source="agentsight:factual_correctness_v0",
    extra={"claim_verdicts": CLAIM_VERDICTS},
)

VERSIONS = {"v1": V1}
