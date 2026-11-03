"""STED (``metrics/ops/sted.py``) and the ``sted`` method of ``ops.consistency``."""

from __future__ import annotations

import itertools
import math

import pytest

from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.ops import sted
from bnbagent_eval.types import EvalCase, MetricConfig

from ._util import case

EXACT = sted.Sted(lambda a, b: 1.0 if a == b else 0.0)  # no semantics: equal strings only


def test_tree_shape_paths_and_sorting() -> None:
    tree = EXACT.tree({"b": [3, 1], "a": {"x": None}})
    (root,) = tree.children
    # keys under the wrapper are bare (the paper's reference implementation does the same)
    assert root.label == "root" and [c.label for c in root.children] == ["a", "b"]
    array = root.children[1]
    assert array.type == "array" and array.value == [1, 3]
    assert [c.label for c in array.children] == ["b[0]", "b[1]"]
    ordered = sted.Sted(lambda a, b: 0.0, order_sensitive_fields=["b"]).tree({"b": [3, 1]})
    assert ordered.children[0].children[0].value == [3, 1]


def test_hungarian_is_optimal() -> None:
    import random

    rng = random.Random(3)
    for n in range(1, 6):
        for _ in range(20):
            m = [[rng.random() for _ in range(n)] for _ in range(n)]
            best = min(sum(m[i][p[i]] for i in range(n)) for p in itertools.permutations(range(n)))
            got = sted.hungarian(m)
            assert sorted(i for i, _ in got) == list(range(n))
            assert sum(m[i][j] for i, j in got) == pytest.approx(best)


def test_similarity_known_values() -> None:
    assert EXACT.similarity({"a": 1, "b": [1, 2]}, {"b": [2, 1], "a": 1}) == 1.0  # order-free
    assert EXACT.similarity({"a": 1}, {"a": 2}) == pytest.approx(0.75)  # (0.5·0 + 0.5·1) / 2
    assert EXACT.similarity({}, {}) == 1.0
    assert 0.0 <= EXACT.similarity({"a": {"b": 1}}, {"c": [1, 2, 3]}) < 0.5
    ordered = sted.Sted(lambda a, b: 1.0 if a == b else 0.0, order_sensitive_fields=["steps"])
    assert ordered.similarity({"steps": ["a", "b"]}, {"steps": ["b", "a"]}) < 1.0
    assert EXACT.similarity({"steps": ["a", "b"]}, {"steps": ["b", "a"]}) == 1.0


def test_semantics_come_from_the_text_similarity() -> None:
    near = {("New York", "NYC"): 0.9, ("NYC", "New York"): 0.9}
    semantic = sted.Sted(lambda a, b: 1.0 if a == b else near.get((a, b), 0.0))
    paraphrase = semantic.similarity({"city": "New York"}, {"city": "NYC"})
    assert paraphrase > EXACT.similarity({"city": "New York"}, {"city": "NYC"})
    exact_city = sted.Sted(semantic.text_similarity, exact_match_fields=["city"])
    assert exact_city.similarity({"city": "New York"}, {"city": "NYC"}) < paraphrase


def test_string_similarity_short_strings_and_embeddings() -> None:
    sim = sted.EmbeddingSimilarity({"hello": [1.0, 0.0], "world": [0.0, 1.0]})
    assert sim("ab", "ab") == 1.0 and sim("ab", "ac") == 0.5  # short: character agreement
    assert sim("hello", "world") == pytest.approx(0.5)  # orthogonal: cosine 0 -> 0.5
    assert sim("hello", "unknown text") == 0.0  # no vector
    assert sted.strings_to_embed([{"user_name": "Alice", "id": "x"}]) == ["Alice", "user name"]


def test_consistency_scores() -> None:
    scores = sted.consistency([1.0, 0.9, 0.9], validity_rate=1.0)
    assert scores["c_mean"] == pytest.approx(2.8 / 3)
    assert scores["d_std"] == pytest.approx(
        math.sqrt(((1 - 2.8 / 3) ** 2 + 2 * (0.9 - 2.8 / 3) ** 2) / 3)
    )
    assert scores["c_adj"] == scores["c_mean"]
    half = sted.consistency([1.0], validity_rate=0.5)
    assert half["c_adj"] == 0.5 and half["stability_score"] == 1.0
    assert sted.consistency([], 0.0)["c_adj"] == 0.0


