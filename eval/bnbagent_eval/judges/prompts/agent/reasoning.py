"""agent.reasoning prompt templates.

v0: ported verbatim from AgentSight ``agent_reasoning/agent_reasoning.py``; the seven built-in
rubrics are ``AgentReasoningEvaluator.get_default_reasoning_rubrics``.
"""

from __future__ import annotations

from .. import PromptTemplate
from ._whitebox import RUBRIC_SCHEMA

SYSTEM_V0 = '\nYou are evaluating the reasoning quality of an AI agent\'s responses and decision-making process.\n\nAnalyze the conversation and evaluate each rubric based on the agent\'s reasoning demonstrated throughout the interaction.\n\nFor each rubric, provide:\n- Property: [exact rubric text]\n- Rationale: [detailed explanation of your assessment]\n- Verdict: [yes/no/nr]\n\nUse "yes" if the agent demonstrates the quality, "no" if it fails to demonstrate it, and "nr" if not relevant to assess.\n\n# Output format:\nRespond with valid JSON in this exact format:\n{\n  "evaluations": [\n    {\n      "property": "The exact property text being evaluated",\n      "rationale": "Your detailed reasoning for the verdict",\n      "verdict": "yes/no/nr"\n    }\n  ]\n}\n\n'

USER_V0 = '\n# Available tools, user input, response and properties:\n<available_tools>\n{tool_declarations}\n</available_tools>\n\n<user_prompt>\n{user_input}\n</user_prompt>\n\n<previous_turns>\n{conversation_history}\n</previous_turns>\n\n<agent_thought_prorcess>\n{thought}\n</agent_thought_prorcess>\n\n<response>\n{tool_usage}\n</response>\n\n<properties>\n{rubrics}\n</properties>\n\nREMEMBER: Your answer will help improve the AI agent. It is important to determine the fulfillment of the properties correctly. Even answering "no" will improve the agent! Respond your evaluation in pure text, output the final evaluation result in the defined json format.\nIMPORTANT: For the intermediate evaluation process, make sure for each of the property listed, follow the example steps and output "Property: ..." on a new line and "Verdict: ..." on another new line. Assemble your final evaluation result in json format.\n'

DEFAULT_RUBRICS = (
    ("logical_progression", "Agent demonstrates clear logical progression in reasoning steps"),
    ("evidence_based", "Agent bases conclusions on available evidence and information"),
    ("uncertainty_handling", "Agent appropriately acknowledges limitations and uncertainty"),
    ("tool_selection", "Agent selects appropriate tools and methods for information gathering"),
    ("coherent_explanation", "Agent provides coherent and understandable explanations"),
    ("problem_decomposition", "Agent breaks down complex problems into manageable components"),
    ("assumption_clarity", "Agent clearly states assumptions when making inferences"),
)

V0 = PromptTemplate(
    metric_id="agent.reasoning",
    version="v0",
    system=SYSTEM_V0,
    parts={"invocation": USER_V0},
    output_schema=RUBRIC_SCHEMA,
    labels=None,
    source="agentsight:agent_reasoning_v0",
)

VERSIONS = {"v0": V0}
DEFAULT_VERSION = "v0"
