"""analysis.diagnosis prompt templates: two-stage failure diagnosis.

v1: ported from AgentSight ``evaluators/diagnoser`` (``prompt_templates/root_cause`` v0 and
``prompt_templates/recommendation`` v0, the ``ROOT_CAUSE_CATEGORIES`` and
``REMEDIATION_TARGET_DESCRIPTIONS`` tables, and the ``RootCauseAnalysis._build_prompt`` /
``Recommender._build_prompt`` user prompts). Changes: untrusted case content is fenced, the
output is a JSON object validated against ``output_schema`` (instead of a pydantic response
model), and stage 1 may return an empty ``root_causes`` list (the source's "return None").
"""

from __future__ import annotations

from .. import PromptTemplate

# AgentSight RootCauseCategory -> description (verbatim, typos fixed).
ROOT_CAUSE_CATEGORIES: dict[str, str] = {
    "prompt_engineering": (
        "Issues with the agent's system prompt or instructions that cause incorrect behavior. "
        "Some examples are: missing constraints, ambiguous instructions, missing persona, "
        "incomplete task definition, missing format specification, overly restrictive "
        "instructions."
    ),
    "tool_usage": (
        "Issues with how the agent selects and invokes tools. Some examples are: wrong tool "
        "selected, tool not invoked, malformed parameters, missing tool, tool error unhandled, "
        "tool misuse, excessive tool calls."
    ),
    "tool_output_quality": (
        "Issues with the data/information returned by tools, including RAG retrieval, APIs, and "
        "databases. Some examples are: irrelevant results, missing information, stale data, "
        "incomplete results, incorrect data, poor ranking, context overflow, poor chunk quality, "
        "tool errors, data format mismatch."
    ),
    "reasoning_planning": (
        "Issues with agent reasoning, logic, and task planning. Agent makes mistakes in "
        "understanding the task, creates erroneous plans, or executes the plan incorrectly."
    ),
    "model_capability": (
        "Issues where the model fundamentally cannot perform the required task. Some examples "
        "are: task too complex, instruction following failure, format compliance failure, "
        "hallucination. This is applicable when agent is provided with clear and explicit "
        "instructions but still fails to perform the task."
    ),
    "agent_coordination": (
        "Issues with multi-agent coordination, handoffs, and communication. Some examples are: "
        "wrong agent selected, premature handoff, delayed handoff, context loss on handoff, "
        "circular delegation, conflicting outputs, role confusion, missing orchestration."
    ),
    "other": (
        "Other issues that do not fit into the categories above. Some examples are: rate "
        "limiting, timeout, unknown root cause."
    ),
}

# AgentSight RemediationTarget -> description (verbatim, typos fixed).
REMEDIATION_TARGETS: dict[str, str] = {
    "system_prompt": (
        "Modify the agent's system prompt or instructions, if not already clear. Examples: Add "
        "explicit constraints ('Always respond in JSON format'), clarify ambiguous instructions, "
        "add few-shot examples, specify tone/persona, or add step-by-step reasoning instructions."
    ),
    "tool_definitions": (
        "Update tool schemas, descriptions, or parameters. Examples: Improve tool descriptions to "
        "clarify when to use each tool, add parameter validation, fix parameter types or "
        "constraints, add missing required parameters."
    ),
    "tool_response": (
        "Fix tool so that it is working without errors. Fix tool implementation errors or bugs. "
        "Update tool response so that it provides more relevant information for the agent, "
        "update tool so that it provides response in the correct format, make sure tool is "
        "working and returning the correct AND RELEVANT data."
    ),
    "model_selection": (
        "Change the underlying LLM model. Examples: Switch to a more capable model for complex "
        "reasoning, use a faster model for simple tasks, choose a model with better instruction "
        "following, or select a model with larger context window. Useful when the model is "
        "fundamentally unable to perform the required task even with explicit and clear "
        "instructions. Prompt engineering cannot solve all issues, if prompts are already "
        "reasonably clear and detailed."
    ),
    "agent_orchestration": (
        "Modify agent workflow and orchestration logic. Examples: break complex tasks into "
        "sub-agents, or add human-in-the-loop checkpoints. Create new agents to handle specific "
        "tasks like sub-agents etc., create intermediate feedback, reflection agent, evaluation "
        "agent etc."
    ),
    "new_modules": (
        "Update the agent configuration with new modules such as new tools, new agents, "
        "reflection agents, evaluation agents etc. that can improve the agent's performance and "
        "reliability."
    ),
    "other": (
        "Other remediation targets not covered by the above categories. Examples: Infrastructure "
        "changes, external API configurations, or custom application-specific modifications."
    ),
}

SEVERITIES = ("critical", "high", "medium", "low")


def _categories_block() -> str:
    return "\n".join(
        f"<category>\n{name}\n<description>{desc}</description>\n</category>"
        for name, desc in ROOT_CAUSE_CATEGORIES.items()
    )


def _targets_block() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in REMEDIATION_TARGETS.items())


