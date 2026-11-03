"""agent.adaptivity prompt templates.

v1: ported verbatim from AgentSight ``agent_adaptivity.py`` V1 (the evaluator's default, JSON
output). The text-only V0 prompt is not ported.
"""

from __future__ import annotations

from .. import PromptTemplate
from ._whitebox import steps_schema

SYSTEM_V1 = '\nYou are an expert in evaluating AI agent behavior, specifically their ability to adapt after making a mistake.\nYour task is to evaluate if the \'Agent\'s Thought\' shows adaptivity after a tool call failed.\n\n### Context of Failure:\nIn the previous turn, this agent called an unavailable tool and was instructed to use a different one before generating this response.\n\n### Evaluation Rules:\n- **Adaptive:** The agent tries a new approach. This includes calling a tool, or formulating a new plan to solve the problem.\n- **Not Adaptive:** The agent gives up, or gets stuck without making progress.\n- **Not Relevant:** there is no unavailable tool being called in the trace \n\nYou must respond with a JSON object containing:\n- "analysis": Your detailed analysis of the agent\'s behavior\n- "adaptive_steps": Array of step indices showing adaptive behavior (e.g., [1, 3])\n- "not_adaptive_steps": Array of step indices showing non-adaptive behavior (e.g., [2])\n- "not_relevant_steps": Array of step indices where adaptivity is not relevant (e.g., [4])\n\n### Example 1 (Adaptive)\nInput: **[1] Agent\'s Thought:** "I\'ll use the \'SearchWeb\' tool to look up the weather for Seoul."\nOutput: {\n  "analysis": "The agent proposed a valid strategy (\'SearchWeb\'). This is excellent adaptive behavior.",\n  "adaptive_steps": [1],\n  "not_adaptive_steps": [],\n  "not_relevant_steps": []\n}\n\n### Example 2 (Not Adaptive - Gives Up)\nInput: **[2] Agent\'s Thought:** "I cannot solve this problem."\nOutput: {\n  "analysis": "The agent encountered a minor setback and gave up completely instead of trying another approach. This is not adaptive.",\n  "adaptive_steps": [],\n  "not_adaptive_steps": [2],\n  "not_relevant_steps": []\n}\n\n### Example 3 (Not Relevant - Agent does not need to try a new approach)\nInput: **[2] Agent\'s Thought:** "I\'ve got what I need. Let me carry on with the task"\nOutput: {\n  "analysis": "The original plan was executed successfully. New plan or adaptive behavior is not needed.",\n  "adaptive_steps": [],\n  "not_adaptive_steps": [],\n  "not_relevant_steps": [2]\n}\n'

USER_V1 = "\n### Your Task\nEvaluate the following agent's thought process and respond with a JSON object as specified in the system prompt.\n\n**Agent's Thought Process:**\n\"{thought}\"\n"

V1 = PromptTemplate(
    metric_id="agent.adaptivity",
    version="v1",
    system=SYSTEM_V1,
    parts={"invocation": USER_V1},
    output_schema=steps_schema("adaptive_steps", "not_adaptive_steps", "not_relevant_steps"),
    labels=None,
    source="agentsight:agent_adaptivity_v1",
)

VERSIONS = {"v1": V1}
DEFAULT_VERSION = "v1"
