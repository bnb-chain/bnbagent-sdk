"""UntrustedSuitePolicy: what a paying stranger may run on the eval service.

Two entry points:

- ``policy(spec, method, cfg)``: the runner's per-metric hook (``Runner(policy=...)``);
- ``await policy.check_request(request, body_bytes=...)``: request-level limits and egress
  checks (targets and ``dataset_ref`` must be public http(s) URLs, checked through DNS).

``check_body`` runs on the raw JSON before it is parsed into an ``EvalRequest`` (it rejects
``$ref``, which ``Suite.load`` would otherwise resolve against the server's file system).
``GuardedTransport`` re-applies the public-IP guard to every outgoing request (and every
redirect hop) the service makes on a caller's behalf.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .. import _optional
from ..exceptions import PolicyError
from ..metrics import registry
from ..metrics.base import Kind, MethodSpec, MetricSpec
from ..net import (
    FetchError,
    Resolver,
    public_host_check,
    public_transport,
)
from ..types.request import EvalRequest
from ..types.suite import MetricConfig

__all__ = [
    "DENIED_IDS",
    "DENIED_PREFIXES",
    "GuardedTransport",
    "LimitExceeded",
    "PolicyLimits",
    "UntrustedSuitePolicy",
]

# Never runnable on the service, whatever their spec says.
DENIED_IDS = frozenset({"custom.python", "custom.human_review"})
DENIED_PREFIXES = ("code.",)  # code execution / sandboxes
SERVICE_TARGET_KINDS = frozenset({"http", "a2a", "mcp"})  # no callable / registry-resolved
_BUILTIN_PREFIX = "bnbagent_eval."
SERVICE_JUDGE_EXTRA = frozenset({"response_format"})  # cost-neutral judge options
SERVICE_JUDGE_PROVIDERS = frozenset({"openai", "anthropic", "bedrock"})


class LimitExceeded(PolicyError):
    """A request is too large (HTTP 413)."""

    status = 413


@dataclass(frozen=True)
class PolicyLimits:
    max_request_bytes: int = 1_000_000
    max_cases: int = 500
    max_runs: int = 5
    max_judge_calls: int = 2_000
    max_judge_samples: int = 5
    max_regex_timeout_ms: int = 1_000
    max_json_depth: int = 64
    max_response_bytes: int = 10_000_000  # each target / dataset response body
    max_concurrency: int = 16  # options.max_concurrency
    max_judge_tokens: int = 4096  # judge max_tokens
    max_redact_patterns: int = 16  # options.privacy.redact
    max_redact_pattern_len: int = 256


def _is_agentic(method: MethodSpec) -> bool:
    return method.name.startswith("agentic")


DEFAULT_KINDS = frozenset({"rule", "judge"})


class UntrustedSuitePolicy:
    """Allow-list + limits + egress guard. Raises :class:`PolicyError` to deny."""

    def __init__(
        self,
        *,
        allowed_ids: Iterable[str] | None = None,
        limits: PolicyLimits | None = None,
        resolver: Resolver | None = None,
        allowed_judge_base_urls: Iterable[str] = (),
        judge_samples: int = 1,
        allowed_kinds: Iterable[str] = DEFAULT_KINDS,
        allowed_judge_models: Iterable[str] | None = None,
    ):
        # Vendor / model methods download models, start local tools or use the server's cloud
        # credentials, so the operator opts in to them (e.g. ``allowed_kinds={"rule",
        # "judge", "vendor"}``).
        self.allowed_kinds = frozenset(str(k) for k in allowed_kinds)
        self._allowed_ids = frozenset(allowed_ids) if allowed_ids is not None else None
        self.limits = limits or PolicyLimits()
        self.resolver = resolver  # DNS resolver override (tests)
        self.allowed_judge_base_urls = frozenset(u.rstrip("/") for u in allowed_judge_base_urls)
        self.judge_samples = judge_samples  # samples of the server's own judge
        # "provider:model" pairs a caller may pin (None: any model of a known provider). The
        # price is per judge call, so operators with expensive models should restrict this.
        self.allowed_judge_models = (
            frozenset(allowed_judge_models) if allowed_judge_models is not None else None
        )

    # ------------------------------------------------------------- metric hook
    @property
    def allowed_ids(self) -> frozenset[str]:
        if self._allowed_ids is None:
            return frozenset(s.id for s in self.catalogue())
        return self._allowed_ids

    def method_allowed(self, spec: MetricSpec, method: MethodSpec) -> bool:
        if spec.id in DENIED_IDS or spec.id.startswith(DENIED_PREFIXES):
            return False
        if not method.service_allowed or _is_agentic(method):
            return False
        if method.kind.value not in self.allowed_kinds:
            return False
        if self._allowed_ids is not None and spec.id not in self._allowed_ids:
            return False
        return _is_builtin(spec, method)

    def catalogue(self) -> list[MetricSpec]:
        """Registered metrics restricted to their service-allowed methods."""
        out = []
        for spec in registry.list_specs():
            methods = tuple(m for m in spec.methods if self.method_allowed(spec, m))
            if methods:
                out.append(dataclasses.replace(spec, methods=methods))
        return out

    def __call__(self, spec: MetricSpec, method: MethodSpec, cfg: MetricConfig) -> None:
        where = f"metric {cfg.key!r} ({spec.id}/{method.name})"
        if not self.method_allowed(spec, method):
            raise PolicyError(f"{where} is not allowed on the eval service")
        if spec.id == "format.regex":
            if not _optional.available("regex"):
                raise PolicyError(f"{where}: needs the 'regex' module (safe engine with timeout)")
            timeout = cfg.params.get("timeout_ms", 1000)
            if timeout > self.limits.max_regex_timeout_ms:
                raise PolicyError(
                    f"{where}: timeout_ms {timeout} > {self.limits.max_regex_timeout_ms}"
                )
        samples = cfg.params.get("samples")
        # 10.0 is a JSON Schema integer too, so the check covers floats.
        is_number = isinstance(samples, (int, float)) and not isinstance(samples, bool)
        if is_number and samples > self.limits.max_judge_samples:
            raise LimitExceeded(
                f"{where}: params.samples {samples} > {self.limits.max_judge_samples}"
            )

    check = __call__

    # ------------------------------------------------------------ request level
    def check_body(self, body: Any, size: int | None = None) -> None:
        """Checks on the raw JSON body (before parsing into an EvalRequest)."""
        if size is not None and size > self.limits.max_request_bytes:
            raise LimitExceeded(f"request body {size} bytes > {self.limits.max_request_bytes}")
        if not isinstance(body, Mapping):
            raise PolicyError("the request body must be a JSON object")
        _scan(body, self.limits.max_json_depth)

    async def check_request(self, request: EvalRequest) -> None:
        lim = self.limits
        if request.suite_ref or request.benchmark_ref:
            raise PolicyError("suite_ref / benchmark_ref are not supported: send the suite inline")
        if request.suite is not None:
            for cfg in request.suite.metrics:
                if cfg.id in DENIED_IDS or cfg.id.startswith(DENIED_PREFIXES):
                    raise PolicyError(f"metric {cfg.id!r} is not allowed on the eval service")
                if self._allowed_ids is not None and cfg.id not in self._allowed_ids:
                    try:
                        canonical = registry.resolve(cfg.id)[0].spec.id
                    except KeyError:
                        canonical = cfg.id
                    if canonical not in self._allowed_ids:
                        raise PolicyError(f"metric {cfg.id!r} is not on the service allow-list")
        if len(request.cases) > lim.max_cases:
            raise LimitExceeded(f"{len(request.cases)} cases > {lim.max_cases}")
        if request.options.runs > lim.max_runs:
            raise LimitExceeded(f"options.runs {request.options.runs} > {lim.max_runs}")
        for judge in (
            request.suite.judge if request.suite else None,
            request.options.judge_override,
        ):
            if judge is None:
                continue
            if judge.base_url and judge.base_url.rstrip("/") not in self.allowed_judge_base_urls:
                raise PolicyError("a judge base_url is not allowed on the eval service")
            if judge.samples > lim.max_judge_samples:
                raise LimitExceeded(f"judge samples {judge.samples} > {lim.max_judge_samples}")
            self._check_judge(judge)
        concurrency = request.options.max_concurrency
        if concurrency is not None and concurrency > lim.max_concurrency:
            raise LimitExceeded(f"options.max_concurrency {concurrency} > {lim.max_concurrency}")
        self._check_redact(request.options.privacy.redact)
        if request.target is not None:
            target = request.target
            if target.kind not in SERVICE_TARGET_KINDS:
                raise PolicyError(f"{target.kind} targets are not allowed on the eval service")
            if target.simulated_user is not None:  # judge calls not covered by the quote
                raise PolicyError("simulated-user targets are not allowed on the eval service")
            await self.check_url(target.uri, what="target")
        if request.dataset_ref:
            await self.check_url(request.dataset_ref.partition("#")[0], what="dataset_ref")

    def _check_judge(self, judge: Any) -> None:
        """A caller-pinned judge runs on the operator's credentials: bound its cost knobs."""
        unknown = set(judge.extra) - SERVICE_JUDGE_EXTRA
        if unknown:  # retries / max_in_flight / timeout_s multiply or stall judge calls
            raise PolicyError(f"judge extra {sorted(unknown)} is not allowed on the eval service")
        if judge.max_tokens > self.limits.max_judge_tokens:
            raise LimitExceeded(
                f"judge max_tokens {judge.max_tokens} > {self.limits.max_judge_tokens}"
            )
        if judge.provider not in SERVICE_JUDGE_PROVIDERS:
            raise PolicyError(f"judge provider {judge.provider!r} is not allowed on the service")
        if (
            self.allowed_judge_models is not None
            and f"{judge.provider}:{judge.model}" not in self.allowed_judge_models
        ):
            raise PolicyError(f"judge model {judge.provider}:{judge.model} is not allowed")

    def _check_redact(self, patterns: Iterable[str]) -> None:
        """Caller regexes run on the server: bounded, and only with the timeout engine."""
        patterns = list(patterns)
        if not patterns:
            return
        lim = self.limits
        if len(patterns) > lim.max_redact_patterns:
            raise LimitExceeded(f"{len(patterns)} redact patterns > {lim.max_redact_patterns}")
        if any(len(p) > lim.max_redact_pattern_len for p in patterns):
            raise LimitExceeded(f"a redact pattern is longer than {lim.max_redact_pattern_len}")
        if not _optional.available("regex"):
            raise PolicyError("options.privacy.redact needs the 'regex' module on the service")

    async def check_url(self, url: str | None, *, what: str = "url") -> None:
        """``url`` must be http(s) and every address of its host public (DNS included)."""
        if not url or urlsplit(url).scheme.lower() not in ("http", "https"):
            raise PolicyError(f"{what} must be a public http(s) URL")
        try:
            await public_host_check(url, self.resolver)
        except FetchError as exc:
            raise PolicyError(f"{what} is not a public URL: {exc}") from exc

    def check_cases(self, n_cases: int) -> None:
        if n_cases > self.limits.max_cases:
            raise LimitExceeded(f"{n_cases} cases > {self.limits.max_cases}")

    def estimate_judge_calls(
        self, request: EvalRequest, metrics: Iterable[Any], n_cases: int
    ) -> int:
        """Judge calls ≈ Σ over judge methods of cases × samples (an upper bound for most).

        ``metrics`` are resolved metrics (``.method`` and ``.config``); a metric's own
        ``params.samples`` takes precedence over the judge's ``samples``, as at run time.
        Bare :class:`MethodSpec` items count with the judge's ``samples``.
        """
        judge = (request.suite.judge if request.suite else None) or request.options.judge_override
        default = judge.samples if judge is not None else self.judge_samples
        total = 0
        for item in metrics:
            method = item if isinstance(item, MethodSpec) else item.method
            if method.kind is not Kind.JUDGE:
                continue
            cfg = getattr(item, "config", None)
            params = cfg.params if cfg is not None else {}
            samples = params.get("samples", default) or 1
            total += n_cases * max(1, int(samples))
        return total

    def check_judge_calls(self, estimate: int) -> None:
        if estimate > self.limits.max_judge_calls:
            raise LimitExceeded(
                f"estimated {estimate} judge calls > {self.limits.max_judge_calls}"
            )


