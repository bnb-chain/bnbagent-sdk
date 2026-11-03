"""Suites: the negotiated / published set of metrics, thresholds, judge pin and pass rule."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..exceptions import SchemaError, SuiteError
from ._serde import Serializable, from_dict
from .judge import JudgeConfig

AggregationMode = Literal["weighted_average", "mean", "min", "max"]


@dataclass(frozen=True)
class MetricConfig(Serializable):
    id: str
    name: str | None = None
    method: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    threshold: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    value_target: float | None = None
    weight: float = 1.0
    required: bool = False
    allow_truncation: bool = False

    @property
    def key(self) -> str:
        """The name results are reported under."""
        return self.name or self.id

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError("weight must be >= 0")
        if self.threshold is not None and not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be within [0, 1]")


@dataclass(frozen=True)
class PassRule(Serializable):
    aggregation: AggregationMode = "weighted_average"
    min_aggregate: float = 0.7
    min_pass_rate: float = 1.0
    inconclusive_band: float = 0.0
    max_error_rate: float = 0.0
    on_inconclusive: Literal["retry", "decline", "submit"] = "retry"


@dataclass(frozen=True)
class LockEntry(Serializable):
    metric_version: str
    prompt_hash: str | None = None


@dataclass(frozen=True)
class Suite(Serializable):
    name: str
    version: str
    metrics: tuple[MetricConfig, ...]
    judge: JudgeConfig | None = None
    pass_rule: PassRule = field(default_factory=PassRule)
    lock: Mapping[str, LockEntry] | None = None  # metric id (name if repeated) -> versions
    description: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.metrics:
            raise ValueError("a suite needs at least one metric")
        keys = [m.key for m in self.metrics]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"duplicate metric names {dupes}; set a distinct 'name' for each")

    @classmethod
    def load(
        cls,
        source: str | Path | Mapping[str, Any],
        *,
        base_dir: str | Path | None = None,
        refs: bool = True,
    ) -> Suite:
        """Load a suite from a YAML/JSON file path or a mapping; inline local ``$ref`` files.

        ``refs=False`` (suites from another party) rejects ``$ref`` instead of reading files.
        """
        if isinstance(source, Mapping):
            data, base = dict(source), Path(base_dir or ".")
        else:
            path = Path(source)
            data, base = _read_file(path), path.parent
        if not refs and _has_ref(data):
            raise SuiteError("$ref is not allowed in this suite: inline the content")
        try:
            resolved = resolve_refs(data, base) if refs else data
            return from_dict(cls, resolved)
        except SchemaError as exc:
            raise SuiteError(f"invalid suite: {exc}") from exc


@dataclass(frozen=True)
class Benchmark(Serializable):
    id: str
    version: str
    dataset_uri: str
    dataset_hash: str
    suite_hash: str
    splits: tuple[str, ...] = ()
    visibility: Literal["public", "held_out"] = "public"


def _read_file(path: Path) -> Any:
    import yaml

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SuiteError(f"cannot read {path}: {exc}") from exc
    is_json = path.suffix.lower() == ".json"
    try:
        return json.loads(text) if is_json else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as exc:
        raise SuiteError(f"{path}: not valid {'JSON' if is_json else 'YAML'}: {exc}") from exc


def _has_ref(obj: Any) -> bool:
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, Mapping):
            if "$ref" in node:
                return True
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False


def resolve_refs(obj: Any, base: Path, _depth: int = 0) -> Any:
    """Inline ``{"$ref": "relative/file.json"}`` objects. Only local files are allowed."""
    if _depth > 20:
        raise SuiteError("$ref nesting too deep")
    if isinstance(obj, Mapping):
        if set(obj) == {"$ref"} and isinstance(obj["$ref"], str):
            ref = obj["$ref"]
            if "://" in ref or ref.startswith("//"):
                raise SuiteError(f"remote $ref not allowed: {ref}")
            target = (base / ref).resolve()
            return resolve_refs(_read_file(target), target.parent, _depth + 1)
        return {k: resolve_refs(v, base, _depth) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_refs(v, base, _depth) for v in obj]
    return obj
