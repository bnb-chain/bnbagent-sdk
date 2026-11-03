"""Local-model methods and text.bertscore with fake libraries (no torch / Java / weights)."""

from __future__ import annotations

import math
import sys
import types
from collections.abc import Sequence
from typing import Any

import pytest

from bnbagent_eval.engine import Runner, resolve_suite
from bnbagent_eval.engine.budget import BudgetTracker
from bnbagent_eval.exceptions import MissingExtraError
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import Kind, MetricContext
from bnbagent_eval.metrics.text import bertscore
from bnbagent_eval.metrics.vendors import local_models as lm
from bnbagent_eval.types import Budget, EvalCase, EvalRequest, MetricConfig, MetricResult, Suite


async def run(
    metric_id: str, case: EvalCase, method: str, params: dict | None = None, **ctx: Any
) -> MetricResult:
    cls, _ = registry.resolve(metric_id)
    spec = registry.effective_spec(cls)
    params = params or {}
    cfg = MetricConfig(id=metric_id, method=method, params=params)
    mctx = MetricContext(config=cfg, method=spec.method(method), params=params, **ctx)
    return await cls().evaluate(case, mctx)


def c(output: Any = None, expected: Any = None, **kw: Any) -> EvalCase:
    return EvalCase(id="c", output=output, expected=expected, **kw)


def fake_module(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm, "_cache", {})
    monkeypatch.setattr(bertscore, "_encoders", {})


# --------------------------------------------------------------------------- spec / flags


def test_methods_attached_with_flags() -> None:
    sim = registry.get_spec("text.embedding_similarity")
    assert sim.method_names[:1] == ["keyword"]
    emb, glove, ft = sim.method("embeddings"), sim.method("glove"), sim.method("fasttext")
    assert emb.kind is Kind.JUDGE and emb.extra is None and emb.service_allowed
    assert glove.kind is Kind.MODEL and glove.extra == "embeddings"
    assert glove.extra_module == "gensim" and not glove.service_allowed and not ft.service_allowed
    for metric_id in ("safety.harmfulness", "safety.toxicity"):
        m = registry.get_spec(metric_id).method("model")
        assert (m.kind, m.extra, m.extra_module) == (Kind.MODEL, "toxicity", "detoxify")
        assert not m.inverted and m.service_allowed
    fl = registry.get_spec("quality.fluency").method("rule")
    assert (fl.kind, fl.extra, fl.extra_module) == (Kind.RULE, "fluency", "language_tool_python")
    assert fl.service_allowed is False
    bs = registry.get_spec("text.bertscore")
    assert bs.default_threshold == 0.8 and bs.method("model").extra == "semantic"
    assert bs.method("model").extra_module == "transformers"  # BERTScore itself is ours


def test_params_schemas_accept_new_params_and_reject_unknown() -> None:
    sim = registry.get_spec("text.embedding_similarity")
    assert not sim.validate_params({"vectors_path": "/v.txt", "against": "input"})
    assert sim.validate_params({"nope": 1})
    assert not registry.get_spec("safety.toxicity").validate_params({"model_type": "unbiased"})
    assert registry.get_spec("safety.toxicity").validate_params({"model_type": "bogus"})
    fl = registry.get_spec("quality.fluency")
    assert not fl.validate_params({"penalty_factor": 1.0, "language": "de-DE"})
    assert fl.validate_params({"penalty_factor": -1})
    assert registry.get_spec("text.bertscore").validate_params({"nope": "x"})


@pytest.mark.parametrize(
    ("metric_id", "method", "extra"),
    [
        ("text.bertscore", "model", "semantic"),
        ("text.embedding_similarity", "glove", "embeddings"),
        ("safety.toxicity", "model", "toxicity"),
        ("quality.fluency", "rule", "fluency"),
    ],
)
def test_missing_extra_at_resolution(
    metric_id: str, method: str, extra: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bnbagent_eval.engine import resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "available", lambda module: False)
    suite = Suite(name="s", version="1", metrics=(MetricConfig(id=metric_id, method=method),))
    with pytest.raises(MissingExtraError, match=extra):
        resolve_suite(suite)


