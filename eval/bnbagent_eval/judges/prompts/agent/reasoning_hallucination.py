"""agent.reasoning_hallucination prompt templates.

v1: ported verbatim from AgentSight ``agent_hallucination.py`` V1 (the evaluator's default, JSON
output). The text-only V0 prompt is not ported.
"""

from __future__ import annotations

from .. import PromptTemplate
from ._whitebox import steps_schema

SYSTEM_V1 = '\nYou are a highly precise logical evaluation expert. Your task is to determine if an agent\'s thought is grounded in the provided evidence by following a strict reasoning process.\nEvaluate if the \'Agent\'s Thought\' is a valid, non-hallucinatory step based on the \'Evidence So Far\' and the provided rules and examples.\n\n### Evaluation Rules:\n1. **Reasonable inferences are NOT hallucinations**: Based on the Overall Goal, the agent can make logical connections (e.g., if the goal is about \'beer\', seeing a \'bottle\' and thinking \'beer\' is acceptable).\n2. **Planning is NOT a hallucination**: Thoughts that describe a plan for the next action are valid.\n3. **A hallucination is stating a specific, verifiable fact that is NOT in the evidence OR contradicts it.**\n\nYou must respond with a JSON object containing:\n- "analysis": Your detailed analysis of the agent\'s thoughts\n- "hallucination_steps": Array of step indices containing hallucinations (e.g., [2, 4])\n- "valid_steps": Array of step indices with valid thoughts (e.g., [1, 3])\n\n### Example 1 (Not a Hallucination)\nInput: [1]Called tool \'CountGivenObject\' with arguments {"image": "img1.jpg", "text": "person"}. Result: "3"\n[1] Agent\'s Thought: Now that I know there are 3 people, I will check their age.\nOutput: {\n  "analysis": "The thought\'s premise (\'I know there are 3 people\') is directly supported by the evidence. The rest of the thought (\'I will check their age\') is a plan for the next action. According to Rule #2, planning is not a hallucination. Therefore, the thought is valid.",\n  "hallucination_steps": [],\n  "valid_steps": [1]\n}\n\n### Example 2 (A Hallucination)\nInput: [2]Called tool \'OCR\' with arguments {"image": "menu.jpg"}. Result: "...Magna 6..."\n[2] Agent\'s Thought: The price of a Magna beer is $7 according to the menu.\nOutput: {\n  "analysis": "The thought states a specific fact (\'price is $7\'). The evidence from the OCR tool explicitly states the price is \'6\'. The thought contradicts the evidence. According to Rule #3, this is a hallucination.",\n  "hallucination_steps": [2],\n  "valid_steps": []\n}\n'

USER_V1 = "\n### Your Task\nEvaluate the agent's thought step by step based on collected evidences and respond with a JSON object as specified in the system prompt.\n\n### Overall Goal (User's query):\n{query}\n\n### Input files:\n{files}\n\n**Evidence with thoughts:**\n{evidence_with_thoughts}\n"

V1 = PromptTemplate(
    metric_id="agent.reasoning_hallucination",
    version="v1",
    system=SYSTEM_V1,
    parts={"invocation": USER_V1},
    output_schema=steps_schema("hallucination_steps", "valid_steps"),
    labels=None,
    source="agentsight:agent_hallucination_v1",
)

VERSIONS = {"v1": V1}
DEFAULT_VERSION = "v1"
