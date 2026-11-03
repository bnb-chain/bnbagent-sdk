"""Metric registry: canonical IDs, aliases, built-ins and (local-only) plugins."""

from __future__ import annotations

import dataclasses
import importlib
import logging
import re
from collections.abc import Callable, Mapping
from importlib.metadata import entry_points
from typing import Any

from .base import BaseMetric, MetricSpec

logger = logging.getLogger(__name__)

ID_PATTERN = re.compile(r"^[a-z]+\.[a-z0-9_]+$")
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_.]+)+$")
ENTRY_POINT_GROUP = "bnbagent_eval.metrics"

# Built-in metrics live in these category packages; every public module in them is imported
# (auto-discovery: adding a metric never requires editing a shared list).
CATEGORY_PACKAGES = (
    "custom",
    "text",
    "format",
    "ops",
    "agent",
    "quality",
    "safety",
    "rag",
    "conversation",
    "onchain",
    "code",
    "redteam",
    "vendors",  # vendor methods (ragas, deepeval, ...) attach to the canonical metrics
)

_METRICS: dict[str, type[BaseMetric]] = {}
# metric id -> method name -> (MethodSpec, handler(metric, case, ctx) -> Outcome | float)
_EXTRA_METHODS: dict[str, dict[str, tuple[Any, Callable[..., Any]]]] = {}
_ALIASES: dict[str, tuple[str, str | None]] = {}
_EXTRA_PARAMS: dict[str, dict[str, Any]] = {}
_PROMPT_HASHES: dict[tuple[str, str], Callable[[Mapping[str, Any]], str | None]] = {}
_builtins_loaded = False


def register(cls: type[BaseMetric]) -> type[BaseMetric]:
    """Class decorator registering a metric under ``cls.spec.id`` (and its aliases)."""
    spec: MetricSpec = cls.spec
    if not ID_PATTERN.match(spec.id):
        raise ValueError(f"invalid metric id {spec.id!r} (expected <category>.<name>)")
    if spec.id.split(".", 1)[0] != spec.category:
        raise ValueError(f"metric {spec.id}: category {spec.category!r} must equal the id prefix")
    if not spec.methods:
        raise ValueError(f"metric {spec.id}: at least one method is required")
    for method in spec.methods:
        if not hasattr(cls, f"_evaluate_{method.name}"):
            raise ValueError(f"metric {spec.id}: missing _evaluate_{method.name}")
    existing = _METRICS.get(spec.id)
    if existing is not None and existing is not cls:
        raise ValueError(f"metric {spec.id} is already registered by {existing.__qualname__}")
    for alias in spec.aliases:
        _add_alias(alias, spec.id, None)
    _METRICS[spec.id] = cls
    return cls


def add_alias(alias: str, metric_id: str, method: str | None = None) -> None:
    """Register ``alias`` (e.g. ``ragas.faithfulness``) for ``metric_id`` with ``method``."""
    _add_alias(alias, metric_id, method)


def _add_alias(alias: str, metric_id: str, method: str | None) -> None:
    if not ALIAS_PATTERN.match(alias):
        raise ValueError(f"invalid alias {alias!r}")
    if alias in _METRICS:
        raise ValueError(f"alias {alias!r} collides with a canonical metric id")
    current = _ALIASES.get(alias)
    if current is not None and current != (metric_id, method):
        raise ValueError(f"alias {alias!r} already maps to {current}")
    _ALIASES[alias] = (metric_id, method)


def load_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    import pkgutil

    for category in CATEGORY_PACKAGES:
        name = f"bnbagent_eval.metrics.{category}"
        try:
            package = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name == name:
                continue  # category package absent
            raise
        for info in pkgutil.iter_modules(package.__path__):
            if not info.name.startswith("_"):
                importlib.import_module(f"{name}.{info.name}")
    _builtins_loaded = True


