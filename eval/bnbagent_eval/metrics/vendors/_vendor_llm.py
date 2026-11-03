"""Shared helpers for the RAGAS / DeepEval bridges: judge-backed vendor models and case views.

Models
- RAGAS: ``bedrock`` -> langchain-aws ``ChatBedrockConverse`` + ``BedrockEmbeddings``;
  ``openai`` -> langchain-openai ``ChatOpenAI`` + ``OpenAIEmbeddings`` (wrapped with RAGAS'
  ``LangchainLLMWrapper``). Any other provider (``fake``, ``anthropic``, registered providers)
  goes through :func:`ragas_judge_llm`, an adapter over our ``JudgeModel``.
- DeepEval: always :func:`deepeval_judge_model`, a ``DeepEvalBaseLLM`` over our ``JudgeModel``,
  so the suite's pinned judge (and FakeJudge in tests) is honoured for every provider.

Vendor libraries are imported lazily; a missing one raises ``MissingExtraError`` naming the extra.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import os
from collections.abc import Mapping
from typing import Any

from ..._optional import require
from ...adapters.traces.render import session_messages
from ...exceptions import SuiteError
from ...judges.base import JudgeModel, JudgeRequest
from ...judges.structured import extract_object
from ...types.case import EvalCase, content_text
from ...types.judge import embedding_model
from ...types.result import ErrorInfo
from ...types.trajectory import AgentStep, LlmStep, ToolCall, ToolStep
from ..base import MetricContext, Outcome
from ..text._common import as_text

# Providers such as Bedrock Converse reject an empty system prompt; vendor prompts are all-user.
VENDOR_SYSTEM = "You are an evaluation judge. Follow the instructions in the user message exactly."


# --------------------------------------------------------------------------- judge access
def judge_of(ctx: MetricContext, metric_id: str) -> JudgeModel:
    if ctx.judge is None:
        raise SuiteError(f"{metric_id}: vendor method {ctx.method.name!r} needs a judge model")
    return ctx.judge


def run_sync(coro: Any) -> Any:
    """Run ``coro`` to completion from sync code, also when an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def judge_provenance(judge: JudgeModel) -> dict[str, Any]:
    cfg = judge.config
    return {
        "judge": {
            "provider": cfg.provider,
            "model": cfg.model,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
        },
        "samples": 1,
    }


# --------------------------------------------------------------------------- RAGAS models
def ragas_models(judge: JudgeModel, *, metric_id: str = "") -> tuple[Any, Any]:
    """``(llm, embeddings)`` RAGAS wrappers built from ``judge.config``."""
    cfg = judge.config
    provider = cfg.provider
    if provider in ("bedrock", "openai"):
        llms = require("ragas.llms.base", "ragas")
        embs = require("ragas.embeddings.base", "ragas")
        if provider == "bedrock":
            lc = require("langchain_aws", "ragas")
            kwargs: dict[str, Any] = {"model": cfg.model, "max_tokens": cfg.max_tokens}
            if cfg.temperature is not None:
                kwargs["temperature"] = cfg.temperature
            if cfg.region:
                kwargs["region_name"] = cfg.region
            if cfg.base_url:
                kwargs["endpoint_url"] = cfg.base_url
            chat = lc.ChatBedrockConverse(**kwargs)
            emb_kwargs: dict[str, Any] = {"model_id": embedding_model(cfg)}
            if cfg.region:
                emb_kwargs["region_name"] = cfg.region
            embeddings = lc.BedrockEmbeddings(**emb_kwargs)
        else:
            lc = require("langchain_openai", "ragas")
            kwargs = {"model": cfg.model, "max_tokens": cfg.max_tokens}
            if cfg.temperature is not None:
                kwargs["temperature"] = cfg.temperature
            if cfg.base_url:
                kwargs["base_url"] = cfg.base_url
            # the same key as our OpenAI judge (EVAL_JUDGE_API_KEY, else OPENAI_API_KEY)
            api_key = os.environ.get("EVAL_JUDGE_API_KEY")
            if api_key:
                kwargs["api_key"] = api_key
            chat = lc.ChatOpenAI(**kwargs)
            emb_kwargs = {"model": embedding_model(cfg)}
            if cfg.base_url:
                emb_kwargs["base_url"] = cfg.base_url
            if api_key:
                emb_kwargs["api_key"] = api_key
            embeddings = lc.OpenAIEmbeddings(**emb_kwargs)
        return llms.LangchainLLMWrapper(chat), embs.LangchainEmbeddingsWrapper(embeddings)
    return ragas_judge_llm(judge, metric_id), ragas_judge_embeddings(judge)


