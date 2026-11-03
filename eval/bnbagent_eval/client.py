"""EvalClient: the one evaluation call."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from ._optional import available
from .config import EvalConfig
from .engine.compare import Comparison, compare
from .engine.runner import Runner
from .engine.seal import verify_hashes
from .exceptions import (
    AttestationError,
    EvalError,
    PaymentRequired,
    PolicyError,
    RequestError,
    SchemaError,
)
from .hashing import hash_obj
from .judges.base import JudgeConfig, JudgeModel
from .metrics.base import Kind, MethodSpec, MetricSpec
from .metrics.registry import list_specs
from .types.request import EvalRequest, EvalResponse
from .types.suite import Suite


class EvalBackend(Protocol):
    async def evaluate(self, request: EvalRequest) -> EvalResponse: ...

    async def describe_metrics(self) -> list[MetricSpec]: ...


class LocalBackend:
    """Runs the engine in-process."""

    def __init__(
        self,
        config: EvalConfig | None = None,
        *,
        judge: JudgeModel | JudgeConfig | None = None,
        ports: Any = None,
    ):
        if isinstance(judge, JudgeConfig):
            from .judges.providers import create_judge

            judge = create_judge(judge)
        self.runner = Runner(
            config,
            judge=judge,
            reader=getattr(ports, "reader", None),
            storage=getattr(ports, "storage", None),
            registry=getattr(ports, "registry", None),
        )

    async def evaluate(self, request: EvalRequest) -> EvalResponse:
        reader = self.runner.reader
        if reader is not None and any(
            c.trajectory and c.trajectory.tx_steps() for c in request.cases
        ):
            # Fill receipts / decoded calls into TxSteps before scoring (onchain.* metrics).
            import dataclasses

            from .chain.onchain import enrich

            cases = []
            for case in request.cases:
                if case.trajectory is not None and case.trajectory.tx_steps():
                    case = dataclasses.replace(
                        case, trajectory=await enrich(case.trajectory, reader)
                    )
                cases.append(case)
            request = dataclasses.replace(request, cases=tuple(cases))
        return await self.runner.run(request)

    async def describe_metrics(self) -> list[MetricSpec]:
        return list_specs()


class RemoteBackend:
    """Evaluates on an eval service over HTTP.

    Without a payer a 402 raises :class:`PaymentRequired` (``.quote`` is the quote dict,
    ``.challenge`` the full x402 body). With a payer (``PayerPort``: the SDK's synchronous
    ``X402Payer.request``) the payer owns the 402 → sign → retry cycle and ``max_payment``
    caps what it may pay. Every response's hashes, request binding and (with ``[evm]``)
    attestation are verified; a mismatch raises :class:`AttestationError`.
    """

    def __init__(
        self,
        base_url: str,
        *,
        payer: Any = None,
        max_payment: int | None = None,
        timeout: float = 120,
        verify_attestation: bool = True,
        client: Any = None,
        expected_evaluator: str | None = None,
    ):
        if payer is not None and max_payment is None:
            raise ValueError("max_payment is required when a payer is given")
        if max_payment is not None and (
            isinstance(max_payment, bool) or not isinstance(max_payment, int)
        ):
            raise TypeError("max_payment is an integer in the token's atomic units")
        self.base_url = base_url.rstrip("/")
        self.payer = payer
        self.max_payment = max_payment
        self.timeout = timeout
        self.verify_attestation = verify_attestation
        self.expected_evaluator = expected_evaluator
        self._client = client  # an httpx.AsyncClient (tests, connection reuse)

    async def _http(self, method: str, path: str, **kw: Any) -> tuple[int, Any]:
        import httpx

        url = f"{self.base_url}{path}"
        if self._client is not None:
            resp = await self._client.request(method, url, timeout=self.timeout, **kw)
        else:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as http:
                resp = await http.request(method, url, **kw)
        try:
            data = resp.json()
        except ValueError:
            data = {"error": "bad_response", "message": resp.text[:500]}
        return resp.status_code, data

    async def _post(self, path: str, body: Mapping[str, Any]) -> tuple[int, Any]:
        text = json.dumps(body, ensure_ascii=False, allow_nan=False)
        key = uuid.uuid4().hex
        if self.payer is not None:
            # The SDK payer cannot set headers: the idempotency key travels as a query param.
            url = f"{self.base_url}{path}?idempotency_key={key}"
            raw = await asyncio.to_thread(
                self.payer.request, url, max_payment=self.max_payment, method="POST", body=text
            )
            return _unpack(raw)
        return await self._http(
            "POST",
            path,
            content=text.encode(),
            headers={"Content-Type": "application/json", "Idempotency-Key": key},
        )

    async def evaluate(self, request: EvalRequest) -> EvalResponse:
        status, data = await self._post("/v1/evaluate", request.to_dict())
        _raise_for(status, data)
        try:
            response = EvalResponse.from_dict(data)
        except SchemaError as exc:
            raise EvalError(f"the service returned an invalid response: {exc}") from exc
        self.verify(request, response)
        return response

    def verify(self, request: EvalRequest, response: EvalResponse) -> None:
        """Hashes, request / stage / subject binding and (with ``[evm]``) the attestation."""
        if not verify_hashes(response):
            raise AttestationError("result_hash / response_hash do not match the response")
        if response.provenance.request_hash != hash_obj(request.to_dict()):
            raise AttestationError("the response is not bound to this request (request_hash)")
        if response.stage != request.stage:
            raise AttestationError("the response stage differs from the request stage")
        want = request.subject.to_dict() if request.subject else None
        got = response.subject.to_dict() if response.subject else None
        if want != got:
            raise AttestationError("the response subject differs from the request subject")
        if self.expected_evaluator is not None and response.attestation is None:
            raise AttestationError("the response is not attested (expected_evaluator is set)")
        if self.verify_attestation and response.attestation is not None:
            if available("eth_account"):
                from .chain.attestation import verify

                verify(response, expected_evaluator=self.expected_evaluator)
            elif self.expected_evaluator is not None:
                raise AttestationError(
                    "checking expected_evaluator needs eth-account: "
                    "pip install 'bnbagent-eval[evm]'"
                )

    async def quote(self, request: EvalRequest) -> dict[str, Any]:
        status, data = await self._http(
            "POST",
            "/v1/quote",
            content=json.dumps(request.to_dict()).encode(),
            headers={"Content-Type": "application/json"},
        )
        _raise_for(status, data)
        return data

    async def describe_metrics(self) -> list[MetricSpec]:
        status, data = await self._http("GET", "/v1/metrics")
        _raise_for(status, data)
        return [_spec_from_dict(d) for d in data["metrics"]]


# HTTP status of the service's error codes, for payers that return only the body.
_ERROR_STATUS = {
    "bad_request": 400,
    "payment_required": 402,
    "credit_insufficient": 402,
    "not_found": 404,
    "credit_used": 409,
    "idempotency_conflict": 409,
    "in_progress": 409,
    "too_large": 413,
    "invalid_request": 422,
    "missing_extra": 422,
    "policy_denied": 422,
    "rate_limited": 429,
    "evaluation_failed": 500,
    "evaluation_timeout": 500,
    "busy": 503,
}


def _body_status(body: Any) -> int:
    """The HTTP status of a bare response body: 200, or the status of its error code."""
    if not isinstance(body, Mapping) or "error" not in body or "result_hash" in body:
        return 200
    if "accepts" in body:  # an x402 challenge (payment missing or rejected)
        return 402
    return _ERROR_STATUS.get(str(body["error"]), 400)


def _unpack(raw: Any) -> tuple[int, Any]:
    """A payer's result: the SDK's ``X402PaymentResult`` (``.response`` is the body verbatim),
    an HTTP response object (httpx / requests), a dict or JSON text."""
    if hasattr(raw, "response") and hasattr(raw, "success") and not isinstance(raw, Mapping):
        body = raw.response
        if isinstance(body, (bytes, bytearray)):
            body = body.decode()
        if isinstance(body, str):
            body = json.loads(body)
        return _body_status(body), body
    status = getattr(raw, "status_code", None)
    if status is not None and not isinstance(raw, Mapping):
        try:
            return int(status), raw.json()
        except ValueError:
            return int(status), {"error": "bad_response", "message": str(raw.text)[:500]}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode()
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, Mapping) and isinstance(raw.get("status_code"), int) and "body" in raw:
        body = raw["body"]
        return raw["status_code"], json.loads(body) if isinstance(body, str) else body
    return _body_status(raw), raw


def _raise_for(status: int, data: Any) -> None:
    if status < 400:
        return
    body = data if isinstance(data, Mapping) else {}
    message = f"HTTP {status} {body.get('error', '')}: {body.get('message', '')}"
    if status == 402:
        exc = PaymentRequired(body.get("quote") or body, message)
        exc.challenge = body  # type: ignore[attr-defined]
        raise exc
    if body.get("error") == "policy_denied":
        raise PolicyError(message)
    if status < 500:
        raise RequestError(message)
    raise EvalError(message)


def _spec_from_dict(d: Mapping[str, Any]) -> MetricSpec:
    methods = tuple(
        MethodSpec(
            name=m["name"],
            kind=Kind(m["kind"]),
            required_inputs=frozenset(m.get("required_inputs", ())),
            optional_inputs=frozenset(m.get("optional_inputs", ())),
            inverted=bool(m.get("inverted", False)),
            raw_range=tuple(m.get("raw_range", (0.0, 1.0))),  # type: ignore[arg-type]
            extra=m.get("extra"),
            service_allowed=bool(m.get("service_allowed", True)),
        )
        for m in d["methods"]
    )
    return MetricSpec(
        id=d["id"],
        category=d["category"],
        description=d.get("description", ""),
        methods=methods,
        params_schema=d.get("params_schema") or {"type": "object"},
        default_threshold=d.get("default_threshold"),
        unit=d.get("unit"),
        value_limits=bool(d.get("value_limits", False)),
        version=d.get("version", "1"),
        aliases=tuple(d.get("aliases", ())),
        sources=tuple(d.get("sources", ())),
    )


class EvalClient:
    def __init__(
        self,
        backend: EvalBackend | None = None,
        *,
        config: EvalConfig | None = None,
        judge: JudgeModel | JudgeConfig | None = None,
        signer: Any = None,
        ports: Any = None,
    ):
        self.config = config or EvalConfig.from_env()
        self.backend = backend or LocalBackend(self.config, judge=judge, ports=ports)
        self.signer = signer  # SignerPort: responses are attested (EIP-712) when set
        self.ports = ports

    @classmethod
    def remote(
        cls,
        base_url: str,
        *,
        payer: Any = None,
        max_payment: int | None = None,
        timeout: float = 120,
        verify_attestation: bool = True,
        client: Any = None,
        expected_evaluator: str | None = None,
    ) -> EvalClient:
        """A client that evaluates on the eval service at ``base_url`` (same API).

        ``expected_evaluator`` (an address) requires every response to be attested by it.
        """
        backend = RemoteBackend(
            base_url,
            payer=payer,
            max_payment=max_payment,
            timeout=timeout,
            verify_attestation=verify_attestation,
            client=client,
            expected_evaluator=expected_evaluator,
        )
        return cls(backend)

    async def quote(self, request: EvalRequest) -> Any:
        """The service's quote for ``request`` (remote backends only)."""
        fn = getattr(self.backend, "quote", None)
        if fn is None:
            raise EvalError("quotes are only available from a remote eval service")
        return await fn(request)

    async def evaluate(self, request: EvalRequest | None = None, /, **fields: Any) -> EvalResponse:
        """Evaluate ``request``, or build one from ``fields`` (unknown fields raise TypeError)."""
        if request is not None and fields:
            raise TypeError("pass either an EvalRequest or keyword fields, not both")
        if request is None:
            request = build_request(**fields)
        response = await self.backend.evaluate(request)
        if self.signer is not None and response.attestation is None:
            from .chain.attestation import sign

            response = sign(response, self.signer)
        return response

    def evaluate_sync(self, request: EvalRequest | None = None, /, **fields: Any) -> EvalResponse:
        """Blocking variant; works inside and outside a running event loop."""
        coro = self.evaluate(request, **fields)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    async def metrics(self) -> list[MetricSpec]:
        return await self.backend.describe_metrics()

    def compare(self, a: EvalResponse, b: EvalResponse, **tolerances: float) -> Comparison:
        """Agreement of two evaluations (rule metrics exact, judge metrics within tolerance)."""
        return compare(a, b, tolerances or None)


def build_request(**fields: Any) -> EvalRequest:
    """Build an EvalRequest from keyword fields; nested values may be objects or plain dicts."""
    unknown = set(fields) - set(EvalRequest.__dataclass_fields__)
    if unknown:
        raise TypeError(f"unknown EvalRequest field(s): {sorted(unknown)}")
    if isinstance(fields.get("suite"), Mapping):
        fields["suite"] = Suite.load(fields["suite"])  # inlines local $ref, raises SuiteError
    data = {k: _plain(v) for k, v in fields.items()}
    return EvalRequest.from_dict(data)


def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


async def evaluate(**fields: Any) -> EvalResponse:
    """Module-level convenience: ``EvalClient().evaluate(**fields)``."""
    return await EvalClient().evaluate(**fields)


__all__ = [
    "EvalBackend",
    "EvalClient",
    "LocalBackend",
    "RemoteBackend",
    "build_request",
    "evaluate",
]
