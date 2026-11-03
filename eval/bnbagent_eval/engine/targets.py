"""Targets: the agent under test.

A target turns a case's input into a *run*: the same case with ``output``, ``trajectory`` and
``app_metrics`` filled in. Failures never raise; they are recorded in ``app_metrics.error``.

This module provides ``CallableTarget`` and ``HttpTarget`` (incl. streaming with TTFT); A2A and
MCP targets are in ``engine.agent_targets``. Simulated-user targets wrap another target
(``engine.simulated``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import inspect
import json
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

from ..exceptions import RequestError
from ..types.case import AppMetrics, EvalCase
from ..types.target import TargetRef
from ..types.trajectory import Session

if TYPE_CHECKING:  # httpx is imported lazily to keep `import bnbagent_eval` light
    import httpx


class Target(Protocol):
    async def invoke(self, case: EvalCase) -> EvalCase: ...


_TARGETS: dict[str, Callable[..., Any]] = {}


def register_target(name: str, fn: Callable[..., Any]) -> None:
    """Make an in-process callable available as ``TargetRef(kind="callable", uri=name)``."""
    if ":" in name:
        raise ValueError("registered names must not contain ':' (reserved for import paths)")
    _TARGETS[name] = fn


def unregister_target(name: str) -> None:
    """Remove a name added by :func:`register_target` (unknown names are ignored)."""
    _TARGETS.pop(name, None)


def _load_callable(ref: str) -> Callable[..., Any]:
    if ref in _TARGETS:
        return _TARGETS[ref]
    if ":" not in ref:
        raise RequestError(f"no target registered as {ref!r}")
    module_name, _, attr = ref.partition(":")
    obj: Any = importlib.import_module(module_name)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _merge(
    case: EvalCase, result: Any, latency_ms: float, ttft_ms: float | None = None
) -> EvalCase:
    """Fold a target's return value into ``case``."""
    if isinstance(result, EvalCase):
        run = result
    elif isinstance(result, Mapping) and "output" in result:
        trajectory = result.get("trajectory")
        if isinstance(trajectory, Mapping):
            trajectory = Session.from_dict(trajectory)
        app = result.get("app_metrics")
        if isinstance(app, Mapping):
            app = AppMetrics.from_dict(app)
        run = dataclasses.replace(
            case,
            output=result["output"],
            trajectory=trajectory or case.trajectory,
            app_metrics=app or case.app_metrics,
        )
    else:
        run = dataclasses.replace(case, output=result)
    app = run.app_metrics or AppMetrics()
    if app.latency_ms is None:
        app = dataclasses.replace(app, latency_ms=latency_ms)
    if ttft_ms is not None and app.ttft_ms is None:
        app = dataclasses.replace(app, ttft_ms=ttft_ms)
    return dataclasses.replace(run, app_metrics=app)


def _failed(case: EvalCase, exc: BaseException, latency_ms: float) -> EvalCase:
    message = (
        "timeout" if isinstance(exc, asyncio.TimeoutError) else f"{type(exc).__name__}: {exc}"
    )
    return dataclasses.replace(
        case, output=None, app_metrics=AppMetrics(latency_ms=latency_ms, error=message)
    )


class CallableTarget:
    """Calls ``fn(input)`` — or ``fn(case)`` when ``pass_case=True`` — sync or async.

    ``fn`` may return the output, a mapping with ``output`` (+ ``trajectory``, ``app_metrics``),
    or a full :class:`EvalCase`.
    """

    def __init__(
        self, fn: Callable[..., Any], *, timeout_s: float | None = None, pass_case: bool = False
    ):
        self.fn = fn
        self.timeout_s = timeout_s
        self.pass_case = pass_case

    async def invoke(self, case: EvalCase) -> EvalCase:
        start = time.perf_counter()
        try:
            arg = case if self.pass_case else case.input
            if inspect.iscoroutinefunction(self.fn):
                coro = self.fn(arg)
            else:
                coro = asyncio.to_thread(self.fn, arg)
            result = await asyncio.wait_for(coro, self.timeout_s)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, self.timeout_s)
        except Exception as exc:  # noqa: BLE001 - failures are recorded, not raised
            return _failed(case, exc, (time.perf_counter() - start) * 1000)
        return _merge(case, result, (time.perf_counter() - start) * 1000)


