"""The service's evaluation backend: the local engine with egress guarded and the policy on.

``harden(client, policy)`` turns any ``EvalClient`` with a ``LocalBackend`` into a service
client: the runner gets the policy hook, and every target request goes through
:class:`~.policy.GuardedTransport` (public IPs only, checked on every redirect hop).
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from typing import Any

import httpx

from ..client import EvalClient, LocalBackend
from ..engine.dataset import parse_cases
from ..engine.runner import Runner
from ..engine.seal import compute_response_hash
from ..engine.targets import from_ref
from ..exceptions import RequestError
from ..hashing import keccak_hex
from ..net import FetchError, fetch_artifact
from ..types.case import EvalCase
from ..types.request import EvalRequest, EvalResponse
from .policy import GuardedTransport, UntrustedSuitePolicy

MAX_REDIRECTS = 3


class ServiceRunner(Runner):
    """A Runner whose live targets use a guarded HTTP client."""

    transport_factory: Any = None  # () -> httpx.AsyncBaseTransport (tests inject a mock)

    def _guarded_client(self, timeout_s: float) -> httpx.AsyncClient:
        limits = getattr(self.policy, "limits", None)
        transport = GuardedTransport(
            self.transport_factory() if self.transport_factory else None,
            resolver=getattr(self.policy, "resolver", None),
            max_response_bytes=limits.max_response_bytes if limits else None,
        )
        return httpx.AsyncClient(
            transport=transport,
            timeout=timeout_s,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            trust_env=False,
        )

    async def _invoke_targets(
        self, request: EvalRequest, cases: Sequence[EvalCase], semaphore: asyncio.Semaphore
    ) -> dict[str, tuple[EvalCase, ...]]:
        if request.target is None:
            return {}
        if request.target.kind not in ("http", "a2a", "mcp"):
            raise RequestError(f"{request.target.kind} targets are not allowed on the service")
        target = from_ref(request.target)
        timeout = float(request.target.config.get("timeout_s") or self.config.timeout_s)
        async with self._guarded_client(timeout) as client:
            target._client = client  # type: ignore[attr-defined]

            async def runs_for(case: EvalCase) -> tuple[str, tuple[EvalCase, ...]]:
                out = []
                for _ in range(request.options.runs):
                    async with semaphore:
                        out.append(await target.invoke(case))
                return case.id, tuple(out)

            return dict(await asyncio.gather(*(runs_for(c) for c in cases)))


def harden(client: EvalClient, policy: UntrustedSuitePolicy) -> EvalClient:
    """Install the policy hook and the guarded runner on ``client``'s local backend."""
    backend = client.backend
    if isinstance(backend, LocalBackend):
        old = backend.runner
        if not isinstance(old, ServiceRunner):
            runner = ServiceRunner(
                old.config,
                judge=old.judge,
                policy=policy,
                reader=old.reader,
                storage=old.storage,
                registry=old.registry,
                cache=old.cache,
                clock=old.clock,
            )
            backend.runner = runner
        else:
            old.policy = policy
    return client


async def fetch_dataset(
    ref: str,
    *,
    cap: int,
    resolver: Any = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[EvalCase, ...]:
    """Fetch a public ``https://…[#0x<keccak>]`` dataset (JSON / JSONL) through the guard."""
    url, _, expected = ref.partition("#")
    try:
        if transport is not None:
            async with httpx.AsyncClient(transport=transport, trust_env=False) as http:
                content, _media = await fetch_artifact(
                    url, cap=cap, client=http, resolver=resolver
                )
        else:
            content, _media = await fetch_artifact(url, cap=cap, resolver=resolver)
    except FetchError as exc:
        raise RequestError(f"cannot fetch dataset_ref: {exc}") from exc
    if expected and keccak_hex(content).lower() != expected.lower():
        raise RequestError("dataset_ref hash mismatch")
    import json

    text = content.decode("utf-8", errors="replace")
    try:
        data: Any = json.loads(text)
    except ValueError:
        try:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        except ValueError as exc:
            raise RequestError("dataset_ref is neither JSON nor JSONL") from exc
    return parse_cases(data, source="dataset_ref")


def rebind_request_hash(response: EvalResponse, request_hash: str) -> EvalResponse:
    """Report the caller's original ``request_hash`` (after server-side dataset expansion)."""
    if response.provenance.request_hash == request_hash:
        return response
    prov = dataclasses.replace(response.provenance, request_hash=request_hash)
    response = dataclasses.replace(response, provenance=prov)
    return dataclasses.replace(response, response_hash=compute_response_hash(response))