ROOT_CAUSE_SYSTEM_V1 = f"""You are an expert AI agent debugger specializing in root cause analysis.

Your task is to analyze a failed agent evaluation and identify the ROOT CAUSE(S) of the failure.
Focus ONLY on diagnosis - do not suggest fixes yet. This root cause will then be used to generate recommendations in a separate stage.

<categories>
{_categories_block()}
</categories>

You will be provided with:
1. The agent definitions for the agents in the session (when available)
2. The agent trajectory or conversation for the session (when available)
3. The task input and the agent's output
4. The failed evaluation result(s)

Instructions:
1. Analyze the failed evaluation and agent trajectory carefully
2. Identify the PRIMARY root causes - the main reasons the evaluation failed. Use the categories above to help you identify the root cause.
3. Provide SPECIFIC EVIDENCE from the trajectory that supports your diagnosis
4. Be precise - use the exact category values from the list above
5. Assign confidence based on how clear the evidence is (0.0-1.0)
6. Assign severity based on impact:
   - critical: blocks core functionality
   - high: significant impact
   - medium: noticeable degradation
   - low: minor issue
7. If the failure is because of the user not providing information, then it is not the fault of the agents.
8. If there is no obvious root cause or the agent did not fail to complete the task, return an empty "root_causes" list. Do not make up a root cause.

Focus on accuracy over completeness. It's better to identify one root cause with high confidence than multiple with low confidence.

## Output Format

Return only a JSON object: {{"thinking": "<3-4 sentences>", "root_causes": [{{"category": "<category>", "description": "<1-2 sentences>", "evidence": ["<specific example>", ...], "confidence": <0.0-1.0>, "severity": "<critical|high|medium|low>"}}]}}."""

RECOMMENDATION_SYSTEM_V1 = f"""You are an expert AI agent developer specializing in fixing agent issues.

You will be given:
1. A diagnosed root cause (or causes) from a failed agent evaluation
2. The agent's trajectory showing what happened
3. Optionally, the agent's definition (system prompt, tools, etc.)

Your task is to generate SPECIFIC, ACTIONABLE recommendations to fix the identified issues.
Where appropriate, provide SPECIFIC EXAMPLES on HOW to implement the fix. Do not provide general instructions.
The recommendations can include one or more of the following remediation targets:

<targets>
{_targets_block()}
</targets>

These recommendations will be directly used by developers to improve their agents. So this advice should consider all aspects of agent design, best practices etc.

Instructions:
1. Generate recommendations to fix the root causes of errors in the agent.
2. Be SPECIFIC - don't say "improve the prompt", say exactly what to add/change
3. Provide SPECIFIC EXAMPLES when possible - show the actual text/code to add rather than general instructions.
4. Prioritize recommendations by impact and ease of implementation
5. If agent definition is not provided, note that more specific recommendations would be possible with it
6. If most root causes are similar and can be fixed with the same recommendation, then provide a single recommendation for all of them.
7. Do NOT provide REDUNDANT recommendations.
8. Don't over index on prompt engineering. If the instructions are already clear but the agent is not performing well, then maybe changing the underlying LLM model might be a better fix.
9. Focus on quality of the recommendations rather than quantity.
10. Provide 1-2 most important recommendations.

## Output Format

Return only a JSON object: {{"thinking": "<your step-by-step analysis>", "recommendations": [{{"target": "<target>", "action": "<specific action>", "priority": "<critical|high|medium|low>"}}]}}."""

ROOT_CAUSE_SCHEMA = {
    "type": "object",
    "required": ["thinking", "root_causes"],
    "properties": {
        "thinking": {"type": "string"},
        "root_causes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["category", "description", "confidence", "severity"],
                "properties": {
                    "category": {"type": "string", "enum": list(ROOT_CAUSE_CATEGORIES)},
                    "description": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                },
            },
        },
    },
}

RECOMMENDATION_SCHEMA = {
    "type": "object",
    "required": ["thinking", "recommendations"],
    "properties": {
        "thinking": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["target", "action", "priority"],
                "properties": {
                    "target": {"type": "string", "enum": list(REMEDIATION_TARGETS)},
                    "action": {"type": "string"},
                    "priority": {"type": "string", "enum": list(SEVERITIES)},
                },
            },
        },
    },
}

ROOT_CAUSE_V1 = PromptTemplate(
    metric_id="analysis.diagnosis.root_cause",
    version="v1",
    system=ROOT_CAUSE_SYSTEM_V1,
    parts={
        "simple": (
            "<agent_definitions>\n{agent_definitions}\n</agent_definitions>\n"
            "<trajectory>\n{trajectory}\n</trajectory>\n"
            "<task_input>\n{input}\n</task_input>\n"
            "<agent_output>\n{actual_output}\n</agent_output>\n"
            "<expected_output>\n{expected_output}\n</expected_output>\n"
            "<failed_evaluation>\n{failed_evaluation}\n</failed_evaluation>"
        ),
        "task_simple": (
            "\n<task>\nAnalyze the above failed evaluation and identify the root cause(s).\n"
            "Focus on WHAT went wrong and WHY, with specific evidence from the trajectory.\n"
            "If there is no obvious root cause or the agent did not fail to complete the task, "
            "return an empty root_causes list.\nDo not make up a root cause.\n</task>"
        ),
    },
    output_schema=ROOT_CAUSE_SCHEMA,
    source="agentsight:diagnoser/root_cause_v0",
)

RECOMMENDATION_V1 = PromptTemplate(
    metric_id="analysis.diagnosis.recommendation",
    version="v1",
    system=RECOMMENDATION_SYSTEM_V1,
    parts={
        "simple": (
            "<root_causes>\n{root_causes}\n</root_causes>\n"
            "<trajectory>\n{trajectory}\n</trajectory>\n"
            "<agent_definitions>\n{agent_definitions}\n</agent_definitions>"
        ),
        "task_simple": (
            "\n<task>\nGenerate specific, actionable recommendations to fix the identified root "
            "causes. Prioritize by impact and provide concrete examples where possible.\n"
            "If the agent definitions are empty, the agent configuration is unknown: provide "
            "more general recommendations.\n</task>"
        ),
    },
    output_schema=RECOMMENDATION_SCHEMA,
    source="agentsight:diagnoser/recommendation_v0",
)

VERSIONS = {"v1": {"root_cause": ROOT_CAUSE_V1, "recommendation": RECOMMENDATION_V1}}