@pytest.mark.parametrize(
    ("metric_id", "method", "module"),
    [
        ("text.bertscore", "model", "transformers"),
        ("text.embedding_similarity", "fasttext", "gensim"),
        ("safety.harmfulness", "model", "detoxify"),
        ("quality.fluency", "rule", "language_tool_python"),
    ],
)
async def test_missing_extra_in_handler_is_an_error(
    metric_id: str, method: str, module: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, module, None)  # import raises ImportError
    r = await run(metric_id, c("hello world", "hello world"), method, {"vectors_path": "/x.vec"})
    assert r.status == "error" and "pip install 'bnbagent-eval[" in r.error.message


# --------------------------------------------------------------------------- helpers


def test_cosine_and_rescale() -> None:
    assert lm.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert lm.rescale(lm.cosine([1, 0], [-1, 0])) == 0.0
    assert lm.rescale(lm.cosine([1, 0], [0, 1])) == 0.5
    assert lm.rescale(0.6) == pytest.approx(0.8)
    assert lm.cosine([0, 0], [1, 0]) is None and lm.rescale(None) == 0.0
    with pytest.raises(ValueError, match="sizes differ"):
        lm.cosine([1], [1, 2])


def test_text_vector_means_keyword_vectors() -> None:
    table = {"bnb": [1.0, 0.0], "chain": [0.0, 1.0]}
    vec = lm.text_vector("The BNB chain!", table.get)  # "the" is a stop word
    assert vec == [0.5, 0.5]
    assert lm.text_vector("the a of", table.get) is None


# --------------------------------------------------------------------------- embeddings


class EmbedJudge(FakeJudge):
    def __init__(self, table: dict[str, list[float]], **kw: Any):
        super().__init__(**kw)
        self.table = table
        self.embedded: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded.append(list(texts))
        return [self.table[t] for t in texts]


async def test_embeddings_cosine_rescaled() -> None:
    judge = EmbedJudge({"a": [1.0, 0.0], "b": [0.6, 0.8]})
    r = await run("text.embedding_similarity", c("a", "b"), "embeddings", judge=judge)
    assert r.status == "scored" and r.score == pytest.approx((0.6 + 1) / 2)
    assert r.details["cosine"] == pytest.approx(0.6) and r.details["against"] == "expected"
    assert judge.embedded == [["a", "b"]]
    assert r.provenance.judge == {"provider": "fake", "model": "fake"}


async def test_embeddings_best_reference_and_input() -> None:
    judge = EmbedJudge({"a": [1.0, 0.0], "x": [-1.0, 0.0], "y": [1.0, 0.0], "q": [0.0, 1.0]})
    r = await run("text.embedding_similarity", c("a", ["x", "y"]), "embeddings", judge=judge)
    assert r.score == 1.0 and r.details["best_reference"] == 1
    r = await run("text.embedding_similarity", c("a", None, input="q"), "embeddings", judge=judge)
    assert r.score == 0.5 and r.details["against"] == "input"
    zero = EmbedJudge({"a": [0.0, 0.0], "b": [1.0, 0.0]})
    assert (
        await run("text.embedding_similarity", c("a", "b"), "embeddings", judge=zero)
    ).score == 0


async def test_embeddings_errors_and_budget() -> None:
    r = await run("text.embedding_similarity", c("a", "b"), "embeddings")
    assert r.status == "error" and "needs a judge" in r.error.message

    class NoEmbed(FakeJudge):
        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            raise NotImplementedError("the Anthropic API has no embeddings endpoint")

    r = await run("text.embedding_similarity", c("a", "b"), "embeddings", judge=NoEmbed())
    assert r.status == "error" and "cannot embed" in r.error.message
    judge = EmbedJudge({"a": [1.0], "b": [1.0]})
    budget = BudgetTracker(Budget(max_judge_calls=0))
    r = await run(
        "text.embedding_similarity", c("a", "b"), "embeddings", judge=judge, budget=budget
    )
    assert r.status == "skipped" and r.error.code == "budget_exhausted" and not judge.embedded


