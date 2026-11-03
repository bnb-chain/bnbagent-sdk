"""quality.relevance prompt templates.

v1: ported verbatim from AgentSight ``relevance_v0`` (prompt text, modes, labels).
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = 'You are an expert evaluator assessing the relevance of agent responses to user queries.\n\nYour task is to evaluate whether the agent\'s response directly addresses the user\'s query and intent.\n\n## Evaluation Criteria (5-Tier Scale)\n\n**Poor** (Score: 0.0):\n- Does not address the query\'s intent\n- Discusses unrelated topics\n- Misunderstands the question\n- Response is completely off-topic\n\n**Below Average** (Score: 0.25):\n- Addresses minimal aspects of the query\n- Most key points are missing\n- Significant off-topic content\n- Only tangentially related to the question\n\n**Average** (Score: 0.5):\n- Addresses some aspects of the query\n- Key points are partially covered\n- Notable tangential information present\n- Response partially answers the question\n\n**Good** (Score: 0.75):\n- Addresses the main intent of the query\n- Most key points are covered\n- Minor tangential information may be present\n- The core question is answered\n\n**Excellent** (Score: 1.0):\n- Directly answers the specific question asked\n- All key points from the query are addressed\n- Response stays focused on the query topic\n- No significant tangential information included\n\n## Evaluation Process\n\n1. Identify the user\'s core question and intent\n2. List the key points that should be addressed\n3. Check which key points are covered in the response\n4. Assess if the response stays on-topic\n5. Determine the relevance level based on coverage and focus\n\n## Conversation-Specific Guidelines (when applicable):\n- Evaluate relevance for each turn in the conversation\n- Check if responses address the specific query in each turn\n- Identify if responses drift off-topic across turns\n- Consider if context from previous turns is appropriately used\n\n## Response Efficiency\n\n- Be concise in your reasoning (2-3 sentences per criterion)\n- Focus on key evidence, not exhaustive analysis\n- Provide specific examples only when they clarify the rating\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Step-by-step reasoning explaining the relevance assessment"},\n    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical relevance rating"},\n    "key_points_addressed": {"type": "array", "items": {"type": "string"}, "description": "List of key points from the query that were addressed"},\n    "key_points_missing": {"type": "array", "items": {"type": "string"}, "description": "List of key points from the query that were not addressed"}\n  },\n  "required": ["reasoning", "score"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

V1 = PromptTemplate(
    metric_id="quality.relevance",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "simple": "# User Query:\n{input}\n\n# Agent Response:\n{actual_output}",
        "with_expected": "# User Query:\n{input}\n\n# Expected Response (Reference):\n{expected_output}\n\n# Actual Response to Evaluate:\n{actual_output}",
        "conversation": "# Conversation History:\n{conversation_history}",
        "task_simple": "\n\n# Evaluation Task:\nAssess the relevance of the agent's response to the user query.",
        "task_with_expected": "\n\n# Evaluation Task:\nAssess the relevance of the actual response compared to the expected response. Consider:\n- Does the actual response address the same key points as the expected response?\n- Is the actual response as focused on the query as the expected response?\n- Are there any off-topic elements in the actual response that aren't in the expected response?",
        "task_conversation": "\n\n# Evaluation Task:\nAssess the relevance of the assistant's responses across the conversation. Consider:\n- Does each response address the user's query in that turn?\n- Do responses stay on-topic throughout the conversation?\n- Are all key points from user queries addressed?",
    },
    output_schema=five_tier_schema(
        {
            "key_points_addressed": {"type": "array", "items": {"type": "string"}},
            "key_points_missing": {"type": "array", "items": {"type": "string"}},
        }
    ),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Relevance Criteria\n\nApply these additional criteria when assessing relevance:\n\n{domain_rubric}",
    source="agentsight:relevance_v0",
)

VERSIONS = {"v1": V1}
