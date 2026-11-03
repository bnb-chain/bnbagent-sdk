"""Amazon Bedrock Converse judge (``[bedrock]`` extra); structured output via a forced tool."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from typing import Any

from ..._optional import require
from ...exceptions import JudgeNonRetryable, JudgeThrottled
from ...types.judge import JudgeConfig, embedding_model
from ...types.trajectory import Usage
from ..base import JudgeRequest, JudgeResponse
from ._common import (
    InFlightLimit,
    rejects_forced_tool,
    rejects_temperature,
    tool_reply,
    with_retries,
)

TOOL_NAME = "submit_verdict"
_THROTTLE_CODES = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelNotReadyException",
    "TooManyRequestsException",
}


class BedrockJudge:
    def __init__(self, config: JudgeConfig, *, client: Any = None):
        self.config = config
        self._client = client
        self._limit = InFlightLimit(int(config.extra.get("max_in_flight", 8)))
        self.omit_temperature = False
        self.auto_tool_choice = False  # set when the model cannot be forced to call the tool

    def _client_or_new(self):
        if self._client is None:
            boto3 = require("boto3", "bedrock")
            self._client = boto3.client("bedrock-runtime", region_name=self.config.region)
        return self._client

    def _converse(self, request: JudgeRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "modelId": self.config.model,
            "system": [{"text": request.system}],
            "messages": [{"role": "user", "content": [{"text": request.user}]}],
            "inferenceConfig": {"maxTokens": self.config.max_tokens},
        }
        if self.config.temperature is not None and not self.omit_temperature:
            kwargs["inferenceConfig"]["temperature"] = self.config.temperature
        if request.output_schema is not None:
            kwargs["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": TOOL_NAME,
                            "description": "Submit the evaluation verdict.",
                            "inputSchema": {"json": dict(request.output_schema)},
                        }
                    }
                ],
                "toolChoice": {"auto": {}}
                if self.auto_tool_choice
                else {"tool": {"name": TOOL_NAME}},
            }
        return self._client_or_new().converse(**kwargs)

    async def complete(self, request: JudgeRequest) -> JudgeResponse:
        async def call() -> JudgeResponse:
            omit_at_start = self.omit_temperature  # the request below is built from this state
            auto_at_start = self.auto_tool_choice
            start = time.perf_counter()
            try:
                async with self._limit:
                    data = await asyncio.to_thread(self._converse, request)
            except Exception as exc:  # botocore ClientError without importing botocore
                code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
                message = str(exc)
                sent = self.config.temperature is not None and not omit_at_start
                if sent and rejects_temperature(message):
                    self.omit_temperature = True  # retry once without it; recorded in provenance
                    return await call()
                forced = request.output_schema is not None and not auto_at_start
                if forced and rejects_forced_tool(message):
                    self.auto_tool_choice = True  # retry once, letting the model pick the tool
                    return await call()
                if code in _THROTTLE_CODES:
                    raise JudgeThrottled(message) from exc
                if "too long" in message.lower() or "context" in message.lower():
                    raise JudgeNonRetryable(f"context overflow: {message}") from exc
                raise JudgeNonRetryable(message) from exc
            calls, texts = [], []
            for block in data.get("output", {}).get("message", {}).get("content", []):
                if "toolUse" in block:
                    payload = block["toolUse"].get("input")
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    calls.append((block["toolUse"].get("name"), payload))
                elif "text" in block:
                    texts.append(block["text"])
            parsed = tool_reply(calls, TOOL_NAME)
            usage = data.get("usage") or {}
            return JudgeResponse(
                text="".join(texts),
                parsed=parsed,
                usage=Usage(
                    input_tokens=usage.get("inputTokens"), output_tokens=usage.get("outputTokens")
                ),
                model=self.config.model,
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        return await with_retries(call, attempts=int(self.config.extra.get("retries", 3)))

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        model = embedding_model(self.config)
        client = self._client_or_new()

        def one(text: str) -> list[float]:
            resp = client.invoke_model(modelId=model, body=json.dumps({"inputText": text}))
            return json.loads(resp["body"].read())["embedding"]

        return [await asyncio.to_thread(one, t) for t in texts]
