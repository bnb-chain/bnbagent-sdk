"""Contract inputs for custom.human_review: pre-written signed results in a temp file sink."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bnbagent_eval._optional import available
from bnbagent_eval.metrics.custom.human_review import HumanReview, sign_human_score
from bnbagent_eval.types import EvalCase

from .catalog import ContractCase

# Publicly known Anvil/Hardhat dev key #0 (not a secret).
DEV_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # secret-scan: allow
)
_DIR = Path(tempfile.mkdtemp(prefix="bnbagent-eval-human-"))


def _best() -> EvalCase:
    return EvalCase(id="human-best", output="great answer")


def _worst() -> EvalCase:
    return EvalCase(id="human-worst", output="bad answer")


if available("eth_account"):
    from bnbagent_eval.chain.attestation import LocalKeySigner

    _signer = LocalKeySigner(DEV_KEY)
    _params = {"sink": "file", "path": str(_DIR), "reviewers": [_signer.address]}
    for case, score in ((_best(), 1.0), (_worst(), 0.0)):
        task_id = HumanReview().task(case, _params)["task_id"]
        out = _DIR / "results" / f"{task_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(sign_human_score(task_id, score, _signer)))
    CONTRACT = {"custom.human_review": ContractCase(best=_best, worst=_worst, params=_params)}
else:
    CONTRACT = {
        "custom.human_review": ContractCase(
            best=_best,
            worst=_worst,
            params={"reviewers": ["0x0"]},
            skip="custom.human_review contract needs the [evm] extra",
        )
    }