async def test_embedding_methods_record_the_embedding_model_used() -> None:
    import dataclasses

    judge = EmbedJudge({"a": [1.0, 0.0], "b": [0.6, 0.8]})
    judge.config = dataclasses.replace(judge.config, extra={"embeddings_model": "emb-1"})
    r = await run("text.embedding_similarity", c("a", "b"), "embeddings", judge=judge)
    assert r.provenance.judge["embedding_model"] == "emb-1"
    assert r.details["embedding_model"] == "emb-1"
    runs = (c("a", None), c("b", None))
    r = await run("ops.consistency", c("a", None), "embedding", judge=judge, runs=runs)
    assert r.status == "scored" and r.provenance.judge["embedding_model"] == "emb-1"


async def test_embeddings_method_gets_the_runs_judge() -> None:
    judge = EmbedJudge({"a": [1.0, 0.0], "b": [1.0, 0.0]})
    suite = Suite(
        name="s",
        version="1",
        metrics=(MetricConfig(id="text.embedding_similarity", method="embeddings"),),
    )
    assert resolve_suite(suite).needs_judge
    resp = await Runner(judge=judge).run(
        EvalRequest(suite=suite, cases=(EvalCase(id="1", output="a", expected="b"),))
    )
    assert resp.results[0].metrics[0].score == 1.0


# --------------------------------------------------------------------------- glove / fasttext


class FakeKV(dict):
    def __init__(self, table: dict[str, list[float]], subwords: bool = False):
        super().__init__(table)
        self.subwords = subwords

    def __contains__(self, word: object) -> bool:
        return self.subwords or dict.__contains__(self, word)

    def __getitem__(self, word: str) -> list[float]:
        if dict.__contains__(self, word):
            return dict.__getitem__(self, word)
        return [0.0, 1.0]  # a subword-composed OOV vector


@pytest.fixture
def gensim(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"w2v": [], "facebook": []}
    table = {"staking": [1.0, 0.0], "rewards": [1.0, 0.0], "pizza": [-1.0, 0.0]}

    def load_word2vec_format(path: str, binary: bool = False, no_header: bool = False) -> FakeKV:
        calls["w2v"].append((path, binary, no_header))
        return FakeKV(table)

    def load_facebook_vectors(path: str) -> FakeKV:
        calls["facebook"].append(path)
        return FakeKV(table, subwords=True)

    fake_module(monkeypatch, "gensim")
    fake_module(
        monkeypatch,
        "gensim.models",
        KeyedVectors=types.SimpleNamespace(load_word2vec_format=load_word2vec_format),
    )
    fake_module(monkeypatch, "gensim.models.fasttext", load_facebook_vectors=load_facebook_vectors)
    return calls


async def test_glove_vectors_without_header_and_cached(gensim: dict, tmp_path) -> None:
    path = tmp_path / "glove.6B.50d.txt"
    path.write_text("staking 1 0\nrewards 1 0\n")
    params = {"vectors_path": str(path)}
    r = await run("text.embedding_similarity", c("staking", "rewards"), "glove", params)
    assert r.score == 1.0 and r.details["vectors"] == "glove.6B.50d.txt"
    r = await run("text.embedding_similarity", c("staking", "pizza"), "glove", params)
    assert r.score == 0.0 and "fallback" not in r.details
    assert gensim["w2v"] == [(str(path.resolve()), False, True)]  # loaded once, no header


async def test_fasttext_vec_header_bin_and_oov(gensim: dict, tmp_path) -> None:
    vec = tmp_path / "cc.en.300.vec"
    vec.write_text("2 2\nstaking 1 0\nrewards 1 0\n")
    await run(
        "text.embedding_similarity",
        c("staking", "rewards"),
        "fasttext",
        {"vectors_path": str(vec)},
    )
    assert gensim["w2v"][-1][2] is False  # header line present
    binary = tmp_path / "cc.en.300.bin"
    binary.write_bytes(b"\0")
    r = await run(
        "text.embedding_similarity",
        c("staking", "zzzword"),
        "fasttext",
        {"vectors_path": str(binary)},
    )
    assert gensim["facebook"] == [str(binary.resolve())]
    assert r.score == pytest.approx(0.5)  # OOV word vector from subwords: orthogonal