def _is_builtin(spec: MetricSpec, method: MethodSpec) -> bool:
    """No plugins: the metric class and any attached method handler ship with the package."""
    try:
        cls, _ = registry.resolve(spec.id)
    except KeyError:
        return False
    if not cls.__module__.startswith(_BUILTIN_PREFIX):
        return False
    handler = registry.method_handler(spec.id, method.name)
    return handler is None or getattr(handler, "__module__", "").startswith(_BUILTIN_PREFIX)


def _scan(obj: Any, max_depth: int) -> None:
    stack: list[tuple[Any, int]] = [(obj, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            raise PolicyError(f"JSON nesting deeper than {max_depth}")
        if isinstance(node, Mapping):
            if "$ref" in node:
                raise PolicyError("$ref is not allowed in service requests: inline the content")
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)


class GuardedTransport(httpx.AsyncBaseTransport):
    """An httpx transport that applies the public-IP guard before every request.

    Used for every request the service makes on a caller's behalf (targets, datasets), so a
    redirect (followed by the client) to a private address is blocked hop by hop. With the
    default network transport the connection is pinned to the checked address (no DNS
    rebinding between the check and the connect), and response bodies are capped at
    ``max_response_bytes``.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        max_response_bytes: int | None = PolicyLimits.max_response_bytes,
    ):
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)
        self.resolver = resolver
        self.max_response_bytes = max_response_bytes
        self._guarded = public_transport(
            self.inner, resolver=resolver, max_response_bytes=max_response_bytes
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._guarded.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()
