"""Suite resolution: metrics, methods, params, extras, judge needs, lock and suite_hash."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .._optional import available
from ..exceptions import MissingExtraError, SuiteError
from ..hashing import hash_obj
from ..metrics import registry
from ..metrics.base import BaseMetric, Kind, MethodSpec, MetricSpec
from ..types.suite import LockEntry, MetricConfig, Suite

# A policy hook (the eval service passes UntrustedSuitePolicy.check): raises PolicyError to deny.
PolicyCheck = Callable[[MetricSpec, MethodSpec, MetricConfig], None]


@dataclass(frozen=True)
class ResolvedMetric:
    config: MetricConfig
    metric: BaseMetric
    method: MethodSpec

    @property
    def spec(self) -> MetricSpec:
        return self.metric.spec


@dataclass(frozen=True)
class ResolvedSuite:
    suite: Suite
    metrics: tuple[ResolvedMetric, ...]
    lock: Mapping[str, LockEntry]
    suite_hash: str

    @property
    def needs_judge(self) -> bool:
        return any(m.method.kind is Kind.JUDGE for m in self.metrics)


def _prompt_hash(metric: BaseMetric, method: MethodSpec, cfg: MetricConfig) -> str | None:
    found, value = registry.method_prompt_hash(metric.spec.id, method.name, cfg.params)
    if found:
        return value
    fn = getattr(metric, "prompt_hash", None)
    return fn(method, cfg.params) if callable(fn) else None


def resolve_suite(suite: Suite, *, policy: PolicyCheck | None = None) -> ResolvedSuite:
    """Validate ``suite`` against the registry and compute its ``suite_hash``.

    Raises :class:`SuiteError` or :class:`MissingExtraError` before any work starts.
    """
    resolved: list[ResolvedMetric] = []
    for cfg in suite.metrics:
        where = f"metric {cfg.key!r}"
        try:
            cls, alias_method = registry.resolve(cfg.id)
        except KeyError as exc:
            raise SuiteError(f"{where}: unknown metric id {cfg.id!r}") from exc
        spec = registry.effective_spec(cls)
        if cfg.method and alias_method and cfg.method != alias_method:
            raise SuiteError(
                f"{where}: alias {cfg.id!r} implies method {alias_method!r}, "
                f"but method {cfg.method!r} was given"
            )
        try:
            method = spec.method(cfg.method or alias_method)
        except KeyError as exc:
            raise SuiteError(f"{where}: {exc.args[0]}") from exc
        errors = spec.validate_params(cfg.params)
        if errors:
            raise SuiteError(f"{where}: invalid params: {'; '.join(errors)}")
        if method.extra_module and not available(method.extra_module):
            raise MissingExtraError(method.extra_module, method.extra or "all")
        if policy is not None:
            policy(spec, method, cfg)
        metric = cls()
        resolved.append(ResolvedMetric(config=cfg, metric=metric, method=method))
    lock = _build_lock(resolved)
    if suite.lock is not None:
        for metric_id, entry in lock.items():
            pinned = suite.lock.get(metric_id)
            if pinned is None:
                raise SuiteError(f"suite lock has no entry for {metric_id}")
            if pinned != entry:
                raise SuiteError(
                    f"suite lock mismatch for {metric_id}: pinned {pinned.to_dict()}, "
                    f"installed {entry.to_dict()}"
                )
    return ResolvedSuite(
        suite=suite, metrics=tuple(resolved), lock=lock, suite_hash=compute_suite_hash(suite, lock)
    )


def lock_key(rm: ResolvedMetric, repeated: frozenset[str] | set[str]) -> str:
    """A lock entry's key: the metric id, or the config key (``name``) for a metric id that
    the suite uses more than once (each use may pin its own method / prompt)."""
    return rm.config.key if rm.spec.id in repeated else rm.spec.id


def _build_lock(resolved: list[ResolvedMetric]) -> dict[str, LockEntry]:
    ids = [rm.spec.id for rm in resolved]
    repeated = {i for i in ids if ids.count(i) > 1}
    lock: dict[str, LockEntry] = {}
    for rm in resolved:
        key = lock_key(rm, repeated)
        if key in lock:
            raise SuiteError(
                f"metric {rm.config.key!r}: lock key {key!r} is used twice; "
                "rename the metric so its name differs from the other metric ids"
            )
        lock[key] = LockEntry(
            metric_version=rm.spec.version,
            prompt_hash=_prompt_hash(rm.metric, rm.method, rm.config),
        )
    return lock


def compute_suite_hash(suite: Suite, lock: Mapping[str, LockEntry]) -> str:
    """keccak over the resolved suite (``$ref`` already inlined) plus the effective lock."""
    body: dict[str, Any] = suite.to_dict()
    body.pop("lock", None)
    return hash_obj({"suite": body, "lock": {k: v.to_dict() for k, v in sorted(lock.items())}})


def build_lock(suite: Suite) -> dict[str, LockEntry]:
    """The lock that pins the installed metric and prompt versions for ``suite``."""
    return dict(resolve_suite(suite).lock)
