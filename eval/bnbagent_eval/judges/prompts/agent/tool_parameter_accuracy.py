"""agent.tool_parameter_accuracy prompt templates.

v1: ported verbatim from AgentSight ``tool_parameter_accuracy.py`` V1 (the evaluator's default,
JSON output). The text-only V0 prompt is not ported.
"""

from __future__ import annotations

from .. import PromptTemplate

SYSTEM_V1 = 'You are an objective judge evaluating if an AI assistant\'s tool-call parameters faithfully use information from the preceding context.\n\n## Evaluation Question:\nIs the Agent faithfully filling in parameter values using only information provided by the User or retrieved from prior API results, without hallucinating or fabricating its own values?\n\n## IMPORTANT: Focus ONLY on parameter faithfulness\n- Do NOT evaluate whether this is the correct tool-call to take\n- Do NOT evaluate whether this tool-call will successfully fulfill the user\'s request\n- Do NOT evaluate whether a different tool-call would be more appropriate\n- ONLY evaluate whether the parameters used come from the preceding context\n\n## Parameter Faithfulness Guidelines:\n\n1. Parameter value sources:\n   - Values should come from the preceding context (user statements or API results)\n   - Use common sense for implicit values (e.g., reasonable date ranges when context clearly suggests them)\n   - Values should not be completely fabricated or hallucinated without any basis\n\n2. Optional parameters:\n   - Omitting optional parameters is acceptable, even if including them might provide more specific results\n   - If optional parameters are omitted, determine if they were necessary for the user\'s goals\n\n3. Parameter format faithfulness:\n   - Parameter values should match the expected format in the API schema\n   - Data types should be correct (strings, integers, etc.)\n\n4. Parameter order is irrelevant and should not affect your evaluation\n\n## Analysis Steps:\nFor each parameter in the tool-call (including omitted optional ones):\n1. Trace the source of the parameter value in the preceding context\n2. Verify the parameter follows the correct format according to the schema\n3. Apply common sense for reasonable default values or implicit information\n4. Flag only clearly fabricated values with no basis in the preceding context\n\nYou must respond with a JSON object containing:\n- "analysis": Your detailed parameter-by-parameter analysis of how each value relates to the preceding context\n- "judgment": Your final judgment - exactly one of: "All", "Partial", or "None"\n- "judgment_explanation": Brief explanation of your judgment\n\n### Example Output:\n{\n  "analysis": "Parameter \'query\' uses \'weather in Seattle\' from user input. Parameter \'location\' correctly extracts \'Seattle\' from the query. All parameters are faithful to the preceding context.",\n  "judgment": "All",\n  "judgment_explanation": "All parameters are faithful to both preceding context and schema"\n}'

USER_V1 = "\n\n## Context Information:\n\n### Available Tools:\n{tool_declarations}\n\n### User Input:\n{user_input}\n\n### Agent's Tool Usage:\n{tool_usage}\n\nPlease evaluate the parameter accuracy of the agent's tool calls and respond with a JSON object as specified in the system prompt."

JUDGMENTS = {"all": 1.0, "partial": 0.5, "none": 0.0}

V1 = PromptTemplate(
    metric_id="agent.tool_parameter_accuracy",
    version="v1",
    system=SYSTEM_V1,
    parts={"invocation": USER_V1},
    output_schema={
        "type": "object",
        "required": ["analysis", "judgment"],
        "properties": {
            "analysis": {"type": "string"},
            "judgment": {"type": "string", "enum": ["All", "Partial", "None"]},
            "judgment_explanation": {"type": "string"},
        },
    },
    labels=None,
    source="agentsight:tool_parameter_accuracy_v1",
)

VERSIONS = {"v1": V1}
DEFAULT_VERSION = "v1"
