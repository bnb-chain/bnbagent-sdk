"""``ops.consistency``: agreement between repeated runs of the same case.

Methods: ``exact`` (normalized string equality), ``rouge`` (ROUGE-L F), ``ted`` (structural
similarity of JSON outputs via Zhang-Shasha tree edit distance, exact labels; dependency-free),
``judge`` (an LLM judge decides
whether two outputs are semantically equivalent: yes = 1, no = 0; lower median over samples).
The score is the mean pairwise agreement over all run pairs. Attached elsewhere: ``embedding``
(rescaled cosine of the judge's embeddings, ``vendors/local_models.py``). ``sted`` (Semantic
Tree Edit Distance, our implementation in ``ops/sted.py``: the JSON structure must match while
values compare by meaning through the judge's embeddings; the score is the paper's validity-
adjusted consistency ``c_adj`` by default).
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...judges.prompts.ops import consistency as prompts
from ...judges.sampling import median_low
from ...judges.structured import extract_json
from ...types.case import EvalCase, content_text
from ...types.judge import JudgeConfig, embedding_model
from ..base import Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..judge_base import JUDGE_PARAMS, JudgeMetric, check_limits
from ..registry import register
from ..text._common import as_text
from ..text.overlap import rouge_prf
from . import sted

# --------------------------------------------------------------------------- tree edit distance


@dataclass
class Node:
    label: str
    children: list[Node] = field(default_factory=list)


def json_tree(value: Any, label: str = "$") -> Node:
    """Ordered labelled tree for a JSON value (object keys sorted, so key order is irrelevant)."""
    if isinstance(value, dict):
        return Node(label + ":{}", [json_tree(value[k], f"key={k}") for k in sorted(value)])
    if isinstance(value, list):
        return Node(label + ":[]", [json_tree(v, "item") for v in value])
    return Node(f"{label}={json.dumps(value, sort_keys=True, ensure_ascii=False)}")


def _postorder(root: Node) -> tuple[list[str], list[int], list[int]]:
    """Labels, leftmost-leaf indices and keyroots of ``root`` in post-order."""
    labels: list[str] = []
    lml: list[int] = []

    def walk(node: Node) -> int:
        first_leaf = None
        for child in node.children:
            leaf = walk(child)
            if first_leaf is None:
                first_leaf = leaf
        labels.append(node.label)
        idx = len(labels) - 1
        lml.append(idx if first_leaf is None else first_leaf)
        return lml[idx]

    walk(root)
    seen: dict[int, int] = {}
    for i, leaf in enumerate(lml):
        seen[leaf] = i  # the highest node with this leftmost leaf is a keyroot
    return labels, lml, sorted(seen.values())


def tree_edit_distance(a: Node, b: Node) -> int:
    """Zhang-Shasha ordered tree edit distance with unit insert / delete / relabel costs."""
    la, lml_a, kr_a = _postorder(a)
    lb, lml_b, kr_b = _postorder(b)
    td = [[0] * len(lb) for _ in la]
    for i in kr_a:
        for j in kr_b:
            li, lj = lml_a[i], lml_b[j]
            m, n = i - li + 2, j - lj + 2
            fd = [[0] * n for _ in range(m)]
            for x in range(1, m):
                fd[x][0] = fd[x - 1][0] + 1
            for y in range(1, n):
                fd[0][y] = fd[0][y - 1] + 1
            for x in range(1, m):
                for y in range(1, n):
                    ai, bj = li + x - 1, lj + y - 1
                    if lml_a[ai] == li and lml_b[bj] == lj:
                        fd[x][y] = min(
                            fd[x - 1][y] + 1,
                            fd[x][y - 1] + 1,
                            fd[x - 1][y - 1] + (la[ai] != lb[bj]),
                        )
                        td[ai][bj] = fd[x][y]
                    else:
                        p, q = lml_a[ai] - li, lml_b[bj] - lj
                        fd[x][y] = min(fd[x - 1][y] + 1, fd[x][y - 1] + 1, fd[p][q] + td[ai][bj])
    return td[-1][-1]


def _size(n: Node) -> int:
    return 1 + sum(_size(c) for c in n.children)


def structural_similarity(a: Any, b: Any) -> float:
    ta, tb = json_tree(a), json_tree(b)
    return 1.0 - tree_edit_distance(ta, tb) / max(_size(ta), _size(tb))


def _as_json(value: Any) -> Any:
    """A run output as JSON: the whole text or a fenced block (no scanning inside prose, so
    "See [1]" stays text), else the text itself."""
    if isinstance(value, (dict, list)):
        return value
    try:
        return extract_json(as_text(value), scan=False)
    except ValueError:
        return as_text(value)


# --------------------------------------------------------------------------- metric

_METHODS: dict[str, Callable[[Any, Any], float]] = {
    "exact": lambda a, b: float(" ".join(as_text(a).split()) == " ".join(as_text(b).split())),
    "rouge": lambda a, b: rouge_prf(as_text(a), as_text(b), "rougeL", use_stemmer=True)[2],
    "ted": lambda a, b: structural_similarity(_as_json(a), _as_json(b)),
}


_JUDGE_PARAMS = {k: v for k, v in JUDGE_PARAMS.items() if k != "input_mode"}
STED_SCORES = ("c_adj", "c_mean", "consistency_coefficient", "stability_score", "ranking_score")
_STED_PARAMS = {  # the ``sted`` method's options (ops/sted.py)
    "score": {"enum": list(STED_SCORES)},
    "structural_weight": {"type": "number", "minimum": 0, "maximum": 1},
    "exact_match_fields": {"type": "array", "items": {"type": "string"}},
    "order_sensitive_fields": {"type": "array", "items": {"type": "string"}},
    "exact_match_all_keys": {"type": "boolean"},
}


@register
class Consistency(JudgeMetric):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="ops.consistency",
        category="ops",
        description="Mean pairwise agreement of outputs across repeated runs of one case.",
        methods=tuple(
            MethodSpec(name, Kind.RULE, required_inputs=frozenset({"runs"})) for name in _METHODS
        )
        + (
            MethodSpec("judge", Kind.JUDGE, required_inputs=frozenset({"runs"})),
            # STED needs embeddings, which come from the judge's provider.
            MethodSpec("sted", Kind.JUDGE, required_inputs=frozenset({"runs"})),
        ),
        # Judge params apply to the ``judge`` method, STED params to ``sted``.
        params_schema={
            "type": "object",
            "properties": {**_JUDGE_PARAMS, **_STED_PARAMS},
            "additionalProperties": False,
        },
        default_threshold=0.9,
        # v2: ``ted`` is plain tree edit distance, ``sted`` Semantic Tree Edit Distance
        # (ops/sted.py).
        # v3: ``ted`` / ``sted`` parse a run as JSON from the whole text or a fenced block
        # (``ted`` ignores "[1]" inside prose; ``sted`` accepts ```json fences).
        version="3",
        sources=(
            "bnbagent-eval:new",
            "paper:STED and consistency scoring (Wang et al., arXiv 2512.23712)",
        ),
    )

    @staticmethod
    def _outputs(ctx: MetricContext) -> list[Any]:
        outputs = [r.output for r in ctx.runs if r.output is not None]
        if len(outputs) < 2:
            raise ValueError("at least two runs with output are required")
        return outputs

    def _pairwise(self, ctx: MetricContext, sim: Callable[[Any, Any], float]) -> Outcome:
        outputs = self._outputs(ctx)
        scores = [sim(a, b) for a, b in itertools.combinations(outputs, 2)]
        return Outcome(
            score=sum(scores) / len(scores),
            details={"runs": len(outputs), "pairs": len(scores), "min_pair": min(scores)},
        )

    def _evaluate_exact(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        return self._pairwise(ctx, _METHODS["exact"])

    def _evaluate_rouge(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        return self._pairwise(ctx, _METHODS["rouge"])

    def _evaluate_ted(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        return self._pairwise(ctx, _METHODS["ted"])

    def prompt_hash(self, method: MethodSpec, params=None) -> str | None:
        if method.name == "sted":  # embeddings, no prompt
            return None
        return super().prompt_hash(method, params)

    async def _evaluate_sted(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        if len(ctx.runs) < 2:
            raise ValueError("at least two runs are required")
        objects = [sted.as_json_object(r.output) for r in ctx.runs]
        valid = [o for o in objects if sted.is_valid(o)]
        validity = len(valid) / len(objects)
        p = ctx.params
        texts = sted.strings_to_embed(valid) if len(valid) >= 2 else []
        vectors: dict[str, list[float]] = {}
        if texts:
            judge = ctx.judge
            if judge is None:
                raise ValueError("method 'sted' needs a judge with an embeddings endpoint")
            check_limits(ctx, what="embed call")
            try:
                embedded = await judge.embed([sted.prepare_text(t) for t in texts])
            except NotImplementedError as exc:
                provider = getattr(getattr(judge, "config", None), "provider", None)
                raise ValueError(f"judge provider {provider!r} cannot embed: {exc}") from exc
            if len(embedded) != len(texts):
                raise ValueError(f"embed returned {len(embedded)} vectors for {len(texts)} texts")
            vectors = dict(zip(texts, embedded, strict=True))
        scorer = sted.Sted(
            sted.EmbeddingSimilarity(vectors),
            structural_weight=float(p.get("structural_weight", 0.5)),
            exact_match_fields=p.get("exact_match_fields", ()),
            order_sensitive_fields=p.get("order_sensitive_fields", ()),
            exact_match_all_keys=bool(p.get("exact_match_all_keys", False)),
        )

        def pairwise() -> list[float]:
            return [scorer.similarity(a, b) for a, b in itertools.combinations(valid, 2)]

        similarities = await asyncio.to_thread(pairwise)
        scores = sted.consistency(similarities, validity)
        field = p.get("score", "c_adj")
        details = {
            "runs": len(objects),
            "valid_runs": len(valid),
            "score_field": field,
            **{k: round(v, 6) for k, v in scores.items()},
            "pair_similarities": [round(x, 6) for x in similarities],
        }
        cfg = getattr(ctx.judge, "config", None)
        judge_info: dict[str, Any] = {}
        if isinstance(cfg, JudgeConfig) and vectors:
            judge_info = {"provider": cfg.provider, "embedding_model": embedding_model(cfg)}
        reason = None if len(valid) >= 2 else "fewer than two valid (non-empty) runs"
        return Outcome(
            score=scores[field],
            reason=reason,
            details=details,
            provenance={"judge": judge_info} if judge_info else {},
        )

    async def _evaluate_judge(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        outputs = [content_text(o) or as_text(o) for o in self._outputs(ctx)]
        template = self.template_for(ctx.params)
        request = content_text(case.input) or as_text(case.input)
        pairs, provenance = [], {}
        for i, j in itertools.combinations(range(len(outputs)), 2):
            fields = {"input": request, "output_a": outputs[i], "output_b": outputs[j]}
            samples, prov = await self.ask(case, ctx, template, "simple", fields, tag=f":{i}-{j}")
            provenance = provenance or prov
            if prov["judge_source"] != provenance["judge_source"]:
                provenance = {**provenance, "judge_source": "mixed"}
            provenance["truncated"] = provenance["truncated"] or prov["truncated"]
            scores = [self.score_parsed(s.parsed, template) for s in samples]
            score = median_low(scores)
            chosen = samples[scores.index(score)].parsed
            pairs.append({"a": i, "b": j, "score": score, "reasoning": chosen.get("reasoning")})
        scores = [p["score"] for p in pairs]
        return Outcome(
            score=sum(scores) / len(scores),
            details={
                "runs": len(outputs),
                "pairs": len(scores),
                "min_pair": min(scores),
                "pair_verdicts": pairs,
            },
            provenance=provenance,
        )