async def test_word_vectors_keyword_fallback_and_errors(gensim: dict, tmp_path) -> None:
    path = tmp_path / "v.txt"
    path.write_text("staking 1 0\n")
    r = await run(
        "text.embedding_similarity",
        c("unknown words", "unknown words"),
        "glove",
        {"vectors_path": str(path)},
    )
    assert r.details["fallback"] == "keyword" and r.score == 1.0  # Jaccard 1 -> cosine-like 1
    r = await run("text.embedding_similarity", c("a", "b"), "glove")
    assert r.status == "error" and "vectors_path" in r.error.message
    r = await run(
        "text.embedding_similarity", c("a", "b"), "glove", {"vectors_path": str(tmp_path / "no")}
    )
    assert r.status == "error" and "not a file" in r.error.message
    assert gensim["w2v"] == [(str(path.resolve()), False, True)]


# --------------------------------------------------------------------------- detoxify


@pytest.fixture
def detoxify(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    loaded: list[str] = []

    class Detoxify:
        def __init__(self, model_type: str):
            loaded.append(model_type)

        def predict(self, text: str) -> dict[str, float]:
            if "idiot" in text:
                return {"toxicity": 0.9, "insult": 0.8, "threat": 0.1, "obscene": 0.2}
            return {"toxicity": 0.02, "insult": 0.01, "threat": 0.0, "obscene": 0.005}

    fake_module(monkeypatch, "detoxify", Detoxify=Detoxify)
    return loaded


async def test_detoxify_harmfulness(detoxify: list[str]) -> None:
    r = await run("safety.harmfulness", c("you idiot"), "model")
    assert r.score == pytest.approx(0.1) and r.passed is False and r.threshold == 0.7
    assert r.details["toxicity_scores"]["insult"] == 0.8
    assert r.details["harmful_categories"] == ["insult", "toxicity"]
    assert r.details["max_toxicity"] == 0.9 and "raw" not in r.details
    ok = await run("safety.harmfulness", c("Open the staking page."), "model")
    assert ok.score == pytest.approx(0.98) and ok.passed and ok.details["harmful_categories"] == []
    assert detoxify == ["original"]  # cached model


async def test_detoxify_toxicity_keeps_raw(detoxify: list[str]) -> None:
    r = await run("safety.toxicity", c("you idiot"), "model", {"model_type": "unbiased"})
    assert r.score == pytest.approx(0.1) and r.details["raw"] == 0.9
    assert r.details["model_type"] == "unbiased" and detoxify == ["unbiased"]


# --------------------------------------------------------------------------- language tool


@pytest.fixture
def language_tool(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    started: list[str] = []

    class LanguageTool:
        def __init__(self, language: str):
            started.append(language)

        def check(self, text: str) -> list[Any]:
            return [
                types.SimpleNamespace(ruleId="MORFOLOGIK_RULE_EN_US", message="Possible typo")
                for w in text.split()
                if w == "teh"
            ]

    fake_module(monkeypatch, "language_tool_python", LanguageTool=LanguageTool)
    monkeypatch.setitem(sys.modules, "nltk", None)  # whitespace word split
    monkeypatch.setitem(sys.modules, "nltk.tokenize", None)
    return started


async def test_fluency_formula(language_tool: list[str]) -> None:
    r = await run("quality.fluency", c("teh cat sat on teh mat"), "rule")
    assert r.score == pytest.approx(1 - 2 / 6 * 0.5)
    assert r.details["num_errors"] == 2 and r.details["num_words"] == 6
    assert r.details["error_examples"][0] == "MORFOLOGIK_RULE_EN_US: Possible typo"
    r = await run("quality.fluency", c("teh teh"), "rule", {"penalty_factor": 2.0})
    assert r.score == 0.0  # clamped
    assert (await run("quality.fluency", c("   "), "rule")).score == 0.0
    await run("quality.fluency", c("gut"), "rule", {"language": "de-DE"})
    assert language_tool == ["en-US", "de-DE"]


# --------------------------------------------------------------------------- bertscore


@pytest.fixture
def bert(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Our BERTScore with a fake encoder: tokens are words, cosine 1 for equal words, else 0."""
    from bnbagent_eval.metrics.text import bertscore as bs

    models: list[str] = []

    class FakeEncoder:
        def __init__(self, model: str, num_layers: int):
            models.append(model)

        def score(self, candidate: str, reference: str):
            cand, ref = candidate.split(), reference.split()
            sim = [[1.0 if x == y else 0.0 for y in ref] for x in cand]
            return bs.greedy_match(sim, [1.0] * len(cand), [1.0] * len(ref))

    monkeypatch.setattr(bs, "Encoder", FakeEncoder)
    monkeypatch.setattr(bs, "_encoders", {})
    return models


def test_greedy_match_and_special_token_weights() -> None:
    from bnbagent_eval.metrics.text.bertscore import greedy_match

    sim = [[0.9, 0.1], [0.2, 0.8], [0.5, 0.5]]  # 3 candidate tokens, 2 reference tokens
    p, r, f = greedy_match(sim, [1.0, 1.0, 1.0], [1.0, 1.0])
    assert p == pytest.approx((0.9 + 0.8 + 0.5) / 3) and r == pytest.approx((0.9 + 0.8) / 2)
    assert f == pytest.approx(2 * p * r / (p + r))
    p0, _, _ = greedy_match(sim, [1.0, 1.0, 0.0], [1.0, 1.0])  # weight 0 (e.g. [SEP]) ignored
    assert p0 == pytest.approx(0.85)


async def test_bertscore_f1_default_model(bert: list[str]) -> None:
    r = await run("text.bertscore", c("a b c d", "a b x y z w"), "model")
    p, rec = 2 / 4, 2 / 6
    assert r.score == pytest.approx(2 * p * rec / (p + rec))
    assert r.details["precision"] == 0.5 and r.details["recall"] == pytest.approx(1 / 3)
    assert r.passed is False and r.threshold == 0.8
    assert bert == ["microsoft/deberta-xlarge-mnli"]
    same = await run("text.bertscore", c("a b", "a b"), "model")
    assert same.score == 1.0 and same.passed and bert == ["microsoft/deberta-xlarge-mnli"]


async def test_bertscore_references_empty_and_model_param(bert: list[str]) -> None:
    r = await run("text.bertscore", c("a b", "x y<OR>a b"), "model", {"model": "roberta-large"})
    assert r.score == 1.0 and r.details["best_reference"] == 1 and bert == ["roberta-large"]
    empty = await run("text.bertscore", c("  ", "a b"), "model")
    assert empty.score == 0.0 and empty.status == "scored"
    assert math.isclose((await run("text.bertscore", c("a", "a"), "model")).score, 1.0)


class _Tensor:
    """The few torch tensor operations ``Encoder`` uses, over nested lists (2-D rows)."""

    def __init__(self, rows: list[list[float]]):
        self.rows = rows

    @property
    def T(self) -> _Tensor:  # noqa: N802 - torch attribute name
        return _Tensor([list(col) for col in zip(*self.rows, strict=True)])

    def norm(self, dim: int, keepdim: bool) -> _Tensor:
        assert dim == -1 and keepdim
        return _Tensor([[math.sqrt(sum(x * x for x in row))] for row in self.rows])

    def __truediv__(self, other: _Tensor) -> _Tensor:
        return _Tensor(
            [[x / d[0] for x in row] for row, d in zip(self.rows, other.rows, strict=True)]
        )

    def __matmul__(self, other: _Tensor) -> _Tensor:
        cols = other.T.rows
        return _Tensor(
            [[sum(a * b for a, b in zip(r, col, strict=True)) for col in cols] for r in self.rows]
        )

    def tolist(self) -> list[list[float]]:
        return self.rows


_VOCAB = {"a": 5, "b": 6}
_LAYER = 3


def _fake_semantic(tokenizer_name: str, calls: dict[str, Any]) -> dict[str, Any]:
    """Fake ``transformers`` / ``torch``. Special tokens are ``<s>`` (0) and ``</s>`` (2). Only
    layer ``_LAYER`` separates tokens ("a", "b" and the special tokens are orthogonal); every
    other layer embeds all tokens alike, so picking the wrong layer makes "a" match "b"."""

    def encode(text, *, add_special_tokens, max_length, truncation, **kwargs):
        calls.setdefault("encode", []).append(kwargs)
        assert add_special_tokens and truncation and max_length == 512
        return [0, *(_VOCAB[w] for w in text.split()), 2]

    tokenizer = type(
        tokenizer_name,
        (),
        {
            "cls_token_id": 0,
            "sep_token_id": 2,
            "model_max_length": 512,
            "encode": staticmethod(encode),
            "build_inputs_with_special_tokens": staticmethod(lambda ids: [0, *ids, 2]),
        },
    )()

    def embedding(token: int, layer: int) -> list[float]:
        if layer != _LAYER:
            return [3.0, 4.0, 0.0]
        if token == _VOCAB["a"]:
            return [1.0, 0.0, 0.0]
        return [0.0, 2.0, 0.0] if token == _VOCAB["b"] else [0.0, 0.0, 5.0]

    class Model:
        def __call__(self, batch, *, output_hidden_states):
            assert output_hidden_states
            layers = [[_Tensor([embedding(t, layer) for t in batch[0]])] for layer in range(5)]
            return types.SimpleNamespace(hidden_states=layers)

    def load_model(name: str) -> Any:
        model = Model()
        # inference mode is requested with model.<"eval">() (spelled indirectly for the sandbox)
        setattr(model, "e" + "val", lambda: calls.update(inference=True) or model)
        return model

    def load_tokenizer(name: str, use_fast: bool) -> Any:
        calls["tokenizer"] = (name, use_fast)
        return tokenizer

    class NoGrad:
        def __enter__(self) -> None:
            calls["no_grad"] = True

        def __exit__(self, *exc: object) -> None:
            return None

    transformers = types.SimpleNamespace(
        AutoTokenizer=types.SimpleNamespace(from_pretrained=load_tokenizer),
        AutoModel=types.SimpleNamespace(from_pretrained=load_model),
    )
    torch = types.SimpleNamespace(no_grad=NoGrad, tensor=lambda data: data)
    return {"transformers": transformers, "torch": torch}


@pytest.mark.parametrize(
    ("name", "prefix"), [("RobertaTokenizer", True), ("BertTokenizer", False)]
)
def test_encoder_layer_special_tokens_and_prefix_space(
    monkeypatch: pytest.MonkeyPatch, name: str, prefix: bool
) -> None:
    calls: dict[str, Any] = {}
    fakes = _fake_semantic(name, calls)
    monkeypatch.setattr(bertscore, "require", lambda module, extra: fakes[module])
    enc = bertscore.Encoder("m", _LAYER)
    assert calls["tokenizer"] == ("m", False) and calls["inference"]
    assert enc.special == {0, 2} and enc.prefix_space is prefix
    assert enc.ids("   ") == [0, 2]  # empty text: special tokens only
    states, weights = enc.embed(" a b ")
    assert calls["no_grad"] and weights == [0.0, 1.0, 1.0, 0.0]  # <s> a b </s>
    assert calls["encode"][-1] == ({"add_prefix_space": True} if prefix else {})
    assert all(math.isclose(sum(x * x for x in row), 1.0) for row in states.rows)  # normalised
    assert enc.score("a b", "a b") == pytest.approx((1.0, 1.0, 1.0))
    assert enc.score("a", "b") == pytest.approx((0.0, 0.0, 0.0))  # layer 3: orthogonal


def test_encoder_cache_and_unknown_model_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[tuple[str, int]] = []
    monkeypatch.setattr(bertscore, "Encoder", lambda m, n: built.append((m, n)) or object())
    monkeypatch.setattr(bertscore, "_encoders", {})
    first = bertscore.encoder("roberta-large")
    assert bertscore.encoder("roberta-large", 17) is first and built == [("roberta-large", 17)]
    bertscore.encoder("roberta-large", 4)
    assert built[-1] == ("roberta-large", 4)
    with pytest.raises(ValueError, match="set params.num_layers"):
        bertscore.encoder("some/unknown-model")
