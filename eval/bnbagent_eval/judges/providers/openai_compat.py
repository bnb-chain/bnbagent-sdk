"""OpenAI-compatible chat-completions judge (OpenAI, vLLM, Ollama, OpenRouter, ...)."""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from typing import Any

from ...exceptions import JudgeNonRetryable, JudgeThrottled
from ...types.judge import JudgeConfig, embedding_model
from ...types.trajectory import Usage
from ..base import JudgeRequest, JudgeResponse
from ._common import InFlightLimit, classify_http_error, rejects_temperature, with_retries

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatJudge:
    def __init__(self, config: JudgeConfig, *, api_key: str | None = None, client: Any = None):
        self.config = config
        self.base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = (
            api_key or os.environ.get("EVAL_JUDGE_API_KEY") or os.environ.get("OPENAI_API_KEY")
        )
        self._client = client
        self._limit = InFlightLimit(int(config.extra.get("max_in_flight", 8)))
        self.omit_temperature = False  # set when the model rejects the parameter

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _client_or_new(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                timeout=float(self.config.extra.get("timeout_s", 120))
            )
        return self._client

    async def complete(self, request: JudgeRequest) -> JudgeResponse:
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": self.config.max_tokens,
        }
        if self.config.temperature is not None and not self.omit_temperature:
            body["temperature"] = self.config.temperature
        mode = self.config.extra.get("response_format", "json_schema")
        if request.output_schema is not None and mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "verdict",
                    "schema": dict(request.output_schema),
                    "strict": False,
                },
            }
        elif request.output_schema is not None and mode == "json_object":
            body["response_format"] = {"type": "json_object"}

        async def call() -> JudgeResponse:
            import httpx

            start = time.perf_counter()
            try:
                async with self._limit:
                    resp = await self._client_or_new().post(
                        f"{self.base_url}/chat/completions", json=body, headers=self._headers()
                    )
            except httpx.TransportError as exc:
                raise JudgeThrottled(f"transport error: {exc}") from exc
            if resp.status_code >= 400:
                if "temperature" in body and rejects_temperature(resp.text):
                    self.omit_temperature = True  # retry once without it; recorded in provenance
                    body.pop("temperature")
                    return await call()
                raise classify_http_error(resp.status_code, resp.text)
            data = resp.json()
            try:
                text = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise JudgeNonRetryable(f"unexpected response shape: {str(data)[:200]}") from exc
            usage = data.get("usage") or {}
            return JudgeResponse(
                text=text,
                usage=Usage(
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                ),
                model=data.get("model", self.config.model),
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        return await with_retries(call, attempts=int(self.config.extra.get("retries", 3)))

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        model = embedding_model(self.config)
        resp = await self._client_or_new().post(
            f"{self.base_url}/embeddings",
            json={"model": model, "input": list(texts)},
            headers=self._headers(),
        )
        if resp.status_code >= 400:
            raise classify_http_error(resp.status_code, resp.text)
        return [item["embedding"] for item in resp.json()["data"]]
