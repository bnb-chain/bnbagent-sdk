"""``text.bertscore``: BERTScore F1 of output vs reference (Zhang et al., ICLR 2020).

Our implementation (no ``bert-score`` package): each text is encoded with a pretrained
transformer (``transformers`` + ``torch``, the ``[semantic]`` extra); the hidden states of layer
``num_layers`` are the token embeddings (L2-normalised). Every candidate token is matched to its
most similar reference token (cosine) and vice versa:

    P = mean over candidate tokens of max cosine, R = mean over reference tokens of max cosine,
    F1 = 2 P R / (P + R)

Special tokens ([CLS] / [SEP] or <s> / </s>) take part in the matching with weight 0, as in the
authors' code; no IDF weighting and no baseline rescaling (their defaults).
``tests/parity/test_bertscore_parity.py`` checks the result against ``bert-score``.

Model: param ``model`` (default ``microsoft/deberta-xlarge-mnli``, AgentSight's choice; any
Hugging Face encoder works), layer: param ``num_layers`` (default: the authors' recommended
layer for known models, see ``LAYERS``). Weights download on first use and are cached per
process. Empty output or reference scores 0.0. With several references the best F1 counts.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from ..._optional import require
from ...types.case import EvalCase
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register
from ._common import output_text, references

DEFAULT_MODEL = "microsoft/deberta-xlarge-mnli"  # AgentSight calculate_bertscore default
# The authors' recommended layer per model (best correlation with human judgments, WMT16).
LAYERS: dict[str, int] = {
    "bert-base-uncased": 9,
    "bert-large-uncased": 18,
    "bert-base-multilingual-cased": 9,
    "distilbert-base-uncased": 5,
    "roberta-base": 10,
    "roberta-large": 17,
    "xlm-roberta-large": 17,
    "microsoft/deberta-base": 9,
    "microsoft/deberta-large": 16,
    "microsoft/deberta-large-mnli": 18,
    "microsoft/deberta-xlarge-mnli": 40,
}

_encoders: dict[tuple[str, int], Any] = {}
_lock = threading.Lock()


class Encoder:
    """Token embeddings of one layer of a Hugging Face model (loaded lazily, CPU or GPU)."""

    def __init__(self, model: str, num_layers: int):
        transformers = require("transformers", "semantic")
        self.torch = require("torch", "semantic")
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(model, use_fast=False)
        self.model = transformers.AutoModel.from_pretrained(model).eval()
        self.layer = num_layers
        self.special = {
            t for t in (self.tokenizer.cls_token_id, self.tokenizer.sep_token_id) if t is not None
        }
        name = type(self.tokenizer).__name__
        self.prefix_space = name.startswith(("Roberta", "GPT2"))  # byte-level BPE tokenizers

    def ids(self, text: str) -> list[int]:
        text = text.strip()
        if not text:
            return self.tokenizer.build_inputs_with_special_tokens([])
        kwargs = {"add_prefix_space": True} if self.prefix_space else {}
        return self.tokenizer.encode(
            text,
            add_special_tokens=True,
            max_length=self.tokenizer.model_max_length,
            truncation=True,
            **kwargs,
        )

    def embed(self, text: str) -> tuple[Any, list[float]]:
        """``(normalised token embeddings [n, d], token weights [n])``."""
        ids = self.ids(text)
        with self.torch.no_grad():
            out = self.model(self.torch.tensor([ids]), output_hidden_states=True)
        states = out.hidden_states[self.layer][0]
        states = states / states.norm(dim=-1, keepdim=True)
        return states, [0.0 if t in self.special else 1.0 for t in ids]

    def score(self, candidate: str, reference: str) -> tuple[float, float, float]:
        cand, w_cand = self.embed(candidate)
        ref, w_ref = self.embed(reference)
        return greedy_match((cand @ ref.T).tolist(), w_cand, w_ref)


def greedy_match(
    sim: list[list[float]], w_cand: list[float], w_ref: list[float]
) -> tuple[float, float, float]:
    """``(P, R, F1)`` from token cosines ``sim[i][j]`` (candidate i, reference j) and weights."""
    best_for_cand = [max(row) for row in sim]
    best_for_ref = [max(col) for col in zip(*sim, strict=True)]
    p = sum(s * w for s, w in zip(best_for_cand, w_cand, strict=True)) / max(sum(w_cand), 1e-12)
    r = sum(s * w for s, w in zip(best_for_ref, w_ref, strict=True)) / max(sum(w_ref), 1e-12)
    f = 2 * p * r / (p + r) if p + r > 0 else 0.0
    return float(p), float(r), float(f)


def encoder(model: str, num_layers: int | None = None) -> Encoder:
    """A cached :class:`Encoder`; ``num_layers`` defaults to ``LAYERS[model]``."""
    layer = num_layers if num_layers is not None else LAYERS.get(model)
    if layer is None:
        raise ValueError(f"no default layer for {model!r}: set params.num_layers")
    with _lock:
        if (model, layer) not in _encoders:
            _encoders[(model, layer)] = Encoder(model, layer)
        return _encoders[(model, layer)]


def bertscore(
    candidate: str, refs: list[str], model: str, num_layers: int | None = None
) -> list[tuple[float, float, float]]:
    """``(precision, recall, f1)`` of ``candidate`` against each reference."""
    enc = encoder(model, num_layers)
    return [enc.score(candidate, ref) for ref in refs]


@register
class BERTScore(BaseMetric):
    spec = MetricSpec(
        id="text.bertscore",
        category="text",
        description="BERTScore F1 of output and reference (contextual token embeddings).",
        methods=(
            MethodSpec(
                "model",
                Kind.MODEL,
                required_inputs=frozenset({"output", "expected"}),
                extra="semantic",
                extra_module="transformers",
            ),
        ),
        params_schema={
            "type": "object",
            "properties": {
                "model": {"type": "string", "minLength": 1, "maxLength": 200},
                "num_layers": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        default_threshold=0.8,
        sources=(
            "agentsight:calculate_bertscore",
            "agentsight:AnswerCorrectness[semantic]",
            "fmeval:bertscore",
        ),
    )

    async def _evaluate_model(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        model = str(ctx.params.get("model", DEFAULT_MODEL))
        out, refs = output_text(case), references(case.expected)
        details: dict[str, Any] = {
            "model": model,
            "num_layers": ctx.params.get("num_layers") or LAYERS.get(model),
        }
        live = [i for i, r in enumerate(refs) if r.strip()]
        if not out.strip() or not live:
            return Outcome(score=0.0, reason="empty output or reference", details=details)
        layers = ctx.params.get("num_layers")
        prf = await asyncio.to_thread(bertscore, out, [refs[i] for i in live], model, layers)
        best = max(range(len(prf)), key=lambda i: prf[i][2])
        p, r, f = prf[best]
        details.update(precision=p, recall=r)
        if len(refs) > 1:
            details["best_reference"] = live[best]
        return Outcome(score=f, details=details)