def _get_path(data: Any, path: str) -> Any:
    for part in path.split(".") if path else []:
        if isinstance(data, Mapping):
            data = data[part]
        elif isinstance(data, list):
            data = data[int(part)]
        else:
            raise KeyError(path)
    return data


class HttpTarget:
    """POSTs ``{input_field: case.input}`` to ``url``; reads ``output_path`` from the JSON reply.

    With ``stream=True`` the response body is consumed incrementally (plain text chunks, or
    Server-Sent Events with ``stream_format="sse"``) and the time to first token is recorded.
    """

    def __init__(
        self,
        url: str,
        *,
        method: str = "POST",
        headers: Mapping[str, str] | None = None,
        timeout_s: float = 60.0,
        input_field: str = "input",
        output_path: str = "output",
        stream: bool = False,
        stream_format: str = "text",
        client: httpx.AsyncClient | None = None,
    ):
        self.url = url
        self.method = method
        self.headers = dict(headers or {})
        self.timeout_s = timeout_s
        self.input_field = input_field
        self.output_path = output_path
        self.stream = stream
        self.stream_format = stream_format
        self._client = client

    def _body(self, case: EvalCase) -> dict[str, Any]:
        body: dict[str, Any] = {self.input_field: case.to_dict()["input"]}
        if case.conversation:
            body["conversation"] = [m.to_dict() for m in case.conversation]
        return body

    async def invoke(self, case: EvalCase) -> EvalCase:
        if self._client is None:
            import httpx

            client = httpx.AsyncClient(timeout=self.timeout_s)
        else:
            client = self._client
        start = time.perf_counter()
        try:
            if self.stream:
                return await self._invoke_stream(client, case, start)
            resp = await client.request(
                self.method, self.url, json=self._body(case), headers=self.headers
            )
            resp.raise_for_status()
            payload = resp.json()
            latency = (time.perf_counter() - start) * 1000
            result: dict[str, Any] = {"output": _get_path(payload, self.output_path)}
            if isinstance(payload, Mapping):
                for key in ("trajectory", "app_metrics"):
                    if key in payload:
                        result[key] = payload[key]
            return _merge(case, result, latency)
        except Exception as exc:  # noqa: BLE001
            return _failed(case, exc, (time.perf_counter() - start) * 1000)
        finally:
            if self._client is None:
                await client.aclose()

    async def _invoke_stream(
        self, client: httpx.AsyncClient, case: EvalCase, start: float
    ) -> EvalCase:
        parts: list[str] = []
        ttft = None
        async with client.stream(
            self.method, self.url, json=self._body(case), headers=self.headers
        ) as resp:
            resp.raise_for_status()
            if self.stream_format == "sse":
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        text = _get_path(chunk, self.output_path) if self.output_path else chunk
                    except (ValueError, KeyError, IndexError, TypeError):
                        text = data
                    if text:
                        ttft = ttft if ttft is not None else (time.perf_counter() - start) * 1000
                        parts.append(str(text))
            else:
                async for chunk in resp.aiter_text():
                    if chunk:
                        ttft = ttft if ttft is not None else (time.perf_counter() - start) * 1000
                        parts.append(chunk)
        return _merge(case, "".join(parts), (time.perf_counter() - start) * 1000, ttft)


