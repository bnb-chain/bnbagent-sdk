"""STED: Semantic Tree Edit Distance and consistency scoring for structured outputs.

Our implementation (pure Python, no third-party code) of Wang et al., "STED and Consistency
Scoring: A Framework for Evaluating LLM Structured Output Reliability" (arXiv 2512.23712).

**Similarity of two JSON values** (``Sted.similarity``, in [0, 1]):

- Each value becomes a tree (a JSON object is wrapped as ``{"root": value}``): objects have one
  child per key (keys sorted), arrays one child per element (elements sorted by their text so
  order does not matter, except under ``order_sensitive_fields``), scalars are leaves. A node's
  path is ``root.key[0].sub``; its depth is the number of ``.`` and ``[``.
- Insert / delete cost of a node: ``path_weight_decay ** depth`` (≤ 1).
- Update cost of two leaves (Eq. 1): ``w · γ_struct + (1 − w) · γ_content`` with
  ``w = structural_weight``:
  - ``γ_struct = 1 − (key similarity + [same path pattern]) / 2``, where the key similarity is the
    semantic similarity of the key names (exact equality with ``exact_match_all_keys``);
  - ``γ_content = 1 − value similarity``: strings by semantic similarity, numbers / booleans /
    null by equality, a type change by ``max(0, similarity(text) − type_change_cost)``; fields in
    ``exact_match_fields`` compare exactly.
  Both are scaled by the mean path weight of the two nodes.
- Two subtrees (structure-guided matching): children are paired by the Hungarian algorithm on the
  structural costs (unpaired children cost a delete / insert); the content cost is then taken
  over that pairing. The subtree cost is ``(w · Σ structural + (1 − w) · Σ content) / n`` with
  ``n = max(#children)``, capped at 1. Order-sensitive arrays are compared position by position.
- ``similarity = 1 − cost(T1, T2)``.

**Semantic similarity of two strings**: equal → 1; shorter than ``min_length_for_embeddings``
(4) → positional character agreement; otherwise the cosine of their embeddings rescaled to
[0, 1] (``(cos + 1) / 2``). Strings are embedded after ``prepare_text`` (camelCase split,
separators to spaces, lowercase). Embeddings come from a caller-supplied ``embed(texts)``
function (the judge's embeddings endpoint in ``ops.consistency``).

**Consistency of N outputs** (``consistency``): over all pairs of valid (non-empty) outputs,
``c_mean`` (mean pairwise similarity), ``d_std`` (their standard deviation), ``r_v`` (share of
valid outputs), ``c_adj = r_v · c_mean`` (the deployment score), ``stability_score``
``= (1 / (1 + 2 · d_std / d_max)) ** 20``, ``ranking_score = r_v · c_mean · stability_score`` and
the paper's legacy ``consistency_coefficient = (c_mean · (1 − min(cv ** 1.5, 1))) ** 5`` (``cv`` =
``d_std / c_mean``).

Follows the paper's structure-guided algorithm; the authors' optimized path adds pruning
shortcuts (fixed costs for mismatched node kinds and very different child counts) that are not
part of the definition and are not reproduced here. When several child pairings are equally good
structurally, the definition does not say which to use for the content cost; our Hungarian solver
picks one deterministically (it can differ from another solver's choice in such ties; measured on
~1 pair in 1000 against the authors' code, by < 0.02). ``tests/parity/test_sted_parity.py``
checks the rest against the reference code.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...judges.structured import extract_json

TYPES = ("object", "array", "string", "number", "boolean", "null")


def _default_type_costs() -> dict[tuple[str, str], float]:
    """Cost of a value changing type (the paper's defaults)."""
    costs = {(a, b): (0.0 if a == b else 1.0) for a in TYPES for b in TYPES}
    for pair, cost in (
        (("string", "number"), 0.1),
        (("boolean", "string"), 0.1),
        (("number", "boolean"), 0.1),
        (("null", "string"), 0.1),
        (("null", "number"), 0.1),
        (("object", "array"), 0.5),
        (("object", "string"), 0.5),
        (("object", "number"), 0.5),
        (("array", "string"), 0.5),
        (("array", "number"), 1.0),
    ):
        costs[pair] = costs[pair[::-1]] = cost
    return costs


TYPE_CHANGE_COSTS = _default_type_costs()
_INDEX = re.compile(r"\[\d+\]")
_NON_ALNUM = re.compile(r"[^a-zA-Z0-9]")


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return type(value).__name__


@dataclass
class Node:
    label: str  # the full path, e.g. "root.items[0].name"
    type: str
    value: Any = None  # scalars: the value; arrays: the (sorted) elements; objects: None
    children: list[Node] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children


def build_tree(
    value: Any,
    path: str = "",
    *,
    sort_arrays: bool = True,
    order_sensitive_fields: frozenset[str] = frozenset(),
) -> Node:
    """The labelled tree of a JSON value (object keys sorted)."""
    here = path or "root"
    if isinstance(value, Mapping):
        node = Node(here, "object")
        for key in sorted(value, key=str):
            child_path = f"{here}.{key}" if here != "root" else str(key)
            node.children.append(
                build_tree(
                    value[key],
                    child_path,
                    sort_arrays=sort_arrays,
                    order_sensitive_fields=order_sensitive_fields,
                )
            )
        return node
    if isinstance(value, (list, tuple)):
        name = here.split(".")[-1].split("[")[0]
        items = list(value)
        if sort_arrays and name not in order_sensitive_fields:
            items = sorted(items, key=str)
        node = Node(here, "array", items)  # an array keeps its elements as its value
        for i, item in enumerate(items):
            node.children.append(
                build_tree(
                    item,
                    f"{here}[{i}]",
                    sort_arrays=sort_arrays,
                    order_sensitive_fields=order_sensitive_fields,
                )
            )
        return node
    return Node(here, json_type(value), value)


def hungarian(cost: Sequence[Sequence[float]]) -> list[tuple[int, int]]:
    """Minimum-cost perfect assignment of a square matrix (Kuhn-Munkres, O(n^3))."""
    n = len(cost)
    if n == 0:
        return []
    inf = float("inf")
    u, v = [0.0] * (n + 1), [0.0] * (n + 1)
    p, way = [0] * (n + 1), [0] * (n + 1)  # p[j]: row matched to column j (1-based)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [inf] * (n + 1), [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    return sorted((p[j] - 1, j - 1) for j in range(1, n + 1))


def char_similarity(a: str, b: str) -> float:
    """Positional character agreement (used for strings too short to embed)."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    distance = sum(x != y for x, y in zip(a, b, strict=False)) + abs(len(a) - len(b))
    return max(0.0, 1.0 - distance / longest)


def cosine01(x: Sequence[float], y: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(x, y, strict=False))
    nx, ny = math.sqrt(sum(a * a for a in x)), math.sqrt(sum(b * b for b in y))
    if nx == 0 or ny == 0:
        return 0.0
    return (max(-1.0, min(1.0, dot / (nx * ny))) + 1.0) / 2.0


def _key(label: str) -> str:
    return _INDEX.sub("", label.split(".")[-1])


class Sted:
    """STED similarity of JSON values, given a semantic similarity for strings."""

    def __init__(
        self,
        text_similarity: Callable[[str, str], float],
        *,
        structural_weight: float = 0.5,
        path_weight_decay: float = 1.0,
        exact_match_fields: Iterable[str] = (),
        exact_match_all_keys: bool = False,
        order_sensitive_fields: Iterable[str] = (),
        sort_arrays: bool = True,
        type_change_costs: Mapping[tuple[str, str], float] | None = None,
    ):
        if not 0.0 <= structural_weight <= 1.0:
            raise ValueError("structural_weight must be within [0, 1]")
        self.text_similarity = text_similarity
        self.w = structural_weight
        self.decay = path_weight_decay
        self.exact_fields = frozenset(exact_match_fields)
        self.exact_keys = exact_match_all_keys
        self.ordered = frozenset(order_sensitive_fields)
        self.sort_arrays = sort_arrays
        self.type_costs = dict(type_change_costs or TYPE_CHANGE_COSTS)

    # ------------------------------------------------------------------ public
    def tree(self, value: Any) -> Node:
        wrapped = {"root": value} if isinstance(value, Mapping) else value
        return build_tree(
            wrapped, sort_arrays=self.sort_arrays, order_sensitive_fields=self.ordered
        )

    def similarity(self, a: Any, b: Any) -> float:
        cost = self.cost(self.tree(a), self.tree(b), "combined")
        return max(0.0, min(1.0, 1.0 - cost))

    # ------------------------------------------------------------------ costs
    def _weight(self, label: str) -> float:
        depth = 0 if label == "root" else label.count(".") + label.count("[")
        return self.decay**depth

    def edit_cost(self, node: Node) -> float:  # insert or delete
        return min(1.0, self._weight(node.label))

    def _pair_weight(self, a: Node, b: Node) -> float:
        return (self._weight(a.label) + self._weight(b.label)) / 2.0

    def structural_cost(self, a: Node, b: Node) -> float:
        if a.is_leaf and b.is_leaf and a.label == b.label and a.type == b.type:
            return 0.0
        same_path = _INDEX.sub("[*]", a.label) == _INDEX.sub("[*]", b.label)
        ka, kb = _key(a.label), _key(b.label)
        if self.exact_keys:
            key_sim = 1.0 if ka == kb else 0.0
        else:
            key_sim = self.text_similarity(_NON_ALNUM.sub(" ", ka), _NON_ALNUM.sub(" ", kb))
        similarity = (key_sim + (1.0 if same_path else 0.0)) / 2.0
        return min(1.0, (1.0 - similarity) * self._pair_weight(a, b))

    def content_cost(self, a: Node, b: Node) -> float:
        if a.type == b.type and a.value == b.value:
            return 0.0
        return min(1.0, (1.0 - self._content_similarity(a, b)) * self._pair_weight(a, b))

    def _content_similarity(self, a: Node, b: Node) -> float:
        if not (a.is_leaf and b.is_leaf):
            return 0.0
        if self.exact_fields and (
            a.label.split(".")[-1] in self.exact_fields or _key(a.label) in self.exact_fields
        ):
            return 1.0 if str(a.value) == str(b.value) else 0.0
        if a.type == b.type:
            if a.type == "string":
                return self.text_similarity(str(a.value), str(b.value))
            if a.type == "number":
                return 1.0 if float(a.value) == float(b.value) else 0.0
            return 1.0 if a.value == b.value else 0.0  # boolean, null, empty containers
        similarity = self.text_similarity(str(a.value), str(b.value))
        return max(0.0, similarity - self.type_costs.get((a.type, b.type), 1.0))

    def update_cost(self, a: Node, b: Node) -> float:  # Eq. (1)
        return self.w * self.structural_cost(a, b) + (1 - self.w) * self.content_cost(a, b)

    def _leaf_cost(self, a: Node, b: Node, mode: str) -> float:
        if mode == "structural":
            return self.structural_cost(a, b)
        if mode == "content":
            return self.content_cost(a, b)
        return self.update_cost(a, b)

    def cost(self, a: Node, b: Node, mode: str = "combined") -> float:
        """Edit cost of turning subtree ``a`` into ``b`` (``mode``: combined / structural /
        content)."""
        if a.is_leaf and b.is_leaf:
            return self._leaf_cost(a, b, mode)
        if a.is_leaf:
            return self.edit_cost(a) + sum(self.edit_cost(c) for c in b.children)
        if b.is_leaf:
            return sum(self.edit_cost(c) for c in a.children) + self.edit_cost(b)
        if a.type == b.type == "array" and (self._ordered(a.label) or self._ordered(b.label)):
            return self._sequential_cost(a, b, mode)
        ca, cb = a.children, b.children
        size = max(len(ca), len(cb))
        first = "structural" if mode == "combined" else mode
        matrix = [[0.0] * size for _ in range(size)]
        for i in range(size):
            for j in range(size):
                if i < len(ca) and j < len(cb):
                    matrix[i][j] = self.cost(ca[i], cb[j], first)
                elif i < len(ca):
                    matrix[i][j] = self.edit_cost(ca[i])
                elif j < len(cb):
                    matrix[i][j] = self.edit_cost(cb[j])
        pairs = hungarian(matrix)
        first_total = sum(matrix[i][j] for i, j in pairs)
        if mode != "combined":
            return min(1.0, first_total / size)
        content_total = 0.0  # content cost over the structural pairing
        for i, j in pairs:
            if i < len(ca) and j < len(cb):
                x, y = ca[i], cb[j]
                both_leaves = x.is_leaf and y.is_leaf
                content_total += self.content_cost(x, y) if both_leaves else self.cost(x, y)
            else:
                content_total += self.edit_cost(ca[i] if i < len(ca) else cb[j])
        return min(1.0, (self.w * first_total + (1 - self.w) * content_total) / size)

    def _ordered(self, label: str) -> bool:
        parts = label.replace("[", ".").replace("]", "").split(".")
        return any(p and not p.isdigit() and p in self.ordered for p in parts)

    def _sequential_cost(self, a: Node, b: Node, mode: str) -> float:
        ca, cb = a.children, b.children
        common = min(len(ca), len(cb))
        total = sum(self.cost(ca[i], cb[i], mode) for i in range(common))
        total += sum(self.edit_cost(c) for c in ca[common:])
        total += sum(self.edit_cost(c) for c in cb[common:])
        total += abs(len(ca) - len(cb)) * 0.1  # length penalty
        return min(1.0, total / max(len(ca), len(cb)))


# --------------------------------------------------------------------------- strings


def prepare_text(text: str) -> str:
    """Text as it is embedded: camelCase / acronyms split, ``_ - .`` → spaces, lowercased (so
    ``userName`` and ``user_name`` embed alike). Applied for every embeddings provider."""
    out = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    out = re.sub(r"([a-z\d])([A-Z])", r"\1 \2", out)
    out = re.sub(r"[_\-.]+", " ", out)
    return " ".join(out.lower().split())


def strings_to_embed(values: Iterable[Any], min_length: int = 4) -> list[str]:
    """Every string a STED comparison of ``values`` may embed (keys and string leaves)."""
    out: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = _NON_ALNUM.sub(" ", _INDEX.sub("", str(key)))
                if len(normalized) >= min_length:
                    out.add(normalized)
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif value is not None:
            text = str(value)
            if len(text) >= min_length:
                out.add(text)

    for value in values:
        walk(value)
    return sorted(out)


class EmbeddingSimilarity:
    """Semantic similarity of strings from precomputed embeddings (``vectors[text]``)."""

    def __init__(self, vectors: Mapping[str, Sequence[float]], min_length: int = 4):
        self.vectors = vectors
        self.min_length = min_length

    def __call__(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        if len(a) < self.min_length or len(b) < self.min_length:
            return char_similarity(a, b)
        va, vb = self.vectors.get(a), self.vectors.get(b)
        if va is None or vb is None:
            return 0.0
        return cosine01(va, vb)


# --------------------------------------------------------------------------- consistency


def as_json_object(output: Any) -> dict[str, Any]:
    """A run output as the JSON object compared: JSON text (whole, or a fenced block) is parsed;
    anything that is not an object becomes ``{"value": ...}``; a missing or blank output is
    ``{}`` (an invalid run)."""
    if output is None:
        return {}
    value: Any = output
    if isinstance(output, str):
        text = output.strip()
        if not text:
            return {}
        try:
            value = extract_json(text, scan=False)  # whole text or a ```json fenced block
        except ValueError:
            value = text
    elif not isinstance(output, (Mapping, list, tuple, int, float, bool)):
        value = str(output)
    if isinstance(value, Mapping):
        return dict(value)
    if value == [] or value == "":
        return {}
    return {"value": value}


def is_valid(value: Any) -> bool:
    """A run output counts as valid unless it is empty: ``None``, ``""``, ``[]`` or ``{}``."""
    return not (value is None or (isinstance(value, (str, list, tuple, Mapping)) and not value))


def consistency(
    similarities: Sequence[float], validity_rate: float, steepness: int = 20
) -> dict[str, float]:
    """The consistency scores of a set of pairwise similarities."""
    if not similarities:
        return {
            "c_mean": 0.0,
            "d_std": 0.0,
            "d_std_normalized": 0.0,
            "r_v": validity_rate,
            "c_adj": 0.0,
            "stability_score": 0.0 if validity_rate == 0 else 1.0,
            "ranking_score": 0.0,
            "consistency_coefficient": 0.0,
        }
    n = len(similarities)
    c_mean = sum(similarities) / n
    d_std = math.sqrt(sum((s - c_mean) ** 2 for s in similarities) / n) if n > 1 else 0.0
    if n > 1:
        half = n // 2
        ones = [0.0] * half + [1.0] * (n - half)
        mean_ones = sum(ones) / n
        d_max = math.sqrt(sum((x - mean_ones) ** 2 for x in ones) / n)
        d_norm = d_std / d_max if d_max > 0 else 0.0
        stability = (1.0 / (1.0 + 2.0 * d_norm)) ** steepness
    else:
        d_norm, stability = 0.0, 1.0
    cv = d_std / c_mean if c_mean > 1e-10 else 0.0
    coefficient = (c_mean * (1 - min(cv**1.5, 1.0))) ** 5 if c_mean > 1e-10 else 0.0
    return {
        "c_mean": c_mean,
        "d_std": d_std,
        "d_std_normalized": d_norm,
        "r_v": validity_rate,
        "c_adj": validity_rate * c_mean,
        "stability_score": stability,
        "ranking_score": validity_rate * c_mean * stability,
        "consistency_coefficient": coefficient,
    }
