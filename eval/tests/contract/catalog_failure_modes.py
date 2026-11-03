"""Contract inputs for conversation.failure_modes."""

from __future__ import annotations

from bnbagent_eval.types.simulation import FailureMode

from .catalog import ContractCase
from .catalog_conversation import conv

CONTRACT = {
    "conversation.failure_modes": ContractCase(
        best=conv,
        worst=conv,
        judge_best={
            "exhibited_stress_axes": ["ambiguity"],
            "exhibited_failure_modes": [],
            "summary": "clean",
        },
        judge_worst={
            "exhibited_stress_axes": [],
            "exhibited_failure_modes": [f.value for f in FailureMode],
            "summary": "every failure mode",
        },
    ),
}
