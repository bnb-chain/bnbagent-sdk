"""agent.goal_success prompt templates.

v0: system prompt ported verbatim from AgentSight ``goal_sucess_rate/goal_success_rate.py``
(v0). The source sends the whole multi-agent history as the user prompt and asks for structured
output (``GoalSuccessRating``); the optional expected-outcome section is ours.
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V0 = "You are an objective judge evaluating the quality of an AI assistant as to whether a conversation between a User and the AI assistant successfully completed all User goals. You will be provided with:\n1. The list of available tools the AI assistant can use. There may be descriptions for each tool about when to use it and how to use it.\n2. The complete conversation record with multiple turns including:\n    - User messages (User:)\n    - Assistant responses (Assistant:)\n    - Tool selected by the assistant (Action:)\n    - Tool outputs (Tool:)\n3. The final assistant response that concludes the conversation.\n    \nYour task is to carefully analyze the conversation and determine if all User goals were successfully achieved. In order to achieve a User goal, the AI assistant usually need to use some tools and respond to User about the outcome. Please assess the goals one by one, following the steps below:\n1. First, analyze the list of available tools, reason about what tools the AI assistant should use, and what response it should provide to the User in order to achieve the goal;\n2. Next, check the conversation record and the final assistant response to decide whether the AI assistant used the expected tools and got the expected output, got the expected information, and responded to the User in the expected way. If the AI assistant did all expected work in the conversation record and provided an appropriate final response, the goal was achieved.\n3. After judging about all the goals, decide whether the conversation achieved all user goals or not.\n4. Output the goals and their success scores in a structured format.\n"

V0 = PromptTemplate(
    metric_id="agent.goal_success",
    version="v0",
    system=SYSTEM_V0,
    parts={
        "simple": "{trajectory}",
        "with_expected": "{trajectory}\n\n# Expected outcome\n{expected_output}",
    },
    output_schema={
        "type": "object",
        "required": ["reasoning", "goals"],
        "properties": {
            "reasoning": {"type": "string"},
            "goals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["goal_description", "goal_success_score"],
                    "properties": {
                        "goal_description": {"type": "string"},
                        "goal_success_reasoning": {"type": "string"},
                        "goal_success_score": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            },
        },
    },
    labels=None,
    source="agentsight:goal_sucess_rate_v0",
)

VERSIONS = {"v0": V0}