def test_run_outputs_as_json_objects() -> None:
    assert sted.as_json_object('{"a": 1}') == {"a": 1}
    assert sted.as_json_object("[1, 2]") == {"value": [1, 2]}
    assert sted.as_json_object("plain") == {"value": "plain"}
    assert sted.as_json_object(None) == {} and sted.as_json_object("  ") == {}
    assert sted.as_json_object(0) == {"value": 0}
    assert not sted.is_valid({}) and sted.is_valid({"value": 0})


# --------------------------------------------------------------------------- the method


class Embedder(FakeJudge):
    """A judge whose embeddings put 'New York' and 'NYC' close together."""

    VECTORS = {"new york": [1.0, 0.1], "alice": [0.0, 1.0], "city": [1.0, 1.0]}  # prepared text

    def __init__(self) -> None:
        super().__init__({})
        self.embedded: list[list[str]] = []

    async def embed(self, texts):
        self.embedded.append(list(texts))
        return [self.VECTORS.get(t, [0.5, 0.5]) for t in texts]


async def _run(runs, judge, **params):
    from bnbagent_eval.metrics.base import MetricContext

    cls, _ = registry.resolve("ops.consistency")
    spec = registry.effective_spec(cls)
    ctx = MetricContext(
        config=MetricConfig(id="ops.consistency", method="sted", params=params),
        method=spec.method("sted"),
        params=params,
        runs=tuple(runs),
        judge=judge,
    )
    return await cls().evaluate(runs[0], ctx)


async def test_method_scores_runs_with_one_embedding_call() -> None:
    judge = Embedder()
    runs = [case('{"name": "Alice", "city": "New York"}')] * 3
    result = await _run(runs, judge)
    assert result.status == "scored" and result.score == 1.0
    assert len(judge.embedded) == 1 and set(judge.embedded[0]) == {
        "alice",
        "new york",
        "city",
        "name",
    }
    assert result.details["c_mean"] == 1.0 and result.details["r_v"] == 1.0
    assert result.provenance.method == "sted"


async def test_invalid_runs_lower_the_default_score() -> None:
    runs = [case('{"a": 1}'), case('{"a": 1}'), case(None)]
    result = await _run(runs, Embedder())
    assert result.details["c_mean"] == 1.0 and result.score == pytest.approx(2 / 3)
    raw_mean = await _run(runs, Embedder(), score="c_mean")
    assert raw_mean.score == 1.0
    lone = await _run([case('{"a": 1}'), case("")], Embedder())
    assert lone.status == "scored" and lone.score == 0.0 and "fewer than two" in lone.reason


async def test_method_needs_a_judge_that_embeds_and_two_runs() -> None:
    no_judge = await _run([case('{"a": "hello"}'), case('{"a": "world"}')], None)
    assert no_judge.status == "error" and "embeddings" in no_judge.error.message
    one = await _run([case('{"a": 1}')], Embedder())
    assert one.status in ("skipped", "error") and one.score is None


def test_spec_and_params() -> None:
    spec = registry.get_spec("ops.consistency")
    assert spec.version == "3" and {"ted", "sted"} <= set(spec.method_names)
    method = spec.method("sted")
    assert method.kind.value == "judge" and method.extra is None  # no third-party dependency
    assert not spec.validate_params({"score": "consistency_coefficient", "structural_weight": 0.3})
    assert spec.validate_params({"score": "bogus"})


async def test_through_the_client_with_a_target() -> None:
    from bnbagent_eval import EvalClient, Suite
    from bnbagent_eval.engine.targets import register_target
    from bnbagent_eval.types import TargetRef

    answers = iter(['{"to": "Alice", "city": "New York"}', '{"to": "Alice", "city": "New York"}'])
    register_target("sted_agent", lambda _q: next(answers))
    suite = Suite(
        name="s", version="1", metrics=(MetricConfig(id="ops.consistency", method="sted"),)
    )
    response = await EvalClient(judge=Embedder()).evaluate(
        suite=suite,
        cases=[EvalCase(id="1", input="send to alice")],
        target=TargetRef(kind="callable", uri="sted_agent"),
        options={"runs": 2},
    )
    assert response.results[0].metrics[0].score == 1.0
    from bnbagent_eval.engine import resolve_suite

    assert resolve_suite(suite).lock["ops.consistency"].prompt_hash is None  # embeddings only


def test_prepare_text() -> None:
    assert sted.prepare_text("userName") == sted.prepare_text("user_name") == "user name"
    assert sted.prepare_text("HTTPSConnection") == "https connection"
    assert sted.prepare_text("0.5 BNB") == "0 5 bnb"
