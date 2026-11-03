"""Generic dataclass <-> JSON-compatible dict conversion.

Rules:
- ``None``-valued optional fields and empty defaults are kept (explicit is better for hashing).
- Tuples serialize as lists; ``Mapping`` fields as plain dicts.
- Unions of dataclasses are resolved through a discriminator: a field whose default is a
  ``Literal`` constant (e.g. ``kind: Literal["tool"] = "tool"``).
- Unknown keys raise :class:`SchemaError`, unless the dataclass has an ``extensions`` field,
  in which case they are preserved there and written back on serialization.
"""

from __future__ import annotations

import dataclasses
import enum
import sys
import types
import typing
from collections.abc import Mapping, Sequence
from functools import cache
from typing import Any, Literal, Union, get_args, get_origin

from ..exceptions import SchemaError

EXTENSIONS = "extensions"


class Serializable:
    """Mixin giving dataclasses ``to_dict`` / ``from_dict``."""

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]):
        return from_dict(cls, data)


@cache
def _hints(cls: type) -> dict[str, Any]:
    # Resolve forward references across the sibling ``types`` modules, then the class's own module.
    globalns: dict[str, Any] = {}
    for name, module in list(sys.modules.items()):
        if module is not None and name.startswith(__package__ + "."):
            globalns.update(vars(module))
    own = sys.modules.get(cls.__module__)
    if own is not None:
        globalns.update(vars(own))
    return typing.get_type_hints(cls, globalns=globalns)


def to_dict(obj: Any) -> Any:
    """Convert dataclasses (recursively) into JSON-compatible values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {}
        extensions: Mapping[str, Any] = {}
        for f in dataclasses.fields(obj):
            if not f.init:
                continue
            value = getattr(obj, f.name)
            if f.name == EXTENSIONS:
                extensions = value or {}
                continue
            out[f.name] = to_dict(value)
        for k, v in extensions.items():
            out.setdefault(k, to_dict(v))
        return out
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, Mapping):
        return {str(k): to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    return obj


def _is_union(tp: Any) -> bool:
    return get_origin(tp) is Union or isinstance(tp, types.UnionType)


def _discriminator(cls: type) -> tuple[str, Any] | None:
    hints = _hints(cls)
    for f in dataclasses.fields(cls):
        tp = hints.get(f.name)
        if get_origin(tp) is Literal and f.default is not dataclasses.MISSING:
            return f.name, f.default
    return None


def _convert(tp: Any, value: Any, path: str) -> Any:
    if tp is Any or tp is object:
        return value
    if value is None:
        if _is_union(tp) and type(None) in get_args(tp):
            return None
        if tp is type(None):
            return None
        raise SchemaError(f"{path}: must not be null")
    if _is_union(tp):
        options = [a for a in get_args(tp) if a is not type(None)]
        if len(options) == 1:
            return _convert(options[0], value, path)
        dataclass_options = [a for a in options if dataclasses.is_dataclass(a)]
        if dataclass_options and isinstance(value, Mapping):
            for opt in dataclass_options:
                disc = _discriminator(opt)
                if disc and value.get(disc[0]) == disc[1]:
                    return from_dict(opt, value, path)
        errors = []
        for opt in options:
            try:
                return _convert(opt, value, path)
            except (SchemaError, TypeError, ValueError) as exc:
                errors.append(str(exc))
        raise SchemaError(f"{path}: value does not match any allowed type ({'; '.join(errors)})")
    origin = get_origin(tp)
    if origin is Literal:
        if value not in get_args(tp):
            raise SchemaError(f"{path}: must be one of {list(get_args(tp))!r}, got {value!r}")
        return value
    if dataclasses.is_dataclass(tp):
        if isinstance(value, tp):
            return value
        if not isinstance(value, Mapping):
            raise SchemaError(f"{path}: expected an object")
        return from_dict(tp, value, path)
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return tp(value)
    if origin in (tuple, Sequence, list) or tp in (tuple, list):
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise SchemaError(f"{path}: expected an array")
        args = get_args(tp)
        item_tp = args[0] if args else Any
        items = [_convert(item_tp, v, f"{path}[{i}]") for i, v in enumerate(value)]
        return list(items) if origin is list else tuple(items)
    if origin in (dict, Mapping) or tp in (dict, Mapping):
        if not isinstance(value, Mapping):
            raise SchemaError(f"{path}: expected an object")
        args = get_args(tp)
        val_tp = args[1] if len(args) == 2 else Any
        return {str(k): _convert(val_tp, v, f"{path}.{k}") for k, v in value.items()}
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaError(f"{path}: expected a number")
        return float(value)
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError(f"{path}: expected an integer")
        return value
    if tp is str:
        if not isinstance(value, str):
            raise SchemaError(f"{path}: expected a string")
        return value
    if tp is bool:
        if not isinstance(value, bool):
            raise SchemaError(f"{path}: expected a boolean")
        return value
    return value


def from_dict(cls: type, data: Mapping[str, Any], path: str = "$") -> Any:
    """Build dataclass ``cls`` from a JSON-compatible mapping (strict about unknown keys)."""
    if not isinstance(data, Mapping):
        raise SchemaError(f"{path}: expected an object for {cls.__name__}")
    hints = _hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls) if f.init}
    kwargs: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    for key, value in data.items():
        if key not in fields or key == EXTENSIONS:
            unknown[key] = value
            continue
        kwargs[key] = _convert(hints[key], value, f"{path}.{key}")
    if unknown:
        if EXTENSIONS in fields:
            existing = (
                dict(data.get(EXTENSIONS) or {})
                if isinstance(data.get(EXTENSIONS), Mapping)
                else {}
            )
            unknown.pop(EXTENSIONS, None)
            kwargs[EXTENSIONS] = {**existing, **unknown}
        else:
            raise SchemaError(f"{path}: unknown field(s) for {cls.__name__}: {sorted(unknown)}")
    missing = [
        n
        for n, f in fields.items()
        if n not in kwargs
        and f.default is dataclasses.MISSING
        and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
    ]
    if missing:
        raise SchemaError(f"{path}: missing required field(s) for {cls.__name__}: {missing}")
    try:
        return cls(**kwargs)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{path}: invalid {cls.__name__}: {exc}") from exc
