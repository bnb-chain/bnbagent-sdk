"""Third-party methods attached to canonical metrics, and the providers behind them.

Each module registers methods with ``registry.register_method``; the provider's library is
imported lazily inside the handlers, so importing these modules is cheap and needs no extra.
Every method is used like a native one (``method: <name>`` in a suite) and is installed with one
command, ``pip install 'bnbagent-eval[<extra>]'``. The metric's native method stays the default.
"""

from __future__ import annotations

from typing import NamedTuple


class Provider(NamedTuple):
    name: str
    url: str
    role: str  # "evaluator": the provider computes the score; "model": our code, their model
    needs: str


# Keyed by the pip extra. Methods whose ``MethodSpec.extra`` is listed here are third-party.
PROVIDERS: dict[str, Provider] = {
    "ragas": Provider(
        "RAGAS",
        "https://github.com/explodinggradients/ragas",
        "evaluator",
        "the suite's judge (its LLM and embeddings are built from the judge config)",
    ),
    "deepeval": Provider(
        "DeepEval",
        "https://github.com/confident-ai/deepeval",
        "evaluator",
        "the suite's judge (wrapped as a DeepEval model)",
    ),
    "fmeval": Provider(
        "FMEval",
        "https://github.com/aws/fmeval",
        "evaluator",
        "nothing else (local algorithms; toxicity downloads a model)",
    ),
    "agentcore": Provider(
        "Amazon Bedrock AgentCore Evaluations",
        "https://docs.aws.amazon.com/bedrock-agentcore/",
        "evaluator",
        "AWS credentials and a region with AgentCore; the case trajectory (a cloud service)",
    ),
    "semantic": Provider(
        "Hugging Face transformers",
        "https://github.com/huggingface/transformers",
        "model",
        "downloads the encoder weights (BERTScore itself is our implementation)",
    ),
    "toxicity": Provider(
        "Detoxify",
        "https://github.com/unitaryai/detoxify",
        "model",
        "downloads the classifier weights",
    ),
    "fluency": Provider(
        "LanguageTool",
        "https://languagetool.org",
        "model",
        "Java (starts a local LanguageTool server)",
    ),
    "embeddings": Provider(
        "gensim word vectors",
        "https://radimrehurek.com/gensim/",
        "model",
        "a local GloVe or fastText vectors file (params.vectors_path)",
    ),
}