def load_plugins() -> list[str]:
    """Load third-party metrics from the ``bnbagent_eval.metrics`` entry point group.

    Local mode only: the HTTP service never calls this (untrusted-suite policy).
    """
    loaded = []
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            ep.load()
            loaded.append(ep.name)
        except Exception:  # noqa: BLE001 - a broken plugin must not break evaluation
            logger.warning("failed to load metric plugin %s", ep.name, exc_info=True)
    return loaded


def resolve(id_or_alias: str) -> tuple[type[BaseMetric], str | None]:
    """Return ``(metric_class, method_from_alias)``. Raises ``KeyError`` if unknown."""
    load_builtins()
    if id_or_alias in _METRICS:
        return _METRICS[id_or_alias], None
    if id_or_alias in _ALIASES:
        metric_id, method = _ALIASES[id_or_alias]
        return _METRICS[metric_id], method
    raise KeyError(f"unknown metric {id_or_alias!r}")


def register_method(
    metric_id: str,
    method: Any,
    handler: Callable[..., Any],
    *,
    params: Mapping[str, Any] | None = None,
    prompt_hash: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> None:
    """Attach an extra method (e.g. a vendor bridge) to a registered canonical metric.

    ``handler(metric, case, ctx)`` (sync or async) returns an ``Outcome`` or a raw float.
    ``params`` adds JSON-Schema properties to the metric's params schema (method-specific
    options); ``prompt_hash(params)`` reports the method's prompt hash for suite locks.
    """
    if metric_id not in _METRICS:
        raise KeyError(f"unknown metric {metric_id!r}")
    spec = _METRICS[metric_id].spec
    if method.name in spec.method_names or method.name in _EXTRA_METHODS.get(metric_id, {}):
        raise ValueError(f"metric {metric_id} already has method {method.name!r}")
    _EXTRA_METHODS.setdefault(metric_id, {})[method.name] = (method, handler)
    merged = _EXTRA_PARAMS.setdefault(metric_id, {})
    for key, schema in (params or {}).items():
        prior = merged.get(key)
        if prior is None or prior == schema:
            merged[key] = dict(schema)
        else:  # two methods use the same param name differently: accept either
            options = prior["anyOf"] if set(prior) == {"anyOf"} else [prior]
            merged[key] = {"anyOf": [*options, dict(schema)]}
    if prompt_hash is not None:
        _PROMPT_HASHES[(metric_id, method.name)] = prompt_hash


def method_handler(metric_id: str, method_name: str) -> Callable[..., Any] | None:
    entry = _EXTRA_METHODS.get(metric_id, {}).get(method_name)
    return entry[1] if entry else None


def method_prompt_hash(
    metric_id: str, method_name: str, params: Mapping[str, Any]
) -> tuple[bool, str | None]:
    """``(True, hash)`` if the method registered a prompt-hash hook, else ``(False, None)``."""
    hook = _PROMPT_HASHES.get((metric_id, method_name))
    return (True, hook(params)) if hook is not None else (False, None)


def effective_spec(cls: type[BaseMetric]) -> MetricSpec:
    """The metric's spec including methods attached with :func:`register_method`."""
    extra = _EXTRA_METHODS.get(cls.spec.id)
    if not extra:
        return cls.spec
    schema = cls.spec.params_schema
    extra_params = _EXTRA_PARAMS.get(cls.spec.id)
    if extra_params:
        props = {**extra_params, **dict(schema.get("properties", {}))}
        schema = {**schema, "properties": props}
    return dataclasses.replace(
        cls.spec,
        methods=cls.spec.methods + tuple(m for m, _ in extra.values()),
        params_schema=schema,
    )


def get_spec(metric_id: str) -> MetricSpec:
    return effective_spec(resolve(metric_id)[0])


def list_specs() -> list[MetricSpec]:
    load_builtins()
    return [effective_spec(cls) for _, cls in sorted(_METRICS.items())]


def aliases() -> dict[str, tuple[str, str | None]]:
    load_builtins()
    return dict(_ALIASES)
