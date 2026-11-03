"""Anthropic Messages API judge; structured output via a forced tool call."""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from typing import Any

from ...exceptions import JudgeNonRetryable, JudgeThrottled
from ...types.judge import JudgeConfig
from ...types.trajectory import Usage
from ..base import JudgeRequest, JudgeResponse
from ._common import (
    InFlightLimit,
    classify_http_error,
    rejects_forced_tool,
    rejects_temperature,
    tool_reply,
    with_retries,
)

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
TOOL_NAME = "submit_verdict"


class AnthropicJudge:
    def __init__(self, config: JudgeConfig, *, api_key: str | None = None, client: Any = None):
        self.config = config
        self.base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = (
            api_key or os.environ.get("EVAL_JUDGE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        )
        self._client = client
        self._limit = InFlightLimit(int(config.extra.get("max_in_flight", 8)))
        self.omit_temperature = False  # set when the model rejects the parameter
        self.auto_tool_choice = False  # set when the model cannot be forced to call the tool

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
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "max_tokens": self.config.max_tokens,
        }
        if self.config.temperature is not None and not self.omit_temperature:
            body["temperature"] = self.config.temperature
        if request.output_schema is not None:
            body["tools"] = [
                {
                    "name": TOOL_NAME,
                    "description": "Submit the evaluation verdict.",
                    "input_schema": dict(request.output_schema),
                }
            ]
            body["tool_choice"] = (
                {"type": "auto"} if self.auto_tool_choice else {"type": "tool", "name": TOOL_NAME}
            )
        headers = {"x-api-key": self.api_key or "", "anthropic-version": API_VERSION}

        async def call() -> JudgeResponse:
            import httpx

            start = time.perf_counter()
            try:
                async with self._limit:
                    resp = await self._client_or_new().post(
                        f"{self.base_url}/v1/messages", json=body, headers=headers
                    )
            except httpx.TransportError as exc:
                raise JudgeThrottled(f"transport error: {exc}") from exc
            if resp.status_code >= 400:
                if "temperature" in body and rejects_temperature(resp.text):
                    self.omit_temperature = True  # retry once without it; recorded in provenance
                    body.pop("temperature")
                    return await call()
                forced = body.get("tool_choice", {}).get("type") == "tool"
                if forced and rejects_forced_tool(resp.text):
                    self.auto_tool_choice = True  # retry once, letting the model pick the tool
                    body["tool_choice"] = {"type": "auto"}
                    return await call()
                raise classify_http_error(resp.status_code, resp.text)
            data = resp.json()
            calls, texts = [], []
            for block in data.get("content", []):
                if block.get("type") == "tool_use":
                    calls.append((block.get("name"), block.get("input")))
                elif block.get("type") == "text":
                    texts.append(block.get("text", ""))
            parsed = tool_reply(calls, TOOL_NAME)
            if parsed is None and not texts:
                raise JudgeNonRetryable(f"empty response: {str(data)[:200]}")
            usage = data.get("usage") or {}
            return JudgeResponse(
                text="".join(texts),
                parsed=parsed,
                usage=Usage(
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                ),
                model=data.get("model", self.config.model),
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        return await with_retries(call, attempts=int(self.config.extra.get("retries", 3)))

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError("the Anthropic API has no embeddings endpoint")
