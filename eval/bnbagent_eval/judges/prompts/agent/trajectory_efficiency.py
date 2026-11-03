"""agent.trajectory_efficiency prompt templates.

v1: ported verbatim from AgentSight ``trajectory_efficiency.py`` V1 (the evaluator's default, JSON
output). The text-only V0 prompt is not ported.
"""

from __future__ import annotations

from .. import PromptTemplate
from ._whitebox import steps_schema

SYSTEM_V1 = '\nYou are an expert evaluator of AI agent reasoning. Your task is to simulate the agent\'s step-by-step thinking process to determine if every piece of evidence was a necessary building block.\nYour task is to analyze the agent\'s reasoning path from the agent\'s perspective, **without using hindsight**. Evaluate if each piece of evidence was a logical and necessary building block to get to the next step in solving the query.\n\n### Rules for Evaluation:\nA piece of evidence is **EFFICIENT** if it provides any of the following "valuable information":\n- **A. Contextual Information**: Helps identify what an object is or its purpose (e.g., "This image is a menu," "This is a receipt"). This is crucial for planning subsequent steps.\n- **B. Linking Information**: Connects two different pieces of evidence (e.g., The brand name \'Magna\' found on a bottle links the bottle to the \'Magna\' item on a menu\'s price list).\n- **C. Calculation Data**: Provides a direct value needed for the final answer (e.g., count is \'2\', price is \'6\').\n\nAn evidence is **INEFFICIENT** ONLY IF it is completely irrelevant to A, B, and C (e.g., getting the weather forecast to calculate a price).\n\nYou must respond with a JSON object containing:\n- "analysis": Your detailed step-by-step analysis of each evidence piece\n- "inefficient_steps": Array of step indices that were inefficient (e.g., [1, 3])\n- "efficient_steps": Array of step indices that were efficient (e.g., [0, 2, 4])\n\n### Example 1 (This entire path is EFFICIENT)\nInput: Query: "What\'s the total price of the \'Coke\' cans in the fridge, based on the receipt?"\nEvidence: [0] ImageDescription(image1): "A kitchen fridge." [1] ImageDescription(image2): "A paper receipt." [2] CountGivenObject(image1): "2" [3] OCR(image1): "Text on can says \'Coac-Cloa\'" [4] OCR(image2): "...Coke....$1.50..."\nOutput: {\n  "analysis": "Evidence [0] and [1] were essential to distinguish the \'fridge\' from the \'receipt\'. This is Contextual Information (A) needed to know where to count objects and where to find prices. Evidence [2] provided the count \'2\'. This is Calculation Data (C). Evidence [3] identified the brand on the can. This is critical Linking Information (B) to find the correct item on the receipt. Evidence [4] provided the price for the specific brand identified. This is Calculation Data (C). Every step was a necessary building block in the investigation.",\n  "inefficient_steps": [],\n  "efficient_steps": [0, 1, 2, 3, 4]\n}\n\n### Example 2 (Contains INEFFICIENT steps)\nInput: Query: "How many apples are in the basket?"\nEvidence: [0] ImageDescription(image1): "A basket with fruits." [1] WeatherForecast(): "Sunny, 75°F" [2] CountGivenObject(image1): "3 apples"\nOutput: {\n  "analysis": "Evidence [0] provided Contextual Information (A) to identify the basket. Evidence [1] about weather is completely irrelevant to counting apples - it provides no contextual, linking, or calculation data needed for this task. Evidence [2] provided the direct count needed for the answer, which is Calculation Data (C).",\n  "inefficient_steps": [1],\n  "efficient_steps": [0, 2]\n}\n'

USER_V1 = "\n### Your Task\nEvaluate the following case using the same process and respond with a JSON object as specified in the system prompt.\n\n**Original Query:**\n{query}\n\n** Input Files **\n{files}\n\n**Collected Evidence:**\n{evidence_with_indices}\n\n**Final Answer:**\n{final_answer}\n"

V1 = PromptTemplate(
    metric_id="agent.trajectory_efficiency",
    version="v1",
    system=SYSTEM_V1,
    parts={"invocation": USER_V1},
    output_schema=steps_schema("inefficient_steps", "efficient_steps"),
    labels=None,
    source="agentsight:trajectory_efficiency_v1",
)

VERSIONS = {"v1": V1}
DEFAULT_VERSION = "v1"
