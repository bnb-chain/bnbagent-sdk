"""A2A and MCP targets, over plain JSON-RPC with httpx.

- ``A2ATarget``: A2A ``message/send``; a ``message`` reply is final, a ``task`` reply is polled
  with ``tasks/get`` until it reaches a terminal state. An agent-card URL is resolved to the
  agent's ``url`` first.
- ``McpTarget``: MCP Streamable HTTP: ``initialize`` → ``notifications/initialized`` →
  ``tools/call``; replies may be JSON or Server-Sent Events. The call is recorded as a ToolStep.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

from ..types.case import EvalCase
from ..types.trajectory import Session, ToolCall, ToolResult, ToolStep, Trace
from .targets import _failed, _merge

A2A_TERMINAL = {"completed", "failed", "canceled", "rejected", "input-required", "auth-required"}
MCP_PROTOCOL_VERSION = "2025-06-18"


class TargetFailure(Exception):
    """The agent replied, but with an error."""


def _parts_output(parts: list[Mapping[str, Any]]) -> Any:
    texts = [p.get("text", "") for p in parts if p.get("kind", p.get("type")) == "text"]
    data = [p.get("data") for p in parts if p.get("kind", p.get("type")) == "data"]
    if texts:
        return "".join(texts)
    if len(data) == 1:
        return data[0]
    return data or None


def _input_parts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        return [{"kind": "data", "data": dict(value)}]
    return [{"kind": "text", "text": "" if value is None else str(value)}]


class _JsonRpcClient:
    def __init__(self, client: Any, timeout_s: float):
        self._client = client
        self._owned = client is None
        self.timeout_s = timeout_s

    async def __aenter__(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()

    @property
    def http(self):
        return self._client


class A2ATarget:
    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_s: float = 120.0,
        poll_interval_s: float = 1.0,
        client: Any = None,
    ):
        self.url = url
        self.headers = dict(headers or {})
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._client = client

    async def _rpc(self, rpc: _JsonRpcClient, url: str, method: str, params: dict) -> Any:
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
        resp = await rpc.http.post(url, json=body, headers=self.headers)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            err = data["error"]
            raise TargetFailure(f"JSON-RPC error {err.get('code')}: {err.get('message')}")
        return data.get("result")

    async def _endpoint(self, rpc: _JsonRpcClient) -> str:
        if not self.url.rstrip("/").endswith("agent-card.json"):
            return self.url
        resp = await rpc.http.get(self.url, headers=self.headers)
        resp.raise_for_status()
        card = resp.json()
        if not card.get("url"):
            raise TargetFailure("agent card has no 'url'")
        return card["url"]

    async def invoke(self, case: EvalCase) -> EvalCase:
        start = time.perf_counter()
        try:
            async with _JsonRpcClient(self._client, self.timeout_s) as rpc:
                endpoint = await self._endpoint(rpc)
                message = {
                    "role": "user",
                    "kind": "message",
                    "messageId": str(uuid.uuid4()),
                    "parts": _input_parts(case.to_dict()["input"]),
                }
                result = await asyncio.wait_for(self._run(rpc, endpoint, message), self.timeout_s)
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            return _failed(case, exc, (time.perf_counter() - start) * 1000)
        return _merge(case, {"output": result}, (time.perf_counter() - start) * 1000)

    async def _run(self, rpc: _JsonRpcClient, endpoint: str, message: dict) -> Any:
        result = await self._rpc(rpc, endpoint, "message/send", {"message": message})
        while True:
            if not isinstance(result, Mapping):
                raise TargetFailure(f"unexpected A2A result: {result!r}")
            if result.get("kind") == "message" or ("parts" in result and "status" not in result):
                return _parts_output(result.get("parts", []))
            state = (result.get("status") or {}).get("state")
            if state in A2A_TERMINAL:
                if state != "completed":
                    raise TargetFailure(f"A2A task ended in state {state!r}")
                parts = [p for a in result.get("artifacts") or [] for p in a.get("parts", [])]
                if not parts:
                    parts = ((result.get("status") or {}).get("message") or {}).get("parts", [])
                return _parts_output(parts)
            await asyncio.sleep(self.poll_interval_s)
            result = await self._rpc(rpc, endpoint, "tasks/get", {"id": result["id"]})


def _sse_or_json(resp: Any) -> dict[str, Any]:
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                if "result" in payload or "error" in payload:
                    return payload
        raise TargetFailure("no JSON-RPC response in event stream")
    return resp.json()


class McpTarget:
    def __init__(
        self,
        url: str,
        *,
        tool: str,
        input_argument: str = "input",
        arguments: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_s: float = 120.0,
        client: Any = None,
    ):
        self.url = url
        self.tool = tool
        self.input_argument = input_argument
        self.arguments = dict(arguments or {})
        self.headers = dict(headers or {})
        self.timeout_s = timeout_s
        self._client = client

    async def _post(self, rpc: _JsonRpcClient, body: dict, session: str | None) -> Any:
        headers = {"Accept": "application/json, text/event-stream", **self.headers}
        if session:
            headers["Mcp-Session-Id"] = session
        resp = await rpc.http.post(self.url, json=body, headers=headers)
        resp.raise_for_status()
        return resp

    async def invoke(self, case: EvalCase) -> EvalCase:
        start = time.perf_counter()
        input_value = case.to_dict()["input"]
        arguments = {**self.arguments, self.input_argument: input_value}
        try:
            async with _JsonRpcClient(self._client, self.timeout_s) as rpc:
                init = await self._post(
                    rpc,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": "bnbagent-eval", "version": "1"},
                        },
                    },
                    None,
                )
                session = init.headers.get("mcp-session-id")
                await self._post(
                    rpc, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session
                )
                call_start = time.perf_counter()
                resp = await self._post(
                    rpc,
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": self.tool, "arguments": arguments},
                    },
                    session,
                )
                payload = _sse_or_json(resp)
        except Exception as exc:  # noqa: BLE001
            return _failed(case, exc, (time.perf_counter() - start) * 1000)
        latency = (time.perf_counter() - start) * 1000
        if "error" in payload:
            err = payload["error"]
            return _failed(
                case, TargetFailure(f"MCP error {err.get('code')}: {err.get('message')}"), latency
            )
        result = payload.get("result") or {}
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        output = "".join(texts) if texts else result.get("structuredContent")
        error = "tool returned isError" if result.get("isError") else None
        step = ToolStep(
            id="mcp-1",
            call=ToolCall(name=self.tool, arguments=arguments, id="2"),
            result=ToolResult(content=output, error=error, call_id="2"),
            start_ms=(call_start - start) * 1000,
            end_ms=latency,
        )
        session_obj = Session(id=f"mcp-{case.id}", traces=(Trace(id="t1", steps=(step,)),))
        run = _merge(
            case, {"output": None if error else output, "trajectory": session_obj}, latency
        )
        if error:
            return _failed(run, TargetFailure(error), latency)
        return run
