"""BERTScore parity: our implementation (``metrics/text/bertscore.py``) vs ``bert-score``.

Runs only where ``bert-score`` is installed (it is not a dependency); downloads two small models:

    pip install 'bnbagent-eval[semantic]' bert-score
    pytest tests/parity/test_bertscore_parity.py
"""

from __future__ import annotations

import socket

import pytest

pytest.importorskip("bert_score")
pytest.importorskip("transformers")

PAIRS = [
    ("The cat sat on the mat.", "A cat was sitting on the mat."),
    ("Gas fees on BNB Smart Chain are paid in BNB.", "Fees are paid in BNB."),
    ("It sends your tokens to the spender.", "approve sets the allowance a spender may transfer"),
    ("identical text", "identical text"),
    ("", "something"),
]


@pytest.fixture(autouse=True)
def allow_network(monkeypatch):
    """Model weights download from the Hugging Face Hub once."""
    from tests.conftest import _real_connect

    monkeypatch.setattr(socket.socket, "connect", _real_connect)


@pytest.mark.parametrize("model", ["distilbert-base-uncased", "roberta-base"])
def test_matches_bert_score(model: str) -> None:
    from bert_score import BERTScorer

    from bnbagent_eval.metrics.text.bertscore import LAYERS, bertscore

    reference = BERTScorer(model_type=model, num_layers=LAYERS[model])
    for cand, ref in PAIRS:
        if not cand.strip():
            continue  # the metric scores empty text 0.0 without encoding
        p, r, f = reference.score([cand], [ref])
        ours = bertscore(cand, [ref], model)[0]
        assert ours == pytest.approx((float(p), float(r), float(f)), abs=1e-5), (cand, ref)
