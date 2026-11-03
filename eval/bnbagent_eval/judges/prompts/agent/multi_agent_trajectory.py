"""agent.multi_agent_trajectory prompt templates (AgentSight ``trajectory_accuracy`` e2e).

v0 / v1: ported verbatim from ``e2e_trajectory_correctness.py`` (source default v1).
"""

from __future__ import annotations

from .. import PromptTemplate
from ._whitebox import RUBRIC_SCHEMA

SYSTEM_V0 = '# Mission\n- Your mission is to evaluate the end-to-end correctness of multi-agent tool usage trajectories in complex hierarchical scenarios.\n- You will be presented with a user prompt (<user_prompt>), the complete agent trajectory (<response>) involving multiple agents and tool calls, and evaluation properties (<property>).\n- Focus on coordination between agents, proper delegation, and overall task completion across the entire multi-agent system.\n\n# Multi-Agent Evaluation Focus\nAssess whether the multi-agent system:\n1. Properly delegates tasks between specialized agents\n2. Maintains correct information flow across agent boundaries  \n3. Coordinates tool usage effectively across the hierarchy\n4. Achieves complete end-to-end task fulfillment\n5. Handles inter-agent communication and handoffs correctly\n\n# Rubric\n"yes": The multi-agent trajectory fulfilled the property across all agents and coordination points.\n"no": The multi-agent trajectory failed to fulfill the property at any coordination or execution point.\n\n# For each property, follow these steps:\nSTEP 1: Repeat the property, word for word, without making any changes.\nSTEP 2: Identify which agents and coordination points are relevant to this property.\nSTEP 3: Trace the multi-agent execution path and evaluate fulfillment across all relevant agents.\nSTEP 4: Review the end-to-end trajectory against the property requirements.\nSTEP 5: Output the final verdict for the complete multi-agent system.\nProperty: [[Repeat the property from STEP 1.]]\nRationale: [[Explain reasoning considering multi-agent coordination and end-to-end execution.]]\nVerdict: [[yes|no]]\n'

USER_V0 = '\n# Available tools, user input, multi-agent trajectory and properties:\n<available_tools>\n{tool_declarations}\n</available_tools>\n\n<agent_name>\n{agent_name}\n</agent_name>\n\n<user_prompt>\n{user_input}\n</user_prompt>\n\n<multi_agent_trajectory>\n{tool_usage}\n</multi_agent_trajectory>\n\n<agent_response>\n{agent_response}\n</agent_response>\n\n<properties>\n{rubrics}\n</properties>\n\nIMPORTANT: Evaluate the complete end-to-end multi-agent trajectory. Consider agent coordination, delegation patterns, and overall system behavior when assessing each property.\nMake sure for each of the property listed, follow the example steps and output "Property: ..." on a new line and "Verdict: ..." on another new line.\n'

SYSTEM_V1 = '# Mission\n- Your mission is to evaluate the end-to-end correctness of multi-agent tool usage trajectories in complex hierarchical scenarios.\n- You will be presented with a user prompt (<user_prompt>), the complete agent trajectory (<response>) involving multiple agents and tool calls, and evaluation properties (<property>).\n- Focus on coordination between agents, proper delegation, and overall task completion across the entire multi-agent system.\n\n# Multi-Agent Evaluation Focus\nAssess whether the multi-agent system:\n1. Properly delegates tasks between specialized agents\n2. Maintains correct information flow across agent boundaries  \n3. Coordinates tool usage effectively across the hierarchy\n4. Achieves complete end-to-end task fulfillment\n5. Handles inter-agent communication and handoffs correctly\n\n# Rubric\n"yes": The multi-agent trajectory fulfilled the property across all agents and coordination points.\n"no": The multi-agent trajectory failed to fulfill the property at any coordination or execution point.\n"nr": The trajectory is not relevant to the property. \n\n# For each property, follow these steps:\nSTEP 1: Repeat the property, word for word, without making any changes.\nSTEP 2: Identify which agents and coordination points are relevant to this property.\nSTEP 3: Trace the multi-agent execution path and evaluate fulfillment across all relevant agents.\nSTEP 4: Review the end-to-end trajectory against the property requirements.\nSTEP 5: Output the final verdict for the complete multi-agent system.\nProperty: [[Repeat the property from STEP 1.]]\nRationale: [[Explain reasoning considering multi-agent coordination and end-to-end execution.]]\nVerdict: [[yes|no|nr]]\n\n# Output format:\nRespond with valid JSON in this exact format:\n{\n  "evaluations": [\n    {\n      "property": "The exact property text being evaluated",\n      "rationale": "Your detailed reasoning for the verdict",\n      "verdict": "yes/no/nr"\n    }\n  ]\n}\n\n'

USER_V1 = '\n# Available tools, user input, multi-agent trajectory and properties:\n<available_tools>\n{tool_declarations}\n</available_tools>\n\n<agent_name>\n{agent_name}\n</agent_name>\n\n<user_prompt>\n{user_input}\n</user_prompt>\n\n<multi_agent_trajectory>\n{tool_usage}\n</multi_agent_trajectory>\n\n<previous_turns>\n{conversation_history}\n</previous_turns>\n\n<agent_response>\n{agent_response}\n</agent_response>\n\n<properties>\n{rubrics}\n</properties>\n\nIMPORTANT: Evaluate the complete end-to-end multi-agent trajectory. Consider agent coordination, delegation patterns, and overall system behavior when assessing each property.\nFor the intermediate evaluation process, make sure for each of the property listed, follow the example steps and output "Property: ..." on a new line and "Verdict: ..." on another new line. Assemble your final evaluation result in json format.\n'

V0 = PromptTemplate(
    metric_id="agent.multi_agent_trajectory",
    version="v0",
    system=SYSTEM_V0,
    parts={"invocation": USER_V0},
    output_schema=RUBRIC_SCHEMA,
    labels=None,
    source="agentsight:trajectory_accuracy.e2e_v0",
)

V1 = PromptTemplate(
    metric_id="agent.multi_agent_trajectory",
    version="v1",
    system=SYSTEM_V1,
    parts={"invocation": USER_V1},
    output_schema=RUBRIC_SCHEMA,
    labels=None,
    source="agentsight:trajectory_accuracy.e2e_v1",
)

VERSIONS = {"v0": V0, "v1": V1}
DEFAULT_VERSION = "v1"