def from_ref(ref: TargetRef, *, user_judge: Any = None) -> Target:
    """Build a live target from a serializable :class:`TargetRef`.

    ``ref.simulated_user`` (``persona``, ``scenario``, ``max_turns``, ``sub_intent``) wraps the
    target in a :class:`SimulatedUserTarget` driven by ``user_judge``.
    """
    if ref.simulated_user is not None:
        from .simulated import SimulatedUserTarget

        if user_judge is None:
            raise RequestError("simulated-user targets need a judge (configure one)")
        su = dict(ref.simulated_user)
        return SimulatedUserTarget(
            from_ref(dataclasses.replace(ref, simulated_user=None)),
            su.get("persona"),
            su.get("scenario"),
            user_judge,
            int(su.get("max_turns", 5)),
            sub_intent=su.get("sub_intent"),
        )
    cfg = dict(ref.config)
    if ref.kind == "callable":
        if not ref.uri:
            raise RequestError("callable targets need uri = '<registered name>' or 'module:attr'")
        return CallableTarget(
            _load_callable(ref.uri),
            timeout_s=cfg.get("timeout_s"),
            pass_case=bool(cfg.get("pass_case", False)),
        )
    if ref.kind == "http":
        if not ref.uri or not ref.uri.startswith(("http://", "https://")):
            raise RequestError("http targets need an http(s) uri")
        allowed = {
            "method",
            "headers",
            "timeout_s",
            "input_field",
            "output_path",
            "stream",
            "stream_format",
        }
        unknown = set(cfg) - allowed
        if unknown:
            raise RequestError(f"unknown http target config: {sorted(unknown)}")
        return HttpTarget(ref.uri, **cfg)
    common = {k: cfg[k] for k in ("headers", "timeout_s") if k in cfg}
    if ref.kind == "a2a":
        if not ref.uri or not ref.uri.startswith(("http://", "https://")):
            raise RequestError("a2a targets need an http(s) uri (endpoint or agent-card URL)")
        unknown = set(cfg) - {"headers", "timeout_s", "poll_interval_s"}
        if unknown:
            raise RequestError(f"unknown a2a target config: {sorted(unknown)}")
        from .agent_targets import A2ATarget

        return A2ATarget(ref.uri, poll_interval_s=cfg.get("poll_interval_s", 1.0), **common)
    if ref.kind == "mcp":
        if not ref.uri or not ref.uri.startswith(("http://", "https://")) or "tool" not in cfg:
            raise RequestError("mcp targets need an http(s) uri and config.tool")
        unknown = set(cfg) - {"headers", "timeout_s", "tool", "input_argument", "arguments"}
        if unknown:
            raise RequestError(f"unknown mcp target config: {sorted(unknown)}")
        from .agent_targets import McpTarget

        return McpTarget(
            ref.uri,
            tool=cfg["tool"],
            input_argument=cfg.get("input_argument", "input"),
            arguments=cfg.get("arguments"),
            **common,
        )
    raise RequestError(
        f"{ref.kind} targets are not supported yet by from_ref; use resolve_agent_target"
    )


async def resolve_agent_target(ref: TargetRef, registry: Any) -> Target:
    """Resolve an ``erc8004`` target: the agent's registration → a service endpoint → a target.

    ``uri`` is ``<chain_id>:<registry>:<agent_id>`` (or just ``<agent_id>``); ``config.service``
    selects a service by name (default: the first A2A, then MCP, then any http(s) endpoint).
    MCP services need ``config.tool``.
    """
    if registry is None:
        raise RequestError("erc8004 targets need a RegistryPort (EvalClient(ports=...))")
    parts = (ref.uri or "").split(":")
    try:
        agent_id = int(parts[-1])
    except ValueError as exc:
        raise RequestError("erc8004 uri must end with the agent id") from exc
    if len(parts) == 3:
        chain_id, address = int(parts[0]), parts[1]
        if chain_id != registry.chain_id or address.lower() != registry.registry_address.lower():
            raise RequestError("erc8004 uri names a different chain or registry than the port")
    doc = await registry.read_registration(agent_id)
    if not doc:
        raise RequestError(f"agent {agent_id} has no registration")
    services = [s for s in doc.get("services", []) if isinstance(s, Mapping)]
    cfg = dict(ref.config)
    wanted = cfg.pop("service", None)
    if wanted:
        chosen = next((s for s in services if s.get("name") == wanted), None)
    else:
        by_name = {str(s.get("name", "")).lower(): s for s in services}
        chosen = (
            by_name.get("a2a")
            or by_name.get("mcp")
            or next((s for s in services if str(s.get("endpoint", "")).startswith("http")), None)
        )
    if chosen is None:
        raise RequestError(f"agent {agent_id} has no usable service (wanted: {wanted or 'any'})")
    name, endpoint = str(chosen.get("name", "")).lower(), str(chosen.get("endpoint", ""))
    kind = "a2a" if name == "a2a" else "mcp" if name == "mcp" else "http"
    return from_ref(TargetRef(kind=kind, uri=endpoint, config=cfg))
