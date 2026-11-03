"""The eval service HTTP API (``[server]`` extra).

``create_app()`` returns a FastAPI app. The protocol logic lives in :class:`EvalService`
(framework-free, returns :class:`Reply`); the FastAPI layer only maps routes onto it.

Payment flow (x402): validate → quote → 402 challenge (no ``X-PAYMENT``) → verify + settle →
evaluate (the caller's stage is kept) → attest (service signer) → 200 + ``X-PAYMENT-RESPONSE``.
A server-side failure after settlement returns a credit receipt instead. ``Idempotency-Key``
(header, or ``idempotency_key`` query parameter for payers that cannot set headers) and the
payment header itself make retries return the stored response without paying again.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .._optional import require
from .._version import __version__
from ..chain.attestation import sign
from ..client import EvalClient, build_request
from ..engine.resolve import resolve_suite
from ..exceptions import (
    BudgetExceeded,
    MissingExtraError,
    PolicyError,
    RequestError,
    SchemaError,
    SuiteError,
)
from ..hashing import hash_obj, keccak_hex
from ..logging_utils import get_logger
from ..metrics import registry
from ..types.case import EvalCase
from ..types.request import EvalRequest, EvalResponse
from .config import ServiceConfig
from .payment_base import NoPaymentGate, PaymentError, PaymentGate, PaymentReceipt, Quote
from .policy import LimitExceeded, UntrustedSuitePolicy
from .runner import fetch_dataset, harden, rebind_request_hash
from .store import IdempotencyRecord, Store

__all__ = ["EvalService", "HttpError", "Reply", "create_app"]

logger = get_logger(__name__)


@dataclass
class Reply:
    status: int
    body: Any
    headers: dict[str, str] = field(default_factory=dict)


class HttpError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any):
        self.status, self.code, self.message, self.extra = status, code, message, extra
        super().__init__(message)

    def reply(self) -> Reply:
        headers = self.extra.pop("headers", {})
        return Reply(
            self.status, {"error": self.code, "message": self.message, **self.extra}, headers
        )


@dataclass(frozen=True)
class Prepared:
    request: EvalRequest  # as sent (hashed, quoted)
    run_request: EvalRequest  # dataset_ref expanded into cases
    body: dict[str, Any]
    request_hash: str
    judge_calls: int


def _map_error(exc: BaseException) -> HttpError:
    if isinstance(exc, HttpError):
        return exc
    if isinstance(exc, LimitExceeded):
        return HttpError(413, "too_large", str(exc))
    if isinstance(exc, PolicyError):
        return HttpError(422, "policy_denied", str(exc))
    if isinstance(exc, MissingExtraError):
        return HttpError(422, "missing_extra", str(exc))
    if isinstance(exc, (RequestError, SuiteError, SchemaError, BudgetExceeded)):
        return HttpError(422, "invalid_request", str(exc))
    if isinstance(exc, (TypeError, ValueError)):
        return HttpError(422, "invalid_request", str(exc))
    if isinstance(exc, asyncio.TimeoutError):
        return HttpError(500, "evaluation_timeout", "the evaluation timed out")
    return HttpError(500, "evaluation_failed", f"internal error ({type(exc).__name__})")


class RateLimiter:
    """Token bucket per key (client IP)."""

    def __init__(self, rate_per_s: float, burst: int, clock=time.monotonic):
        self.rate, self.burst, self.clock = rate_per_s, burst, clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.rate <= 0:
            return True
        now = self.clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            if len(self._buckets) > 100_000:  # bound memory under address churn
                self._buckets.clear()
            return True


class EvalService:
    def __init__(
        self,
        client: EvalClient,
        *,
        gate: PaymentGate,
        policy: UntrustedSuitePolicy,
        store: Store,
        config: ServiceConfig,
        signer: Any = None,
    ):
        self.client, self.gate, self.policy = client, gate, policy
        self.store, self.config, self.signer = store, config, signer
        self.limiter = RateLimiter(config.rate_per_s, config.rate_burst)
        self._tasks: set[asyncio.Task] = set()
        self._last_purge = float("-inf")
        self.dataset_transport: Any = None  # tests: httpx mock transport for dataset_ref

    # ------------------------------------------------------------- validation
    def rate_limit(self, key: str) -> None:
        if not self.limiter.allow(key):
            raise HttpError(429, "rate_limited", "too many requests", headers={"Retry-After": "1"})

    async def prepare(self, raw: bytes | Mapping[str, Any]) -> Prepared:
        limits = self.policy.limits
        if isinstance(raw, (bytes, bytearray)):
            if len(raw) > limits.max_request_bytes:
                raise HttpError(413, "too_large", f"body > {limits.max_request_bytes} bytes")
            try:
                body = json.loads(raw)
            except (ValueError, RecursionError) as exc:
                raise HttpError(400, "bad_request", "the body is not valid JSON") from exc
            size = len(raw)
        else:
            body, size = raw, None
        if not isinstance(body, Mapping):
            raise HttpError(400, "bad_request", "the body must be a JSON object")
        try:
            self.policy.check_body(body, size)
            request = build_request(**dict(body))
            await self.policy.check_request(request)
            run_request = request
            if request.dataset_ref:
                if request.cases:
                    raise RequestError("give either cases or dataset_ref, not both")
                cases: tuple[EvalCase, ...] = await fetch_dataset(
                    request.dataset_ref,
                    cap=limits.max_request_bytes,
                    resolver=self.policy.resolver,
                    transport=self.dataset_transport,
                )
                run_request = dataclasses.replace(request, cases=cases, dataset_ref=None)
            self.policy.check_cases(len(run_request.cases))
            if request.suite is None:
                raise RequestError("a suite is required")
            resolved = resolve_suite(request.suite, policy=self.policy)
            judge_calls = self.policy.estimate_judge_calls(
                request, resolved.metrics, len(run_request.cases)
            )
            self.policy.check_judge_calls(judge_calls)
        except HttpError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped onto 4xx below
            raise _map_error(exc) from exc
        normalized = request.to_dict()
        return Prepared(request, run_request, normalized, hash_obj(normalized), judge_calls)

    # ------------------------------------------------------------------ quote
    async def quote(self, raw: bytes) -> Reply:
        prepared = await self.prepare(raw)
        quote = await self.gate.quote(prepared.body, judge_calls=prepared.judge_calls)
        return Reply(
            200,
            {
                "request_hash": prepared.request_hash,
                "judge_calls": prepared.judge_calls,
                "payment_required": quote is not None,
                "quote": quote.to_dict() if quote else None,
            },
        )

    # ---------------------------------------------------------------- evaluate
    async def submit(
        self, raw: bytes, headers: Mapping[str, str], *, mode: str, query_key: str | None = None
    ) -> Reply:
        """``mode`` is ``sync`` (POST /v1/evaluate) or ``async`` (POST /v1/evaluations)."""
        prepared = await self.prepare(raw)
        self._housekeeping()
        payment = headers.get("x-payment")
        key = headers.get("idempotency-key") or query_key
        pay_key = f"pay:{keccak_hex(payment)}" if payment else None
        idem = await self._idempotency_scope(key, payment) if key else pay_key
        route_hash = f"{mode}:{prepared.request_hash}"
        if pay_key and pay_key != idem:
            prior = self.store.get_record(pay_key)
            if prior is not None:
                return self._replay(prior, route_hash)
        if idem:
            existing = self.store.reserve(idem, route_hash, self.config.idempotency_ttl_s)
            if existing is not None:
                return self._replay(existing, route_hash)
        quote: Quote | None = None
        receipt: PaymentReceipt | None = None
        try:
            if mode == "async" and len(self._tasks) >= self.config.max_running_jobs:
                raise HttpError(503, "busy", "too many running jobs", headers={"Retry-After": "5"})
            quote = await self.gate.quote(prepared.body, judge_calls=prepared.judge_calls)
            if quote is not None:
                if not payment:
                    raise HttpError(402, "payment_required", "X-PAYMENT header is required")
                receipt = await self.gate.verify_and_settle(payment, quote)
        except PaymentError as exc:
            if idem:
                self.store.release(idem)
            body = {"error": exc.code, "message": str(exc)}
            if quote is not None:
                body = {**self._challenge(quote), **body}
            return Reply(exc.status, body)
        except HttpError as exc:
            if idem:
                self.store.release(idem)
            if exc.status == 402 and quote is not None:
                return Reply(402, {**self._challenge(quote), "error": exc.code})
            raise
        except BaseException:
            if idem:
                self.store.release(idem)
            raise
        logger.info(
            "evaluation %s request_hash=%s paid=%s", mode, prepared.request_hash, bool(receipt)
        )
        if mode == "sync":
            reply = await self._run_sync(prepared, receipt, quote)
            stored = reply
        else:
            reply, stored = self._start_job(prepared, receipt, quote)
        if idem:
            self.store.complete(idem, stored.status, stored.body, stored.headers)
            if pay_key and pay_key != idem:
                self.store.alias(idem, pay_key)
        return reply

    async def _idempotency_scope(self, key: str, payment: str | None) -> str:
        """``Idempotency-Key`` scoped to its payer, so another caller cannot replay it.

        The payer is the address that signed ``X-PAYMENT`` (verified by the gate, nothing is
        charged). Gates without ``identify`` scope the key to the exact payment header. Without
        a payment (free service), keys share one anonymous scope.
        """
        principal = "anon"
        if payment:
            identify = getattr(self.gate, "identify", None)
            payer = await identify(payment) if identify is not None else None
            principal = f"payer:{payer}" if payer else f"pay:{keccak_hex(payment)}"
        return f"key:{keccak_hex(principal)[2:18]}:{key}"

    def _housekeeping(self) -> None:
        """Drop expired jobs / idempotency records / credits (at most once a minute)."""
        now = time.monotonic()
        if now - self._last_purge < 60.0:
            return
        self._last_purge = now
        self.store.purge_expired()

    def _challenge(self, quote: Quote) -> dict[str, Any]:
        body = dict(self.gate.challenge(quote))
        body.setdefault("quote", quote.to_dict())
        body.setdefault("error", "X-PAYMENT header is required")
        return body

    def _replay(self, record: IdempotencyRecord, route_hash: str) -> Reply:
        if record.request_hash != route_hash:
            raise HttpError(409, "idempotency_conflict", "the key was used for another request")
        if record.state != "done" or record.status_code is None:
            raise HttpError(409, "in_progress", "a request with this key is still running")
        body = dict(record.response or {})
        if route_hash.startswith("async:") and "job_id" in body:
            body["access_token"] = self.store.rotate_token(
                body["job_id"]
            )  # the previous token is revoked
        headers = {**record.headers, "Idempotent-Replayed": "true"}
        return Reply(record.status_code, body, headers)

    async def _evaluate(self, prepared: Prepared) -> EvalResponse:
        response = await asyncio.wait_for(
            self.client.evaluate(prepared.run_request), self.config.eval_timeout_s
        )
        response = rebind_request_hash(response, prepared.request_hash)
        if self.signer is not None and response.attestation is None:
            response = sign(response, self.signer)
        return response

    def _failure(
        self, exc: BaseException, prepared: Prepared, receipt: PaymentReceipt | None, quote
    ) -> Reply:
        err = _map_error(exc)
        if self.config.no_retention:  # messages may quote case content
            err.message = type(exc).__name__
        logger.warning(
            "evaluation failed request_hash=%s error=%s", prepared.request_hash, type(exc).__name__
        )
        reply = err.reply()
        if receipt is not None:
            # In the quote's asset units: ``receipt.amount`` is in the units of whatever asset
            # paid (decimals differ), and redemption compares against new quotes.
            credit = self.store.issue_credit(
                receipt.payer,
                quote.amount if quote is not None else receipt.amount,
                asset=quote.asset if quote else None,
                quote_id=receipt.quote_id,
                request_hash=prepared.request_hash,
                ttl_s=self.config.credit_ttl_s,
            )
            reply.body["credit"] = credit.to_dict()
            reply.headers["X-PAYMENT-RESPONSE"] = receipt.header()
        return reply

    async def _run_sync(
        self, prepared: Prepared, receipt: PaymentReceipt | None, quote: Quote | None
    ) -> Reply:
        try:
            response = await self._evaluate(prepared)
        except Exception as exc:  # noqa: BLE001 - after payment: credit, never a lost payment
            return self._failure(exc, prepared, receipt, quote)
        headers = {"X-PAYMENT-RESPONSE": receipt.header()} if receipt else {}
        return Reply(200, response.to_dict(), headers)

    # -------------------------------------------------------------------- jobs
    def _start_job(
        self, prepared: Prepared, receipt: PaymentReceipt | None, quote: Quote | None
    ) -> tuple[Reply, Reply]:
        job_id, token = self.store.create_job(prepared.request_hash, self.config.job_ttl_s)
        headers = {"X-PAYMENT-RESPONSE": receipt.header()} if receipt else {}
        body = {
            "job_id": job_id,
            "status": "queued",
            "expires_at": int(time.time() + self.config.job_ttl_s),
        }
        task = asyncio.get_running_loop().create_task(self._job(job_id, prepared, receipt, quote))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return Reply(202, {**body, "access_token": token}, headers), Reply(202, body, headers)

    async def _job(
        self, job_id: str, prepared: Prepared, receipt: PaymentReceipt | None, quote
    ) -> None:
        self.store.set_job(job_id, "running")
        try:
            response = await self._evaluate(prepared)
        except Exception as exc:  # noqa: BLE001
            failure = self._failure(exc, prepared, receipt, quote)
            self.store.set_job(
                job_id,
                "failed",
                error=failure.body.get("message"),
                credit=failure.body.get("credit"),
            )
            return
        self.store.set_job(job_id, "done", result=response.to_dict())

    def get_job(self, job_id: str, authorization: str | None) -> Reply:
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        job = self.store.get_job(job_id, token)
        if job is None:
            raise HttpError(404, "not_found", "no such job")
        return Reply(
            200,
            {
                "job_id": job.job_id,
                "status": job.status,
                "request_hash": job.request_hash,
                "result": job.result,
                "error": job.error,
                "credit": job.credit,
                "expires_at": int(job.expires_at),
            },
        )

    async def drain(self) -> None:
        """Wait for running jobs (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ----------------------------------------------------------------- credits
    async def redeem(self, raw: bytes) -> Reply:
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise HttpError(400, "bad_request", "the body is not valid JSON") from exc
        if (
            not isinstance(body, Mapping)
            or not isinstance(body.get("credit_id"), str)
            or not isinstance(body.get("request"), Mapping)
        ):
            raise HttpError(400, "bad_request", "expected {credit_id, request}")
        if len(raw) > self.policy.limits.max_request_bytes:
            raise HttpError(413, "too_large", "request too large")
        prepared = await self.prepare(body["request"])
        credit = self.store.get_credit(body["credit_id"])
        if credit is None:
            raise HttpError(404, "not_found", "no such credit")
        if credit.used:
            raise HttpError(409, "credit_used", "the credit was already redeemed")
        quote = await self.gate.quote(prepared.body, judge_calls=prepared.judge_calls)
        if quote is not None and (
            quote.amount > credit.amount
            or (credit.asset and str(credit.asset).lower() != quote.asset.lower())
        ):
            raise HttpError(402, "credit_insufficient", "the credit does not cover this request")
        if not self.store.redeem_credit(credit.credit_id):
            raise HttpError(409, "credit_used", "the credit was already redeemed")
        try:
            response = await self._evaluate(prepared)
        except Exception as exc:  # noqa: BLE001
            self.store.restore_credit(credit.credit_id)
            reply = _map_error(exc).reply()
            reply.body["credit"] = credit.to_dict()
            return reply
        return Reply(200, response.to_dict(), {"X-Credit-Redeemed": credit.credit_id})

    # --------------------------------------------------------------- discovery
    def catalogue(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.policy.catalogue()]

    def metric(self, metric_id: str) -> dict[str, Any]:
        try:
            canonical = registry.resolve(metric_id)[0].spec.id
        except KeyError as exc:
            raise HttpError(404, "not_found", f"unknown metric {metric_id!r}") from exc
        for spec in self.policy.catalogue():
            if spec.id == canonical:
                return spec.to_dict()
        raise HttpError(404, "not_found", f"metric {metric_id!r} is not offered by this service")

    def agent_card(self, base_url: str) -> dict[str, Any]:
        url = (self.config.base_url or base_url).rstrip("/")
        specs = self.policy.catalogue()
        return {
            "name": self.config.name,
            "description": "Agent evaluation as a service (bnbagent-eval).",
            "url": url,
            "version": __version__,
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "metrics": [s.id for s in specs],
            },
            "defaultInputModes": ["application/json"],
            "defaultOutputModes": ["application/json"],
            "skills": [
                {
                    "id": "evaluate",
                    "name": "Evaluate",
                    "description": "Score cases against a suite of allow-listed metrics.",
                    "tags": sorted({s.category for s in specs}),
                }
            ],
            "endpoints": {
                "evaluate": f"{url}/v1/evaluate",
                "evaluations": f"{url}/v1/evaluations",
                "quote": f"{url}/v1/quote",
                "metrics": f"{url}/v1/metrics",
            },
            "payment": {"x402": not isinstance(self.gate, NoPaymentGate)},
        }


