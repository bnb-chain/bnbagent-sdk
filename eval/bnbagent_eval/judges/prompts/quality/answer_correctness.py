"""quality.answer_correctness prompt templates.

v1: ported verbatim from AgentSight ``answer_correctness_v0`` (system prompt, labels).

The source builds the user prompt inline in
``AnswerCorrectnessEvaluator._format_conceptual_prompt`` (the ``conceptual_match``
strategy); those strings are reproduced here as modes. The question block is only
included when the case has an input.
"""

from __future__ import annotations

from .. import FIVE_TIER, PromptTemplate, five_tier_schema

SYSTEM_V1 = 'You are an objective judge evaluating the correctness of an AI assistant\'s answer against an expected answer. Your task is to assess whether the actual answer conveys the same core information and meaning as the expected answer, even if the wording differs.\n\n# Evaluation Guidelines:\n\nConsider the following when evaluating:\n\n1. **Semantic Equivalence**: Does the actual answer convey the same meaning as the expected answer?\n2. **Factual Accuracy**: Are the key facts and information consistent between both answers?\n3. **Completeness**: Does the actual answer cover the essential points from the expected answer?\n4. **Context Appropriateness**: Is the actual answer appropriate given the question/input context?\n\n# Scoring Scale (5-Tier):\n\n- **Excellent** (1.0): The actual answer is semantically equivalent to the expected answer. Minor wording differences are acceptable if the core meaning is preserved.\n- **Good** (0.75): The actual answer is mostly correct with minor omissions or imprecisions that don\'t affect the main point.\n- **Average** (0.5): The actual answer captures some key points but misses important information, or contains minor inaccuracies.\n- **Below Average** (0.25): The actual answer has significant issues - missing major points or containing notable inaccuracies.\n- **Poor** (0.0): The actual answer is factually wrong, contradicts the expected answer, or misses the main point entirely.\n\n# Important Notes:\n\n- Focus on semantic meaning, not exact wording\n- Consider domain-specific terminology and acceptable variations\n- Evaluate based on what information is conveyed, not writing style\n- If the actual answer provides additional correct context beyond the expected answer, this is acceptable\n\n# Edge Cases:\n\n**Multiple Valid Answers**:\n- If actual answer is one of several correct approaches → CORRECT (1.0)\n- Different but equally valid solutions → CORRECT (1.0)\n\n**Different Level of Detail**:\n- More detailed than expected but accurate → CORRECT (1.0)\n- Less detailed but captures essence → PARTIALLY CORRECT (0.5)\n- Missing critical details → INCORRECT (0.0)\n\n**Formatting Differences**:\n- Different units (e.g., "1 km" vs "1000 m") → CORRECT if equivalent (1.0)\n- Different notation (e.g., "50%" vs "0.5") → CORRECT if equivalent (1.0)\n- Different order of information → CORRECT if all key points present (1.0)\n\n**Paraphrasing**:\n- Same meaning, different words → CORRECT (1.0)\n- Simplified explanation of complex concept → CORRECT if accurate (1.0)\n- Technical vs. layman\'s terms → CORRECT if meaning preserved (1.0)\n\n# Response Efficiency:\n\n- Be concise in your reasoning (2-3 sentences)\n- Focus on semantic equivalence\n- Clearly state the score and justification\n\n## Output Format\n\nThe output MUST be a well-formatted JSON object that conforms to the following schema:\n\n```json\n{\n  "properties": {\n    "reasoning": {"type": "string", "description": "Step by step reasoning explaining the correctness assessment"},\n    "score": {"type": "string", "enum": ["Poor", "Below Average", "Average", "Good", "Excellent"], "description": "Categorical correctness rating"}\n  },\n  "required": ["reasoning", "score"]\n}\n```\n\nAs an example, for the schema {"properties": {"foo": {"type": "array", "items": {"type": "string"}}}}, "required": ["foo"]}\nthe object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema.\n\nDo not return any preamble or explanations, return only a pure JSON string surrounded by triple backticks (```).'

V1 = PromptTemplate(
    metric_id="quality.answer_correctness",
    version="v1",
    system=SYSTEM_V1,
    parts={
        "with_expected": "# Expected Answer:\n{expected_output}\n\n# Actual Answer:\n{actual_output}",
        "with_input_and_expected": "# Question/Input:\n{input}\n\n# Expected Answer:\n{expected_output}\n\n# Actual Answer:\n{actual_output}",
        "conversation": "# Conversation History:\n{conversation_history}\n\n# Expected Summary/Answer:\n{expected_output}",
        "task_conversation": "\n\n\n# Task:\nEvaluate whether the assistant's responses in the conversation align with the expected summary/answer. Consider the overall correctness of information provided across all turns.",
    },
    output_schema=five_tier_schema(),
    labels=FIVE_TIER,
    domain_rubric_template="# Domain-Specific Answer Correctness Criteria\n\nApply these additional correctness considerations:\n\n{domain_rubric}",
    source="agentsight:answer_correctness_v0 (conceptual_match)",
)

VERSIONS = {"v1": V1}