def ragas_judge_llm(judge: JudgeModel, metric_id: str = "") -> Any:
    """A RAGAS ``BaseRagasLLM`` that sends each prompt to our ``JudgeModel``."""
    base = require("ragas.llms.base", "ragas")
    outputs = require("langchain_core.outputs", "ragas")

    class JudgeRagasLLM(base.BaseRagasLLM):  # type: ignore[misc, name-defined]
        def __init__(self) -> None:
            super().__init__()
            self.judge = judge

        async def _text(self, prompt: Any) -> str:
            text = prompt.to_string() if hasattr(prompt, "to_string") else str(prompt)
            request = JudgeRequest(
                system=VENDOR_SYSTEM,
                user=text,
                metadata={"metric_id": metric_id, "vendor": "ragas"},
            )
            return (await self.judge.complete(request)).text

        async def agenerate_text(  # noqa: D401 - RAGAS signature
            self, prompt: Any, n: int = 1, temperature: Any = None, stop: Any = None, **_: Any
        ) -> Any:
            texts = [await self._text(prompt) for _ in range(max(1, n))]
            return outputs.LLMResult(generations=[[outputs.Generation(text=t) for t in texts]])

        def generate_text(
            self, prompt: Any, n: int = 1, temperature: Any = None, stop: Any = None, **kw: Any
        ) -> Any:
            return run_sync(self.agenerate_text(prompt, n, temperature, stop, **kw))

        def is_finished(self, response: Any) -> bool:
            return True

    return JudgeRagasLLM()


def ragas_judge_embeddings(judge: JudgeModel) -> Any:
    """A RAGAS ``BaseRagasEmbeddings`` over ``JudgeModel.embed``."""
    base = require("ragas.embeddings.base", "ragas")

    class JudgeRagasEmbeddings(base.BaseRagasEmbeddings):  # type: ignore[misc, name-defined]
        def __init__(self) -> None:
            super().__init__()
            self.judge = judge

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [list(v) for v in await self.judge.embed(list(texts))]

        async def aembed_query(self, text: str) -> list[float]:
            return (await self.aembed_documents([text]))[0]

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return run_sync(self.aembed_documents(texts))

        def embed_query(self, text: str) -> list[float]:
            return run_sync(self.aembed_query(text))

    return JudgeRagasEmbeddings()


# --------------------------------------------------------------------------- DeepEval model
def deepeval_judge_model(
    judge: JudgeModel | None, metric_id: str = "", vendor_metric: str = ""
) -> Any:
    """A ``DeepEvalBaseLLM`` that sends each prompt to our ``JudgeModel``.

    With a pydantic ``schema`` the request carries its JSON schema (structured output) and the
    reply is validated into the schema; otherwise the raw text is returned. With ``judge=None``
    (deterministic DeepEval metrics) any model call raises ``SuiteError``.
    """
    models = require("deepeval.models", "deepeval")

    class JudgeDeepEvalModel(models.DeepEvalBaseLLM):  # type: ignore[misc, name-defined]
        def __init__(self) -> None:
            self.judge = judge
            super().__init__(judge.config.model if judge is not None else "none")

        def load_model(self, *args: Any, **kwargs: Any) -> Any:
            return self.judge

        def get_model_name(self, *args: Any, **kwargs: Any) -> str:
            if self.judge is None:
                return "none"
            return f"{self.judge.config.provider}:{self.judge.config.model}"

        async def a_generate(self, prompt: str, schema: Any = None, **_: Any) -> Any:
            if self.judge is None:
                raise SuiteError(f"{metric_id}: deepeval {vendor_metric} needs a judge model")
            output_schema = schema.model_json_schema() if schema is not None else None
            request = JudgeRequest(
                system=VENDOR_SYSTEM,
                user=prompt,
                output_schema=output_schema,
                metadata={
                    "metric_id": metric_id,
                    "vendor": "deepeval",
                    "vendor_metric": vendor_metric,
                },
            )
            response = await self.judge.complete(request)
            if schema is None:
                return response.text
            data = dict(response.parsed) if response.parsed is not None else None
            if data is None:
                data = extract_object(response.text)
            return schema.model_validate(data)

        def generate(self, prompt: str, schema: Any = None, **kw: Any) -> Any:
            return run_sync(self.a_generate(prompt, schema, **kw))

    return JudgeDeepEvalModel()


# --------------------------------------------------------------------------- case views
def input_text(case: EvalCase) -> str:
    if isinstance(case.input, Mapping):
        for key in ("text", "user_input", "input", "query", "question"):
            if isinstance(case.input.get(key), str):
                return case.input[key]
    return content_text(case.input) or as_text(case.input)


