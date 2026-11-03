"""A small JSON-Schema subset validator (no dependency).

Used for metric ``params_schema`` and judge output schemas. Supported keywords: ``type``,
``properties``, ``required``, ``additionalProperties`` (bool or schema), ``items``, ``enum``,
``const``, ``minimum``, ``maximum``, ``exclusiveMinimum``, ``exclusiveMaximum``, ``minLength``,
``maxLength``, ``pattern``, ``minItems``, ``maxItems``, ``anyOf``, ``oneOf`` and local ``$ref``
(``#/...`` JSON pointers into the same schema; any other ``$ref`` is an error, never fetched).
``errorMessage`` replaces the generic ``anyOf`` failure message.
Unknown keywords are ignored.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (Mapping,),
    "array": (list, tuple),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _is_type(value: Any, name: str) -> bool:
    if name in ("integer", "number") and isinstance(value, bool):
        return False
    if name == "integer" and isinstance(value, float):
        return value.is_integer()
    return isinstance(value, _TYPES[name])


_MAX_REF_DEPTH = 64


def _pointer(root: Mapping[str, Any], ref: str) -> Any:
    node: Any = root
    for part in ref[1:].lstrip("/").split("/") if ref not in ("#", "#/") else ():
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(ref)
    return node


def validate(
    value: Any,
    schema: Mapping[str, Any],
    path: str = "$",
    _root: Mapping[str, Any] | None = None,
    _depth: int = 0,
) -> list[str]:
    """Return a list of human-readable errors (empty when ``value`` is valid)."""
    root = schema if _root is None else _root
    errors: list[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#"):
            return [f"{path}: only local $ref ('#/...') is supported, got {ref!r}"]
        if _depth >= _MAX_REF_DEPTH:
            return [f"{path}: $ref nesting deeper than {_MAX_REF_DEPTH}"]
        try:
            target = _pointer(root, ref)
        except KeyError:
            return [f"{path}: unresolvable $ref {ref!r}"]
        if not isinstance(target, Mapping):
            return [f"{path}: $ref {ref!r} does not point to a schema"]
        errors.extend(validate(value, target, path, root, _depth + 1))
    stype = schema.get("type")
    if stype is not None:
        names = [stype] if isinstance(stype, str) else list(stype)
        if not any(_is_type(value, n) for n in names):
            return [
                *errors,
                f"{path}: expected {'/'.join(names)}, got {type(value).__name__}",
            ]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {list(schema['enum'])!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: must be < {schema['exclusiveMaximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
    if isinstance(value, Mapping):
        props: Mapping[str, Any] = schema.get("properties", {})
        for key in schema.get("required", ()):
            if key not in value:
                errors.append(f"{path}: missing required property '{key}'")
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                errors.extend(validate(item, props[key], f"{path}.{key}", root, _depth))
            elif additional is False:
                errors.append(f"{path}: unknown property '{key}'")
            elif isinstance(additional, Mapping):
                errors.extend(validate(item, additional, f"{path}.{key}", root, _depth))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if isinstance(schema.get("items"), Mapping):
            for i, item in enumerate(value):
                errors.extend(validate(item, schema["items"], f"{path}[{i}]", root, _depth))
    for key in ("anyOf", "oneOf"):
        if key in schema:
            matches = [not validate(value, sub, path, root, _depth) for sub in schema[key]]
            if key == "anyOf" and not any(matches):
                errors.append(
                    f"{path}: {schema.get('errorMessage', 'does not match any allowed schema')}"
                )
            if key == "oneOf" and sum(matches) != 1:
                errors.append(f"{path}: must match exactly one allowed schema")
    return errors