def default_client(policy: UntrustedSuitePolicy) -> EvalClient:
    """``EvalClient()`` with the policy hook and guarded targets; plugins are never loaded."""
    return harden(EvalClient(), policy)


def _ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{name}"}


def _json_body(name: str) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _ref(name)}},
        }
    }


def _responses(ok: str | None, *codes: int) -> dict[Any, Any]:
    out: dict[Any, Any] = {}
    if ok:
        out[200] = {"description": "OK", "content": {"application/json": {"schema": _ref(ok)}}}
    for code in codes:
        out[code] = {
            "description": _CODE_TEXT[code],
            "content": {"application/json": {"schema": _ref("ErrorBody")}},
        }
    return out


_CODE_TEXT = {
    400: "Malformed body",
    402: "Payment required (x402 challenge)",
    404: "Not found",
    409: "Idempotency conflict / credit already used",
    413: "Request too large",
    422: "Invalid request or denied by the untrusted-suite policy",
    429: "Rate limited",
    503: "Too many running jobs",
}


def create_app(
    client: EvalClient | None = None,
    *,
    gate: PaymentGate | None = None,
    policy: UntrustedSuitePolicy | None = None,
    store: Store | None = None,
    config: ServiceConfig | None = None,
    signer: Any = None,
):
    """Build the FastAPI app. Needs the ``[server]`` extra."""
    require("fastapi", "server")
    from fastapi import FastAPI, Request
    from fastapi.openapi.utils import get_openapi
    from fastapi.responses import JSONResponse

    # FastAPI resolves the (string) route annotations in this module's globals.
    globals().setdefault("Request", Request)
    config = config or ServiceConfig()
    policy = policy or UntrustedSuitePolicy(
        limits=config.limits, allowed_kinds=config.allowed_kinds
    )
    client = harden(client, policy) if client is not None else default_client(policy)
    store = store or Store(config.store_path, no_retention=config.no_retention)
    if config.no_retention:
        store.no_retention = True
    service = EvalService(
        client,
        gate=gate or NoPaymentGate(),
        policy=policy,
        store=store,
        config=config,
        signer=signer,
    )
    app = FastAPI(title="bnbagent-eval service", version=__version__)
    app.state.service = service

    def respond(reply: Reply) -> JSONResponse:
        return JSONResponse(reply.body, status_code=reply.status, headers=reply.headers)

    @app.exception_handler(HttpError)
    async def _http_error(_request: Request, exc: HttpError) -> JSONResponse:
        return respond(exc.reply())

    def ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    async def body_of(request: Request) -> bytes:
        length = request.headers.get("content-length")
        limit = policy.limits.max_request_bytes
        if length and length.isdigit() and int(length) > limit:
            raise HttpError(413, "too_large", f"body > {limit} bytes")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HttpError(413, "too_large", f"body > {limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/v1/metrics", responses=_responses("MetricCatalogue"))
    async def metrics():
        return {"metrics": service.catalogue()}

    @app.get("/v1/metrics/{metric_id}", responses=_responses("MetricSpec", 404))
    async def metric(metric_id: str):
        return service.metric(metric_id)

    @app.post(
        "/v1/quote",
        openapi_extra=_json_body("EvalRequest"),
        responses=_responses("QuoteResponse", 400, 413, 422, 429),
    )
    async def quote(request: Request):
        service.rate_limit(ip(request))
        return respond(await service.quote(await body_of(request)))

    @app.post(
        "/v1/evaluate",
        openapi_extra=_json_body("EvalRequest"),
        responses=_responses("EvalResponse", 400, 402, 409, 413, 422, 429),
    )
    async def evaluate(request: Request):
        service.rate_limit(ip(request))
        raw = await body_of(request)
        reply = await service.submit(
            raw,
            request.headers,
            mode="sync",
            query_key=request.query_params.get("idempotency_key"),
        )
        return respond(reply)

    @app.post(
        "/v1/evaluations",
        status_code=202,
        openapi_extra=_json_body("EvalRequest"),
        responses={
            202: {
                "description": "Job accepted",
                "content": {"application/json": {"schema": _ref("JobCreated")}},
            },
            **_responses(None, 400, 402, 409, 413, 422, 429, 503),
        },
    )
    async def evaluations(request: Request):
        service.rate_limit(ip(request))
        raw = await body_of(request)
        reply = await service.submit(
            raw,
            request.headers,
            mode="async",
            query_key=request.query_params.get("idempotency_key"),
        )
        return respond(reply)

    @app.get("/v1/evaluations/{job_id}", responses=_responses("JobStatus", 404))
    async def job(job_id: str, request: Request):
        return respond(service.get_job(job_id, request.headers.get("authorization")))

    @app.post(
        "/v1/credits/redeem",
        openapi_extra=_json_body("CreditRedeem"),
        responses=_responses("EvalResponse", 400, 402, 404, 409, 413, 422, 429),
    )
    async def redeem(request: Request):
        service.rate_limit(ip(request))
        return respond(await service.redeem(await body_of(request)))

    @app.get("/.well-known/agent-card.json")
    async def agent_card(request: Request):
        return service.agent_card(str(request.base_url))

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        from .schemas import component_schemas

        doc = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version="3.1.0",
            description="Evaluation as a service with x402 payments (bnbagent-eval).",
            routes=app.routes,
        )
        doc.setdefault("components", {}).setdefault("schemas", {}).update(component_schemas())
        app.openapi_schema = doc
        return doc

    app.openapi = openapi  # type: ignore[method-assign]
    return app
