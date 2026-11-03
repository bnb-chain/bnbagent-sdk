"""STED parity: our implementation (``metrics/ops/sted.py``) vs the authors' reference code.

Runs only where the reference package is installed (it is not a dependency):

    pip install 'sted @ git+https://github.com/amazon-science/sted'
    pytest tests/parity/test_sted_parity.py

Both sides get the same string similarity (the reference's own, over the same local embedding
model), so any difference is in the tree algorithm or the consistency scores.
"""

from __future__ import annotations

import copy
import random
import socket

import pytest

from bnbagent_eval.metrics.ops import sted

pytest.importorskip("sted.semantic_json_tree_consistency")

WORDS = ["Alice", "Bob", "New York", "NYC", "New York City", "transfer", "send", "BNB",
         "bnb token", "approve", "pending", "done", "ok", "x", "error: timeout", "0.5 BNB",
         "userName", "HTTPSConnection"]  # fmt: skip
KEYS = ["name", "city", "location", "amount", "to", "token", "status", "steps", "id", "user_name"]
EDGE = [
    (
        {"name": "Alice", "age": 25, "city": "New York"},
        {"name": "Alice", "age": 25, "city": "NYC"},
    ),
    ({"name": "Alice", "age": 25}, {"items": ["Alice", 25]}),
    ({"steps": ["a", "b", "c"]}, {"steps": ["c", "b", "a"]}),
    ({"a": []}, {"a": {}}),
    ({"a": {"b": 1}}, {"a": 1}),
    ({}, {"a": 1}),
]


@pytest.fixture(autouse=True)
def allow_network(monkeypatch):
    """The reference loads a sentence-transformers model (downloaded once)."""
    from tests.conftest import _real_connect

    monkeypatch.setattr(socket.socket, "connect", _real_connect)


@pytest.fixture
def reference():
    from sted.semantic_json_tree_consistency import SemanticJsonTreeConsistencyEvaluator

    return SemanticJsonTreeConsistencyEvaluator


def _value(r: random.Random, depth: int):
    k = r.random()
    if depth > 2 or k < 0.45:
        c = r.random()
        if c < 0.55:
            return r.choice(WORDS)
        if c < 0.75:
            return r.choice([0, 1, 2.5, 25])
        return r.choice([True, False, None])
    if k < 0.75:
        return {r.choice(KEYS): _value(r, depth + 1) for _ in range(r.randint(0, 4))}
    return [_value(r, depth + 1) for _ in range(r.randint(0, 4))]


def _pairs(seed: int = 7, n: int = 200):
    r = random.Random(seed)
    out = list(EDGE)
    for _ in range(n):
        a = {r.choice(KEYS): _value(r, 1) for _ in range(r.randint(1, 5))}
        b = copy.deepcopy(a)
        if b and r.random() < 0.6:
            b[r.choice(list(b))] = _value(r, 1)
        out.append((a, b if r.random() < 0.7 else {r.choice(KEYS): _value(r, 1)}))
    return out


def _reference_similarity(ev, a, b) -> float:
    from sted.json_tree_node import JsonNode

    def tree(x):
        wrapped = {"root": x} if isinstance(x, dict) else x
        return JsonNode.from_dict(
            wrapped,
            sort_arrays=ev.sort_arrays,
            sort_keys=ev.sort_keys,
            order_sensitive_fields=ev.order_sensitive_fields,
        )

    return max(0.0, min(1.0, 1.0 - ev._calculate_optimal_matching_cost(tree(a), tree(b))))


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"exact_match_fields": {"name"}},
        {"order_sensitive_fields": {"steps"}},
        {"structural_weight": 0.3},
        {"exact_match_all_keys": True},
    ],  # fmt: skip
)
def test_tree_similarity_matches_the_reference(reference, options) -> None:
    ev = reference(model_id="all-MiniLM-L6-v2", **options)
    ours = sted.Sted(ev._calculate_semantic_similarity, **options)
    diffs = [abs(ours.similarity(a, b) - _reference_similarity(ev, a, b)) for a, b in _pairs()]
    # Identical except where several pairings are structurally optimal (a tie the definition
    # leaves open): the solvers may then pick different pairings; measured ~1 pair in 1000.
    assert sum(d < 1e-9 for d in diffs) >= 0.99 * len(diffs)
    assert max(diffs) < 0.05


def test_string_similarity_matches_the_reference(reference) -> None:
    ev = reference(model_id="all-MiniLM-L6-v2")
    texts = sted.strings_to_embed([a for a, _ in EDGE] + [{w: w for w in WORDS}])
    vectors = ev.embedding_model.encode([sted.prepare_text(t) for t in texts]).tolist()
    ours = sted.EmbeddingSimilarity(dict(zip(texts, vectors, strict=True)))
    for x in texts + ["ab", "ok"]:
        for y in texts + ["ac"]:
            assert ours(x, y) == pytest.approx(ev._calculate_semantic_similarity(x, y), abs=1e-6)


def test_consistency_scores_match_the_reference(reference) -> None:
    from sted.structural_consistency_analyzer import StructuralConsistencyAnalyzer

    analyzer = StructuralConsistencyAnalyzer(reference(model_id="all-MiniLM-L6-v2"))
    r = random.Random(1)
    for _ in range(200):
        sims = [r.random() for _ in range(r.randint(1, 10))]
        validity = r.choice([1.0, 0.75, 0.5])
        expected = analyzer._calculate_consistency_metrics(sims, validity_rate=validity)
        for key, value in sted.consistency(sims, validity).items():
            assert value == pytest.approx(expected[key], abs=1e-9), key