def output_text(case: EvalCase) -> str:
    text = content_text(case.output)
    if text is None and case.trajectory is not None:
        text = case.trajectory.final_response()
    return text or ""


def reference_text(expected: Any) -> str | None:
    """The reference answer / expected outcome text from ``expected``."""
    if expected is None:
        return None
    if isinstance(expected, Mapping):
        for key in ("reference", "expected_outcome", "outcome", "goal", "answer", "text"):
            value = expected.get(key)
            if isinstance(value, str):
                return value
        if "tool_calls" in expected and len(expected) == 1:
            return None
    if isinstance(expected, (list, tuple)) and all(isinstance(x, str) for x in expected):
        return "\n".join(expected)
    return as_text(expected)


def context_texts(case: EvalCase) -> list[str]:
    return [c.text for c in case.contexts]


def case_turns(case: EvalCase) -> list[dict[str, Any]]:
    """Ordered turns as ``{"role", "content", "tool_calls", "tool_call_id"}`` dicts.

    Source: ``case.conversation``; else the trajectory (agent prompts / responses, LLM tool
    calls and tool steps); else ``input`` / ``output`` as one user / assistant exchange.
    System messages are dropped.
    """
    turns: list[dict[str, Any]] = []
    if case.conversation:
        for m in case.conversation:
            if m.role == "system":
                continue
            turns.append(
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": list(m.tool_calls),
                    "tool_call_id": m.tool_call_id,
                }
            )
        return turns
    if case.trajectory is not None:
        session = case.trajectory
        has_tool_steps = bool(session.tool_steps())
        pending: str | None = None
        for step in session.steps():
            if isinstance(step, AgentStep) and step.parent_id is None:
                if pending:
                    turns.append(_turn("assistant", pending))
                    pending = None
                if step.user_prompt:
                    turns.append(_turn("user", step.user_prompt))
                pending = step.response
            elif isinstance(step, LlmStep) and step.tool_calls and not has_tool_steps:
                turns.append(_turn("assistant", step.response or "", list(step.tool_calls)))
            elif isinstance(step, ToolStep):
                turns.append(_turn("assistant", "", [step.call]))
                result = step.result
                if result is not None:
                    content = result.error if result.error else as_text(result.content)
                    turns.append(_turn("tool", content, call_id=step.call.id))
        if pending:
            turns.append(_turn("assistant", pending))
        if not any(t["role"] == "user" for t in turns) and case.input is not None:
            turns.insert(0, _turn("user", input_text(case)))
        if turns and not any(t["role"] == "assistant" and t["content"] for t in turns):
            final = output_text(case)
            if final:
                turns.append(_turn("assistant", final))
        if turns:
            return turns
        derived = session_messages(session)
        if derived:
            return [_turn(m.role, m.content) for m in derived]
    if case.input is not None:
        turns.append(_turn("user", input_text(case)))
    if case.output is not None:
        turns.append(_turn("assistant", output_text(case)))
    return turns


def _turn(
    role: str,
    content: str,
    tool_calls: list[ToolCall] | None = None,
    *,
    call_id: str | None = None,
) -> dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "tool_calls": tool_calls or [],
        "tool_call_id": call_id,
    }


def tool_call_outputs(case: EvalCase) -> list[tuple[ToolCall, Any]]:
    """The trajectory's tool calls with their outputs (``None`` when unknown)."""
    if case.trajectory is None:
        return []
    steps = case.trajectory.tool_steps()
    if steps:
        return [
            (s.call, (s.result.error or s.result.content) if s.result is not None else None)
            for s in steps
        ]
    return [(c, None) for c in case.trajectory.tool_calls()]


# --------------------------------------------------------------------------- results
def vendor_outcome(
    value: Any,
    *,
    vendor: str,
    vendor_metric: str,
    reason: str | None = None,
    provenance: Mapping[str, Any] | None = None,
    **details: Any,
) -> Outcome:
    """Map a vendor score to an :class:`Outcome`; ``None`` / NaN become an error."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return Outcome(
            None,
            status="error",
            error=ErrorInfo("vendor_nan", f"{vendor} {vendor_metric} returned no score (NaN)"),
            details={"vendor": vendor, "vendor_metric": vendor_metric},
        )
    score = float(value)
    if math.isnan(score):
        return vendor_outcome(None, vendor=vendor, vendor_metric=vendor_metric)
    return Outcome(
        score,
        reason=reason,
        details={"raw": score, "vendor": vendor, "vendor_metric": vendor_metric, **details},
        provenance=dict(provenance or {}),
    )
