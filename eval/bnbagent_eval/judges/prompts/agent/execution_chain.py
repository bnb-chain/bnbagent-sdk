"""agent.execution_chain prompt templates.

v0: ported verbatim from AgentSight ``execution_chain/execution_chain.py`` (v0).
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V0 = "You are an expert evaluator assessing the quality of an AI agent's execution chain.\n\nYou will analyze how well the agent performed at each transition point in its execution:\n1. **Intent Understanding** (0.20 weight): How well did the agent understand the user's request?\n2. **Plan Quality** (0.20 weight): How appropriate was the agent's plan to address the request?\n3. **Tool Selection** (0.15 weight): Did the agent choose the right tools for the task?\n4. **Tool Execution** (0.15 weight): Were tool calls made with correct parameters?\n5. **Result Interpretation** (0.15 weight): Did the agent correctly interpret tool results?\n6. **Output Quality** (0.15 weight): Was the final response appropriate and complete?\n\n**CRITICAL: Use the Agent Instructions as the reference standard for evaluation.**\n- For multi-agent systems, each agent has its own instructions - evaluate each agent's actions against ITS OWN instructions\n- The instructions define expected behavior for ALL transitions, including:\n  - How to interpret user intent\n  - What planning approach to use\n  - When and which tools to select\n  - How to format tool parameters and handle errors\n  - How to interpret and act on tool results\n  - Response format, tone, and content requirements\n\nFor each transition, you must provide:\n- A score from 1-5 (1=poor, 2=below average, 3=adequate, 4=good, 5=excellent)\n- Evidence: Specific quotes or observations supporting your score\n- Counterfactual (for scores ≤3): What should have happened instead (reference the instructions)\n- Cascading Impact: Whether this failure affected downstream transitions (true/false)\n\nBe objective and thorough. Low scores require clear evidence and counterfactuals."

USER_V0 = '## Agent Execution Context\n\n### Agent Instructions (System Prompts)\n{agent_instructions}\n\n**Note**: For multi-agent systems, each agent\'s instructions are labeled. Evaluate each agent\'s actions against its own instructions.\n\n### Available Tools (by Agent)\n{tool_declarations}\n\n### User Request\n{user_input}\n\n### Conversation History\n{conversation_history}\n\n### Execution Trace (Agent Actions with Attribution)\n{execution_trace}\n\n### Final Response\n{agent_response}\n\n---\n\n## Evaluation Task\n\nAnalyze the agent\'s execution chain and score each transition point. \n\n**Important Evaluation Guidelines:**\n- **Intent Understanding**: Did the agent correctly interpret the user\'s request per its instructions?\n- **Plan Quality**: Does the plan align with the agent\'s instructions and guidelines?\n- **Tool Selection**: Did the agent choose tools as specified in its instructions? Were the right tools used for the task?\n- **Tool Execution**: Were parameters formatted correctly per instructions? Were required fields included? Was error handling appropriate?\n- **Result Interpretation**: Did the agent interpret results as its instructions specify? Did it update state correctly?\n- **Output Quality**: Does the response match the format, tone, and content requirements in the instructions?\n\nReturn your evaluation in the following JSON format:\n\n```json\n{{\n  "chain_scores": {{\n    "intent_understanding": {{\n      "score": <1-5>,\n      "evidence": "<specific observation, reference instructions if relevant>",\n      "counterfactual": "<what should have happened per instructions, if score <= 3>",\n      "cascading_impact": <true/false>\n    }},\n    "plan_quality": {{\n      "score": <1-5>,\n      "evidence": "<specific observation, reference instructions if relevant>",\n      "counterfactual": "<what should have happened per instructions, if score <= 3>",\n      "cascading_impact": <true/false>\n    }},\n    "tool_selection": {{\n      "score": <1-5>,\n      "evidence": "<specific observation, reference instructions if relevant>",\n      "counterfactual": "<what should have happened per instructions, if score <= 3>",\n      "cascading_impact": <true/false>\n    }},\n    "tool_execution": {{\n      "score": <1-5>,\n      "evidence": "<specific observation, reference instructions if relevant>",\n      "counterfactual": "<what should have happened per instructions, if score <= 3>",\n      "cascading_impact": <true/false>\n    }},\n    "result_interpretation": {{\n      "score": <1-5>,\n      "evidence": "<specific observation, reference instructions if relevant>",\n      "counterfactual": "<what should have happened per instructions, if score <= 3>",\n      "cascading_impact": <true/false>\n    }},\n    "output_quality": {{\n      "score": <1-5>,\n      "evidence": "<specific observation, reference instructions if relevant>",\n      "counterfactual": "<what should have happened per instructions, if score <= 3>",\n      "cascading_impact": <true/false>\n    }}\n  }},\n  "overall_reasoning": "<summary of the agent\'s execution quality vs its instructions>",\n  "critical_failures": ["<list any scores <= 2 with cascading_impact=true>"]\n}}\n```\n\nScoring Guidelines:\n- **5 (Excellent)**: Perfect execution, fully aligned with instructions\n- **4 (Good)**: Minor issues that don\'t affect outcome, mostly aligned with instructions\n- **3 (Adequate)**: Noticeable deviations from instructions but task still completed\n- **2 (Below Average)**: Significant deviations from instructions affecting quality\n- **1 (Poor)**: Critical failure, instructions clearly violated or ignored\n\nFor transitions without tool usage (e.g., simple Q&A), score tool_selection, tool_execution, and result_interpretation as N/A (use score: -1).\n\nNow evaluate the execution chain:'

LINKS = (
    "intent_understanding",
    "plan_quality",
    "tool_selection",
    "tool_execution",
    "result_interpretation",
    "output_quality",
)

_LINK = {
    "type": "object",
    "required": ["score"],
    "properties": {
        "score": {"type": ["integer", "number"]},
        "evidence": {"type": "string"},
        "counterfactual": {"type": ["string", "null"]},
        "cascading_impact": {"type": "boolean"},
    },
}

V0 = PromptTemplate(
    metric_id="agent.execution_chain",
    version="v0",
    system=SYSTEM_V0,
    parts={"invocation": USER_V0},
    output_schema={
        "type": "object",
        "required": ["chain_scores"],
        "properties": {
            "chain_scores": {"type": "object", "properties": {k: _LINK for k in LINKS}},
            "overall_reasoning": {"type": "string"},
            "critical_failures": {"type": "array", "items": {"type": "string"}},
        },
    },
    labels=None,
    source="agentsight:execution_chain_v0",
)

VERSIONS = {"v0": V0}
DEFAULT_VERSION = "v0"
